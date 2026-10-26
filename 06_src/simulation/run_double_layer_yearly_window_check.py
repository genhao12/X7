# -*- coding: utf-8 -*-
"""
run_double_layer_yearly_window_check.py — 内层不确定性窗口敏感性核验（审查任务 D 轻量版）

目的：检验"双层不确定性内层仅覆盖 2017 单年气象窗口"这一限定的稳健性——
对 2016/2017/2018 三年分别做内层块 Bootstrap（n_inner=500，轻量），对比 BAT 年损伤
分布的均值/90% 区间与方差分解，给出窗口敏感性结论。

判定（对齐学习曲线判据 <5%）：
  - 三年 BAT 年损伤均值两两相对偏差 <5% → 内层窗口敏感性低（2017 单年代表性可接受）
  - ≥5% → 单年窗口代表性受限，论文维持"仅 2017 单年、不外推"限定（维持现状即可）

输出：07_results/rectification_v2_stage2c/stage3/double_layer_yearly_window_check.json
"""
import sys, json
import numpy as np
from pathlib import Path

ROOT = Path(r"d:\matlab\C")
sys.path.insert(0, str(ROOT / "06_src"))

from ems.ems_stage2b import Stage2BEMSConfig, EMSState, dispatch_stage2b
from components.annual_input_contract import load_evaluation_years, scale_pv_available
from simulation.run_double_layer_propagation import (
    precompute_inner, efc_k_weighted, PARAM_DEFS,
)
from simulation.run_stage3_full_analysis import build_c1_config

DATA = ROOT / "02_data_processed/tradestreet_pipeline/64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv"
ADM = ROOT / "07_results/rectification_v2_stage2c/search/verified_admissions.json"
OUT = ROOT / "07_results/rectification_v2_stage2c/stage3/double_layer_yearly_window_check.json"

N_INNER = 500      # 轻量：每年内层 Bootstrap 样本数
INNER_SEED = 42    # 与主传播一致

# BAT-EFC 参数中值（外层 Sobol 区间中值，用于固定参数核验）
a_d_mid = float(np.mean([PARAM_DEFS["BAT_EFC_A_D"]["low"], PARAM_DEFS["BAT_EFC_A_D"]["high"]]))
k_mid = float(np.mean([PARAM_DEFS["BAT_EFC_K"]["low"], PARAM_DEFS["BAT_EFC_K"]["high"]]))


def main():
    loaded = load_evaluation_years(DATA)
    admissions = json.loads(ADM.read_text(encoding="utf-8"))
    reps = admissions["representatives"]

    all_out = {}
    for rep in reps:
        role = rep["representative_role"]
        cfg_ems = Stage2BEMSConfig(
            pv_kwp=float(rep["pv_kwp"]), bat_kwh=float(rep["bat_kwh"]),
            pemfc_kw=float(rep["pemfc_kw"]), pemel_kw=float(rep["pemel_kw"]),
            h2_kg=float(rep["h2_kg"]),
        )
        c1 = build_c1_config(rep)
        per_year = {}
        means = {}
        for y in (2016, 2017, 2018):
            frame = loaded[y]
            pv = scale_pv_available(frame["pv_raw_kw"].to_numpy(dtype=float), nominal_kw=float(rep["pv_kwp"]))
            load = frame["load_kw"].to_numpy(dtype=float)
            cmds = dispatch_stage2b(
                load, pv, cfg_ems, strategy="battery_first",
                initial_state=EMSState(soc=0.5, h2_kg=cfg_ems.h2_kg * 0.5),
            ).commands
            inner = precompute_inner(cmds, N_INNER, INNER_SEED, c1)
            # BAT 年损伤样本（固定中值参数）
            bat_damage = inner["bat_cal"] + a_d_mid * efc_k_weighted(inner["bat_hists"], k_mid)
            lo, hi = np.percentile(bat_damage, [5, 95])
            per_year[y] = {
                "n_inner": N_INNER,
                "mean": round(float(bat_damage.mean()), 6),
                "std": round(float(bat_damage.std()), 6),
                "p5": round(float(lo), 6),
                "p95": round(float(hi), 6),
            }
            means[y] = float(bat_damage.mean())
        # 两两相对偏差（以 2017 为基准）
        dev = {y: round((means[y] - means[2017]) / abs(means[2017]) * 100.0, 3) for y in (2016, 2018)}
        max_dev = max(abs(v) for v in dev.values())
        per_year["dev_vs_2017_pct"] = dev
        per_year["max_dev_pct"] = round(max_dev, 3)
        per_year["conclusion"] = "窗口敏感性低（<5%，2017 单年代表性可接受）" if max_dev < 5 else \
            "窗口敏感性中高（≥5%），维持'仅 2017 单年、不外推'限定"
        all_out[role] = per_year

    # 汇总
    all_max = max(r["max_dev_pct"] for r in all_out.values())
    all_out["_summary"] = {
        "n_inner": N_INNER,
        "seed": INNER_SEED,
        "params_fixed": {"BAT_EFC_A_D": a_d_mid, "BAT_EFC_K": k_mid},
        "max_dev_across_configs_pct": round(all_max, 3),
        "global_conclusion": "三年窗口敏感性低（全部 <5%），2017 单年内层窗口代表性可接受" if all_max < 5 else \
            "存在 ≥5% 窗口敏感性，维持'仅 2017 单年、不作三年气候稳健性外推'限定",
        "note": "轻量核验（n_inner=500）：仅 BAT 损伤、固定中值参数，用于窗口敏感性方向判断；完整双层 256×4000 仍以 2017 为内层窗口（double_layer_propagation.json）",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(all_out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"结果已写入: {OUT}")
    print("global:", json.dumps(all_out["_summary"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
