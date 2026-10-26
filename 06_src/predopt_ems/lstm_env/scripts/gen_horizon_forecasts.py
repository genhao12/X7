# -*- coding: utf-8 -*-
"""
gen_horizon_forecasts.py — 48h/168h 预测文件生成（v9 §5.7 敏感性实验配套，2026-08-10）

背景：LSTM 冻结模型为 direct multi-output（读 96 步→输出 96 步，v9 §3.3 固定）。
24/48/168h 敏感性 = MILP 预测窗口长度（96/192/672 步）。更长窗口的预测由
**冻结模型滚动拼接**生成：每 96 步调用一次 LSTM（输入=上一段实际+已预测段），
拼接为 192/672 步预测——模型输出方式不变（仍逐次 96 步），符合冻结规则。

输出（MPC 口径，与 24h 预测文件同格式，lstm_env/results/forecasts/）：
  Load_forecast_2018_h48.csv / _h168.csv  （48h=192 步、168h=672 步）
  PV_forecast_2018_h48.csv  / _h168.csv
第 w 行（时刻 t_w）给出 [t_w, t_w+H) 的 H 步预测（kW）。

滚动拼接实现（因果，只用 t_w 之前的历史）：
  段 1 输入 = 实际历史 [t_w-96, t_w) → 输出 [t_w, t_w+96)
  段 k 输入 = 前段输入右移 96 步（真实历史不足时用已预测段填充，保持因果）
  —— 对 t_w 位于 2018 起点（t_w-96 在 2017 末）首段用真实历史，后续段用预测回填。

运行环境：LSTM venv
命令：
  D:/matlab/C/LSTM_Forecast/.venv/Scripts/python.exe 06_src/predopt_ems/lstm_env/scripts/gen_horizon_forecasts.py
输出：4 个 CSV（h48/h168 × PV/Load）
"""

import os
import sys

import numpy as np
import pandas as pd
import torch
import torch.nn as nn

_HERE = os.path.dirname(os.path.abspath(__file__))
_LSTM_LIB = r"D:\matlab\C\LSTM_Forecast"
for p in (_HERE, _LSTM_LIB):
    if p not in sys.path:
        sys.path.insert(0, p)

CSV = r"D:\matlab\C\02_data_processed\tradestreet_pipeline\64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv"
RES_DIR = os.path.join(_HERE, "..", "results")
FC_DIR = os.path.join(RES_DIR, "forecasts")

SEQ_LEN = 96
BATCH_PRED = 512
N_TR, N_VA = 35136, 35040
N_2018 = 35040
HORIZONS = {"h48": 192, "h168": 672}

# 冻结模型（Load 带时间特征 input_size=5；PV 无时间特征 input_size=1）
LOAD_PT = os.path.join(RES_DIR, "BuildingLoad_final_lstm.pt")
LOAD_SCALER = os.path.join(RES_DIR, "BuildingLoad_final_scaler.npz")
PV_PT = os.path.join(_LSTM_LIB, "outputs", "PV_lstm.pt")
PV_SCALER = os.path.join(_LSTM_LIB, "outputs", "PV_scaler.npz")


class LSTMForecaster(nn.Module):
    def __init__(self, input_size, hidden_size=64, num_layers=2, dropout=0.1, horizon=96):
        super().__init__()
        self.lstm = nn.LSTM(input_size, hidden_size, num_layers,
                            batch_first=True, dropout=dropout)
        self.fc = nn.Linear(hidden_size, horizon)

    def forward(self, x):
        _, (h, _) = self.lstm(x)
        return self.fc(h[-1])


def build_time_features(index: pd.DatetimeIndex) -> np.ndarray:
    n = len(index)
    hour = index.hour + index.minute / 60.0
    dow = index.dayofweek.astype(float)
    f = np.zeros((n, 4), dtype=np.float32)
    f[:, 0] = np.sin(2 * np.pi * hour / 24.0)
    f[:, 1] = np.cos(2 * np.pi * hour / 24.0)
    f[:, 2] = np.sin(2 * np.pi * dow / 7.0)
    f[:, 3] = np.cos(2 * np.pi * dow / 7.0)
    return f


def load_series(col):
    df = pd.read_csv(CSV, usecols=["timestamp_local_civil", col])
    df["t"] = pd.to_datetime(df["timestamp_local_civil"])
    df = df.set_index("t").sort_index()
    return df[col].ffill().bfill().to_numpy(dtype=float), df.index


def gen_horizon(model, values_s, tf, t_start, n_w, H, device, has_tf):
    """滚动拼接：第 w 行 = [t_w, t_w+H) 的 H 步预测（标度值）。
    段 k 输入 = 实际历史（若可用）否则预测回填；保持因果。"""
    out = np.zeros((n_w, H), dtype=np.float32)
    with torch.no_grad():
        for b0 in range(0, n_w, BATCH_PRED):
            b1 = min(b0 + BATCH_PRED, n_w)
            # 初始化每窗口的"可观测历史"：拷贝实际序列（2018 起点 t_w 之前的实际）
            hist = np.zeros((b1 - b0, H + SEQ_LEN), dtype=np.float32)
            for j, w in enumerate(range(b0, b1)):
                t_w = t_start + w
                # 可观测历史 [t_w-SEQ_LEN, t_w+H) 中 [t_w-SEQ_LEN, t_w) 为实际值
                hist[j, :SEQ_LEN] = values_s[t_w - SEQ_LEN:t_w]
            for seg in range(0, H, SEQ_LEN):
                # 每段输入 = hist[:, seg:seg+SEQ_LEN]
                Xb = np.zeros((b1 - b0, SEQ_LEN, 1 + (4 if has_tf else 0)), dtype=np.float32)
                for j, w in enumerate(range(b0, b1)):
                    t_w = t_start + w
                    Xb[j, :, 0] = hist[j, seg:seg + SEQ_LEN]
                    if has_tf:
                        # 时间特征取输入窗口段（与 hist[:, seg:seg+SEQ_LEN] 对齐，
                        # 即时刻 [t_w+seg-96, t_w+seg)，MPC 语义与 final 脚本一致）
                        gi = t_w + seg - SEQ_LEN
                        Xb[j, :, 1:] = tf[gi:gi + SEQ_LEN]
                yb = model(torch.from_numpy(Xb).to(device)).cpu().numpy()
                # 写入预测段 + 回填 hist 供下一段输入（预测值作为"观测"）
                out[b0:b1, seg:seg + SEQ_LEN] = yb
                hist[:, seg + SEQ_LEN:seg + 2 * SEQ_LEN] = yb
    return out


def write_csv(fname, ts_2018, pred_kw, H):
    cols = ["timestamp"] + [f"pred_h{h+1}" for h in range(H)]
    out_df = pd.DataFrame(np.column_stack([ts_2018.strftime("%Y-%m-%d %H:%M:%S"),
                                           np.round(pred_kw, 3)]), columns=cols)
    out_df.to_csv(fname, index=False)
    print(f"  {os.path.basename(fname)}: {len(out_df)}×{H} 步")


def main():
    print("=" * 70)
    print("48h/168h 敏感性预测文件生成（冻结 96 步模型滚动拼接，v9 §5.7）")
    print("=" * 70)
    os.makedirs(FC_DIR, exist_ok=True)
    device = torch.device("cpu")
    ts_2018 = pd.date_range("2018-01-01", periods=N_2018, freq="15min")
    t_start = N_TR + N_VA

    # ---- Load（input_size=5，时间特征）----
    load, idx = load_series("p_building_load_reconstructed_kW")
    sc = np.load(LOAD_SCALER)
    load_s = ((load.reshape(-1, 1) - sc["data_min"]) * sc["scale"]).ravel()
    # 延长时间特征索引：滚动拼接段 2+ 的输入窗口延伸到 t_w+H_max（未来时刻 hour/dow
    # 属外生日历信息，MPC 语义下已知，可直接外推——不是用预测值，只是日历编码）
    need = t_start + N_2018 + max(HORIZONS.values())
    if len(idx) < need:
        pad = need - len(idx)
        extra = pd.date_range(idx[-1] + pd.Timedelta(minutes=15), periods=pad, freq="15min")
        idx = idx.append(extra)
    tf = build_time_features(idx)
    model = LSTMForecaster(5, horizon=96)
    model.load_state_dict(torch.load(LOAD_PT, map_location="cpu"))
    model.eval()
    print(f"\nLoad 模型加载: {LOAD_PT}")
    for tag, H in HORIZONS.items():
        pred_s = gen_horizon(model, load_s, tf, t_start, N_2018, H, device, has_tf=True)
        pred_kw = (pred_s / sc["scale"]) + sc["data_min"]
        write_csv(os.path.join(FC_DIR, f"Load_forecast_2018_{tag}.csv"), ts_2018, pred_kw, H)

    # ---- PV（input_size=1，无时间特征，clip≥0）----
    pv, _ = load_series("p_pv_available_reconstructed_nonnegative_kW")
    psc = np.load(PV_SCALER)
    pv_s = ((pv.reshape(-1, 1) - psc["data_min"]) * psc["scale"]).ravel()
    pv_model = LSTMForecaster(1, horizon=96)
    pv_model.load_state_dict(torch.load(PV_PT, map_location="cpu"))
    pv_model.eval()
    print(f"\nPV 模型加载: {PV_PT}")
    for tag, H in HORIZONS.items():
        pred_s = gen_horizon(pv_model, pv_s, None, t_start, N_2018, H, device, has_tf=False)
        pred_s = np.maximum(pred_s, 0.0)
        pred_kw = np.maximum((pred_s / psc["scale"]) + psc["data_min"], 0.0)
        write_csv(os.path.join(FC_DIR, f"PV_forecast_2018_{tag}.csv"), ts_2018, pred_kw, H)

    print("\n48h/168h 预测文件生成完成 ✅")


if __name__ == "__main__":
    main()
