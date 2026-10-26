# -*- coding: utf-8 -*-
"""
lstm_forecast_building_load_final.py — Load 预测正式版（Gate B ① 定稿，2026-08-08）

用途：
  1) 冻结 v3 超参（方案 A：绝对值目标 + 时间特征 + MAE 损失 + direct multi-output）
     5-seed 训练（2016）→ 2017 验证选中位 seed（应复现 v3 的 seed 777，交叉验证）；
  2) 【2018 首次一次性评估】（v9 §13 held-out：超参冻结后才允许看 2018）：
     报告 2018 MASE/MAE/R² + 相对 B*_Load 改善（B*_2018 = 2018 自身 seasonal naive）；
  3) 生成 2018 全年 96 步预测文件（MPC 口径：每 15min 起点给出未来 96 步预测），
     供 DL 环境 MILP 读取（第二关），格式：宽表 CSV timestamp,pred_h1..pred_h96；
  4) 保存冻结模型 + scaler。

判据（v9 §3.4）：LSTM 相对 B*_Load MASE 改善 ≥5%。
  2018 自身 naive MAE = 10.613 kW → B* 伪 MASE = 10.613/7.656 = 1.386
  → 2018 门槛：LSTM 2018 MAE ≤ 10.613×0.95 = 10.082 kW（或 MASE ≤ 1.317）

运行环境：LSTM venv（D:/matlab/C/LSTM_Forecast/.venv，Py3.13 + torch CPU）
命令：
  D:/matlab/C/LSTM_Forecast/.venv/Scripts/python.exe 06_src/predopt_ems/lstm_env/scripts/lstm_forecast_building_load_final.py
输出：
  - lstm_env/results/BuildingLoad_final_metrics.json（2018 评估 + 5-seed 汇总）
  - lstm_env/results/forecasts/Load_forecast_2018.csv（35040 起点 × 96 步预测宽表）
  - lstm_env/results/BuildingLoad_final_lstm.pt + _scaler.npz（冻结模型）
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
_LSTM_LIB = r"D:\matlab\C\LSTM_Forecast"
for p in (_HERE, _LSTM_LIB):
    if p not in sys.path:
        sys.path.insert(0, p)
from lstm_utils import load_series, split_by_year, DEFAULT_SEEDS

CSV = r"D:\matlab\C\02_data_processed\tradestreet_pipeline\64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv"
RES_DIR = os.path.join(_HERE, "..", "results")
FC_DIR = os.path.join(RES_DIR, "forecasts")
OUT_JSON = os.path.join(RES_DIR, "BuildingLoad_final_metrics.json")
FC_CSV = os.path.join(FC_DIR, "Load_forecast_2018.csv")
MODEL_PT = os.path.join(RES_DIR, "BuildingLoad_final_lstm.pt")
SCALER_NPZ = os.path.join(RES_DIR, "BuildingLoad_final_scaler.npz")

SEQ_LEN, HORIZON, LAG = 96, 96, 96
STRIDE = 24
HIDDEN, N_LAYERS, DROPOUT = 64, 2, 0.1
EPOCHS, BATCH, LR, PATIENCE = 40, 64, 1e-3, 8
LOSS = "mae"
SEEDS = DEFAULT_SEEDS
N_TIME_FEAT = 4
IMPROVE_CRITERION = 0.05
BATCH_PRED = 512          # 预测文件批量前向窗口数


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


def make_sequences_abs(values, time_feat, seq_len, horizon, stride):
    X, y = [], []
    L = len(values)
    for i in range(0, L - seq_len - horizon + 1, stride):
        x_in = values[i:i + seq_len]
        x_tf = time_feat[i:i + seq_len]
        fut = values[i + seq_len:i + seq_len + horizon]
        xin = np.concatenate([x_in[:, None], x_tf], axis=1)
        X.append(xin)
        y.append(fut)
    return np.asarray(X, dtype=np.float32), np.asarray(y, dtype=np.float32)


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


def train_model(model, train_loader, val_loader, epochs, lr, patience,
                device, seed, loss_fn="mae"):
    torch.manual_seed(seed)
    np.random.seed(seed)
    criterion = (nn.L1Loss() if loss_fn == "mae"
                 else nn.SmoothL1Loss() if loss_fn == "smooth_l1" else nn.MSELoss())
    optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=1e-5)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="min", factor=0.5, patience=3)
    history = {"train": [], "val": []}
    best_val, best_state, wait = float("inf"), None, 0
    for epoch in range(1, epochs + 1):
        model.train()
        tl = 0.0
        for xb, yb in train_loader:
            xb, yb = xb.to(device), yb.to(device)
            optimizer.zero_grad()
            loss = criterion(model(xb), yb)
            loss.backward()
            optimizer.step()
            tl += loss.item() * xb.size(0)
        tl /= len(train_loader.dataset)
        model.eval()
        vl = 0.0
        with torch.no_grad():
            for xb, yb in val_loader:
                xb, yb = xb.to(device), yb.to(device)
                loss = criterion(model(xb), yb)
                vl += loss.item() * xb.size(0)
        vl /= len(val_loader.dataset)
        scheduler.step(vl)
        history["train"].append(tl)
        history["val"].append(vl)
        if vl < best_val - 1e-4:
            best_val = vl
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            wait = 0
        else:
            wait += 1
            if wait >= patience:
                print(f"  [seed {seed}] 早停于 epoch {epoch}")
                break
        if epoch == 1 or epoch % 10 == 0:
            print(f"  [seed {seed}] epoch {epoch:3d} | train {tl:.6f} | val {vl:.6f}")
    if best_state is not None:
        model.load_state_dict(best_state)
    return model, history


def predict_blocks_abs(model, values, time_feat, start_idx, seq_len, horizon, device):
    """不重叠块预测（与 v3 评估口径一致）。返回 (actual, pred) 标度值。"""
    model.eval()
    actuals, preds = [], []
    with torch.no_grad():
        start = start_idx
        while start + seq_len + horizon <= len(values):
            x_in = values[start:start + seq_len]
            x_tf = time_feat[start:start + seq_len]
            xin = np.concatenate([x_in[:, None], x_tf], axis=1)[None, :, :]
            y = model(torch.from_numpy(xin).to(device)).cpu().numpy().reshape(-1)
            actuals.append(values[start + seq_len:start + seq_len + horizon])
            preds.append(y)
            start += horizon
    return np.concatenate(actuals), np.concatenate(preds)


def forecast_windows_all(model, values, time_feat, n_windows, seq_len, horizon,
                         device, start_idx=0):
    """MPC 口径：对目标年每个 15min 起点 w 生成 96 步预测（批量前向）。
    第 w 行对应时刻 t_w = start_idx + w：
      输入 = 过去 96 步 values[t_w-seq_len : t_w]（历史信息，MPC 语义）
      输出 = 未来 96 步预测 [t_w, t_w+horizon)（供 MILP 窗口读取）
    返回 pred_matrix (n_windows, horizon)，标度值，由调用方反归一化。"""
    model.eval()
    preds = []
    with torch.no_grad():
        for b0 in range(0, n_windows, BATCH_PRED):
            b1 = min(b0 + BATCH_PRED, n_windows)
            Xb = np.zeros((b1 - b0, seq_len, 1 + N_TIME_FEAT), dtype=np.float32)
            for j, w in enumerate(range(b0, b1)):
                gi = start_idx + w                     # 预测起点 t_w（输出基准）
                Xb[j, :, 0] = values[gi - seq_len:gi]             # 过去 96 步
                Xb[j, :, 1:] = time_feat[gi - seq_len:gi]
            yb = model(torch.from_numpy(Xb).to(device)).cpu().numpy()   # (B, horizon)
            preds.append(yb)
    return np.concatenate(preds, axis=0)   # (n_windows, horizon)


def main():
    print("=" * 70)
    print("Load 预测正式版（Gate B ① 定稿）— v3 冻结超参 + 2018 首次评估 + 预测文件")
    print("年度切分: 2016 训练 / 2017 选中位seed / 2018 一次性 held-out")
    print("=" * 70)
    os.makedirs(RES_DIR, exist_ok=True)
    os.makedirs(FC_DIR, exist_ok=True)

    df = load_series(CSV, "p_building_load_reconstructed_kW")
    train, val, test = split_by_year(df, "p_building_load_reconstructed_kW")
    print(f"数据: {df.index[0]} ~ {df.index[-1]} | 2016={len(train)} 2017={len(val)} 2018={len(test)}")

    from sklearn.preprocessing import MinMaxScaler
    scaler = MinMaxScaler()
    train_s = scaler.fit_transform(train.reshape(-1, 1)).ravel()
    val_s = scaler.transform(val.reshape(-1, 1)).ravel()
    test_s = scaler.transform(test.reshape(-1, 1)).ravel()

    tf = build_time_features(df.index)
    n_tr, n_va = len(train), len(val)
    tf_train, tf_val, tf_test = tf[:n_tr], tf[n_tr:n_tr + n_va], tf[n_tr + n_va:]

    # B* 基准（2017 调参已冻结；2018 为正式判据）
    naive16 = float(np.mean(np.abs(train[LAG:] - train[:-LAG])))
    naive17 = float(np.mean(np.abs(val[LAG:] - val[:-LAG])))
    naive18 = float(np.mean(np.abs(test[LAG:] - test[:-LAG])))
    thr18 = naive18 * (1 - IMPROVE_CRITERION)
    print(f"B*_Load: 2016={naive16:.3f} | 2017={naive17:.3f} | 2018={naive18:.3f} kW")
    print(f"2018 判据: LSTM MAE ≤ {thr18:.3f} kW（相对 B*_2018 改善≥5%）")

    Xtr, ytr = make_sequences_abs(train_s, tf_train, SEQ_LEN, HORIZON, STRIDE)
    Xva, yva = make_sequences_abs(val_s, tf_val, SEQ_LEN, HORIZON, STRIDE)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"设备: {device} | 样本: train {len(Xtr)} val {len(Xva)}")

    tl_ = DataLoader(TensorDataset(torch.from_numpy(Xtr), torch.from_numpy(ytr)),
                     batch_size=BATCH, shuffle=True, num_workers=0)
    vl_ = DataLoader(TensorDataset(torch.from_numpy(Xva), torch.from_numpy(yva)),
                     batch_size=BATCH, shuffle=False, num_workers=0)

    # 5-seed 训练 → 2017 选中位（应复现 v3 seed 777）
    print(f"\n5-seed 训练（seeds={SEEDS}）...")
    val_mase, models = {}, {}
    for seed in SEEDS:
        torch.manual_seed(seed)
        model = LSTMForecasterInput(1 + N_TIME_FEAT, HIDDEN, N_LAYERS, DROPOUT, HORIZON).to(device)
        model, _ = train_model(model, tl_, vl_, EPOCHS, LR, PATIENCE, device, seed, LOSS)
        act_s, pred_s = predict_blocks_abs(model, val_s, tf_val, 0, SEQ_LEN, HORIZON, device)
        act = scaler.inverse_transform(act_s.reshape(-1, 1)).ravel()
        pred = scaler.inverse_transform(pred_s.reshape(-1, 1)).ravel()
        mae = float(np.mean(np.abs(act - pred)))
        val_mase[seed] = mae / naive16
        models[seed] = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
        print(f"  seed {seed}: 2017 MASE={val_mase[seed]:.4f} MAE={mae:.3f} kW")
    arr = np.array([val_mase[s] for s in SEEDS])
    chosen = SEEDS[int(np.argsort(arr)[len(SEEDS) // 2])]
    print(f"2017 中位 seed = {chosen}（MASE={val_mase[chosen]:.4f}）"
          f"{'✓ 与 v3 一致' if chosen == 777 else '⚠ 与 v3(777) 不同，需核验'}")

    # 冻结模型 + 2018 首次评估
    model = LSTMForecasterInput(1 + N_TIME_FEAT, HIDDEN, N_LAYERS, DROPOUT, HORIZON).to(device)
    model.load_state_dict(models[chosen])
    torch.save(models[chosen], MODEL_PT)
    np.savez(SCALER_NPZ, data_min=scaler.data_min_, scale=scaler.scale_)

    act_s, pred_s = predict_blocks_abs(model, test_s, tf_test, 0, SEQ_LEN, HORIZON, device)
    act = scaler.inverse_transform(act_s.reshape(-1, 1)).ravel()
    pred = scaler.inverse_transform(pred_s.reshape(-1, 1)).ravel()
    mae18 = float(np.mean(np.abs(act - pred)))
    rmse18 = float(np.sqrt(np.mean((act - pred) ** 2)))
    from sklearn.metrics import r2_score
    r2_18 = float(r2_score(act, pred))
    mase18 = mae18 / naive16
    improve18 = (1.0 - mae18 / naive18) * 100.0
    ok18 = mae18 <= thr18
    print("\n" + "-" * 70)
    print("2018 一次性评估（held-out，首次）：")
    print(f"  MAE={mae18:.3f} kW | RMSE={rmse18:.3f} | R²={r2_18:.3f} | MASE={mase18:.4f}")
    print(f"  相对 B*_2018（{naive18:.3f} kW）改善 {improve18:+.2f}% "
          f"→ {'PASS ✅' if ok18 else 'FAIL ❌'}（门槛 MAE≤{thr18:.3f}）")

    # 预测文件（MPC 口径：2018 全年每 15min 起点 96 步预测）
    print("\n生成 2018 预测文件（MPC 口径，每 15min 起点 96 步）...")
    n_w = len(test)   # 35040 个起点
    all_scaled = np.concatenate([train_s, val_s, test_s])
    all_tf = tf
    t_start = n_tr + n_va   # 2018 起点索引（all_scaled 中）
    pred_mat = forecast_windows_all(model, all_scaled, all_tf, n_w, SEQ_LEN, HORIZON,
                                    device, start_idx=t_start)
    # 反归一化
    pred_kw = scaler.inverse_transform(pred_mat.reshape(-1, 1)).reshape(n_w, HORIZON)
    ts_2018 = df.index[n_tr + n_va:]
    cols = ["timestamp"] + [f"pred_h{h+1}" for h in range(HORIZON)]
    out_df = pd.DataFrame(np.column_stack([ts_2018.strftime("%Y-%m-%d %H:%M:%S"),
                                           np.round(pred_kw, 3)]), columns=cols)
    out_df.to_csv(FC_CSV, index=False)
    print(f"  预测文件: {FC_CSV}（{n_w} 起点 × {HORIZON} 步）")

    out = {
        "timestamp": pd.Timestamp.now().isoformat(timespec="seconds"),
        "purpose": "Load 预测正式版：v3 超参冻结 + 2018 首次 held-out 评估 + MPC 预测文件",
        "cfg": {"seq_len": SEQ_LEN, "horizon": HORIZON, "lag": LAG, "stride": STRIDE,
                "hidden": HIDDEN, "layers": N_LAYERS, "dropout": DROPOUT,
                "epochs": EPOCHS, "batch": BATCH, "lr": LR, "patience": PATIENCE,
                "loss": LOSS, "time_feat": N_TIME_FEAT, "seeds": SEEDS,
                "improve_criterion": IMPROVE_CRITERION},
        "baseline": {"naive_mae_2016": round(naive16, 4), "naive_mae_2017": round(naive17, 4),
                     "naive_mae_2018": round(naive18, 4), "thr_2018_mae": round(thr18, 4)},
        "seeds_mase_2017": {str(s): float(v) for s, v in val_mase.items()},
        "chosen_seed": chosen,
        "metrics_2018": {"mae_kW": round(mae18, 4), "rmse_kW": round(rmse18, 4),
                         "r2": round(r2_18, 4), "mase": round(mase18, 4),
                         "improve_vs_bstar2018_pct": round(improve18, 3)},
        "criteria_2018": {"mae_le_threshold": bool(ok18), "improve_ge5pct": bool(ok18)},
        "forecast_file": FC_CSV,
        "note": ("2018 为 v9 §13 held-out 首次评估（超参冻结后）；MASE 分母=2016 训练段 naive 7.656，"
                 "B*_2018=2018 自身 naive 10.613；判据=相对 B*_2018 改善≥5%"),
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n结果已保存: {OUT_JSON}")
    print(f"冻结模型: {MODEL_PT} | scaler: {SCALER_NPZ}")
    return bool(ok18)


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
