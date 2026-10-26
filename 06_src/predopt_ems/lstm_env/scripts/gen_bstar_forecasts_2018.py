# -*- coding: utf-8 -*-
"""
gen_bstar_forecasts_2018.py — B* 基线预测文件生成（v9 §3.2/§3.4，2026-08-08）

B* = (B*_PV, B*_Load) 成对预测器，2016 建立、2017 选择、2018 冻结。
本项目简化采用（v9 §3.2 候选中最简单且可冻结者）：
  B*_PV   = 季节持久 seasonal naive（lag=96 点=24h，预测=昨天同时刻）
  B*_Load = 季节持久 seasonal naive（lag=96 点=24h）
生成 MPC 口径 2018 全年预测文件（与 LSTM 预测文件同格式），供 DL 环境第二关对照。

注意：seasonal naive 在 2017/2018 的"自身伪 MASE">1（合成数据逐年漂移），
这正是 LSTM 改善 28.7% 的对照基准（v9 §3.4 判据：LSTM 相对 B* 改善≥5%）。

运行环境：LSTM venv（D:/matlab/C/LSTM_Forecast/.venv）
命令：
  D:/matlab/C/LSTM_Forecast/.venv/Scripts/python.exe 06_src/predopt_ems/lstm_env/scripts/gen_bstar_forecasts_2018.py
输出：
  - lstm_env/results/forecasts/Bstar_PV_forecast_2018.csv
  - lstm_env/results/forecasts/Bstar_Load_forecast_2018.csv
"""

import os
import sys

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
CSV = r"D:\matlab\C\02_data_processed\tradestreet_pipeline\64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv"
RES_DIR = os.path.join(_HERE, "..", "results")
FC_DIR = os.path.join(RES_DIR, "forecasts")

SEQ_LEN, HORIZON = 96, 96
N_TR, N_VA = 35136, 35040      # 2016 / 2017 长度


def load_series(col):
    df = pd.read_csv(CSV, usecols=["timestamp_local_civil", col])
    df["t"] = pd.to_datetime(df["timestamp_local_civil"])
    df = df.set_index("t").sort_index()
    return df[col].ffill().bfill().to_numpy(dtype=float)


def seasonal_naive_windows(series, t_start, n_w, lag=96):
    """MPC 口径：第 w 行（时刻 t_w=t_start+w）预测 = 昨天同时刻（t_w-lag）。
    即 pred_h[k] = series[t_w - lag + k]，k=0..95（未来 96 步 = 昨天 96 步）。"""
    pred = np.zeros((n_w, HORIZON), dtype=float)
    for w in range(n_w):
        t_w = t_start + w
        base = t_w - lag
        pred[w, :] = series[base:base + HORIZON]
    return pred


def write_csv(fname, ts_2018, pred_kw):
    cols = ["timestamp"] + [f"pred_h{h+1}" for h in range(HORIZON)]
    out_df = pd.DataFrame(np.column_stack([ts_2018.strftime("%Y-%m-%d %H:%M:%S"),
                                           np.round(pred_kw, 3)]), columns=cols)
    out_df.to_csv(fname, index=False)
    print(f"  {fname}: {len(out_df)} 起点 × {HORIZON} 步")


def main():
    print("=" * 70)
    print("B* 基线预测文件生成（seasonal naive lag=96，v9 §3.2）")
    print("=" * 70)
    os.makedirs(FC_DIR, exist_ok=True)

    df = pd.read_csv(CSV, usecols=["timestamp_local_civil"])
    df["t"] = pd.to_datetime(df["timestamp_local_civil"])
    df = df.set_index("t").sort_index()
    ts_2018 = df.index[N_TR + N_VA:]
    n_w = len(ts_2018)                       # 35040
    t_start = N_TR + N_VA

    for name, col in [("PV", "p_pv_available_reconstructed_nonnegative_kW"),
                      ("Load", "p_building_load_reconstructed_kW")]:
        s = load_series(col)
        pred = seasonal_naive_windows(s, t_start, n_w)
        if name == "PV":
            pred = np.maximum(pred, 0.0)
        fname = os.path.join(FC_DIR, f"Bstar_{name}_forecast_2018.csv")
        write_csv(fname, ts_2018, pred)

    print("\nB* 预测文件生成完成 ✅")


if __name__ == "__main__":
    main()
