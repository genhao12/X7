# predopt_ems 工作区（预测优化型 EMS）

按**运行环境**分两个主目录，每个主目录内再按**脚本/结果**分为两个子文件夹：

| 主目录 | 运行环境 | Python | 内容 |
|---|---|---|---|
| `dl_env/` | **DL 环境** `D:/Anaconda/envs/DL/python.exe` | 3.9.24 | **MILP/Gurobi + 项目仿真**：P0 验收/测试、MILP 核心、Gate A/B、第一关、Bootstrap、ε 冻结（依赖 06_src 组件与 gurobipy 12.0.3） |
| `lstm_env/` | **LSTM venv** `D:/matlab/C/LSTM_Forecast/.venv/Scripts/python.exe` | 3.13.14 | **LSTM 预测调参**（torch 2.13 CPU；共享模块 lstm_utils.py 在 `D:\matlab\C\LSTM_Forecast\`，保持原样不动） |

每个主目录内：
- `scripts/` — 运行脚本（`.py`）+ 预注册规范文档（`.md`）
- `results/` — 运行输出结果（`*_result.json` 等复现证据）

## dl_env/scripts 脚本清单

| 脚本 | 用途 | 运行命令（VSCode 终端） |
|---|---|---|
| `p0_env_check.py` | P0 环境验收 4 项（gurobipy/许可证/SOS2/3000 变量） | `D:/Anaconda/envs/DL/python.exe 06_src/predopt_ems/dl_env/scripts/p0_env_check.py` |
| `p0_unit_tests.py` | **P0 六类数学单元测试第 1/2/3 类**（SOC 守恒 / FC-EL 分段线性 / H₂ 守恒，v9 §16.1） | `D:/Anaconda/envs/DL/python.exe 06_src/predopt_ems/dl_env/scripts/p0_unit_tests.py` |
| `milp_scheduler.py` | **MILP 核心调度器**（v9 §5/§6：电池拆 P_ch/P_dis、FC/EL 原生 SOS2+动态约束、三级字典序、终端线性化、跨窗口继承）——自带 smoke test（1 窗口×96 步） | `D:/Anaconda/envs/DL/python.exe 06_src/predopt_ems/dl_env/scripts/milp_scheduler.py` |
| `p0_unit_tests_456.py` | **P0 六类测试第 4/5/6 类**（三级字典序不破坏 / open-closed 一致 / MPC 跨窗口衔接，挂 milp_scheduler） | `D:/Anaconda/envs/DL/python.exe 06_src/predopt_ems/dl_env/scripts/p0_unit_tests_456.py` |
| `degradation_feedback.py` | **Gate A：闭环退化反馈映射唯一接口 + δ 阈值**（BAT f_E/f_P、FC/EL η(D) 支撑点重构；δ_BAT/δ_FC/δ_EL 实测确定，v9 §11） | `D:/Anaconda/envs/DL/python.exe 06_src/predopt_ems/dl_env/scripts/degradation_feedback.py` |
| `run_gate1_30day.py` | **第一关：compact×30 天 2,880 窗口 MILP 稳定性**（perfect 预测 + normal 三级字典序滚动，2018-06-01 起夏季 30 天；7 项验收；`--limit 96` 快速 1 天切片、`--start-day N` 选起点） | `D:/Anaconda/envs/DL/python.exe 06_src/predopt_ems/dl_env/scripts/run_gate1_30day.py` |
| `run_gate1_fc_active.py` | **第一关补充：FC 强制活跃专项**（低初始 SOC=0.15 + 冬季 2018-01-01 触发 FC，检验启停跨窗口最小运行/停机约束；默认 10 天，`--limit`/`--start-day`/`--init-soc` 可调） | `D:/Anaconda/envs/DL/python.exe 06_src/predopt_ems/dl_env/scripts/run_gate1_fc_active.py` |
| `gen_seasonal_h2_ref.py` | **Gate B 冻结项③：季节 H₂ 参考轨迹生成器**（v9 §5.7 唯一流程：2016 日尺度全年能量平衡优化 LP → ρ 归一化 → 删 2/29 → 365 点 → 周期边界 7 日平滑 → clip[0.15,0.85] → ×M → 冻结；5 配置独立；含 2017 自由 vs 带参考 LPSP 验证；约 3s） | `D:/Anaconda/envs/DL/python.exe 06_src/predopt_ems/dl_env/scripts/gen_seasonal_h2_ref.py` |
| `bootstrap_check.py` | **Gate B 冻结项④：Bootstrap 无效样本规则校验（方案 B）**：compact 2 周基准轨迹 → 前置切块误差 → 672 点周块重采样+独立 rainflow 计数 → 无效剔除 → 损伤 CI；`--start-day 0` 冬季跨季验证（已 PASS） | `D:/Anaconda/envs/DL/python.exe 06_src/predopt_ems/dl_env/scripts/bootstrap_check.py` |
| `freeze_eps.py` | **Gate B 冻结项②：ε₁/ε₂ 正式冻结**（v3.1：单窗口直接效应，ΔL1≤ε₁/ΔL2≤1e-3/ΔL3≤1%；`--reuse-step1` 复用已跑步骤1） | `D:/Anaconda/envs/DL/python.exe 06_src/predopt_ems/dl_env/scripts/freeze_eps.py --reuse-step1` |
| `check_bootstrap_invalid.py` | Bootstrap 合成轨迹单元校验（与 bootstrap_check.py 功能重叠，次要） | `D:/Anaconda/envs/DL/python.exe 06_src/predopt_ems/dl_env/scripts/check_bootstrap_invalid.py` |
| `bootstrap_invalid_rule.md` | **Gate B ④ 预注册规范（方案 B 冻结版）** | —（规范文档） |
| `freeze_eps_rule.md` | **Gate B ② 预注册规范（v3 冻结版）**：五判据 + 披露项 D + §4 冻结记录 | —（规范文档） |
| `run_gate2_2018.py` | **第二关：compact×2018 全年三源滚动 MILP**（bstar/lstm/perfect，MPC 语义状态继承；主指标=代理 Spearman≥0.90 周块 672 聚合、LPSP<1% 可靠性检查；逐源落盘+`--resume`；PV 统一 ×388/233+clip 口径；L3 TIME_LIMIT 判定含 incumbent+gap 记录） | `D:/Anaconda/envs/DL/python.exe 06_src/predopt_ems/dl_env/scripts/run_gate2_2018.py [--sources bstar] [--limit 960]` |
| `run_gate2_sensitivity.py` | **第二关敏感性：24/48/168h 窗口长度对比**（compact×LSTM da，4 季锚点周抽样 2688 窗；LCOE 分季权重年化；阈值 ΔLPSP≤0.1pp/ΔLCOE≤2%/Δ损伤≤5%；逐长度落盘+`--resume`） | `D:/Anaconda/envs/DL/python.exe 06_src/predopt_ems/dl_env/scripts/run_gate2_sensitivity.py [--lengths 96,192] [--limit 96]` |
| `run_gate3_2018.py` | **第三关：5 配置×M1/M4/M5×2018 全年**（5 指标：BAT/FC/EL 精算损伤+LPSP+正式 LCOE；closed 周级退化更新；经济链路=事件驱动寿命→compute_npc_timed，EL 不触发替换；逐配置落盘+`--resume`） | `D:/Anaconda/envs/DL/python.exe 06_src/predopt_ems/dl_env/scripts/run_gate3_2018.py [--configs compact] [--limit 960]` |
| `run_gate4_3yr.py` | **第四关：3 年链式（2016-2018，105,216 窗口）**（状态与 D 跨年连续继承；M1/M1c/M4/M5，M1c=B*+da 作 TE 配对源；TE(M1c−M1)+正式 LCOE+周块 Bootstrap 95%CI；逐年 LPSP 按 exec_idx 归集；逐方法落盘+`--resume`） | `D:/Anaconda/envs/DL/python.exe 06_src/predopt_ems/dl_env/scripts/run_gate4_3yr.py [--methods m4] [--limit 960 --no-bootstrap]` |
| `gate3_econ_lite.py` | **正式经济链路模块**（损伤增量→20 年 tiling→eol_events 事件驱动寿命→compute_npc_timed→NPC/LCOE；BAT/FC 按 EOL、EL 默认剔除；`--from-stage3` 快速验证） | `D:/Anaconda/envs/DL/python.exe 06_src/predopt_ems/dl_env/scripts/gate3_econ_lite.py --from-stage3 --role compact` |

每个脚本输出：控制台 PASS/FAIL + `dl_env/results/*_result.json`（复现证据）。

## lstm_env/scripts 脚本清单

| 脚本 | 用途 | 运行命令（VSCode 终端） |
|---|---|---|
| `lstm_forecast_building_load_v2.py` | **Gate B ① Load 预测调参 v2（已废弃）**（差分目标退化 0.08%，判据 <1.0 确认错误——MASE 分母应按验证年自身 naive） | `D:/matlab/C/LSTM_Forecast/.venv/Scripts/python.exe 06_src/predopt_ems/lstm_env/scripts/lstm_forecast_building_load_v2.py` |
| `lstm_forecast_building_load_v3.py` | **Gate B ① Load 预测调参 v3（方案 A，PASS）**（绝对值目标+时间特征+MAE+判据修正：B\*=验证年自身 naive，门槛 MASE≤1.594；2017 5-seed 中位 seed 777，改善 +37.38%） | `D:/matlab/C/LSTM_Forecast/.venv/Scripts/python.exe 06_src/predopt_ems/lstm_env/scripts/lstm_forecast_building_load_v3.py` |
| `lstm_forecast_building_load_final.py` | **Gate B ① Load 预测定稿**（v3 超参冻结+5-seed 2017 选中位 777 → 2018 首次 held-out 评估：MASE=0.988/R²=0.822/+28.73% → 生成 MPC 口径预测文件 + 冻结模型） | `D:/matlab/C/LSTM_Forecast/.venv/Scripts/python.exe 06_src/predopt_ems/lstm_env/scripts/lstm_forecast_building_load_final.py` |
| `gen_pv_forecast_2018.py` | **PV 2018 预测文件生成**（复用冻结 PV 模型 seed 20260807，input_size=1，MPC 口径 35040×96，clip≥0） | `D:/matlab/C/LSTM_Forecast/.venv/Scripts/python.exe 06_src/predopt_ems/lstm_env/scripts/gen_pv_forecast_2018.py` |
| `gen_bstar_forecasts_2018.py` | **B\* 基线预测文件生成**（seasonal naive lag=96，PV/Load 各 35040×96） | `D:/matlab/C/LSTM_Forecast/.venv/Scripts/python.exe 06_src/predopt_ems/lstm_env/scripts/gen_bstar_forecasts_2018.py` |
| `gen_horizon_forecasts.py` | **48h/168h 敏感性预测文件**（冻结 96 步模型滚动拼接，段 1=真实历史/后续段=预测回填保持因果；时间特征外推覆盖未来窗口） | `D:/matlab/C/LSTM_Forecast/.venv/Scripts/python.exe 06_src/predopt_ems/lstm_env/scripts/gen_horizon_forecasts.py` |
| `gen_forecasts_2016_2017.py` | **2016/2017 预测文件（第四关前置）**（冻结模型 MPC 口径×3 年；2016 首日 96 窗无历史→perfect 兜底+披露；2017 全量） | `D:/matlab/C/LSTM_Forecast/.venv/Scripts/python.exe 06_src/predopt_ems/lstm_env/scripts/gen_forecasts_2016_2017.py` |

输出：`lstm_env/results/`（模型 pt/scaler + `BuildingLoad_*_metrics.json`）与 `lstm_env/results/forecasts/`（预测文件：Load/PV/Bstar × 2016/2017/2018 × h24/h48/h168 共 14 个 CSV）。

## 目录治理记录（2026-08-08）

- `dl_env/`、`lstm_env/` 内部分为 `scripts/`（脚本+规范）与 `results/`（结果），脚本 `_SRC` 路径已同步改为三级向上（`06_src/`），输出统一写入 `../results/`。
- `D:\matlab\C\LSTM_Forecast\`（共享模块 lstm_utils.py + 原始脚本 + .venv）**保持原样**，仅被引用。

## 状态（Gate 进度）

- ✅ **Gate A 三项完成**（2026-08-08）：退化反馈映射、FC/EL 参数、δ 阈值（δ_BAT=1.43e-6/δ_FC=2.09e-4/δ_EL=1.68e-4）
- ✅ **第一关 GATE1 PASS**（2026-08-08 完整 30 天 2,880 窗口 7/7；修正版 EL/FC 真实启停、中位 0.294s/窗口）
- ✅ **FC 强制活跃专项 PASS**（10 天 960 窗口，启停跨窗口衔接真实验证）
- ✅ **Gate B ③ 季节 H₂ 冻结**（h2_ref_seasonal.json，5 配置 365 点，2017 验证 5/5 PASS）
- ✅ **Gate B ④ Bootstrap 方案 B 冻结**（夏季 2 周 + 冬季跨季 `--start-day 0` 双 PASS；CI 可用）
- ✅ **Gate B ② ε₁/ε₂ 冻结**（ε₁=1.0e-6 kWh、ε₂=6.584e-6，2026-08-08T19:40 五判据全 True PASS；GATE1 占位 ε 差异作披露项 D，稳定性结论无需重跑）
- ⏳ **Gate B ① Load 预测调参**：v1 MASE 1.61（口径失真，见下）→ v2 差分退化（0.08%）→ **v3 方案 A PASS ✅**：2017 5-seed MASE 1.028~1.065、中位 seed 777 MASE=1.0506、**相对 B\*_Load（2017 自身 naive）改善 +37.38%（远超 5% 门槛）**；判据修正要点：MASE>1 是合成数据逐年漂移（2016/2017 naive=7.66/12.84 kW）导致分母失真，B\* 应按验证年自身 naive 评估（伪 MASE 1.678），门槛=MASE≤1.594 而非 <1.0
- ✅ **Gate B ① Load 预测定稿**（2026-08-08）：v3 方案 A 2017 调参 PASS（中位 seed 777，相对 B\* 改善 +37.38%）→ **final 2018 首次 held-out 评估 PASS**：MAE=7.564 kW、R²=0.822、**MASE=0.988（首次<1）**、相对 B\*_2018（10.613）改善 **+28.73%**（门槛 10.082）→ **Gate B ①②③④ 全部冻结 ✅**；产物：`results/forecasts/Load_forecast_2018.csv`（35040 起点×96 步 MPC 口径）+ `BuildingLoad_final_lstm.pt` + `_scaler.npz`（seed 777）
- ✅ **预测层冻结规则**（2026-08-09 用户确认）：2018 全年 35,040 预测时刻 B\*/LSTM/perfect 三源一次性生成缓存（forecasts/ 14 个文件：h24/h48/h168 × 2016/2017/2018），MILP 方法×5 配置×open/closed 全部复用不重算
- ✅ **第二关 compact×2018 全年三源**（2026-08-12，35040 窗口×3）：bstar/perfect PASS（LPSP 0%、Spearman BAT/FC/EL 全 ≥0.90）；lstm 边缘 FAIL（LPSP 1.5482%、EL 0.894——LSTM PV 峰值低估→保守化，诊断留痕 `gate2_lstm_diagnosis.md`）；PV 口径统一修复（bstar LPSP 11.9%→0.0000%）
- ✅ **敏感性 24/48/168h**（2026-08-14，抽样 2688 窗）：LPSP 不敏感 PASS；ΔLCOE −21.75%/Δ损伤超阈值 → **不通过如实报告**（窗口长度重构 FC/EL 结构：FC 损伤 −93%、EL +229%）；h168 37% 窗 L3 超时 incumbent 披露
- ✅ **第三关 5 配置×M1/M4/M5×2018**（2026-08-18）：**m4（LSTM+da）全部 5 配置无综合收益**（LPSP 0.33~2.89%、FC 损伤 2.7~6.2× m1）；m5（perfect+da）与 m1 中性一致；正式 LCOE 事件驱动链路（BAT/FC 按 EOL、EL 不触发替换）
- ✅ **第四关 compact×3 年链式 105,216 窗口**（2026-08-23）：**TE(M1c−M1 同源 B\* 配对)=BAT −0.9% / FC −4.5% / EL −8.0%——退化感知长期降损伤**（单年 D=0.165 无收益 vs 三年 D=0.49 生效）；m4 链式放大 LPSP 5.56%；Bootstrap 156 块 95%CI
- ✅ **全部数据阶段完成**（2026-08-23）：Gate A/B → 预测层 → 第二关 → 敏感性 → 第三关 → 第四关；文档收尾：统一结果台账第 5 章 + limitations L40–L47 + 论文定稿_LSTM_MILP结论与局限

## 关键结果产物（results/ 目录）

| 文件 | 内容 |
|---|---|
| `gate2_2018_result.json` + `gate2_partial_{src}.json` | 第二关三源 LPSP/Spearman + 周块损伤明细 |
| `gate2_sensitivity_result.json` + `gate2_sens_partial_{h}.json` | 敏感性三长度 + 阈值判定（不通过） |
| `gate2_lstm_diagnosis.md` | lstm FAIL 诊断留痕（特定日误差/EL 代理标定） |
| `gate3_2018_result.json` + `gate3_partial_{cfg}.json` | 第三关 5 配置×3 方法 5 指标 + 正式 LCOE |
| `gate4_3yr_result.json` + `gate4_partial_compact_{m}.json` | 第四关三年链式 + TE + LCOE + Bootstrap CI |
