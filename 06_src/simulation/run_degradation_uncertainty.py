"""Degradation parameter uncertainty propagation via Latin Hypercube Sampling.

Samples over degradation parameters within their literature uncertainty
ranges and propagates through the EOL event model to get lifetime and
NPC/LCOE confidence intervals.

Parameters and ranges (BAT upgraded to EFC / energy-throughput model):
  BAT_EFC_A_D:  [2.5e-4, 1.0e-3] D/EFC  (nominal 5.0714e-4, calibrated to 280Ah)
  BAT_EFC_K:    [0.7, 1.5]  (Woehler depth exponent, nominal 1.0)
  FC_K_RUN_H:   [1.2e-4, 3.6e-4] /h  (nominal 2.4e-4, ±50%)
  FC_K_START:   [1e-4, 4e-4] /event  (nominal 2e-4, ±100%)
  EL_K_RUN_H:   [1e-4, 3e-4] /h  (nominal 2e-4, ±50%)
  EL_K_START:   [0.8e-4, 3.2e-4] /event  (nominal 1.6e-4, ±100%)
"""
import sys
import json
import numpy as np
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "06_src") not in sys.path:
    sys.path.insert(0, str(ROOT / "06_src"))

from simulation.closed_loop_lite import (
    compute_damage,
    BAT_K_CAL_H,
)
from components import efc_cycle_model
from simulation.eol_events import advance_replacement_events_with_downtime
from economics.npc_lite import EconomicConfig, compute_equipment_capex


# ── Parameter definitions ──

PARAM_DEFS = {
    "BAT_EFC_A_D":  {"nominal": efc_cycle_model.EFC_A_D, "low": 2.5e-4, "high": 1.0e-3,
                      "unit": "D/EFC", "source": "280Ah LFP aging calibration, 700 cycles -> D=0.355"},
    "BAT_EFC_K":    {"nominal": 1.0, "low": 0.7, "high": 1.5,
                      "unit": "-", "source": "Woehler depth exponent, linear baseline"},
    "FC_K_RUN_H":   {"nominal": 2.4e-4, "low": 1.2e-4, "high": 3.6e-4,
                      "unit": "/h", "source": "IEEE PHM 2014, ±50%"},
    "FC_K_START":   {"nominal": 2.0e-4, "low": 1.0e-4, "high": 4.0e-4,
                      "unit": "/event", "source": "IEEE PHM 2014, ±100%"},
    "EL_K_RUN_H":   {"nominal": 2.0e-4, "low": 1.0e-4, "high": 3.0e-4,
                      "unit": "/h", "source": "NAICNO, ±50%"},
    "EL_K_START":   {"nominal": 1.6e-4, "low": 0.8e-4, "high": 3.2e-4,
                      "unit": "/event", "source": "literature, ±100%"},
}


def lhs_sample(n_samples: int, seed: int = 20260803) -> Dict[str, np.ndarray]:
    """Generate Latin Hypercube samples for all degradation parameters."""
    from scipy.stats import qmc

    param_names = list(PARAM_DEFS.keys())
    n_params = len(param_names)

    sampler = qmc.LatinHypercube(d=n_params, seed=seed)
    unit_samples = sampler.random(n=n_samples)  # (n_samples, n_params) in [0,1]

    samples = {}
    for i, name in enumerate(param_names):
        low = PARAM_DEFS[name]["low"]
        high = PARAM_DEFS[name]["high"]
        samples[name] = low + unit_samples[:, i] * (high - low)

    return samples


def compute_damage_with_params(
    commands: np.ndarray,
    c1_config: dict,
    params: dict,
    D_state: dict = None,
) -> dict:
    """Compute damage with custom degradation parameters."""
    if D_state is None:
        D_state = {"bat": 0.0, "fc": 0.0, "el": 0.0}

    bat_kwh = c1_config["capacity"]["BAT_kWh"]
    fc_rated = c1_config["capacity"]["PEMFC_kW"]
    el_rated = c1_config["capacity"]["PEMEL_kW"]

    dt_h = 0.25
    p_bat = commands[:, 0]
    p_fc = commands[:, 1]
    p_el = commands[:, 2]

    # BAT damage: calendar + EFC cyclic (rainflow / energy-throughput model)
    d_bat = D_state.get("bat", 0.0)
    Q = max(0.80, 1.0 - 0.20 * d_bat)
    eff_cap = bat_kwh * Q
    bc = c1_config.get("battery_constraints", {})
    soc_min = bc.get("soc_min", 0.15)
    soc_max = bc.get("soc_max", 0.95)
    soc_init = bc.get("soc_initial", 0.5)
    soc = np.zeros(len(p_bat) + 1)
    soc[0] = soc_init
    for t in range(len(p_bat)):
        soc[t + 1] = np.clip(soc[t] - p_bat[t] * dt_h / max(eff_cap, 1e-6), soc_min, soc_max)
    dD_cyc_bat = efc_cycle_model.cycle_damage_increments(
        soc,
        a_d=params["BAT_EFC_A_D"],
        k=params["BAT_EFC_K"],
    )[: len(p_bat)]
    dD_bat = BAT_K_CAL_H * dt_h + dD_cyc_bat

    # FC damage: running + start-stop
    d_fc = D_state.get("fc", 0.0)
    eta_factor_fc = 1.0 + 0.10 * d_fc
    p_ratio_fc = np.abs(p_fc) / max(fc_rated, 1e-6)
    fc_active = p_fc >= (fc_rated * 0.15)
    fc_starts = np.zeros(len(p_fc))
    for t in range(1, len(p_fc)):
        if fc_active[t] and not fc_active[t - 1]:
            fc_starts[t] = 1.0
    dD_fc = params["FC_K_RUN_H"] * eta_factor_fc * p_ratio_fc * dt_h + params["FC_K_START"] * fc_starts

    # EL damage: running + start-stop
    d_el = D_state.get("el", 0.0)
    eta_factor_el = 1.0 + 0.10 * d_el
    p_ratio_el = np.abs(p_el) / max(el_rated, 1e-6)
    el_active = p_el >= (el_rated * 0.15)
    el_starts = np.zeros(len(p_el))
    for t in range(1, len(p_el)):
        if el_active[t] and not el_active[t - 1]:
            el_starts[t] = 1.0
    dD_el = params["EL_K_RUN_H"] * eta_factor_el * p_ratio_el * dt_h + params["EL_K_START"] * el_starts

    return {
        "dD_bat": dD_bat,
        "dD_fc": dD_fc,
        "dD_el": dD_el,
    }


def compute_lifetime_and_economics(
    cmds_by_year: dict,
    c1_config: dict,
    params: dict,
    rep: dict,
    horizon_years: int = 20,
) -> dict:
    """Compute lifetime and NPC/LCOE for one parameter sample."""

    # Compute raw per-step damage for one 3-year cycle
    years_cycle = [2016, 2017, 2018]
    cycle_dD = {"bat": [], "fc": [], "el": []}
    for year in years_cycle:
        dD = compute_damage_with_params(cmds_by_year[year], c1_config, params)
        cycle_dD["bat"].append(dD["dD_bat"])
        cycle_dD["fc"].append(dD["dD_fc"])
        cycle_dD["el"].append(dD["dD_el"])

    bat_cycle = np.concatenate(cycle_dD["bat"])
    fc_cycle = np.concatenate(cycle_dD["fc"])
    el_cycle = np.concatenate(cycle_dD["el"])

    # Tile over horizon
    n_cycles = int(np.ceil(horizon_years / 3))
    steps_per_year = len(bat_cycle) // 3
    total_steps = steps_per_year * horizon_years
    bat_tiled = np.tile(bat_cycle, n_cycles)[:total_steps]
    fc_tiled = np.tile(fc_cycle, n_cycles)[:total_steps]
    el_tiled = np.tile(el_cycle, n_cycles)[:total_steps]

    eol = advance_replacement_events_with_downtime(
        {"bat": bat_tiled, "fc": fc_tiled, "el": el_tiled},
        {"bat": 0.0, "fc": 0.0, "el": 0.0},
        dt_hours=0.25,
        downtime_steps={"bat": 96, "fc": 48, "el": 48},
    )

    # Lifetimes
    lifetimes = {}
    for dev in ["bat", "fc", "el"]:
        events = [e for e in eol.events if e.device == dev]
        lifetimes[dev] = events[0].time_hours / 8760.0 if events else float(horizon_years)

    # Economics
    econ_cfg = EconomicConfig(
        pv_kwp=float(rep["pv_kwp"]), bat_kwh=float(rep["bat_kwh"]),
        pemfc_kw=float(rep["pemfc_kw"]), pemel_kw=float(rep["pemel_kw"]),
        h2_kg=float(rep["h2_kg"]),
    )
    capex = compute_equipment_capex(econ_cfg)
    total_capex = capex.get("total_initial", 0)

    metrics = rep.get("metrics", {})
    yearly = metrics.get("yearly", {})
    annual_served = np.mean([
        yearly[str(y)].get("load_energy_kwh", 500000) -
        yearly[str(y)].get("unserved_energy_kwh", 0)
        for y in (2016, 2017, 2018)
    ])

    discount = econ_cfg.discount_rate
    npc = total_capex + total_capex * econ_cfg.general_opex_fraction * sum(
        1.0 / (1 + discount) ** y for y in range(1, horizon_years + 1)
    )

    # Add replacement costs
    device_map = {"bat": "BAT", "fc": "PEMFC", "el": "PEMEL"}
    frac_map = {"BAT": econ_cfg.bat_replacement_fraction,
                "PEMFC": econ_cfg.pemfc_replacement_fraction,
                "PEMEL": econ_cfg.pemel_replacement_fraction}
    for event in eol.events:
        dev_key = device_map.get(event.device)
        if dev_key:
            yr = event.time_hours / 8760.0
            if 0 < yr < horizon_years:
                pv_factor = 1.0 / (1 + discount) ** int(yr)
                npc += capex.get(dev_key, 0) * frac_map[dev_key] * pv_factor

    crf = discount * (1 + discount) ** horizon_years / (
        (1 + discount) ** horizon_years - 1) if discount > 0 else 1.0 / horizon_years
    lcoe = npc * crf / max(annual_served, 1e-6)

    return {
        "lifetimes": lifetimes,
        "replacement_counts": eol.replacement_counts,
        "npc": npc,
        "lcoe": lcoe,
    }


def run_uncertainty_propagation(
    cmds_by_year: dict,
    c1_config: dict,
    rep: dict,
    n_samples: int = 128,
    seed: int = 20260803,
) -> dict:
    """Run full uncertainty propagation for one representative config."""

    samples = lhs_sample(n_samples, seed)
    results = []

    for i in range(n_samples):
        params = {name: samples[name][i] for name in PARAM_DEFS}
        r = compute_lifetime_and_economics(cmds_by_year, c1_config, params, rep)
        r["params"] = params
        results.append(r)

    # Aggregate statistics
    bat_lives = [r["lifetimes"]["bat"] for r in results]
    fc_lives = [r["lifetimes"]["fc"] for r in results]
    el_lives = [r["lifetimes"]["el"] for r in results]
    npcs = [r["npc"] for r in results]
    lcoes = [r["lcoe"] for r in results]

    def stats(arr):
        a = np.array(arr)
        return {
            "mean": float(np.mean(a)),
            "std": float(np.std(a)),
            "p05": float(np.percentile(a, 5)),
            "p25": float(np.percentile(a, 25)),
            "p50": float(np.percentile(a, 50)),
            "p75": float(np.percentile(a, 75)),
            "p95": float(np.percentile(a, 95)),
            "min": float(np.min(a)),
            "max": float(np.max(a)),
        }

    return {
        "n_samples": n_samples,
        "seed": seed,
        "param_ranges": {name: {
            "low": PARAM_DEFS[name]["low"],
            "high": PARAM_DEFS[name]["high"],
            "nominal": PARAM_DEFS[name]["nominal"],
            "unit": PARAM_DEFS[name]["unit"],
        } for name in PARAM_DEFS},
        "bat_lifetime": stats(bat_lives),
        "fc_lifetime": stats(fc_lives),
        "el_lifetime": stats(el_lives),
        "npc": stats(npcs),
        "lcoe": stats(lcoes),
        "bat_replacements": stats([r["replacement_counts"]["bat"] for r in results]),
        "fc_replacements": stats([r["replacement_counts"]["fc"] for r in results]),
        "el_replacements": stats([r["replacement_counts"]["el"] for r in results]),
    }


def print_uncertainty_report(unc: dict, role: str):
    """Print formatted uncertainty report for one representative."""
    print(f"\n{'='*70}")
    print(f"Uncertainty Propagation: {role} ({unc['n_samples']} LHS samples)")
    print(f"{'='*70}")

    print(f"\n  Parameter ranges:")
    for name, rng in unc["param_ranges"].items():
        print(f"    {name}: [{rng['low']:.2e}, {rng['high']:.2e}] {rng['unit']} "
              f"(nominal {rng['nominal']:.2e})")

    print(f"\n  Lifetime (years):")
    for dev, key in [("BAT", "bat_lifetime"), ("FC", "fc_lifetime"), ("EL", "el_lifetime")]:
        s = unc[key]
        print(f"    {dev}: median={s['p50']:.1f}  90%CI=[{s['p05']:.1f}, {s['p95']:.1f}]  "
              f"range=[{s['min']:.1f}, {s['max']:.1f}]")

    print(f"\n  Replacements (20-year horizon):")
    for dev, key in [("BAT", "bat_replacements"), ("FC", "fc_replacements"), ("EL", "el_replacements")]:
        s = unc[key]
        print(f"    {dev}: median={s['p50']:.0f}  90%CI=[{s['p05']:.0f}, {s['p95']:.0f}]")

    print(f"\n  Economics:")
    s = unc["npc"]
    print(f"    NPC: median={s['p50']/1000:.0f} kCNY  90%CI=[{s['p05']/1000:.0f}, {s['p95']/1000:.0f}]")
    s = unc["lcoe"]
    print(f"    LCOE: median={s['p50']:.3f} CNY/kWh  90%CI=[{s['p05']:.3f}, {s['p95']:.3f}]")
    print(f"          ({s['p50']/7.25:.3f} USD/kWh  [{s['p05']/7.25:.3f}, {s['p95']/7.25:.3f}])")


if __name__ == "__main__":
    # Load Stage3 results to get representative configs and dispatch commands
    from simulation.run_stage3_full_analysis import (
        build_c1_config, run_three_year_cycle, extract_commands,
    )
    from ems.ems_stage2b import Stage2BEMSConfig
    from components.annual_input_contract import load_evaluation_years, scale_pv_available

    config_path = ROOT / "05_config" / "rectification_v2_stage2c.json"
    data_path = ROOT / "02_data_processed" / "tradestreet_pipeline" / \
        "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv"
    admissions_path = ROOT / "07_results" / "rectification_v2_stage2c" / "search" / "verified_admissions.json"

    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    admissions = json.loads(admissions_path.read_text(encoding="utf-8"))
    loaded = load_evaluation_years(data_path)

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
        prepared = {}
        for year, frame in loaded.items():
            pv_raw = frame["pv_raw_kw"].to_numpy(dtype=float)
            pv_scaled = scale_pv_available(pv_raw, nominal_kw=pv_kwp)
            prepared[year] = {
                "load": frame["load_kw"].to_numpy(dtype=float),
                "pv": pv_scaled,
            }

        c1_config = build_c1_config(rep)
        cycle = run_three_year_cycle(prepared, cfg_ems)
        cmds = extract_commands(cycle)

        unc = run_uncertainty_propagation(cmds, c1_config, rep, n_samples=128)
        print_uncertainty_report(unc, role)
        all_results[role] = unc

    # Save results
    output_path = ROOT / "07_results" / "rectification_v2_stage2c" / "stage3" / \
        "uncertainty_propagation.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Make results JSON-serializable
    serializable = {}
    for role, unc in all_results.items():
        serializable[role] = {
            "n_samples": unc["n_samples"],
            "param_ranges": unc["param_ranges"],
            "bat_lifetime": unc["bat_lifetime"],
            "fc_lifetime": unc["fc_lifetime"],
            "el_lifetime": unc["el_lifetime"],
            "npc": unc["npc"],
            "lcoe": unc["lcoe"],
            "bat_replacements": unc["bat_replacements"],
            "fc_replacements": unc["fc_replacements"],
            "el_replacements": unc["el_replacements"],
        }

    with open(output_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(serializable, f, ensure_ascii=False, indent=2)
    print(f"\nResults written to: {output_path}")
