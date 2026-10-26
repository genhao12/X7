# -*- coding: utf-8 -*-
"""
lstm_forecast_building_load_v3.py — Load 预测调参 v3（Gate B ①，方案 A）

v2 结论（20:20 运行核验）：MASE>1 是口径失真非模型差——
  2016/2017/2018 自身 seasonal naive MAE = 7.656/12.845/10.613 kW（逐年漂移），
  训练段分母不适配验证年 → naive 自身在 2017 的伪 MASE=1.678；
  v2 差分目标退化（模型输出≈seasonal naive，中位 1.6765，改善仅 0.08%）。

v3（方案 A，2026-08-08 用户选定）：
  1) 【目标回绝对值】预测未来 96 步绝对值（v1 结构，direct multi-output，
     符合 v9 §3.3 冻结），弃差分目标（Δ 近白噪声，direct 学不动）；
  2) 【保留时间特征】hour/dow sin-cos 4 维拼输入（v2 已实现，继续用）；
  3) 【保留 MAE 损失】L1 与 MASE/MAE 评估口径一致；
  4) 【判据修正】B*_Load = 2017 自身 seasonal naive（伪 MASE 1.6778），
     门槛 = LSTM 2017 MASE ≤ B*×0.95（相对改善 ≥5%），**非 <1.0**。

v9 约束：2016 训练 / 2017 验证（选超参与中位 seed）/ 2018 一次性 held-out；
本调参脚本【不评估 2018】（v9 §13），2018 留待定稿后正式脚本评估。

运行环境：LSTM venv（D:/matlab/C/LSTM_Forecast/.venv，Py3.13 + torch CPU）
命令：
  D:/matlab/C/LSTM_Forecast/.venv/Scripts/python.exe 06_src/predopt_ems/lstm_env/scripts/lstm_forecast_building_load_v3.py
输出：06_src/predopt_ems/lstm_env/results/BuildingLoad_v3_metrics.json + 2017 5-seed 汇总
验收目标（Gate B ① 调参阶段）：2017 中位 seed 相对 B*_Load 改善 ≥5%。
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
                        DEFAULT_SEEDS)

CSV = r"D:\matlab\C\02_data_processed\tradestreet_pipeline\64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv"
OUT_DIR = os.path.join(_HERE, "..", "results")   # 输出统一到 lstm_env/results/
OUT_JSON = os.path.join(OUT_DIR, "BuildingLoad_v3_metrics.json")

SEQ_LEN, HORIZON, LAG = 96, 96, 96
STRIDE = 24
HIDDEN, N_LAYERS, DROPOUT = 64, 2, 0.1
EPOCHS, BATCH, LR, PATIENCE = 40, 64, 1e-3, 8
LOSS = "mae"            # mae / mse / smooth_l1
SEEDS = DEFAULT_SEEDS
N_TIME_FEAT = 4         # hour_sin, hour_cos, dow_sin, dow_cos
IMPROVE_CRITERION = 0.05   # LSTM 相对 B*_Load 改善 ≥5%（v9 §3.4）


# ------------------------------------------------------------------
# 时间特征（sin-cos 周期编码，全序列一次性生成）
# ------------------------------------------------------------------

def build_time_features(index: pd.DatetimeIndex) -> np.ndarray:
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
# 绝对值样本构造（v3：目标=未来绝对值；输入=负荷+时间特征）
# ------------------------------------------------------------------

def make_sequences_abs(values, time_feat, seq_len, horizon, stride):
    X, y = [], []
    L = len(values)
    for i in range(0, L - seq_len - horizon + 1, stride):
        x_in = values[i:i + seq_len]                          # (seq_len,)
        x_tf = time_feat[i:i + seq_len]                       # (seq_len, 4)
        fut = values[i + seq_len:i + seq_len + horizon]       # (horizon,)
        xin = np.concatenate([x_in[:, None], x_tf], axis=1)   # (seq_len, 5)
        X.append(xin)
        y.append(fut)
    return np.asarray(X, dtype=np.float32), np.asarray(y, dtype=np.float32)


# ------------------------------------------------------------------
# 模型（input_size 可配）
# ------------------------------------------------------------------

class LSTMForecasterInput(nn.Module):
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
# 块预测（直接输出绝对值）
# ------------------------------------------------------------------

def predict_blocks_abs(model, values, time_feat, start_idx, seq_len, horizon,
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
            y = model(xin).cpu().numpy().reshape(-1)
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
    print("Load 预测调参 v3（Gate B ①，方案 A）— 绝对值目标 + 时间特征 + MAE + 判据修正")
    print("年度切分: 2016 训练 / 2017 验证(选超参+中位seed) / 2018 不评估(held-out)")
    print(f"超参: hidden={HIDDEN} layers={N_LAYERS} drop={DROPOUT} epochs={EPOCHS} "
          f"batch={BATCH} lr={LR} patience={PATIENCE} loss={LOSS} stride={STRIDE}")
    print("=" * 70)

    os.makedirs(OUT_DIR, exist_ok=True)

    df = load_series(CSV, "p_building_load_reconstructed_kW")
    print(f"数据范围: {df.index[0]} ~ {df.index[-1]}，共 {len(df)} 条")
    train, val, _test = split_by_year(df, "p_building_load_reconstructed_kW")

    from sklearn.preprocessing import MinMaxScaler
    scaler = MinMaxScaler()
    train_s = scaler.fit_transform(train.reshape(-1, 1)).ravel()
    val_s = scaler.transform(val.reshape(-1, 1)).ravel()

    tf = build_time_features(df.index)
    n_train, n_val = len(train), len(val)
    tf_train, tf_val = tf[:n_train], tf[n_train:n_train + n_val]

    # —— B*_Load 基准（判据修正核心：用 2017 自身 seasonal naive）——
    naive_2016 = float(np.mean(np.abs(train[LAG:] - train[:-LAG])))
    naive_2017 = float(np.mean(np.abs(val[LAG:] - val[:-LAG])))
    bstar_pseudo_mase = naive_2017 / naive_2016          # B* 在 MASE 口径下的基准值
    mase_threshold = bstar_pseudo_mase * (1 - IMPROVE_CRITERION)   # 改善≥5% → MASE ≤ 0.95×B*
    print(f"\nMASE 基准: 2016 训练段 naive MAE={naive_2016:.4f} | "
          f"2017 自身 naive MAE={naive_2017:.4f}")
    print(f"B*_Load（2017 自身 naive）伪 MASE={bstar_pseudo_mase:.4f} | "
          f"5% 改善门槛 → LSTM 2017 MASE ≤ {mase_threshold:.4f}")

    Xtr, ytr = make_sequences_abs(train_s, tf_train, SEQ_LEN, HORIZON, STRIDE)
    Xva, yva = make_sequences_abs(val_s, tf_val, SEQ_LEN, HORIZON, STRIDE)
    print(f"滑窗样本: 2016 train {len(Xtr)} | 2017 val {len(Xva)} "
          f"(输入 {SEQ_LEN}×{1 + N_TIME_FEAT} 维 -> 绝对值输出 {HORIZON} 步)")

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"设备: {device}")

    train_ds = TensorDataset(torch.from_numpy(Xtr), torch.from_numpy(ytr))
    val_ds = TensorDataset(torch.from_numpy(Xva), torch.from_numpy(yva))
    train_loader = DataLoader(train_ds, batch_size=BATCH, shuffle=True, num_workers=0)
    val_loader = DataLoader(val_ds, batch_size=BATCH, shuffle=False, num_workers=0)

    print(f"\n5-seed 训练（seeds={SEEDS}），2017 验证 MASE 选中位 seed...")
    val_primary = {}
    val_mae = {}
    histories = {}
    for seed in SEEDS:
        torch.manual_seed(seed)
        model = LSTMForecasterInput(input_size=1 + N_TIME_FEAT,
                                    hidden_size=HIDDEN, num_layers=N_LAYERS,
                                    dropout=DROPOUT, horizon=HORIZON).to(device)
        model, history = train_model(model, train_loader, val_loader,
                                     EPOCHS, LR, PATIENCE, device, seed, LOSS)
        act_s, pred_s = predict_blocks_abs(model, val_s, tf_val, 0,
                                           SEQ_LEN, HORIZON, device)
        act = scaler.inverse_transform(act_s.reshape(-1, 1)).ravel()
        pred = scaler.inverse_transform(pred_s.reshape(-1, 1)).ravel()
        mm = compute_metrics(act, pred, train, LAG)
        val_primary[seed] = mm["mase"]
        val_mae[seed] = mm["mae_kW"]
        improve = (1.0 - mm["mae_kW"] / naive_2017) * 100.0
        histories[seed] = history
        print(f"  seed {seed}: 2017 MASE={val_primary[seed]:.4f} | MAE={mm['mae_kW']:.3f} kW | "
              f"R²={mm['r2']:.3f} | 相对B*改善 {improve:+.2f}%")

    arr = np.array([val_primary[s] for s in SEEDS])
    chosen = SEEDS[int(np.argsort(arr)[len(SEEDS) // 2])]
    improve_mean = (1.0 - np.mean([val_mae[s] for s in SEEDS]) / naive_2017) * 100.0
    improve_chosen = (1.0 - val_mae[chosen] / naive_2017) * 100.0
    print(f"\n2017 5-seed MASE: mean={arr.mean():.4f} std={arr.std():.4f} | "
          f"中位 seed={chosen} ({val_primary[chosen]:.4f})")
    print(f"相对 B*_Load 改善: mean {improve_mean:+.2f}% | 中位 seed {improve_chosen:+.2f}%")

    # 判据（v9 §3.4：LSTM 相对 B*_Load MASE 改善 ≥5%，用 2017 中位 seed）
    ok_5pct = bool(improve_chosen >= 5.0)
    ok_mase = bool(val_primary[chosen] <= mase_threshold)
    print("-" * 70)
    print(f"判据: 中位 seed 相对 B* 改善 {improve_chosen:+.2f}% ≥5% → "
          f"{'PASS ✅' if ok_5pct else 'FAIL ❌'} | "
          f"MASE {val_primary[chosen]:.4f} ≤ {mase_threshold:.4f} → "
          f"{'PASS ✅' if ok_mase else 'FAIL ❌'}")

    out = {
        "timestamp": pd.Timestamp.now().isoformat(timespec="seconds"),
        "purpose": "Load 调参 v3（方案 A：绝对值+时间特征+MAE+判据修正），仅 2017 验证，未评估 2018",
        "cfg": {"seq_len": SEQ_LEN, "horizon": HORIZON, "lag": LAG, "stride": STRIDE,
                "hidden": HIDDEN, "layers": N_LAYERS, "dropout": DROPOUT,
                "epochs": EPOCHS, "batch": BATCH, "lr": LR, "patience": PATIENCE,
                "loss": LOSS, "time_feat": N_TIME_FEAT, "seeds": SEEDS,
                "improve_criterion": IMPROVE_CRITERION},
        "baseline": {"naive_mae_2016": round(naive_2016, 4),
                     "naive_mae_2017": round(naive_2017, 4),
                     "bstar_pseudo_mase": round(bstar_pseudo_mase, 4),
                     "mase_threshold_5pct": round(mase_threshold, 4)},
        "seeds_mase_2017": {str(s): float(v) for s, v in val_primary.items()},
        "seeds_mae_2017": {str(s): float(v) for s, v in val_mae.items()},
        "summary_2017": {"mean_mase": float(arr.mean()), "std_mase": float(arr.std()),
                         "chosen_seed": chosen, "chosen_mase": float(val_primary[chosen]),
                         "chosen_mae_kw": float(val_mae[chosen]),
                         "improve_vs_bstar_mean_pct": round(improve_mean, 3),
                         "improve_vs_bstar_chosen_pct": round(improve_chosen, 3)},
        "criteria": {"improve_ge5pct": bool(ok_5pct), "mase_le_threshold": bool(ok_mase)},
        "history_chosen": histories[chosen],
        "note": ("判据修正（v2 教训）：B*_Load 用 2017 自身 seasonal naive（伪 MASE 1.678），"
                 "非 <1.0；MASE 分母仍为 2016 训练段（v9 §3.4 口径），门槛 = B*×0.95。"
                 "调参期间未评估 2018（v9 §13 held-out）。"),
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n结果已保存: {OUT_JSON}")
    return bool(ok_5pct)


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
