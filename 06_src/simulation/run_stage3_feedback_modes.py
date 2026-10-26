# -*- coding: utf-8 -*-
"""
run_stage3_feedback_modes.py — Stage2c 5 代表配置的链式反馈模式主仿真（P1a）

目的：将"全设备退化反馈"提升为主仿真口径（对应 L1/L13/L20 的改善途径 P1）。
对 5 个代表配置 × 3 年（2016→2017→2018）链式运行三种反馈模式，双口径（raw/cap）：
  M0 open_loop : D_state 恒 (0,0,0)      —— 开环基线（应与 degradation_summary 一致）
  M1 bat_only  : D_state=(D_bat,0,0)     —— 仅电池反馈（D_bat 逐年累积）
  M4 full      : D_state=(D_bat,D_fc,D_el) —— 全设备反馈（全部逐年累积）

D 累积：raw（不封顶）/ cap（封顶 1.0）两种 eol_policy 各跑一遍。
输出 TE（相对开环）设备级 + 总损伤，供双报告使用。

输出：07_results/rectification_v2_stage2c/stage3/stage3_feedback_modes.json
"""
import sys, json
import numpy as np
from pathlib import Path

ROOT = Path(r"d:\matlab\C")
sys.path.insert(0, str(ROOT / "06_src"))

from ems.ems_stage2b import Stage2BEMSConfig, EMSState, dispatch_stage2b
from components.annual_input_contract import load_evaluation_years, scale_pv_available
from simulation.run_stage3_full_analysis import build_c1_config, hp_bat_default, run_three_year_cycle, extract_commands
from simulation.closed_loop_lite import compute_damage, CoalitionState

DATA = ROOT / "02_data_processed/tradestreet_pipeline/64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv"
ADM = ROOT / "07_results/rectification_v2_stage2c/search/verified_admissions.json"
OUT = ROOT / "07_results/rectification_v2_stage2c/stage3/stage3_feedback_modes.json"
COAL = CoalitionState.from_code("1111")

MODES = ["open_loop", "bat_only", "full"]
YEARS = (2016, 2017, 2018)


def run_chained(rep, loaded, policy: str):
    """3 年链式：返回各模式年均损伤 dict {mode: {dev: dD}} 与最终 D。"""
    cfg_ems = Stage2BEMSConfig(
        pv_kwp=float(rep["pv_kwp"]), bat_kwh=float(rep["bat_kwh"]),
        pemfc_kw=float(rep["pemfc_kw"]), pemel_kw=float(rep["pemel_kw"]),
        h2_kg=float(rep["h2_kg"]),
    )
    c1 = build_c1_config(rep)
    # 预调度三年（与模式无关；与 degradation_summary 同用 run_three_year_cycle 不动点迭代）
    prepared = {}
    for y in YEARS:
        frame = loaded[y]
        pv = scale_pv_available(frame["pv_raw_kw"].to_numpy(dtype=float), nominal_kw=float(rep["pv_kwp"]))
        prepared[y] = {"load": frame["load_kw"].to_numpy(dtype=float), "pv": pv}
    cycle = run_three_year_cycle(prepared, cfg_ems)
    cmds_by_year = extract_commands(cycle)

    out = {}
    for mode in MODES:
        D = {"bat": 0.0, "fc": 0.0, "el": 0.0}
        year_sums = {y: {"bat": 0.0, "fc": 0.0, "el": 0.0} for y in YEARS}
        for y in YEARS:
            if mode == "open_loop":
                D_state = {"bat": 0.0, "fc": 0.0, "el": 0.0}
            elif mode == "bat_only":
                D_state = {"bat": D["bat"], "fc": 0.0, "el": 0.0}
            else:  # full
                D_state = dict(D)
            dmg = compute_damage(
                hp_func=lambda Dv, bkw=float(rep["bat_kwh"]): hp_bat_default(Dv, bat_kwh=bkw),
                ems_commands=cmds_by_year[y], config=c1,
                dt_min=15.0, D_state=D_state, coalition=COAL,
                eta_degrade=0.10, eol_policy=policy,
            )
            # 使用 eol_policy 对应的 dD（cap 时已封顶）
            dD = {dev: float(np.sum(dmg[f"dD_{dev}"])) for dev in ("bat", "fc", "el")}
            year_sums[y] = dD
            if policy == "cap":
                D = {dev: min(1.0, D[dev] + dD[dev]) for dev in ("bat", "fc", "el")}
            else:
                D = {dev: D[dev] + dD[dev] for dev in ("bat", "fc", "el")}
        out[mode] = {
            "annual": year_sums,
            "avg": {dev: float(np.mean([year_sums[y][dev] for y in YEARS])) for dev in ("bat", "fc", "el")},
            "total_avg": float(sum(np.mean([year_sums[y][dev] for y in YEARS]) for dev in ("bat", "fc", "el"))),
            "final_D": D,
        }
    return out


def main():
    loaded = load_evaluation_years(DATA)
    admissions = json.loads(ADM.read_text(encoding="utf-8"))
    all_out = {}
    for rep in admissions["representatives"]:
        role = rep["representative_role"]
        per_policy = {}
        for policy in ("raw", "cap"):
            per_policy[policy] = run_chained(rep, loaded, policy)
        # TE 相对开环（raw/cap 分别）
        for policy in ("raw", "cap"):
            base = per_policy[policy]["open_loop"]
            per_policy[policy]["_te_vs_open"] = {}
            for mode in ("bat_only", "full"):
                d = per_policy[policy][mode]
                te = {}
                for dev in ("bat", "fc", "el"):
                    te[dev] = round((d["avg"][dev] - base["avg"][dev]) / abs(base["avg"][dev]) * 100.0, 3) if abs(base["avg"][dev]) > 1e-12 else None
                te["total"] = round((d["total_avg"] - base["total_avg"]) / abs(base["total_avg"]) * 100.0, 3)
                per_policy[policy]["_te_vs_open"][mode] = te
        all_out[role] = per_policy
        p = per_policy["cap"]
        print(f"{role:<18} cap TE total: bat_only={p['_te_vs_open']['bat_only']['total']}% full={p['_te_vs_open']['full']['total']}% | full dev: {p['_te_vs_open']['full']}")

    all_out["_method"] = {
        "modes": MODES,
        "years": list(YEARS),
        "chaining": "D 逐年累积（raw 不封顶 / cap 封顶 1.0）；D_state 用于效率退化修正（FC/EL eta_factor）与 BAT hp 映射",
        "note": "open_loop 对应 degradation_summary 开环基线（D_state=0）口径，用于交叉验证；"
                "full（cap）为本文件建议的主仿真双报告口径之一；与 T2 固定强度消融（D=0.5）属不同口径，须分别引用",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(all_out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"结果已写入: {OUT}")
    # 开环交叉验证提示
    summ = json.loads((ROOT / "07_results/rectification_v2_stage2c/stage3/degradation_summary.json").read_text(encoding="utf-8"))
    print("\n=== 开环交叉验证（本脚本 open_loop avg vs degradation_summary avg）===")
    for rep in summ["representatives"]:
        role = rep["role"]
        ours = all_out[role]["raw"]["open_loop"]["avg"]
        ref = rep["avg_annual_damage"]
        for dev in ("bat", "fc", "el"):
            d = abs(ours[dev] - ref[dev]) / abs(ref[dev]) * 100 if abs(ref[dev]) > 1e-12 else 0
            flag = "OK" if d < 1.0 else "DIFF!"
            print(f"  {role:<18} {dev}: ours={ours[dev]:.4f} ref={ref[dev]:.4f} dev={d:.2f}% {flag}")


if __name__ == "__main__":
    main()
