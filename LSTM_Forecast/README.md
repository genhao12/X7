# LSTM 预测脚本（光伏 / 建筑负荷）— v9 对齐版

针对数据集 `64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv`
（15 分钟粒度，2016–2018）编写的两个 LSTM 预测脚本，**已按 v9 方案对齐**。

## 文件结构

| 文件 | 说明 |
| --- | --- |
| `lstm_forecast_PV.py` | 脚本 1：预测 `p_pv_available_reconstructed_nonnegative_kW`（光伏可用出力） |
| `lstm_forecast_building_load.py` | 脚本 2：预测 `p_building_load_reconstructed_kW`（建筑负荷） |
| `lstm_utils.py` | 共享模块（v9 对齐版）：年度切分、5-seed、指标、训练、绘图 |
| `outputs/` | 运行后自动生成的结果目录 |

## 环境（路径 A：项目内 venv，D 盘）

```powershell
cd D:\matlab\C\LSTM_Forecast
C:\Users\HP\.workbuddy\binaries\python\versions\3.13.12\python.exe -m venv .venv
.venv\Scripts\activate
pip install pandas numpy matplotlib scikit-learn
pip install torch --index-url https://download.pytorch.org/whl/cpu   # CPU 版（本机推荐）
```

VSCode：`Ctrl+Shift+P` → `Python: Select Interpreter` → 选 `.venv`。

## v9 对齐点（5 处）

1. **年度切分**（§3.1）：2016 训练（35,136）/ 2017 验证（35,040）/ 2018 测试（35,040）——替代初版 70/15/15；
2. **5 固定种子 + 中位 seed**（§13）：2016 训 5 seeds（默认 `[42,202,777,1337,20260807]`）→ 2017 验证按主指标选**中位表现 seed** → **2018 只评估冻结的 1 个模型**（一次性 held-out）；
3. **主指标**（§3.4）：PV = **日照时段 nMAE**；Load = **MASE（seasonal lag=96 点=24h）**；RMSE/MAE/R²/MAPE 辅助报告；
4. **PV 非负输出**（§3.3）：`clip_nonneg=True` 预测后 clip ≥0；
5. **数据路径**：`csv_path` 指向项目正式文件 `D:\matlab\C\02_data_processed\tradestreet_pipeline\64_...csv`。

## 运行方法

```bash
python lstm_forecast_PV.py
python lstm_forecast_building_load.py
```

## 输出（outputs/）

| 文件 | 说明 |
| --- | --- |
| `*_metrics.json` | **5-seed 汇总（mean±std + 中位 seed）+ 2018 一次性指标**（nMAE/MASE/RMSE/MAE/R²） |
| `*_predictions.csv` | 2018 逐日块预测（时间戳/实际/预测） |
| `*_lstm.pt` / `*_scaler.npz` | **中位 seed 冻结模型**与归一化器（可复用于推理） |
| `*_forecast_test.png` / `*_zoom.png` / `*_loss.png` | 预测曲线与损失曲线 |

## 说明

- 归一化参数仅 2016 训练段拟合（防泄漏）；
- 2018 为 held-out：任何调参/选 seed/选阈值都不得使用 2018 结果（v9 §3.1）；
- 数据为合成重建时序（`prohibited_use`），预测精度为**方法演示级**，不跨站点外推（v9 §14）。
