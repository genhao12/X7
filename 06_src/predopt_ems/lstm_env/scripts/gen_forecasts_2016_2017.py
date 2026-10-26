# -*- coding: utf-8 -*-
"""
gen_forecasts_2016_2017.py — 第四关前置：2016/2017 预测文件生成（LSTM 环境，2026-08-16）

第四关 3 年链式（2016-2018）需要每年的 MPC 口径预测文件。现有 forecasts/ 仅 2018。
本脚本用冻结模型（Load seed777 / PV seed20260807）对 2016、2017 生成：

  Load_forecast_2016.csv       (35136 行 × 96 步，2016 闰年)
  PV_forecast_2016.csv
  Load_forecast_2017.csv       (35040 行 × 96 步)
  PV_forecast_2017.csv
  Bstar_Load_forecast_2016.csv / Bstar_PV_forecast_2016.csv
  Bstar_Load_forecast_2017.csv / Bstar_PV_forecast_2017.csv

2016 首日处理：w=0..95 的 MPC 输入需 2015 末 96 步历史（数据从 2016-01-01 起，
不存在）→ 该 96 行用 perfect（实际窗口值）兜底并写入 note 披露（占全年 0.27%）。

口径：PV 预测为原始 reference=233 口径（DL 侧统一 ×cfg.pv_kwp/233 + clip）；
Load 不缩放（identity）。与 2018 预测文件一致。

运行环境：LSTM venv
命令：
  D:/matlab/C/LSTM_Forecast/.venv/Scripts/python.exe 06_src/predopt_ems/lstm_env/scripts/gen_forecasts_2016_2017.py
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
N2016, N2017, N2018 = 35136, 35040, 35040     # 2016 闰年
START = {"2016": 0, "2017": N2016}            # 全序列中的年起点索引
NW = {"2016": N2016, "2017": N2017}           # 每年窗口数（MPC 起点数）

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


def load_all():
    df = pd.read_csv(CSV, usecols=["timestamp_local_civil",
                                   "p_pv_available_reconstructed_nonnegative_kW",
                                   "p_building_load_reconstructed_kW"])
    df["t"] = pd.to_datetime(df["timestamp_local_civil"])
    df = df.set_index("t").sort_index()
    idx = df.index
    pv = df["p_pv_available_reconstructed_nonnegative_kW"].ffill().bfill().to_numpy(dtype=float)
    load = df["p_building_load_reconstructed_kW"].ffill().bfill().to_numpy(dtype=float)
    return pv, load, idx


def predict_year(model, values_s, tf, year_start, n_w, has_tf, device):
    """MPC 口径：对目标年每个起点生成 96 步预测（标度值）。
    2016 年 w=0..95 无历史输入 → 返回 None 行由调用方 perfect 兜底。
    输入 = 过去 96 步 values_s[t_w-96 : t_w]（MPC 语义，与 2018 脚本一致）。"""
    out = np.zeros((n_w, 96), dtype=np.float32)
    fallback = np.zeros(n_w, dtype=bool)     # 需 perfect 兜底的行
    first = max(year_start, SEQ_LEN) - year_start   # 第一个可预测窗口（2016=96，2017=0）
    for j in range(first):
        fallback[j] = True
    with torch.no_grad():
        for b0 in range(first, n_w, BATCH_PRED):
            b1 = min(b0 + BATCH_PRED, n_w)
            Xb = np.zeros((b1 - b0, SEQ_LEN, 1 + (4 if has_tf else 0)), dtype=np.float32)
            for j, w in enumerate(range(b0, b1)):
                gi = year_start + w
                Xb[j, :, 0] = values_s[gi - SEQ_LEN:gi]
                if has_tf:
                    Xb[j, :, 1:] = tf[gi - SEQ_LEN:gi]
            out[b0:b1] = model(torch.from_numpy(Xb).to(device)).cpu().numpy()
    return out, fallback


def write_csv(fname, year, pred_kw, fallback_idx):
    n = NW[year]
    ts = pd.date_range(f"{year}-01-01", periods=n, freq="15min")
    cols = ["timestamp"] + [f"pred_h{h+1}" for h in range(96)]
    df = pd.DataFrame(np.column_stack([ts.strftime("%Y-%m-%d %H:%M:%S"),
                                       np.round(pred_kw, 3)]), columns=cols)
    df.to_csv(os.path.join(FC_DIR, fname), index=False)
    print(f"  {fname}: {len(df)}×96 步 | perfect 兜底 {int(fallback_idx.sum())} 行")


def main():
    print("=" * 70)
    print("2016/2017 预测文件生成（第四关前置，冻结模型 MPC 口径）")
    print("=" * 70)
    os.makedirs(FC_DIR, exist_ok=True)
    device = torch.device("cpu")

    pv, load, idx = load_all()
    print(f"全序列 {len(idx)} 点 | 2016={N2016} 2017={N2017} 2018={N2018}")
    tf = build_time_features(idx)
    sc = np.load(LOAD_SCALER)
    load_s = ((load.reshape(-1, 1) - sc["data_min"]) * sc["scale"]).ravel()
    psc = np.load(PV_SCALER)
    pv_s = ((pv.reshape(-1, 1) - psc["data_min"]) * psc["scale"]).ravel()

    load_model = LSTMForecaster(5, horizon=96)
    load_model.load_state_dict(torch.load(LOAD_PT, map_location="cpu"))
    load_model.eval()
    pv_model = LSTMForecaster(1, horizon=96)
    pv_model.load_state_dict(torch.load(PV_PT, map_location="cpu"))
    pv_model.eval()

    for year in ("2016", "2017"):
        ys, n = START[year], NW[year]
        print(f"\n--- {year}（{n} 窗口）---")
        # LSTM Load（input_size=5）
        pred_s, fb = predict_year(load_model, load_s, tf, ys, n, True, device)
        pred_kw = (pred_s / sc["scale"]) + sc["data_min"]
        # perfect 兜底（2016 首日 96 行：逐行取实际窗口）
        for j in np.where(fb)[0]:
            pred_kw[j] = load[ys + j:ys + j + 96]
        write_csv(f"Load_forecast_{year}.csv", year, pred_kw, fb)

        # LSTM PV（input_size=1，clip≥0）
        pred_s, fb = predict_year(pv_model, pv_s, tf, ys, n, False, device)
        pred_s = np.maximum(pred_s, 0.0)
        pred_kw = np.maximum((pred_s / psc["scale"]) + psc["data_min"], 0.0)
        for j in np.where(fb)[0]:
            pred_kw[j] = pv[ys + j:ys + j + 96]
        write_csv(f"PV_forecast_{year}.csv", year, pred_kw, fb)

        # B*（seasonal naive lag=96：pred[w] = 实际 [t_w-96, t_w)；2016 首日无历史 → perfect）
        for tag, col in (("Load", load), ("PV", pv)):
            bstar = np.zeros((n, 96), dtype=float)
            fb_b = np.zeros(n, dtype=bool)
            for w in range(n):
                lo = ys + w - SEQ_LEN
                if lo < 0:
                    fb_b[w] = True
                    bstar[w] = col[ys + w:ys + w + 96]     # perfect 兜底
                else:
                    bstar[w] = col[lo:lo + SEQ_LEN]         # 昨天同时刻
            write_csv(f"Bstar_{tag}_forecast_{year}.csv", year, bstar, fb_b)

    print("\n2016/2017 预测文件生成完成 ✅")


if __name__ == "__main__":
    main()
