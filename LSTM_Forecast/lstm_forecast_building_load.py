# -*- coding: utf-8 -*-
"""
LSTM 预测脚本（2/2）：预测 p_building_load_reconstructed_kW（建筑负荷）
- 15 分钟粒度，输入过去 1 天（96 步）预测未来 1 天（96 步）
- v9 §3.1 年度切分：2016 训练 / 2017 验证 / 2018 测试（held-out 一次性评估）
- v9 §13：5 固定种子训练，2017 选中位表现 seed，2018 只评估冻结的 1 个
- v9 §3.4：Load 主指标 = MASE（seasonal lag=96 点=24h）
- 用法：python lstm_forecast_building_load.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lstm_utils import run

if __name__ == "__main__":
    cfg = {
        # ---- 数据（v9：项目正式路径）----
        "csv_path": r"D:\matlab\C\02_data_processed\tradestreet_pipeline\64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv",
        "target_col": "p_building_load_reconstructed_kW",
        "series_name": "BuildingLoad",
        "title": "建筑负荷",
        "freq": "15min",
        "out_dir": os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "outputs"),

        # ---- 序列参数（15min/步，96 步 = 1 天）----
        "seq_len": 96,      # 输入步数
        "horizon": 96,      # 预测步数
        "stride": 24,       # 训练滑窗步长
        "lag": 96,          # MASE seasonal lag（=24h，v9 §3.4）

        # ---- 模型参数 ----
        "hidden_size": 64,
        "num_layers": 2,
        "dropout": 0.1,

        # ---- 训练参数 ----
        "epochs": 30,
        "batch_size": 64,
        "lr": 1e-3,
        "patience": 6,

        # ---- v9 §13：5 固定种子 + 2017 选中位 seed ----
        "seeds": [42, 202, 777, 1337, 20260807],

        # ---- v9 §3.4：Load 主指标 = MASE；负荷无需非负裁剪 ----
        "clip_nonneg": False,
        "primary_metric": "mase",
    }
    run(cfg)
