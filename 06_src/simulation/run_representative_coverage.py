# -*- coding: utf-8 -*-
"""
run_representative_coverage.py — 任务3：代表配置选择透明化 + 149 组 TE 覆盖验证

1. 完整公开 1792→149→5 筛选过程（容量范围/准入阈值/守恒误差/LPSP/聚类/代表规则）
2. 计算 149 个准入配置的 TE（单年 2017，battery_first，cap 口径，轻量近似）：
   - 验证 5 代表 TE 覆盖 149 组的 min/median/max
   - 容量类型覆盖（PV/BAT/FC/EL 范围）
输出：07_results/rectification_v2_stage2c/stage3/representative_coverage.json
"""
import sys, json
import numpy as np
from pathlib import Path

ROOT = Path(r"d:\matlab\C")
sys.path.insert(0, str(ROOT / "06_src"))

from ems.ems_stage2b import Stage2BEMSConfig, EMSState, dispatch_stage2b
from components.annual_input_contract import load_evaluation_years, scale_pv_available
from simulation.run_stage3_full_analysis import build_c1_config, hp_bat_default
from simulation.closed_loop_lite import compute_damage, CoalitionState

DATA = ROOT / "02_data_processed/tradestreet_pipeline/64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv"
ADM = ROOT / "07_results/rectification_v2_stage2c/search/verified_admissions.json"
OUT = ROOT / "07_results/rectification_v2_stage2c/stage3/representative_coverage.json"
COAL = CoalitionState.from_code("1111")


def te_single_year(load, pv, rep, D_preset=0.3):
    """单年 2017 TE（cap 口径，D 预置模拟累积状态，轻量覆盖验证用）。"""
    cfg_ems = Stage2BEMSConfig(
        pv_kwp=float(rep["pv_kwp"]), bat_kwh=float(rep["bat_kwh"]),
        pemfc_kw=float(rep["pemfc_kw"]), pemel_kw=float(rep["pemel_kw"]),
        h2_kg=float(rep["h2_kg"]),
    )
    c1 = build_c1_config(rep)
    cmds = dispatch_stage2b(
        load, pv, cfg_ems, strategy="battery_first",
        initial_state=EMSState(soc=0.5, h2_kg=cfg_ems.h2_kg * 0.5),
    ).commands
    out = {}
    for mode, Ds in (("open", {"bat": 0.0, "fc": 0.0, "el": 0.0}),
                     ("full", {"bat": D_preset, "fc": D_preset, "el": D_preset})):
        dmg = compute_damage(
            hp_func=lambda Dv, bkw=float(rep["bat_kwh"]): hp_bat_default(Dv, bat_kwh=bkw),
            ems_commands=cmds, config=c1, dt_min=15.0,
            D_state=Ds, coalition=COAL, eta_degrade=0.10, eol_policy="cap",
        )
        out[mode] = sum(float(np.sum(dmg[f"dD_{d}"])) for d in ("bat", "fc", "el"))
    te = (out["full"] - out["open"]) / max(abs(out["open"]), 1e-12) * 100.0
    return te


def main():
    loaded = load_evaluation_years(DATA)
    frame = loaded[2017]
    load = frame["load_kw"].to_numpy(dtype=float)
    adm = json.loads(ADM.read_text(encoding="utf-8"))
    cands = adm["candidates"]
    reps = adm["representatives"]

    # 1) 筛选过程统计
    proc = {
        "total_candidates_stage2c": 1792,
        "admitted": len(cands),
        "admission_checks": list(cands[0]["admission"]["checks"].keys()),
        "capacity_domain": {
            "pv_kwp": [374.7, 499.4], "bat_kwh": [1203.0, 2391.1],
            "pemfc_kw": [84.2, 198.6], "pemel_kw": [200.9, 495.6],
            "h2_kg": [2666.7, 5995.3],
        },
        "admission_thresholds": {
            "three_year_lpsp": "<1%", "cycle_converged": "soc/h2 闭合 ≤0.001",
            "power_conservation": "逐步守恒 1e-6 kW", "h2_conservation": "氢守恒",
            "machine_state_closure": "状态机闭合", "minimum_run_segments": "最小运行段",
            "state_closure_pass": True, "conservation_pass": True,
        },
        "representative_selection": {
            "method": "Sobol 分层抽样 + 覆盖最大化（5 冻结代表覆盖容量空间极值/中值/典型）",
            "roles": [r["representative_role"] for r in reps],
            "sobol_indices": [r["sobol_index"] for r in reps],
            "rules": "compact(小容量)/balanced(均衡)/hydrogen_dominant(氢大)/battery_dominant(电池大)/critical(临界容量)",
        },
    }

    # 2) 149 组 TE（单年 2017 轻量，逐配置独立进程避免内存崩溃）
    te_vals = {}
    for i, c in enumerate(cands):
        role_key = c["candidate_id"]
        te_vals[role_key] = te_single_year(load, scale_pv_available(
            frame["pv_raw_kw"].to_numpy(dtype=float), nominal_kw=float(c["pv_kwp"])), c)
        if (i + 1) % 30 == 0:
            print(f"  [{i+1}/149] done", flush=True)

    te_arr = np.array(list(te_vals.values()))
    # 5 代表 TE
    rep_te = {}
    for r in reps:
        rep_te[r["representative_role"]] = te_single_year(load, scale_pv_available(
            frame["pv_raw_kw"].to_numpy(dtype=float), nominal_kw=float(r["pv_kwp"])), r)
    # 覆盖验证
    stats = {
        "n": len(te_arr),
        "min": round(float(te_arr.min()), 3),
        "median": round(float(np.median(te_arr)), 3),
        "max": round(float(te_arr.max()), 3),
        "mean": round(float(te_arr.mean()), 3),
        "p10": round(float(np.percentile(te_arr, 10)), 3),
        "p90": round(float(np.percentile(te_arr, 90)), 3),
    }
    rep_te_sorted = sorted(rep_te.items(), key=lambda kv: kv[1])
    coverage = {
        "rep_te": {k: round(v, 3) for k, v in rep_te.items()},
        "rep_min_covers": stats["min"] >= min(v for _, v in rep_te_sorted) - 1e-9,
        "rep_median_covers": any(abs(v - stats["median"]) < 1.0 for _, v in rep_te_sorted),
        "rep_max_covers": stats["max"] <= max(v for _, v in rep_te_sorted) + 1e-9,
        "rep_te_range": [round(min(v for _, v in rep_te_sorted), 3), round(max(v for _, v in rep_te_sorted), 3)],
        "verdict": (
            "5 代表覆盖 149 组 TE 最小值/最大值" if (stats["min"] >= min(v for _, v in rep_te_sorted) - 1e-9
                                                and stats["max"] <= max(v for _, v in rep_te_sorted) + 1e-9)
            else "代表未完全覆盖 149 组极值（需补代表）"
        ),
    }
    out = {"selection_process": proc, "te_149_stats": stats, "coverage": coverage,
           "_method": {"note": "149 组与 5 代表 TE 均用单年 2017 battery_first cap 口径、D=0.3 预置（模拟累积状态的轻量近似，仅覆盖性验证用）；"
                               "主 TE 精确值（三年链式 +0.82~+1.13%）见 stage3_feedback_modes.json；本文件验证 5 代表覆盖 149 组 TE 极值/中位数"}}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入: {OUT}")
    print("149 TE stats:", stats)
    print("代表 TE:", {k: round(v, 3) for k, v in rep_te.items()})
    print("覆盖判定:", coverage["verdict"])


if __name__ == "__main__":
    main()
