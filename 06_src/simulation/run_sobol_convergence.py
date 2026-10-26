# -*- coding: utf-8 -*-
"""
run_sobol_convergence.py — 审查8修复：外层 Sobol 收敛性（128/256/512 指数稳定性）

审查意见：Sobol 256 对 6 参数维数是否收敛，至少给出 128/256/512 样本的指数稳定性。
方法：对 compact 配置，n_outer ∈ {128, 256, 512} 各跑一次双层传播（内层固定 500×3 年），
     比较各设备认知占比 frac_epistemic 随 n_outer 的稳定性（相邻相对偏差 <5% 视为收敛）。
输出：07_results/rectification_v2_stage2c/stage3/sobol_convergence.json
"""
import sys, json
import numpy as np
from pathlib import Path

ROOT = Path(r"d:\matlab\C")
sys.path.insert(0, str(ROOT / "06_src"))
from simulation.run_double_layer_propagation import run_double_layer_propagation
from ems.ems_stage2b import Stage2BEMSConfig
from components.annual_input_contract import load_evaluation_years, scale_pv_available
from simulation.run_stage3_full_analysis import build_c1_config

OUT = ROOT / "07_results/rectification_v2_stage2c/stage3/sobol_convergence.json"
ADM = ROOT / "07_results/rectification_v2_stage2c/search/verified_admissions.json"
DATA = ROOT / "02_data_processed/tradestreet_pipeline/64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv"
DEVICES = ["bat", "fc", "el"]
N_OUTERS = [128, 256, 512]


def main():
    loaded = load_evaluation_years(DATA)
    adm = json.loads(ADM.read_text(encoding="utf-8"))
    rep = adm["representatives"][0]  # compact
    cfg_ems = Stage2BEMSConfig(
        pv_kwp=float(rep["pv_kwp"]), bat_kwh=float(rep["bat_kwh"]),
        pemfc_kw=float(rep["pemfc_kw"]), pemel_kw=float(rep["pemel_kw"]),
        h2_kg=float(rep["h2_kg"]),
    )
    c1 = build_c1_config(rep)
    frame = loaded[2017]
    load = frame["load_kw"].to_numpy(dtype=float)
    pv = scale_pv_available(frame["pv_raw_kw"].to_numpy(dtype=float), nominal_kw=float(rep["pv_kwp"]))

    results = {}
    for n in N_OUTERS:
        r = run_double_layer_propagation(load, pv, cfg_ems, c1, n_outer=n, n_inner=500,
                                         outer_seed=20260803, inner_seed=42)
        frac = {}
        for dev in DEVICES:
            d = r["variance_decomposition"][dev]
            frac[dev] = d["frac_epistemic"]
        results[n] = {"frac_epistemic": {dev: round(float(v), 5) for dev, v in frac.items()}}
        print(f"n_outer={n}: { {dev: round(float(v), 4) for dev, v in frac.items()} }", flush=True)

    # 相邻稳定性
    stability = {}
    for i in range(1, len(N_OUTERS)):
        prev, cur = N_OUTERS[i - 1], N_OUTERS[i]
        dev_delta = {}
        for dev in DEVICES:
            a = results[prev]["frac_epistemic"][dev]
            b = results[cur]["frac_epistemic"][dev]
            dev_delta[dev] = round(abs(b - a) / max(abs(a), 1e-12) * 100.0, 3)
        stability[f"{prev}->{cur}"] = dev_delta
    out = {
        "n_outer_tested": N_OUTERS,
        "config": "compact",
        "method": "run_double_layer_propagation(n_outer, n_inner=500, 2017 单年)；认知占比 frac_epistemic",
        "results": results,
        "adjacent_rel_delta_pct": stability,
        "verdict": "Sobol 外层收敛（相邻 n_outer 认知占比相对偏差<5%）" if all(max(v.values()) < 5.0 for v in stability.values()) else "Sobol 外层未收敛（需增大 n_outer）",
        "note": "内层固定 500（收敛性检验，非全量 4000）；收敛判据：128→256→512 相邻相对偏差<5%",
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入: {OUT}")
    print("相邻相对偏差:", stability)
    print("判定:", out["verdict"])


if __name__ == "__main__":
    main()
