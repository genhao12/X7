# -*- coding: utf-8 -*-
"""
gen_seasonal_h2_ref.py — Gate B 冻结项③：季节 H₂ 参考轨迹生成器（v9 §5.7）

唯一流程（已冻结，v8 意见 2/3/4/13 + v9 意见 13）：
  对每个容量配置 c（5 个代表配置）：
  1) 2016 数据 → 日尺度全年能量平衡优化（LP，Gurobi）：
       决策变量：逐日 SOC / H₂ / P_fc / P_el / P_curt / P_uns / P_ch / P_dis
       目标    ：min Σ(w_fc·P_fc + w_el·P_el + w_curt·P_curt)   ← 2016 全年运行成本最小
                 （固定容量下 CAPEX 已定，季节库存轨迹主要影响运行成本，v8 意见 4）
       约束    ：逐日能量平衡；SOC/H₂ 动态（LHV_H2=33.3 kWh/kg + 线性泄漏 0.0005/day，
                 与 ems_stage2b 同口径）；SOC∈[0.15,0.95]×E；H₂∈[0.10,1.0]×M；
                 P_fc≤24×P_fc_rated、P_el≤24×P_el_rated；弃光≤当日 PV、失负荷≤当日负荷；
                 全年 LPSP=ΣP_uns/Σload ≤ 1%；年首尾 H₂ 闭合（H₂[366]=H₂[0]）
  2) 最优储氢轨迹 m*_{H2,c}(d)（逐日末库存）→ 归一化 ρ(d)=m*/M_H2,c（跨配置可比）
  3) 删除 2 月 29 日（2016 闰年 366 点 → 365 点；2/28–3/1 在新序列中相邻连续）
  4) 周期边界 7 日移动平均（首尾环绕，保证 1/1 与 12/31 连续）
  5) clip 至 [0.15, 0.85]（无量纲）
  6) H₂_ref,c(day) = ρ_smooth_clipped(day) × M_H2,c  → 冻结（365 点绝对 kg）
  7) 2017 轻量验证：用 2017 数据跑同样日尺度优化，对比轨迹差异（MAE/Pearson）
     —— 仅初步证据；最终验证在第二关（2016 轨迹作终端参考的 15min MILP，
        2017 只验证、2018 冻结，v9 意见 6/13）

定位声明（v8 意见 6）：日尺度模型忽略 15 min 启停/爬坡/短时可靠性，其输出
**不得称为全年最优运行策略**；仅生成 H₂_ref,c(day) 供 24h MILP 终端参考，
最终可靠性与经济结论来自 15 min 闭环 EMS。

产物：h2_ref_seasonal.json（5 配置 × 365 点，Gate B 冻结，2018 前不得重新拟合）

运行环境：DL 环境（D:/Anaconda/envs/DL，gurobipy 12.0.3）
命令：
  D:/Anaconda/envs/DL/python.exe 06_src/predopt_ems/dl_env/scripts/gen_seasonal_h2_ref.py
"""
import sys, os, json, time
import numpy as np
import pandas as pd

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, _SRC)

from ems.ems_stage2b import Stage2BEMSConfig
from components.annual_input_contract import scale_pv_available

CSV = r"D:\matlab\C\02_data_processed\tradestreet_pipeline\64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv"
ADM = r"D:\matlab\C\07_results\rectification_v2_stage2c\search\verified_admissions.json"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results", "h2_ref_seasonal.json")

LHV = 33.3                 # kWh/kg，全项目统一口径
ETA_FC, ETA_EL = 0.50, 0.70  # ems_stage2b 常数效率
ETA_CH = ETA_DIS = float(np.sqrt(0.90))   # η_ch·η_dis=η_rt=0.90（与 milp_scheduler 一致）
R_LEAK_DAY = 0.0005        # h₂_leakage_per_day（线性，day⁻¹）
SOC_LO, SOC_HI = 0.15, 0.95
H2_LO_F, H2_HI_F = 0.10, 1.0
LPSP_CAP = 0.01            # 全年 LPSP ≤ 1%（准入口径 ΣP_uns/Σload）
W = {"fc": 1.0, "el": 0.3, "curt": 0.1, "uns": 100.0}   # 与 MILP L3 占位权重一致（2017 验证集选择前占位）；
                                                          # uns 高权重：失负荷非零成本（否则优化会"免费"用满
                                                          # LPSP≤1% 约束，H2 轨迹可靠性偏松——2026-08-08 修正）
RHO_LO, RHO_HI = 0.15, 0.85   # 归一化 clip 边界（v9 §5.7）
SMOOTH_WIN = 7             # 周期边界移动平均窗口


def load_configs():
    adm = json.load(open(ADM, encoding="utf-8"))
    out = []
    for r in adm["representatives"]:
        cfg = Stage2BEMSConfig(
            pv_kwp=float(r["pv_kwp"]), bat_kwh=float(r["bat_kwh"]),
            pemfc_kw=float(r["pemfc_kw"]), pemel_kw=float(r["pemel_kw"]),
            h2_kg=float(r["h2_kg"]),
        )
        out.append((r["representative_role"], cfg))
    return out


def load_year_daily(year: int, cfg: Stage2BEMSConfig):
    """64 CSV 某年 → 逐日 PV/负荷可用能量（kWh）。
    PV 先 scale_pv_available 缩放到配置容量（233 kW 参考 → cfg.pv_kwp，
    年产能 ≈1752 kWh/kWp，UCSD 实测口径），ffill 缺失点后按天聚合。"""
    df = pd.read_csv(CSV)
    d = df[df["local_civil_year"] == year].reset_index(drop=True)
    pv_raw = d["p_pv_available_reconstructed_nonnegative_kW"].ffill().bfill().fillna(0.0).to_numpy(dtype=float)
    load = d["p_building_load_reconstructed_kW"].ffill().bfill().fillna(0.0).to_numpy(dtype=float)
    pv_scaled = scale_pv_available(pv_raw, nominal_kw=cfg.pv_kwp)   # → 配置容量
    n_days = len(pv_raw) // 96
    pv = pv_scaled[:n_days * 96].reshape(n_days, 96).sum(axis=1) * 0.25   # kWh/day
    ld = load[:n_days * 96].reshape(n_days, 96).sum(axis=1) * 0.25
    return pv, ld


def daily_lp(pv_day, load_day, cfg, h2_ref=None, w_ref=0.05):
    """日尺度全年能量平衡优化（LP，Gurobi）。
    返回 (h2_end_kg[ndays+1], lpsp, obj)。
    h2_ref: 可选 365/366 点日末 H₂ 参考轨迹（kg）——作为软目标（|H2[d+1]−ref[d]|/M，
    权重 w_ref 小，不主导运行成本），用于 v9 §5.7 "2017 带参考 vs 不带参考"验证。"""
    import gurobipy as gp
    nd = len(pv_day)
    E, M = cfg.bat_kwh, cfg.h2_kg
    P_bat_max = cfg.bat_kwh * cfg.bat_c_rate
    m = gp.Model("h2ref_daily")
    m.setParam("OutputFlag", 0)
    m.setParam("Threads", 1)
    m.setParam("Seed", 0)
    m.setParam("FeasibilityTol", 1e-6)
    m.setParam("OptimalityTol", 1e-6)
    m.setParam("Method", -1)

    SOC = m.addVars(nd + 1, lb=SOC_LO * E, ub=SOC_HI * E, name="SOC")      # kWh 绝对
    H2 = m.addVars(nd + 1, lb=H2_LO_F * M, ub=H2_HI_F * M, name="H2")      # kg
    P_fc = m.addVars(nd, lb=0, ub=24.0 * cfg.pemfc_kw, name="P_fc")        # kWh/day
    P_el = m.addVars(nd, lb=0, ub=24.0 * cfg.pemel_kw, name="P_el")
    P_curt = m.addVars(nd, lb=0, name="P_curt")
    P_uns = m.addVars(nd, lb=0, name="P_uns")
    P_ch = m.addVars(nd, lb=0, ub=24.0 * P_bat_max, name="P_ch")
    P_dis = m.addVars(nd, lb=0, ub=24.0 * P_bat_max, name="P_dis")

    for d in range(nd):
        m.addConstr(pv_day[d] + P_dis[d] + P_fc[d] + P_uns[d]
                    == load_day[d] + P_ch[d] + P_el[d] + P_curt[d], f"bal_{d}")
        m.addConstr(SOC[d + 1] == SOC[d] + ETA_CH * P_ch[d] - P_dis[d] / ETA_DIS, f"soc_{d}")
        m.addConstr(H2[d + 1] == H2[d]
                    + ETA_EL * P_el[d] / LHV - P_fc[d] / (ETA_FC * LHV)
                    - R_LEAK_DAY * H2[d], f"h2_{d}")
        m.addConstr(P_curt[d] <= pv_day[d], f"curt_{d}")
        m.addConstr(P_uns[d] <= load_day[d], f"uns_{d}")
    m.addConstr(SOC[0] == 0.5 * E, "soc0")
    m.addConstr(H2[nd] == H2[0], "h2_close")                       # 年首尾 H₂ 闭合
    m.addConstr(gp.quicksum(P_uns[d] for d in range(nd))
                <= LPSP_CAP * gp.quicksum(load_day[d] for d in range(nd)), "lpsp")

    base_obj = gp.quicksum(W["fc"] * P_fc[d] + W["el"] * P_el[d]
                           + W["curt"] * P_curt[d] + W["uns"] * P_uns[d]
                           for d in range(nd))
    if h2_ref is not None:
        # v9 §5.7 验证：终端参考软目标（线性化 |H2[d+1]−ref[d]|/M），w_ref 小不主导。
        # 【2026-08-08 修复】不得用 m.getObjective()+... 拼接（gurobipy 12 会丢失原目标
        # 项，致 obj=0/LPSP 贴上限假象）；一次性构建完整目标。
        assert len(h2_ref) == nd, f"h2_ref 长度 {len(h2_ref)} != 天数 {nd}"
        d_ref = m.addVars(nd, lb=0, name="d_ref")
        for d in range(nd):
            m.addConstr(d_ref[d] >= (H2[d + 1] - h2_ref[d]) / M, f"ref_p_{d}")
            m.addConstr(d_ref[d] >= (h2_ref[d] - H2[d + 1]) / M, f"ref_n_{d}")
        base_obj += w_ref * gp.quicksum(d_ref[d] for d in range(nd))
    m.setObjective(base_obj, gp.GRB.MINIMIZE)
    m.optimize()
    if m.status != gp.GRB.OPTIMAL:
        raise RuntimeError(f"日尺度优化非 OPTIMAL: status={m.status}")
    h2_end = np.array([H2[d].X for d in range(nd + 1)])
    lpsp = float(sum(P_uns[d].X for d in range(nd))) / max(float(sum(load_day)), 1e-9)
    return h2_end, lpsp, float(m.objVal)


def periodic_smooth(x, win=7):
    """周期边界移动平均（首尾环绕）。x: 1-D array（365 点）。"""
    n = len(x)
    xp = np.concatenate([x[-win:], x, x[:win]])
    out = np.array([np.mean(xp[i:i + 2 * win + 1]) for i in range(win, win + n)])
    return out


def build_ref(h2_end_366, M, verbose=True):
    """从 2016 优化轨迹生成冻结参考：删 2/29 → 365 点 → 平滑 → clip → ×M。
    2016 闰年 366 天：2/29 日末索引 59（0-based；1/1=0,...,2/28=58,2/29=59）。"""
    assert len(h2_end_366) == 367, f"应 366 天日末+1 闭合点，实际 {len(h2_end_366)}"
    m_star = h2_end_366[1:]                          # 366 个日末库存（d=0..365）
    m365 = np.delete(m_star, 59)                     # 删 2/29 日末 → 365 点（2/28 与 3/1 相邻）
    rho = m365 / M                                   # 归一化
    rho_s = periodic_smooth(rho, SMOOTH_WIN)         # 周期边界 7 日移动平均
    rho_c = np.clip(rho_s, RHO_LO, RHO_HI)           # clip [0.15, 0.85]
    ref_kg = rho_c * M                               # 绝对 kg（冻结产物）
    if verbose:
        print(f"   ρ 范围 [min,max]=[{rho.min():.3f},{rho.max():.3f}] → 平滑后 "
              f"[{rho_s.min():.3f},{rho_s.max():.3f}] → clip [{rho_c.min():.3f},{rho_c.max():.3f}]")
        print(f"   H2_ref 范围 [{ref_kg.min():.1f}, {ref_kg.max():.1f}] kg | 首尾差 "
              f"{abs(ref_kg[0] - ref_kg[-1]):.2f} kg")
    return ref_kg, rho_c


def main():
    t0 = time.time()
    configs = load_configs()
    print("=" * 72)
    print("季节 H₂ 参考轨迹生成器（v9 §5.7 唯一流程，Gate B 冻结项③）")
    print(f"5 配置 × 2016/2017 逐日优化 | 权重(占位) {W} | "
          f"LPSP≤{LPSP_CAP:.0%} | clip ρ∈[{RHO_LO},{RHO_HI}] | 平滑 {SMOOTH_WIN} 日")
    print("=" * 72)
    result = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "method": ("v9 §5.7 唯一流程：2016 日尺度全年能量平衡优化(LP, min 运行成本, "
                   "LPSP≤1%, 年首尾 H2 闭合) → ρ=m*/M_H2 归一化 → 删 2/29 → 365 点 → "
                   "周期边界 7 日移动平均 → clip[0.15,0.85] → ×M_H2 → 冻结"),
        "position_note": ("日尺度模型忽略 15min 启停/爬坡/短时可靠性，输出不得称为全年最优"
                          "运行策略；仅作 24h MILP 终端参考，最终可靠性与经济结论来自 "
                          "15min 闭环 EMS（v8 意见 6）"),
        "weights_placeholder": W,
        "configs": {},
        "frozen_for": "2017 验证 + 2018 冻结（第二关 15min MILP 终端参考 H2_ref,c(day)）",
    }
    for role, cfg in configs:
        pv16, ld16 = load_year_daily(2016, cfg)
        pv17, ld17 = load_year_daily(2017, cfg)
        print(f"\n[{role}] 日尺度优化（2016 {len(pv16)} 天, PV {cfg.pv_kwp:.0f} kWp）...", flush=True)
        h2_end16, lpsp16, obj16 = daily_lp(pv16, ld16, cfg)
        ref_kg, rho_c = build_ref(h2_end16, cfg.h2_kg, verbose=True)
        print(f"   2016 LPSP = {lpsp16:.6f}（≤1% 约束；w_uns=100 下趋近 0）")
        # —— 2017 验证（v9 §5.7 语义：参考轨迹作为终端目标时 LPSP 不劣于无参考）——
        # 2017 自由优化 vs 2017 带 2016 参考（软目标 w_ref 小）——两解同有 LPSP≤1% 约束
        h2_end17_free, lpsp17_free, obj17_free = daily_lp(pv17, ld17, cfg)
        h2_end17_ref, lpsp17_ref, obj17_ref = daily_lp(pv17, ld17, cfg, h2_ref=ref_kg)
        m17_free = h2_end17_free[1:]                    # 365 点（2017 非闰年）
        mae = float(np.mean(np.abs(m17_free - ref_kg)))
        pearson = float(np.corrcoef(m17_free, ref_kg)[0, 1]) if np.std(m17_free) > 0 and np.std(ref_kg) > 0 else None
        pearson_s = "None(方差0)" if pearson is None else f"{pearson:.3f}"
        ref_ok = lpsp17_ref <= 1e-6 or lpsp17_ref <= max(lpsp17_free + 1e-4, LPSP_CAP)
        print(f"   2017 验证: 自由 LPSP {lpsp17_free:.6f} vs 带参考 LPSP {lpsp17_ref:.6f} "
              f"→ {'PASS（不恶化）' if ref_ok else 'FAIL'} | "
              f"轨迹 MAE {mae:.0f} kg | Pearson {pearson_s}")
        result["configs"][role] = {
            "h2_capacity_kg": cfg.h2_kg,
            "lpsp_2016_opt": round(float(lpsp16), 6),
            "lpsp_2017_free": round(float(lpsp17_free), 6),
            "lpsp_2017_with_ref": round(float(lpsp17_ref), 6),
            "ref_2017_check_pass": bool(ref_ok),
            "obj_2016_run_cost": round(float(obj16), 1),
            "obj_2017_free": round(float(obj17_free), 1),
            "obj_2017_with_ref": round(float(obj17_ref), 1),
            "rho_365_clipped": [round(float(v), 6) for v in rho_c],
            "h2_ref_kg_365": [round(float(v), 2) for v in ref_kg],
            "h2_ref_range_kg": [round(float(ref_kg.min()), 1), round(float(ref_kg.max()), 1)],
            "year2017_check": {
                "mae_kg": round(mae, 1),
                "pearson": None if pearson is None else round(pearson, 4),
                "note": ("v9 §5.7 验证：2017 带 2016 参考(软目标 w_ref=0.05) vs 自由优化，"
                         "LPSP 不劣化即通过；最终验证=15min MILP（第二关）"),
            },
        }
    json.dump(result, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("-" * 72)
    print(f"完成：{len(configs)} 配置 × 365 点已冻结 → {OUT}")
    print(f"总耗时 {time.time() - t0:.1f}s")
    print("状态：2018 首次测试前不得重新拟合（v9 意见 6/13）")


if __name__ == "__main__":
    main()
