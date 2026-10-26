# -*- coding: utf-8 -*-
"""
run_rep_coverage_3yr.py — 审查1修复：149 组覆盖验证用与主 TE 完全相同的三年链式口径

主 TE（stage3_feedback_modes.json）口径：
  - 三年链式（2016-2018）battery_first，raw 损伤（cap 等价，三年内不达 EOL）
  - TE = (full 三年总损伤 - open_loop 三年总损伤) / open_loop 三年总损伤 × 100
  - 归一化分母 = 开环三年总损伤；反馈模式 = 全设备闭环

用法：python run_rep_coverage_3yr.py <start_idx> <end_idx>  （逐配置独立进程，断点续跑）
输出：07_results/rectification_v2_stage2c/stage3/representative_coverage_3yr.json
"""
import sys, json
import numpy as np
from pathlib import Path

ROOT = Path(r"d:\matlab\C")
sys.path.insert(0, str(ROOT / "06_src"))

from ems.ems_stage2b import Stage2BEMSConfig
from components.annual_input_contract import load_evaluation_years, scale_pv_available
from simulation.run_stage3_full_analysis import build_c1_config, hp_bat_default, run_three_year_cycle, extract_commands
from simulation.closed_loop_lite import compute_damage, CoalitionState

DATA = ROOT / "02_data_processed/tradestreet_pipeline/64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv"
ADM = ROOT / "07_results/rectification_v2_stage2c/search/verified_admissions.json"
OUT = ROOT / "07_results/rectification_v2_stage2c/stage3/representative_coverage_3yr.json"
COAL = CoalitionState.from_code("1111")
YEARS = (2016, 2017, 2018)


def te_3yr(loaded, rep):
    """与 stage3_feedback_modes 完全同口径的三年链式 TE（raw/cap 等价）。"""
    cfg_ems = Stage2BEMSConfig(
        pv_kwp=float(rep["pv_kwp"]), bat_kwh=float(rep["bat_kwh"]),
        pemfc_kw=float(rep["pemfc_kw"]), pemel_kw=float(rep["pemel_kw"]),
        h2_kg=float(rep["h2_kg"]),
    )
    c1 = build_c1_config(rep)
    prepared = {}
    for y in YEARS:
        frame = loaded[y]
        pv = scale_pv_available(frame["pv_raw_kw"].to_numpy(dtype=float), nominal_kw=float(rep["pv_kwp"]))
        prepared[y] = {"load": frame["load_kw"].to_numpy(dtype=float), "pv": pv}
    cycle = run_three_year_cycle(prepared, cfg_ems, strategy="battery_first")
    cmds = extract_commands(cycle)
    del cycle, prepared, cfg_ems
    import gc; gc.collect()

    out = {}
    for mode in ("open_loop", "full"):
        D = {"bat": 0.0, "fc": 0.0, "el": 0.0}
        year_sums = {y: {"bat": 0.0, "fc": 0.0, "el": 0.0} for y in YEARS}
        for y in YEARS:
            D_state = dict(D) if mode == "full" else {"bat": 0.0, "fc": 0.0, "el": 0.0}
            dmg = compute_damage(
                hp_func=lambda Dv, bkw=float(rep["bat_kwh"]): hp_bat_default(Dv, bat_kwh=bkw),
                ems_commands=cmds[y], config=c1, dt_min=15.0,
                D_state=D_state, coalition=COAL, eta_degrade=0.10, eol_policy="raw",
            )
            dD = {dev: float(np.sum(dmg[f"dD_{dev}"])) for dev in ("bat", "fc", "el")}
            year_sums[y] = dD
            del dmg
            D = {dev: D[dev] + dD[dev] for dev in ("bat", "fc", "el")}
        out[mode] = {dev: float(np.mean([year_sums[y][dev] for y in YEARS])) for dev in ("bat", "fc", "el")}
    open_tot = sum(out["open_loop"].values())
    full_tot = sum(out["full"].values())
    te = (full_tot - open_tot) / max(abs(open_tot), 1e-12) * 100.0
    return {"te": te, "open_avg": out["open_loop"], "full_avg": out["full"]}


def main():
    start, end = int(sys.argv[1]), int(sys.argv[2])
    loaded = load_evaluation_years(DATA)
    adm = json.loads(ADM.read_text(encoding="utf-8"))
    cands = adm["candidates"][start:end]

    acc = {}
    if OUT.exists():
        try:
            acc = json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            acc = {}

    for c in cands:
        cid = c["candidate_id"]
        try:
            r = te_3yr(loaded, c)
            acc[cid] = {"te": round(r["te"], 4),
                        "open_avg": {k: round(v, 5) for k, v in r["open_avg"].items()},
                        "full_avg": {k: round(v, 5) for k, v in r["full_avg"].items()}}
            print(f"[{cid}] TE={r['te']:.3f}%", flush=True)
        except Exception as e:
            acc[cid] = {"error": str(e)[:150]}
            print(f"[{cid}] ERR {str(e)[:80]}", flush=True)
        # 增量写盘
        OUT.write_text(json.dumps(acc, ensure_ascii=False, indent=2), encoding="utf-8")
        import gc; gc.collect()


if __name__ == "__main__":
    main()
