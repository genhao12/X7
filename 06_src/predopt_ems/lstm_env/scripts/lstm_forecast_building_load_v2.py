# -*- coding: utf-8 -*-
"""
lstm_forecast_building_load_v2.py — Load 预测调参 v2（Gate B ①，三件套）

目标：解决 v1 MASE=1.61>1（LSTM 打不过 seasonal naive 基准）的问题。
三件套（2026-08-08 用户选定，全部实现）：
  1) 【差分目标】预测"未来相对过去 96 步（=lag）的变化量"Δ，而非绝对值。
     因 seq_len=horizon=96，未来 96 步的"昨天同时刻"恰等于输入窗口本身，
     Δ = y[t+1:t+97] − y[t-95:t+1]；预测恢复 yhat = 输入窗口 + Δhat。
     → 模型空间包含 Δ≡0（即 seasonal naive），最差 ≈ 基准，好则 MASE<1。
  2) 【时间特征】输入拼接 hour/dow 的 sin-cos 周期编码（4 维），先验日内/周内周期。
  3) 【MAE 损失】训练损失改 L1（与 MASE/MAE 评估口径一致），弃 MSE 的保守平均化。

v9 约束：
  - 2016 训练 / 2017 验证（选超参与中位 seed）/ 2018 一次性 held-out（§3.1/§13）；
  - 【本调参脚本不评估 2018】——调参期间不得使用 2018 结果（v9 §3.1/§13），
    2018 留待超参定稿后在正式脚本一次性评估；
  - 5 固定种子训练，2017 选中位表现 seed（§13）。

运行环境：LSTM venv（D:/matlab/C/LSTM_Forecast/.venv，Py3.13 + torch CPU）
命令：
  D:/matlab/C/LSTM_Forecast/.venv/Scripts/python.exe 06_src/predopt_ems/lstm_env/scripts/lstm_forecast_building_load_v2.py
依赖：lstm_utils.py 位于 D:/matlab/C/LSTM_Forecast/（共享模块，不移动，LSTM_Forecast 保持原样）
输出：06_src/predopt_ems/lstm_env/results/BuildingLoad_v2_metrics.json + 2017 5-seed MASE 汇总
验收目标（Gate B ① 调参阶段）：2017 5-seed 中位 MASE < 1.0（优于 naive）；
最终目标（第二关）：LSTM 优于 B* ≥5%（MASE ≤ 0.95）。
"""

import os
import sys
import json

import matplotlib

matplotlib.use("Agg")
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

_HERE = os.path.dirname(os.path.abspath(__file__))
_LSTM_LIB = r"D:\matlab\C\LSTM_Forecast"      # 共享模块 lstm_utils.py 所在（保持原样）
for p in (_HERE, _LSTM_LIB):
    if p not in sys.path:
        sys.path.insert(0, p)
from lstm_utils import (load_series, split_by_year, compute_metrics,
                        seasonal_naive_mae_train, DEFAULT_SEEDS)

CSV = r"D:\matlab\C\02_data_processed\tradestreet_pipeline\64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv"
OUT_DIR = os.path.join(_HERE, "..", "results")   # 输出统一到 lstm_env/results/
OUT_JSON = os.path.join(OUT_DIR, "BuildingLoad_v2_metrics.json")

SEQ_LEN, HORIZON, LAG = 96, 96, 96
STRIDE = 24
HIDDEN, N_LAYERS, DROPOUT = 64, 2, 0.1
EPOCHS, BATCH, LR, PATIENCE = 40, 64, 1e-3, 8
LOSS = "mae"            # mae / mse / smooth_l1
SEEDS = DEFAULT_SEEDS
N_TIME_FEAT = 4         # hour_sin, hour_cos, dow_sin, dow_cos


# ------------------------------------------------------------------
# 时间特征（sin-cos 周期编码，全序列一次性生成）
# ------------------------------------------------------------------

def build_time_features(index: pd.DatetimeIndex) -> np.ndarray:
    """对每个时刻生成 4 维周期编码：hour(24h)、dow(7d) 的 sin/cos。"""
    n = len(index)
    hour = index.hour + index.minute / 60.0
    dow = index.dayofweek.astype(float)
    f = np.zeros((n, N_TIME_FEAT), dtype=np.float32)
    f[:, 0] = np.sin(2 * np.pi * hour / 24.0)
    f[:, 1] = np.cos(2 * np.pi * hour / 24.0)
    f[:, 2] = np.sin(2 * np.pi * dow / 7.0)
    f[:, 3] = np.cos(2 * np.pi * dow / 7.0)
    return f


# ------------------------------------------------------------------
# 差分样本构造（三件套核心 1：目标 = 未来 − 输入窗口）
# ------------------------------------------------------------------

def make_sequences_diff(values, time_feat, seq_len, horizon, stride):
    """输入 (values+time_feat, seq_len) → 输出 Δ = future − input_window。
    future[t+1:t+1+horizon] 的 lag=horizon 基准恰为 input_window[t-seq_len+1:t+1]。"""
    X, y = [], []
    L = len(values)
    for i in range(0, L - seq_len - horizon + 1, stride):
        x_in = values[i:i + seq_len]                          # (seq_len,)
        x_tf = time_feat[i:i + seq_len]                       # (seq_len, 4)
        fut = values[i + seq_len:i + seq_len + horizon]       # (horizon,)
        xin = np.concatenate([x_in[:, None], x_tf], axis=1)   # (seq_len, 5)
        d = fut - x_in                                        # Δ（与输入逐元素对应）
        X.append(xin)
        y.append(d)
    return np.asarray(X, dtype=np.float32), np.asarray(y, dtype=np.float32)


# ------------------------------------------------------------------
# 模型（input_size 可配）
# ------------------------------------------------------------------

class LSTMForecasterInput(nn.Module):
    """LSTM 编码最后一步隐状态 -> FC 直接输出 horizon 步（input_size 可配）。"""

    def __init__(self, input_size, hidden_size=64, num_layers=2,
                 dropout=0.1, horizon=96):
        super().__init__()
        self.lstm = nn.LSTM(input_size, hidden_size, num_layers,
                            batch_first=True, dropout=dropout)
        self.fc = nn.Linear(hidden_size, horizon)

    def forward(self, x):
        _, (h, _) = self.lstm(x)
        return self.fc(h[-1])


# ------------------------------------------------------------------
# 训练（loss_fn 可选 mae/mse/smooth_l1）
# ------------------------------------------------------------------

def train_model(model, train_loader, val_loader, epochs, lr, patience,
                device, seed, loss_fn="mae"):
    torch.manual_seed(seed)
    np.random.seed(seed)

    if loss_fn == "mae":
        criterion = nn.L1Loss()
    elif loss_fn == "smooth_l1":
        criterion = nn.SmoothL1Loss()
    else:
        criterion = nn.MSELoss()

    optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=1e-5)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="min", factor=0.5, patience=3)

    history = {"train": [], "val": []}
    best_val = float("inf")
    best_state = None
    wait = 0

    for epoch in range(1, epochs + 1):
        model.train()
        train_loss = 0.0
        for xb, yb in train_loader:
            xb, yb = xb.to(device), yb.to(device)
            optimizer.zero_grad()
            loss = criterion(model(xb), yb)
            loss.backward()
            optimizer.step()
            train_loss += loss.item() * xb.size(0)
        train_loss /= len(train_loader.dataset)

        model.eval()
        val_loss = 0.0
        with torch.no_grad():
            for xb, yb in val_loader:
                xb, yb = xb.to(device), yb.to(device)
                loss = criterion(model(xb), yb)
                val_loss += loss.item() * xb.size(0)
        val_loss /= len(val_loader.dataset)
        scheduler.step(val_loss)

        history["train"].append(train_loss)
        history["val"].append(val_loss)

        if val_loss < best_val - 1e-4:
            best_val = val_loss
            best_state = {k: v.detach().cpu().clone()
                          for k, v in model.state_dict().items()}
            wait = 0
        else:
            wait += 1
            if wait >= patience:
                print(f"  [seed {seed}] 早停于 epoch {epoch}")
                break

        if epoch == 1 or epoch % 10 == 0:
            print(f"  [seed {seed}] epoch {epoch:3d} | train {train_loss:.6f} | val {val_loss:.6f}")

    if best_state is not None:
        model.load_state_dict(best_state)
    return model, history


# ------------------------------------------------------------------
# 块预测（差分输出恢复绝对值：yhat = 输入窗口 + Δhat）
# ------------------------------------------------------------------

def predict_blocks_diff(model, values, time_feat, start_idx, seq_len, horizon,
                        device, clip_nonneg=False):
    model.eval()
    actuals, preds = [], []
    with torch.no_grad():
        start = start_idx
        while start + seq_len + horizon <= len(values):
            x_in = values[start:start + seq_len]
            x_tf = time_feat[start:start + seq_len]
            xin = np.concatenate([x_in[:, None], x_tf], axis=1)[None, :, :]
            xin = torch.from_numpy(xin).to(device)
            d = model(xin).cpu().numpy().reshape(-1)
            y = x_in + d                               # 恢复绝对值
            if clip_nonneg:
                y = np.maximum(y, 0.0)
            actuals.append(values[start + seq_len:start + seq_len + horizon])
            preds.append(y)
            start += horizon
    return np.concatenate(actuals), np.concatenate(preds)


# ------------------------------------------------------------------
# 主流程（仅 2016 训练 / 2017 验证；不评估 2018）
# ------------------------------------------------------------------

def main():
    print("=" * 70)
    print("Load 预测调参 v2（Gate B ①）— 三件套：差分目标 + 时间特征 + MAE 损失")
    print("年度切分: 2016 训练 / 2017 验证(选超参+中位seed) / 2018 不评估(held-out)")
    print(f"超参: hidden={HIDDEN} layers={N_LAYERS} drop={DROPOUT} epochs={EPOCHS} "
          f"batch={BATCH} lr={LR} patience={PATIENCE} loss={LOSS} stride={STRIDE}")
    print("=" * 70)

    os.makedirs(OUT_DIR, exist_ok=True)

    df = load_series(CSV, "p_building_load_reconstructed_kW")
    print(f"数据范围: {df.index[0]} ~ {df.index[-1]}，共 {len(df)} 条")
    train, val, _test = split_by_year(df, "p_building_load_reconstructed_kW")

    # 归一化（仅 2016 训练段拟合，防泄漏）
    from sklearn.preprocessing import MinMaxScaler
    scaler = MinMaxScaler()
    train_s = scaler.fit_transform(train.reshape(-1, 1)).ravel()
    val_s = scaler.transform(val.reshape(-1, 1)).ravel()

    # 时间特征（全序列；与切分对齐）
    tf = build_time_features(df.index)
    n_train, n_val = len(train), len(val)
    tf_train, tf_val = tf[:n_train], tf[n_train:n_train + n_val]

    naive_mae = seasonal_naive_mae_train(train, LAG)
    print(f"\nMASE 基准（2016 训练段 seasonal naive, lag={LAG}）: MAE = {naive_mae:.4f} kW（=MASE 1.0 对应）")

    Xtr, ytr = make_sequences_diff(train_s, tf_train, SEQ_LEN, HORIZON, STRIDE)
    Xva, yva = make_sequences_diff(val_s, tf_val, SEQ_LEN, HORIZON, STRIDE)
    print(f"滑窗样本: 2016 train {len(Xtr)} | 2017 val {len(Xva)} "
          f"(输入 {SEQ_LEN}×{1 + N_TIME_FEAT} 维 -> 差分输出 {HORIZON} 步)")

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"设备: {device}")

    train_ds = TensorDataset(torch.from_numpy(Xtr), torch.from_numpy(ytr))
    val_ds = TensorDataset(torch.from_numpy(Xva), torch.from_numpy(yva))
    train_loader = DataLoader(train_ds, batch_size=BATCH, shuffle=True, num_workers=0)
    val_loader = DataLoader(val_ds, batch_size=BATCH, shuffle=False, num_workers=0)

    # 5-seed 训练 + 2017 验证选中位（v9 §13）
    print(f"\n5-seed 训练（seeds={SEEDS}），2017 验证 MASE 选中位 seed...")
    val_primary = {}
    histories = {}
    for seed in SEEDS:
        torch.manual_seed(seed)
        model = LSTMForecasterInput(input_size=1 + N_TIME_FEAT,
                                    hidden_size=HIDDEN, num_layers=N_LAYERS,
                                    dropout=DROPOUT, horizon=HORIZON).to(device)
        model, history = train_model(model, train_loader, val_loader,
                                     EPOCHS, LR, PATIENCE, device, seed, LOSS)
        act_s, pred_s = predict_blocks_diff(model, val_s, tf_val, 0,
                                            SEQ_LEN, HORIZON, device)
        act = scaler.inverse_transform(act_s.reshape(-1, 1)).ravel()
        pred = scaler.inverse_transform(pred_s.reshape(-1, 1)).ravel()
        mm = compute_metrics(act, pred, train, LAG)
        val_primary[seed] = mm["mase"]
        histories[seed] = history
        print(f"  seed {seed}: 2017 MASE = {val_primary[seed]:.4f} | MAE = {mm['mae_kW']:.3f} kW | R² = {mm['r2']:.3f}")

    arr = np.array([val_primary[s] for s in SEEDS])
    chosen = SEEDS[int(np.argsort(arr)[len(SEEDS) // 2])]
    improve = (1.0 - float(arr.mean())) * 100.0
    print(f"\n2017 5-seed MASE: mean={arr.mean():.4f} std={arr.std():.4f} | "
          f"中位 seed={chosen} ({val_primary[chosen]:.4f}) | 相对 naive 改善 {improve:.2f}%")

    # 调参判据（Gate B ① 阶段）
    ok = bool(float(arr.mean()) < 1.0)
    ok_target = bool(float(arr.mean()) <= 0.95)   # 第二关 LSTM 优于 B*≥5% 的代理判据
    print("-" * 70)
    print(f"调参判据: 2017 5-seed mean MASE < 1.0（优于 naive）→ {'PASS ✅' if ok else 'FAIL ❌'} | "
          f"≤0.95（B* 优于 5% 代理）→ {'PASS ✅' if ok_target else '未达'}")

    out = {
        "timestamp": pd.Timestamp.now().isoformat(timespec="seconds"),
        "purpose": "Load 调参 v2（三件套：差分目标+时间特征+MAE），仅 2017 验证，未评估 2018",
        "cfg": {"seq_len": SEQ_LEN, "horizon": HORIZON, "lag": LAG, "stride": STRIDE,
                "hidden": HIDDEN, "layers": N_LAYERS, "dropout": DROPOUT,
                "epochs": EPOCHS, "batch": BATCH, "lr": LR, "patience": PATIENCE,
                "loss": LOSS, "time_feat": N_TIME_FEAT, "seeds": SEEDS},
        "naive_mae_train_lag96": float(naive_mae),
        "seeds_mase_2017": {str(s): float(v) for s, v in val_primary.items()},
        "summary_2017": {"mean_mase": float(arr.mean()), "std_mase": float(arr.std()),
                         "chosen_seed": chosen, "chosen_mase": float(val_primary[chosen]),
                         "improve_vs_naive_pct": round(improve, 3)},
        "criteria": {"mean_mase_lt_1": bool(ok), "mean_mase_le_095": bool(ok_target)},
        "history_chosen": histories[chosen],
        "note": ("调参期间未评估 2018（v9 §13 held-out）；超参定稿后由正式脚本一次性评估 2018。"
                 "差分目标使模型空间包含 seasonal naive（Δ≡0），MASE<1 即优于基准。"),
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n结果已保存: {OUT_JSON}")
    return bool(ok)


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
