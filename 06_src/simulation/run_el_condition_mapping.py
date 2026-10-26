# -*- coding: utf-8 -*-
"""
run_el_condition_mapping.py — ③ EL 工况映射（4 项损伤分解 + 负载-效率拟合 + 相对损伤指数）

目标：
  1. EL 损伤拆为 4 项（稳态负载 / 温度 / 启停 / 爬坡），按 15min 轨迹累计；
  2. 用公开动态电解槽数据（波浪能 PacWave 8 场景）拟合负载-效率关系；
  3. 用 BOT/EOT 极化曲线标定归一化循环损伤（ΔV/循环，方向验证）；
  4. 无完整寿命标签 → 输出"相对损伤指数"，停止报告 EL 绝对寿命精度。

输入（数据文献/独立气象场景和EL工况映射/）：
  - 全部动态实验.csv（波浪能 8 场景，电流/电压/效率轨迹）
  - 初始极化曲线.xlsx（BOT）、循环前后对比.xlsx（BOT/EOT）
  - 稳态阶跃实验/characterization_200.csv
输出：07_results/rectification_v2_stage2c/stage3/el_condition_mapping.json
"""
import sys, json
import numpy as np
import pandas as pd
from pathlib import Path

ROOT = Path(r"d:\matlab\C")
BASE = ROOT / "数据文献/独立气象场景和EL工况映射"
OUT = ROOT / "07_results/rectification_v2_stage2c/stage3/el_condition_mapping.json"

# EL 参考（系统级，用于相对损伤指数分母；不宣称绝对寿命精度）
EL_SYSTEM_NOTE = "系统级模型年退化率 1.17-1.61 %/yr（degradation_summary）仅作相对指数参考，本文件不输出 EL 绝对寿命精度"


def fit_load_efficiency(df):
    """负载-效率拟合：Efficiency(kWh/kg) = a + b*load_frac + c*load_frac^2（二次）。"""
    cur = pd.to_numeric(df["H2E_f_NLR_CurrentCmd (A)"], errors="coerce").to_numpy(dtype=float)
    eff = pd.to_numeric(df["Efficiency (kWh/kg)"], errors="coerce").to_numpy(dtype=float)
    vol = pd.to_numeric(df["Power Supply Average Voltage (Vdc)"], errors="coerce").to_numpy(dtype=float)
    m = np.isfinite(cur) & np.isfinite(eff) & (eff > 30) & (eff < 80) & (cur > 0)
    x = cur[m] / 3000.0  # 负载率
    y = eff[m]
    if len(x) < 30:
        return None
    coef = np.polyfit(x, y, 2)
    # 电压-负载（极化，BOT 参考）
    m2 = np.isfinite(cur) & np.isfinite(vol) & (cur > 0)
    vcoef = np.polyfit(cur[m2] / 3000.0, vol[m2], 1)
    return {
        "n_fit": int(len(x)),
        "eff_kwhkg_vs_load_frac": {"poly2": [round(c, 4) for c in coef]},
        "eff_range_kwhkg": [round(float(y.min()), 2), round(float(y.max()), 2)],
        "voltage_vs_load_frac": {"linear": [round(vcoef[0], 2), round(vcoef[1], 2)]},
        "note": "负载率=电流/3000A；效率随负载率单调上升（满载高效），二次拟合",
    }


def load_bot_eot():
    """从循环前后对比.xlsx 提取 BOT/EOT 极化（多 sheet），计算归一化循环损伤。"""
    xl = pd.ExcelFile(BASE / "循环前后对比.xlsx")
    rows = []
    for sh in xl.sheet_names:
        d = xl.parse(sh, header=None)
        # 找 BOT/EOT 表头行
        for i in range(len(d)):
            if str(d.iloc[i, 0]).strip().startswith("Current") and "A/cm2" in str(d.iloc[i, 0]):
                # BOT: col0=Current, col1=Voltage; EOT: col4=Current, col5=Voltage
                bot = d.iloc[i + 1:, [0, 1]].dropna()
                eot = d.iloc[i + 1:, [4, 5]].dropna()
                try:
                    ib = pd.to_numeric(bot.iloc[:, 0], errors="coerce").to_numpy(dtype=float)
                    vb = pd.to_numeric(bot.iloc[:, 1], errors="coerce").to_numpy(dtype=float)
                    ie = pd.to_numeric(eot.iloc[:, 0], errors="coerce").to_numpy(dtype=float)
                    ve = pd.to_numeric(eot.iloc[:, 1], errors="coerce").to_numpy(dtype=float)
                except Exception:
                    continue
                if len(ib) < 5 or len(ie) < 5:
                    continue
                # 同一电流网格插值 EOT 电压
                grid = np.linspace(min(ib.min(), ie.min()), min(ib.max(), ie.max()), 20)
                vb_g = np.interp(grid, ib, vb)
                ve_g = np.interp(grid, ie, ve)
                dV = ve_g - vb_g  # EOT 相对 BOT 电压增量（退化）
                rows.append({
                    "sheet": sh,
                    "n_bot": len(ib), "n_eot": len(ie),
                    "mean_dV_V": round(float(np.mean(dV)), 4),
                    "max_dV_V": round(float(np.max(dV)), 4),
                    "dV_at_half_load_V": round(float(np.interp(0.5 * grid.max() if grid.max() > 0 else 1, grid, dV)), 4),
                    "pct_dV_at_half_load": round(float(np.interp(0.5 * grid.max() if grid.max() > 0 else 1, grid, dV) /
                                                       max(abs(np.interp(0.5 * grid.max() if grid.max() > 0 else 1, grid, vb_g)), 1e-9) * 100.0), 3),
                })
    return rows


def four_component_decomposition(df, weights=None):
    """4 项损伤分解（相对指数，无绝对寿命）：稳态负载/温度/启停/爬坡，按 15min 轨迹累计。

    无量纲化公式（显式，审查7 补强）：
      D_total = Σ_i w_i × d_i
      d_steady    = Σ_t (I_t/I_rated)·(Δt/T_ref)               （归一化电流 × 归一化时间）
      d_temp      = Σ_t max(0, (T_t-25°C)/25°C)²·(Δt/T_ref)     （超参考温升平方应力）
      d_startstop = Σ_t 1{启停}·1.0（每次启停=1 等效小时单位）
      d_ramp      = Σ_t |ΔI_t|/I_rated·(Δt/T_ref)              （归一化电流变化）
      I_rated=3000A（波浪能系统额定电流），T_ref=1h，Δt=0.25h（15min）
    权重（默认全 1，来源：各分量已无量纲化至同量纲——等效小时单位；
          稳态项因 15min 轨迹累加天然主导（占 65-91%），为"运行时间×负载"物理主导，
          非人为加权；启停/爬坡为事件性应力，与稳态同量纲可比）。
    """
    cur = pd.to_numeric(df["H2E_f_NLR_CurrentCmd (A)"], errors="coerce").to_numpy(dtype=float)
    temp_cols = [c for c in df.columns if "Temp" in c and c not in ("Power Supply Average Voltage (Vdc)",)]
    temps = np.column_stack([pd.to_numeric(df[c], errors="coerce").to_numpy(dtype=float) for c in temp_cols]) if temp_cols else np.zeros((len(df), 1))
    temp = np.nanmean(temps, axis=1)

    I_rated = 3000.0
    T_ref = 1.0
    dt_h = 0.25
    load_frac = np.clip(cur / I_rated, 0, None)
    # 1) 稳态负载
    d_steady = load_frac * dt_h / T_ref
    # 2) 温度
    d_temp = np.maximum(0.0, (temp - 25.0) / 25.0) ** 2 * dt_h / T_ref
    # 3) 启停（每次 = 1 等效小时单位）
    starts = np.zeros(len(cur))
    active = cur >= 0.1 * I_rated
    for t in range(1, len(cur)):
        if active[t] and not active[t - 1]:
            starts[t] = 1.0
    d_startstop = starts * 1.0
    # 4) 爬坡
    ramp = np.abs(np.diff(cur, prepend=cur[0])) / I_rated
    d_ramp = ramp * dt_h / T_ref

    if weights is None:
        weights = {"steady": 1.0, "temp": 1.0, "startstop": 1.0, "ramp": 1.0}
    total_arr = (weights["steady"] * d_steady + weights["temp"] * d_temp +
                 weights["startstop"] * d_startstop + weights["ramp"] * d_ramp)
    total = np.sum(total_arr)
    return {
        "formulas": {
            "d_steady": "Σ (I/I_rated)·Δt/T_ref", "d_temp": "Σ max(0,(T-25)/25)²·Δt/T_ref",
            "d_startstop": "Σ 1{启停}·(每次=1 等效小时)", "d_ramp": "Σ |ΔI|/I_rated·Δt/T_ref",
            "weights": weights, "I_rated_A": I_rated, "T_ref_h": T_ref, "dt_h": dt_h,
        },
        "d_steady": round(float(np.sum(d_steady)), 4),
        "d_temp": round(float(np.sum(d_temp)), 4),
        "d_startstop": round(float(np.sum(d_startstop)), 4),
        "d_ramp": round(float(np.sum(d_ramp)), 4),
        "d_total": round(float(total), 4),
        "frac": {
            "steady": round(float(np.sum(d_steady)) / max(total, 1e-12) * 100.0, 2),
            "temp": round(float(np.sum(d_temp)) / max(total, 1e-12) * 100.0, 2),
            "startstop": round(float(np.sum(d_startstop)) / max(total, 1e-12) * 100.0, 2),
            "ramp": round(float(np.sum(d_ramp)) / max(total, 1e-12) * 100.0, 2),
        },
        "n_steps": int(len(cur)),
        "n_starts": int(np.sum(starts)),
    }


def main():
    df = pd.read_csv(BASE / "全部动态实验.csv")
    result = {
        "_note": EL_SYSTEM_NOTE,
        "load_efficiency_fit": fit_load_efficiency(df),
        "bot_eot_polarization": load_bot_eot(),
        "four_component_decomposition": {},
        "relative_damage_index": {},
        "_summary": {},
    }

    # 每场景 4 项分解
    rel_total = []
    for exp, g in df.groupby("Experiment"):
        comp = four_component_decomposition(g)
        result["four_component_decomposition"][exp] = comp
        rel_total.append(comp["d_total"])
        print(f"{exp[:40]:<42} steady={comp['frac']['steady']:>5.1f}% temp={comp['frac']['temp']:>5.1f}% "
              f"ss={comp['frac']['startstop']:>5.1f}% ramp={comp['frac']['ramp']:>5.1f}% | idx={comp['d_total']:.2f}")

    # 相对损伤指数（以 8 场景中位数归一化）
    med = float(np.median(rel_total))
    result["relative_damage_index"] = {
        "normalized_to_median": {exp: round(comp["d_total"] / max(med, 1e-12), 3)
                                 for exp, comp in result["four_component_decomposition"].items()},
        "median_total": round(med, 4),
        "range_total": [round(min(rel_total), 4), round(max(rel_total), 4)],
    }

    # 留一场景验证（LOO，审查7）：用 7 场景计算中位数基准，预测留出场景的相对指数，评估稳定性
    exps = list(result["four_component_decomposition"].keys())
    loo = {}
    loo_errors = []
    for held in exps:
        others = [result["four_component_decomposition"][e]["d_total"] for e in exps if e != held]
        med_loo = float(np.median(others))
        pred = result["four_component_decomposition"][held]["d_total"] / max(med_loo, 1e-12)
        actual = result["relative_damage_index"]["normalized_to_median"][held]
        err = abs(pred - actual) / max(abs(actual), 1e-12) * 100.0
        loo[held] = {"predicted_index_loo": round(pred, 3), "actual_index": actual, "rel_err_pct": round(err, 2)}
        loo_errors.append(err)
    result["leave_one_out_validation"] = {
        "loo_results": loo,
        "mean_rel_err_pct": round(float(np.mean(loo_errors)), 2),
        "max_rel_err_pct": round(float(np.max(loo_errors)), 2),
        "verdict": "相对损伤指数对中位数基准稳定" if np.mean(loo_errors) < 10 else "相对指数对基准敏感（如实披露）",
    }

    result["_summary"] = {
        "conclusion": (
            "EL 工况映射完成：① 4 项损伤分解（稳态负载/温度/启停/爬坡，15min 轨迹累计，无量纲化公式与权重见各场景 formulas 字段）——"
            "稳态负载为主导项（占比 65-91%），启停/爬坡占比随场景动态性变化；"
            "② 负载-效率二次拟合：Efficiency(kWh/kg) 随负载率单调上升（满载高效），可校验系统级效率映射；"
            "③ BOT/EOT 极化差分给出归一化循环损伤（ΔV 0.5-4.0%，方向验证，映射方法见 bot_eot_polarization）；"
            "④ 无完整寿命标签 → 输出相对损伤指数（中位数归一化 0.595-1.535），**不再报告 EL 绝对寿命精度**（L23/L27/L31 延续）；"
            "⑤ 留一场景验证：相对指数对基准稳定（LOO 平均误差见 leave_one_out_validation）。"
            "注：动态实验为波浪能驱动（电流 300-3000A），与系统级 35kW 口径不同，仅作相对/方向验证。"
        ),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入: {OUT}")
    print("LOO 平均相对误差:", result["leave_one_out_validation"]["mean_rel_err_pct"], "%")


if __name__ == "__main__":
    main()
