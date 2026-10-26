# -*- coding: utf-8 -*-
"""
gen_pv_forecast_2018.py — PV 预测文件生成（Gate B ① 定稿配套，2026-08-08）

复用已有 PV 冻结模型（LSTM_Forecast/outputs/PV_lstm.pt，中位 seed 20260807，
v9 结构：direct multi-output、input_size=1、clip_nonneg=True，2018 nMAE 0.2258、R² 0.88），
生成 MPC 口径 2018 全年预测文件（与 Load_forecast_2018.csv 同格式）：
  每 15min 起点给出未来 96 步 PV 预测（kW，≥0），供 DL 环境 MILP 第二关读取。

v9 约束：PV 模型已在 2017 选中位 seed 并冻结（2018 held-out 一次性评估已完成），
本脚本仅做预测文件导出，不重新训练、不改模型。

运行环境：LSTM venv（D:/matlab/C/LSTM_Forecast/.venv，Py3.13 + torch CPU）
命令：
  D:/matlab/C/LSTM_Forecast/.venv/Scripts/python.exe 06_src/predopt_ems/lstm_env/scripts/gen_pv_forecast_2018.py
输出：lstm_env/results/forecasts/PV_forecast_2018.csv（35040 起点 × 96 步）
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
PV_PT = os.path.join(_LSTM_LIB, "outputs", "PV_lstm.pt")
PV_SCALER = os.path.join(_LSTM_LIB, "outputs", "PV_scaler.npz")
RES_DIR = os.path.join(_HERE, "..", "results")
FC_DIR = os.path.join(RES_DIR, "forecasts")
FC_CSV = os.path.join(FC_DIR, "PV_forecast_2018.csv")

SEQ_LEN, HORIZON = 96, 96
BATCH_PRED = 512
N_TIME_FEAT = 0          # PV 模型为 v1 结构（input_size=1，无时间特征）


class LSTMForecaster(nn.Module):
    """与 lstm_utils.LSTMForecaster 一致（PV 冻结模型结构，input_size=1）。"""

    def __init__(self, input_size=1, hidden_size=64, num_layers=2,
                 dropout=0.1, horizon=96):
        super().__init__()
        self.lstm = nn.LSTM(input_size, hidden_size, num_layers,
                            batch_first=True, dropout=dropout)
        self.fc = nn.Linear(hidden_size, horizon)

    def forward(self, x):
        _, (h, _) = self.lstm(x)
        return self.fc(h[-1])


def main():
    print("=" * 70)
    print("PV 预测文件生成（复用冻结模型 seed 20260807，MPC 口径 96 步）")
    print("=" * 70)
    os.makedirs(FC_DIR, exist_ok=True)

    df = pd.read_csv(CSV, usecols=["timestamp_local_civil",
                                   "p_pv_available_reconstructed_nonnegative_kW"])
    df["t"] = pd.to_datetime(df["timestamp_local_civil"])
    df = df.set_index("t").sort_index()
    pv = df["p_pv_available_reconstructed_nonnegative_kW"].ffill().bfill().to_numpy(dtype=float)
    print(f"数据: {df.index[0]} ~ {df.index[-1]}，共 {len(pv)} 条")

    sc = np.load(PV_SCALER)
    pv_s = ((pv.reshape(-1, 1) - sc["data_min"]) * sc["scale"]).ravel()   # 1D (105216,)

    model = LSTMForecaster(input_size=1, hidden_size=64, num_layers=2,
                           dropout=0.1, horizon=HORIZON)
    model.load_state_dict(torch.load(PV_PT, map_location="cpu"))
    model.eval()
    print(f"模型加载: {PV_PT}")

    # 2018 起点（all_scaled 中索引）：前两年 = 35136 + 35040 = 70176
    n_tr, n_va = 35136, 35040
    t_start = n_tr + n_va
    n_w = 35040  # 2018 全年 15min 起点
    assert t_start >= SEQ_LEN and len(pv_s) >= t_start + n_w  # 2018 起点前有 96 步上下文

    preds = []
    with torch.no_grad():
        for b0 in range(0, n_w, BATCH_PRED):
            b1 = min(b0 + BATCH_PRED, n_w)
            Xb = np.zeros((b1 - b0, SEQ_LEN, 1), dtype=np.float32)
            for j, w in enumerate(range(b0, b1)):
                gi = t_start + w                       # 预测起点 t_w（输出基准）
                Xb[j, :, 0] = pv_s[gi - SEQ_LEN:gi]   # 过去 96 步（MPC 语义）
            yb = model(torch.from_numpy(Xb)).cpu().numpy()          # (B, 96) 标度
            yb = np.maximum(yb, 0.0)                                 # v9 §3.3 非负 clip
            preds.append(yb)
    pred_mat = np.concatenate(preds, axis=0)                         # (35040, 96) 标度
    pred_kw = (pred_mat / sc["scale"]) + sc["data_min"]              # 反归一化
    pred_kw = np.maximum(pred_kw, 0.0)

    ts_2018 = df.index[t_start:t_start + n_w]
    cols = ["timestamp"] + [f"pred_h{h+1}" for h in range(HORIZON)]
    out_df = pd.DataFrame(np.column_stack([ts_2018.strftime("%Y-%m-%d %H:%M:%S"),
                                           np.round(pred_kw, 3)]), columns=cols)
    out_df.to_csv(FC_CSV, index=False)
    print(f"预测文件: {FC_CSV}（{n_w} 起点 × {HORIZON} 步）")
    print(f"  pred_h1 范围: {pred_kw[:,0].min():.1f} ~ {pred_kw[:,0].max():.1f} kW（非负 ✓）")
    print("PV 预测文件生成完成 ✅")


if __name__ == "__main__":
    main()
