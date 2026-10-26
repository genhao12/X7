# -*- coding: utf-8 -*-
"""Double-layer uncertainty propagation (full nested design).

Outer layer: Sobol quasi-random sampling of degradation parameters.
Inner layer: Seasonal stratified 4-week block Bootstrap of the dispatch.

Design (scalable to outer=256, inner=4000):
  - Dispatch is deterministic in load/PV, so the inner Bootstrap resamples
    the dispatch command blocks (seasonal stratified 4-week moving block).
    This is equivalent to resampling the load/PV blocks that drive the EMS.
  - For every inner sample the BAT SoC trace is rebuilt and rainflow-counted
    once; the cycle histogram is stored (k-independent).
  - FC/EL base quantities (running-hour-weighted and start counts) are also
    precomputed once per inner sample.
  - The outer parameter samples then only evaluate closed-form weighted sums
    over the cached inner quantities (vectorised over the inner dimension).

BAT cycle damage uses the EFC / energy-throughput model
  dD_cyc = a_d * count_i * (range_i / DOD_ref)**k   (rainflow EFC, Woehler k).
"""
import sys
import json
import numpy as np
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "06_src") not in sys.path:
    sys.path.insert(0, str(ROOT / "06_src"))

from scipy.stats import qmc
from ems.ems_stage2b import Stage2BEMSConfig, EMSState, dispatch_stage2b
from components.annual_input_contract import load_evaluation_years, scale_pv_available
from simulation.closed_loop_lite import BAT_K_CAL_H
from components import efc_cycle_model as ecm
from simulation.run_stage3_full_analysis import build_c1_config


# ── Parameter definitions ──
# BAT upgraded to EFC / energy-throughput model (rainflow EFC + Woehler depth).

PARAM_DEFS = {
    "BAT_EFC_A_D": {"nominal": ecm.EFC_A_D, "low": 2.5e-4, "high": 1.0e-3},
    "BAT_EFC_K":   {"nominal": 1.0, "low": 0.7, "high": 1.5},
    "FC_K_RUN_H":  {"nominal": 2.4e-4, "low": 1.2e-4, "high": 3.6e-4},
    "FC_K_START":  {"nominal": 2.0e-4, "low": 1.0e-4, "high": 4.0e-4},
    "EL_K_RUN_H":  {"nominal": 2.0e-4, "low": 1.0e-4, "high": 3.0e-4},
    "EL_K_START":  {"nominal": 1.6e-4, "low": 0.8e-4, "high": 3.2e-4},
}


def sobol_sample_params(n_samples: int, seed: int = 20260803) -> Dict[str, np.ndarray]:
    """Generate Sobol quasi-random samples for degradation parameters."""
    param_names = list(PARAM_DEFS.keys())
    sampler = qmc.Sobol(d=len(param_names), scramble=True, seed=seed)
    m = int(np.ceil(np.log2(n_samples)))
    unit_samples = sampler.random_base2(m=m)[:n_samples]

    samples = {}
    for i, name in enumerate(param_names):
        low = PARAM_DEFS[name]["low"]
        high = PARAM_DEFS[name]["high"]
        samples[name] = low + unit_samples[:, i] * (high - low)
    return samples


# ── Inner layer: seasonal stratified 4-week block bootstrap on dispatch ──

STEPS_PER_WEEK = 96 * 7
STEPS_PER_BLOCK = STEPS_PER_WEEK * 4  # 4-week block


def seasonal_block_indices(
    n_steps: int,
    n_bootstrap: int,
    block_weeks: int = 4,
    seed: int = 42,
) -> List[np.ndarray]:
    """Seasonal stratified block-Bootstrap index arrays over [0, n_steps)."""
    rng = np.random.default_rng(seed)
    steps_per_block = STEPS_PER_WEEK * block_weeks
    season_length = n_steps // 4
    seasons = [
        (s * season_length, s * season_length + season_length if s < 3 else n_steps)
        for s in range(4)
    ]

    boots = []
    for _ in range(n_bootstrap):
        idx = np.zeros(n_steps, dtype=np.int64)
        pos = 0
        for start, end in seasons:
            seg_len = end - start
            n_blocks_needed = int(np.ceil(seg_len / steps_per_block))
            seg_parts = []
            for _ in range(n_blocks_needed):
                max_start = max(start, end - steps_per_block)
                bs = int(rng.integers(start, max_start + 1))
                be = min(bs + steps_per_block, end)
                seg_parts.append(np.arange(bs, be))
            seg = np.concatenate(seg_parts)[:seg_len]
            idx[pos:pos + len(seg)] = seg
            pos += len(seg)
        boots.append(idx)
    return boots


def rebuild_soc(
    commands: np.ndarray,
    cap_kwh: float,
    bc: dict,
    dt_h: float = 0.25,
) -> np.ndarray:
    """Reconstruct the clipped SoC trace from battery power commands."""
    soc_min = bc.get("soc_min", 0.15)
    soc_max = bc.get("soc_max", 0.95)
    soc_init = bc.get("soc_initial", 0.5)
    n = len(commands)
    soc = np.empty(n + 1)
    soc[0] = soc_init
    p_bat = commands[:, 0]
    step = dt_h / max(cap_kwh, 1e-6)
    for t in range(n):
        s = soc[t] - p_bat[t] * step
        if s > soc_max:
            soc[t + 1] = soc_max
        elif s < soc_min:
            soc[t + 1] = soc_min
        else:
            soc[t + 1] = s
    return soc


def precompute_inner(
    commands: np.ndarray,
    n_inner: int,
    inner_seed: int,
    c1_config: dict,
) -> dict:
    """Precompute per-inner-sample BAT cycle histograms and FC/EL base quantities."""
    n_steps = len(commands)
    dt_h = 0.25
    p_fc = commands[:, 1]
    p_el = commands[:, 2]
    cap_kwh = c1_config["capacity"]["BAT_kWh"]
    fc_rated = c1_config["capacity"]["PEMFC_kW"]
    el_rated = c1_config["capacity"]["PEMEL_kW"]
    bc = c1_config.get("battery_constraints", {})
    ss_thr = c1_config.get("ems_parameters", {}).get("start_stop_threshold_fraction", 0.15)

    bat_cal = BAT_K_CAL_H * n_steps * dt_h
    fc_active_all = p_fc >= (fc_rated * ss_thr)
    el_active_all = p_el >= (el_rated * ss_thr)

    boots = seasonal_block_indices(n_steps, n_inner, seed=inner_seed)

    bat_hists = []
    fc_run_base = np.zeros(n_inner)
    fc_starts = np.zeros(n_inner)
    el_run_base = np.zeros(n_inner)
    el_starts = np.zeros(n_inner)

    for j, idx in enumerate(boots):
        cmds_b = commands[idx]
        soc_b = rebuild_soc(cmds_b, cap_kwh, bc, dt_h)
        bat_hists.append(ecm.rainflow_cycles(soc_b))
        fc_act = fc_active_all[idx]
        el_act = el_active_all[idx]
        fc_starts[j] = float(np.sum(fc_act[1:] & ~fc_act[:-1]))
        el_starts[j] = float(np.sum(el_act[1:] & ~el_act[:-1]))
        fc_run_base[j] = float(np.sum(np.abs(cmds_b[:, 1]) / max(fc_rated, 1e-6)) * dt_h)
        el_run_base[j] = float(np.sum(np.abs(cmds_b[:, 2]) / max(el_rated, 1e-6)) * dt_h)

    return {
        "bat_cal": float(bat_cal),
        "bat_hists": bat_hists,
        "fc_run_base": fc_run_base,
        "fc_starts": fc_starts,
        "el_run_base": el_run_base,
        "el_starts": el_starts,
    }


def efc_k_weighted(bat_hists, k: float, dod_ref: float = 1.0) -> np.ndarray:
    """EFC (k-weighted) per inner sample from the cached rainflow histograms."""
    out = np.zeros(len(bat_hists))
    for i, hist in enumerate(bat_hists):
        s = 0.0
        for rng, _mean, count, _i0, _i1 in hist:
            depth = min(rng / dod_ref, 1.0) if dod_ref > 0 else 0.0
            s += count * (depth ** k)
        out[i] = s
    return out


def run_double_layer_propagation(
    load: np.ndarray,
    pv: np.ndarray,
    cfg_ems: Stage2BEMSConfig,
    c1_config: dict,
    n_outer: int = 256,
    n_inner: int = 4000,
    outer_seed: int = 20260803,
    inner_seed: int = 42,
) -> dict:
    """Run double-layer uncertainty propagation (nested design)."""
    outer_samples = sobol_sample_params(n_outer, seed=outer_seed)

    # single baseline dispatch; inner layer resamples its command blocks
    initial = EMSState(soc=0.50, h2_kg=cfg_ems.h2_kg * 0.50)
    dispatch_result = dispatch_stage2b(
        load, pv, cfg_ems, strategy="battery_first", initial_state=initial,
    )
    commands = dispatch_result.commands

    inner = precompute_inner(commands, n_inner, inner_seed, c1_config)
    bat_hists = inner["bat_hists"]
    n_in = len(bat_hists)

    matrix = {dev: np.zeros((n_outer, n_in)) for dev in ("bat", "fc", "el")}

    for i_outer in range(n_outer):
        params = {name: outer_samples[name][i_outer] for name in PARAM_DEFS}
        efc_w = efc_k_weighted(bat_hists, params["BAT_EFC_K"])
        matrix["bat"][i_outer, :] = inner["bat_cal"] + params["BAT_EFC_A_D"] * efc_w
        matrix["fc"][i_outer, :] = (
            params["FC_K_RUN_H"] * inner["fc_run_base"] + params["FC_K_START"] * inner["fc_starts"]
        )
        matrix["el"][i_outer, :] = (
            params["EL_K_RUN_H"] * inner["el_run_base"] + params["EL_K_START"] * inner["el_starts"]
        )

    def stats(arr):
        a = np.asarray(arr).ravel()
        return {
            "mean": float(np.mean(a)),
            "std": float(np.std(a)),
            "p05": float(np.percentile(a, 5)),
            "p25": float(np.percentile(a, 25)),
            "p50": float(np.percentile(a, 50)),
            "p75": float(np.percentile(a, 75)),
            "p95": float(np.percentile(a, 95)),
            "n_samples": int(a.size),
        }

    def decompose(m):
        outer_means = m.mean(axis=1)
        v_epistemic = float(np.var(outer_means))
        v_aleatory = float(np.mean(m.var(axis=1)))
        v_total = float(np.var(m))
        residual = abs(v_total - (v_epistemic + v_aleatory)) / max(v_total, 1e-30)
        centered = (m - outer_means[:, None]).ravel()
        grand_mean = float(m.mean())
        return {
            "var_total": v_total,
            "var_epistemic_outer": v_epistemic,
            "var_aleatory_inner": v_aleatory,
            "frac_epistemic": v_epistemic / max(v_total, 1e-30),
            "frac_aleatory": v_aleatory / max(v_total, 1e-30),
            "closure_residual_rel": residual,
            "closure_pass": bool(residual < 1e-9),
            "epistemic_ci90": [float(np.percentile(outer_means, 5)),
                               float(np.percentile(outer_means, 95))],
            "aleatory_ci90": [float(grand_mean + np.percentile(centered, 5)),
                              float(grand_mean + np.percentile(centered, 95))],
        }

    def learning_curve(m):
        """Inner-dimension variance estimates at pre-registered checkpoints [1000,2000,4000]
        plus adjacent-relative-deviation used for the convergence judgement."""
        ckpts = [1000, 2000, 4000]
        n_in = m.shape[1]
        points = []
        for k in ckpts:
            kk = min(k, n_in)
            sub = m[:, :kk]
            om = sub.mean(axis=1)
            vt = float(np.var(sub))
            ve = float(np.var(om))
            points.append({
                "checkpoint": kk,
                "var_total": vt,
                "var_epistemic_outer": ve,
                "frac_epistemic": ve / max(vt, 1e-30),
            })
        for i in range(1, len(points)):
            prev = points[i - 1]["var_total"]
            cur = points[i]["var_total"]
            points[i]["rel_dev_vs_prev"] = (
                abs(cur - prev) / max(abs(prev), 1e-30)
            )
        return points

    return {
        "n_outer": n_outer,
        "n_inner": n_in,
        "total_samples": n_outer * n_in,
        "outer_seed": outer_seed,
        "inner_seed": inner_seed,
        "inner_method": "season_stratified_4week_moving_block_bootstrap_on_dispatch",
        "bat_annual_damage": stats(matrix["bat"]),
        "fc_annual_damage": stats(matrix["fc"]),
        "el_annual_damage": stats(matrix["el"]),
        "variance_decomposition": {
            "bat": decompose(matrix["bat"]),
            "fc": decompose(matrix["fc"]),
            "el": decompose(matrix["el"]),
        },
        "learning_curve": {
            "bat": learning_curve(matrix["bat"]),
            "fc": learning_curve(matrix["fc"]),
            "el": learning_curve(matrix["el"]),
        },
        "param_ranges": {name: dict(PARAM_DEFS[name]) for name in PARAM_DEFS},
    }


def print_double_layer_report(role: str, result: dict):
    """Print formatted double-layer propagation report."""
    print(f"\n{'='*70}")
    print(f"Double-layer propagation: {role}")
    print(f"  Outer: {result['n_outer']} Sobol x Inner: {result['n_inner']} Bootstrap = {result['total_samples']} samples")
    print(f"{'='*70}")

    for dev, key in [("BAT", "bat_annual_damage"), ("FC", "fc_annual_damage"), ("EL", "el_annual_damage")]:
        s = result[key]
        lifetime = 1.0 / s['mean'] if s['mean'] > 0 else float('inf')
        lifetime_p05 = 1.0 / s['p95'] if s['p95'] > 0 else float('inf')
        lifetime_p95 = 1.0 / s['p05'] if s['p05'] > 0 else float('inf')
        print(f"\n  {dev} annual damage (ΔD/yr):")
        print(f"    Mean:  {s['mean']:.6f}  (lifetime {lifetime:.1f}y)")
        print(f"    90%CI: [{s['p05']:.6f}, {s['p95']:.6f}]  (lifetime [{lifetime_p05:.1f}, {lifetime_p95:.1f}]y)")

        vd = result.get("variance_decomposition", {}).get(dev.lower())
        if vd:
            flag = "PASS" if vd["closure_pass"] else "FAIL"
            print(f"    Variance split: epistemic(params) {vd['frac_epistemic'] * 100:.1f}% | "
                  f"aleatory(load/PV) {vd['frac_aleatory'] * 100:.1f}%  "
                  f"[closure {flag}, rel={vd['closure_residual_rel']:.2e}]")
            print(f"      epistemic-only 90%CI: [{vd['epistemic_ci90'][0]:.6f}, {vd['epistemic_ci90'][1]:.6f}]")
            print(f"      aleatory-only  90%CI: [{vd['aleatory_ci90'][0]:.6f}, {vd['aleatory_ci90'][1]:.6f}]")


if __name__ == "__main__":
    config_path = ROOT / "05_config" / "rectification_v2_stage2c.json"
    data_path = ROOT / "02_data_processed" / "tradestreet_pipeline" / \
        "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv"
    admissions_path = ROOT / "07_results" / "rectification_v2_stage2c" / "search" / "verified_admissions.json"

    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    admissions = json.loads(admissions_path.read_text(encoding="utf-8"))
    loaded = load_evaluation_years(data_path)

    dl_cfg = cfg.get("double_layer_uncertainty") or {}
    n_outer = int(dl_cfg.get("min_outer", 256))
    n_inner = int(dl_cfg.get("min_inner", 4000))

    all_results = {}
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
        pv_raw = frame["pv_raw_kw"].to_numpy(dtype=float)
        pv_scaled = scale_pv_available(pv_raw, nominal_kw=pv_kwp)
        load = frame["load_kw"].to_numpy(dtype=float)

        c1_config = build_c1_config(rep)

        result = run_double_layer_propagation(
            load, pv_scaled, cfg_ems, c1_config,
            n_outer=n_outer, n_inner=n_inner,
        )
        print_double_layer_report(role, result)
        all_results[role] = result

    output_path = ROOT / "07_results" / "rectification_v2_stage2c" / "stage3" / \
        "double_layer_propagation.json"
    with open(output_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\nResults written to: {output_path}")
