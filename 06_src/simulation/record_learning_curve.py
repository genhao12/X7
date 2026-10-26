# -*- coding: utf-8 -*-
"""Record learning-curve checkpoints for the nested double-layer propagation.

Pre-registered spec (rectification_v2_stage2c.json double_layer_uncertainty):
  - outer: Sobol >= 256
  - inner: season-stratified 4-week moving block Bootstrap,
           minimum_inner_samples = 4000, inner_checkpoints = [1000, 2000, 4000]
  - stopping rule: two *consecutive* checkpoints stable before declaring
    Monte-Carlo convergence.

This script evaluates the nested design at inner checkpoints {1000, 2000, 4000}
using *nested* samples (first c of the single 4000-block bootstrap draw,
seed=42) so the checkpoints form a proper learning curve, records
var_total / var_epistemic per device per checkpoint, computes the relative
change between consecutive checkpoints, and appends a `learning_curve`
section to double_layer_propagation.json. The full 256x4000 result in the
file remains the authoritative decomposition.

Stability criterion: relative change of both var_total and var_epistemic
between consecutive checkpoints < STABILITY_REL_TOL (default 5%).
"""
import sys
import json
import numpy as np
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "06_src") not in sys.path:
    sys.path.insert(0, str(ROOT / "06_src"))

from ems.ems_stage2b import Stage2BEMSConfig, EMSState, dispatch_stage2b
from components.annual_input_contract import load_evaluation_years, scale_pv_available
from simulation.run_stage3_full_analysis import build_c1_config
from simulation.run_double_layer_propagation import (
    PARAM_DEFS,
    sobol_sample_params,
    precompute_inner,
    efc_k_weighted,
)

CHECKPOINTS = [1000, 2000, 4000]
N_OUTER = 256
OUTER_SEED = 20260803
INNER_SEED = 42
STABILITY_REL_TOL = 0.05  # 5%


def decompose(matrix: np.ndarray) -> dict:
    outer_means = matrix.mean(axis=1)
    v_epi = float(np.var(outer_means))
    v_ale = float(np.mean(matrix.var(axis=1)))
    v_tot = float(np.var(matrix))
    residual = abs(v_tot - (v_epi + v_ale)) / max(v_tot, 1e-30)
    return {
        "var_total": v_tot,
        "var_epistemic_outer": v_epi,
        "var_aleatory_inner": v_ale,
        "frac_epistemic": v_epi / max(v_tot, 1e-30),
        "frac_aleatory": v_ale / max(v_tot, 1e-30),
        "closure_residual_rel": residual,
        "closure_pass": bool(residual < 1e-9),
    }


def record(
    config_path: Path,
    data_path: Path,
    admissions_path: Path,
    output_path: Path,
) -> None:
    admissions = json.loads(admissions_path.read_text(encoding="utf-8"))
    loaded = load_evaluation_years(data_path)
    outer_samples = sobol_sample_params(N_OUTER, seed=OUTER_SEED)

    lc = {}
    for rep in admissions["representatives"]:
        role = rep["representative_role"]
        pv_kwp = float(rep["pv_kwp"])
        bat_kwh = float(rep["bat_kwh"])
        pemfc_kw = float(rep["pemfc_kw"])
        pemel_kw = float(rep["pemel_kw"])
        h2_kg = float(rep["h2_kg"])

        cfg_ems = Stage2BEMSConfig(
            pv_kwp=pv_kwp, bat_kwh=bat_kwh, pemfc_kw=pemfc_kw,
            pemel_kw=pemel_kw, h2_kg=h2_kg,
        )
        frame = loaded[2017]
        pv_scaled = scale_pv_available(frame["pv_raw_kw"].to_numpy(dtype=float), nominal_kw=pv_kwp)
        load = frame["load_kw"].to_numpy(dtype=float)
        c1_config = build_c1_config(rep)

        initial = EMSState(soc=0.50, h2_kg=cfg_ems.h2_kg * 0.50)
        commands = dispatch_stage2b(
            load, pv_scaled, cfg_ems, strategy="battery_first", initial_state=initial,
        ).commands

        # Nested inner samples: generate the full 4000-block draw once,
        # then use the first c samples as the checkpoint-c inner set.
        inner = precompute_inner(commands, 4000, INNER_SEED, c1_config)
        bat_hists = inner["bat_hists"]
        fc_run = inner["fc_run_base"]
        fc_st = inner["fc_starts"]
        el_run = inner["el_run_base"]
        el_st = inner["el_starts"]
        bat_cal = inner["bat_cal"]

        rows = []
        for c in CHECKPOINTS:
            h = bat_hists[:c]
            fcr, fcs = fc_run[:c], fc_st[:c]
            elr, els = el_run[:c], el_st[:c]
            m = {dev: np.zeros((N_OUTER, c)) for dev in ("bat", "fc", "el")}
            for i_outer in range(N_OUTER):
                params = {name: outer_samples[name][i_outer] for name in PARAM_DEFS}
                m["bat"][i_outer, :] = bat_cal + params["BAT_EFC_A_D"] * efc_k_weighted(
                    h, params["BAT_EFC_K"]
                )
                m["fc"][i_outer, :] = params["FC_K_RUN_H"] * fcr + params["FC_K_START"] * fcs
                m["el"][i_outer, :] = params["EL_K_RUN_H"] * elr + params["EL_K_START"] * els
            vd = {dev: decompose(m[dev]) for dev in ("bat", "fc", "el")}
            rows.append({
                "n_outer": N_OUTER,
                "n_inner": int(c),
                "total_samples": int(N_OUTER * c),
                "variance_decomposition": {
                    dev: {k: vd[dev][k] for k in (
                        "var_total", "var_epistemic_outer", "var_aleatory_inner",
                        "frac_epistemic", "frac_aleatory",
                        "closure_residual_rel", "closure_pass",
                    )}
                    for dev in vd
                },
            })

        stability = {}
        for i in range(len(CHECKPOINTS) - 1):
            a, b = rows[i], rows[i + 1]
            pair = {}
            for dev in ("bat", "fc", "el"):
                va, vb = a["variance_decomposition"][dev], b["variance_decomposition"][dev]
                rel_var = abs(vb["var_total"] - va["var_total"]) / max(va["var_total"], 1e-30)
                rel_epi = abs(vb["var_epistemic_outer"] - va["var_epistemic_outer"]) / max(
                    va["var_epistemic_outer"], 1e-30
                )
                pair[dev] = {
                    "rel_change_var_total": float(rel_var),
                    "rel_change_var_epistemic": float(rel_epi),
                    "stable": bool(rel_var < STABILITY_REL_TOL and rel_epi < STABILITY_REL_TOL),
                }
            stability[f"{CHECKPOINTS[i]}->{CHECKPOINTS[i+1]}"] = pair

        two_consecutive = all(
            stability[f"{CHECKPOINTS[i]}->{CHECKPOINTS[i+1]}"][dev]["stable"]
            for i in range(len(CHECKPOINTS) - 1)
            for dev in ("bat", "fc", "el")
        )
        lc[role] = {
            "checkpoints": rows,
            "stability": stability,
            "stability_rel_tol": STABILITY_REL_TOL,
            "two_consecutive_checkpoints_stable": bool(two_consecutive),
        }
        print(f"{role}: two_consecutive_checkpoints_stable={two_consecutive}", flush=True)

    existing = json.loads(output_path.read_text(encoding="utf-8"))
    existing["learning_curve"] = {
        "method": (
            "nested inner checkpoints (first c of the 4000-block seasonal bootstrap draw, "
            f"seed={INNER_SEED}); outer Sobol {N_OUTER}"
        ),
        "checkpoints": CHECKPOINTS,
        "stability_rel_tol_pct": STABILITY_REL_TOL * 100,
        "convergence_declared": bool(
            all(lc[r]["two_consecutive_checkpoints_stable"] for r in lc)
        ),
        "per_config": lc,
        "note": (
            "预注册要求连续两个检查点稳定方判定蒙特卡洛收敛。逐点证据见 per_config；"
            "全方差闭合残差<=1e-9 仅证明方差分解恒等式，不单独构成收敛判定。"
            "checkpoint 4000 与文件主体 256x4000 结果同源（同一 4000 块 Bootstrap 抽取）。"
        ),
    }
    output_path.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    print("learning_curve written to", output_path)


if __name__ == "__main__":
    record(
        ROOT / "05_config" / "rectification_v2_stage2c.json",
        ROOT / "02_data_processed" / "tradestreet_pipeline" /
        "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv",
        ROOT / "07_results" / "rectification_v2_stage2c" / "search" / "verified_admissions.json",
        ROOT / "07_results" / "rectification_v2_stage2c" / "stage3" / "double_layer_propagation.json",
    )
