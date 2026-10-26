"""L4 ablation experiment: R1 only, R2 only, R1+R2 combined, baseline.

Quantifies each L4 rule's independent contribution and interaction effect
on coupling strength (Shapley phi_C) and FC/EL damage.
"""
import sys
import json
import numpy as np
from pathlib import Path
from typing import Dict

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "06_src") not in sys.path:
    sys.path.insert(0, str(ROOT / "06_src"))

from ems.ems_stage2b import Stage2BEMSConfig, dispatch_stage2b
from components.annual_input_contract import load_evaluation_years, scale_pv_available
from simulation.closed_loop_lite import compute_damage
from simulation.run_stage3_full_analysis import (
    build_c1_config, run_three_year_cycle, extract_commands,
    dispatch_with_degraded_bat, compute_annual_damage, hp_bat_default,
    make_l4_adjusted_config,
)


def run_ablation_for_config(
    prepared: dict,
    cfg_ems: Stage2BEMSConfig,
    c1_config: dict,
    D_bat: float = 0.5,
) -> dict:
    """Run 4-way ablation: baseline, R1-only, R2-only, R1+R2."""
    year = 2017
    load = prepared[year]["load"]
    pv = prepared[year]["pv"]
    bat_rated_kw = cfg_ems.bat_kwh * 0.5

    Q, _, P_max_kw = hp_bat_default(D_bat, bat_kwh=cfg_ems.bat_kwh)

    # Create 4 configs
    configs = {
        "baseline": cfg_ems,
        "R1_only": make_l4_adjusted_config(cfg_ems, D_bat, soc_narrow_k=0.15, fc_extend_k=0.0),
        "R2_only": make_l4_adjusted_config(cfg_ems, D_bat, soc_narrow_k=0.0, fc_extend_k=1.0),
        "R1+R2": make_l4_adjusted_config(cfg_ems, D_bat, soc_narrow_k=0.15, fc_extend_k=1.0),
    }

    coalition_params = {
        "11": (Q, P_max_kw),
        "10": (Q, bat_rated_kw),
        "01": (1.0, P_max_kw),
        "00": (1.0, bat_rated_kw),
    }

    ablation = {}
    for label, dispatch_cfg in configs.items():
        damages = {}
        for code, (Q_frac, P_kw) in coalition_params.items():
            result = dispatch_with_degraded_bat(
                dispatch_cfg, load, pv, Q_frac=Q_frac, P_max_kw=P_kw
            )
            dD = compute_annual_damage(
                result.commands, c1_config,
                D_state={"bat": D_bat, "fc": 0.0, "el": 0.0},
            )
            damages[code] = dD

        # Shapley values for FC
        v_fc = {code: damages[code]["dD_fc"] for code in damages}
        phi_C_fc = 0.5 * (v_fc["11"] - v_fc["01"]) + 0.5 * (v_fc["10"] - v_fc["00"])
        phi_P_fc = 0.5 * (v_fc["11"] - v_fc["10"]) + 0.5 * (v_fc["01"] - v_fc["00"])
        total_fc = v_fc["11"] - v_fc["00"]

        # Shapley values for EL
        v_el = {code: damages[code]["dD_el"] for code in damages}
        phi_C_el = 0.5 * (v_el["11"] - v_el["01"]) + 0.5 * (v_el["10"] - v_el["00"])
        phi_P_el = 0.5 * (v_el["11"] - v_el["10"]) + 0.5 * (v_el["01"] - v_el["00"])
        total_el = v_el["11"] - v_el["00"]

        # FC start-stop count
        result_11 = dispatch_with_degraded_bat(
            dispatch_cfg, load, pv, Q_frac=Q, P_max_kw=P_max_kw
        )
        fc_cmds = result_11.commands[:, 1]
        fc_threshold = dispatch_cfg.pemfc_kw * dispatch_cfg.min_stable_load_fc
        fc_starts = np.sum((fc_cmds[1:] >= fc_threshold) & (fc_cmds[:-1] < fc_threshold))

        ablation[label] = {
            "fc_total": total_fc,
            "fc_phi_C": phi_C_fc,
            "fc_phi_P": phi_P_fc,
            "el_total": total_el,
            "el_phi_C": phi_C_el,
            "el_phi_P": phi_P_el,
            "fc_starts": int(fc_starts),
            "soc_min": dispatch_cfg.soc_min,
            "soc_max": dispatch_cfg.soc_max,
            "min_run_steps": dispatch_cfg.minimum_run_steps,
        }

    # Compute interaction effect: (R1+R2) - R1 - R2 + baseline
    interaction_fc = (
        ablation["R1+R2"]["fc_total"]
        - ablation["R1_only"]["fc_total"]
        - ablation["R2_only"]["fc_total"]
        + ablation["baseline"]["fc_total"]
    )
    interaction_phi_C = (
        ablation["R1+R2"]["fc_phi_C"]
        - ablation["R1_only"]["fc_phi_C"]
        - ablation["R2_only"]["fc_phi_C"]
        + ablation["baseline"]["fc_phi_C"]
    )

    return {
        "ablation": ablation,
        "interaction_fc_total": interaction_fc,
        "interaction_phi_C": interaction_phi_C,
    }


def print_ablation_report(role: str, result: dict):
    """Print formatted ablation report."""
    abl = result["ablation"]
    print(f"\n{'='*80}")
    print(f"L4 Ablation: {role} (D_bat=0.5)")
    print(f"{'='*80}")
    print(f"\n  {'Variant':>10s} | {'FC total':>10s} | {'FC phi_C':>10s} | {'FC phi_P':>10s} | "
          f"{'EL total':>10s} | {'FC starts':>9s} | {'SOC range':>10s} | {'min_run':>7s}")
    print(f"  {'-'*10}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}-+-{'-'*9}-+-{'-'*10}-+-{'-'*7}")
    for label in ["baseline", "R1_only", "R2_only", "R1+R2"]:
        a = abl[label]
        soc_range = f"[{a['soc_min']:.2f},{a['soc_max']:.2f}]"
        print(f"  {label:>10s} | {a['fc_total']:+10.6f} | {a['fc_phi_C']:+10.6f} | "
              f"{a['fc_phi_P']:+10.6f} | {a['el_total']:+10.6f} | {a['fc_starts']:>9d} | "
              f"{soc_range:>10s} | {a['min_run_steps']:>7d}")

    # Decomposition
    bl = abl["baseline"]["fc_total"]
    r1 = abl["R1_only"]["fc_total"] - bl
    r2 = abl["R2_only"]["fc_total"] - bl
    combined = abl["R1+R2"]["fc_total"] - bl
    interaction = result["interaction_fc_total"]

    print(f"\n  FC coupling decomposition (Δ vs baseline):")
    print(f"    R1 effect (SOC narrowing):  {r1:+.6f}")
    print(f"    R2 effect (FC min run):     {r2:+.6f}")
    print(f"    R1×R2 interaction:          {interaction:+.6f}")
    print(f"    Combined (R1+R2):           {combined:+.6f}")
    print(f"    Check: R1+R2+interaction = {r1+r2+interaction:+.6f} (should = {combined:+.6f})")

    # Same for phi_C
    bl_phi = abl["baseline"]["fc_phi_C"]
    r1_phi = abl["R1_only"]["fc_phi_C"] - bl_phi
    r2_phi = abl["R2_only"]["fc_phi_C"] - bl_phi
    combined_phi = abl["R1+R2"]["fc_phi_C"] - bl_phi
    interaction_phi = result["interaction_phi_C"]

    print(f"\n  FC phi_C decomposition (Δ vs baseline):")
    print(f"    R1 effect:       {r1_phi:+.6f}")
    print(f"    R2 effect:       {r2_phi:+.6f}")
    print(f"    R1×R2 interact:  {interaction_phi:+.6f}")
    print(f"    Combined:        {combined_phi:+.6f}")


if __name__ == "__main__":
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
        result = run_ablation_for_config(prepared, cfg_ems, c1_config)
        print_ablation_report(role, result)
        all_results[role] = result

    # Save results
    output_path = ROOT / "07_results" / "rectification_v2_stage2c" / "stage3" / "l4_ablation.json"
    serializable = {}
    for role, res in all_results.items():
        serializable[role] = {
            "ablation": res["ablation"],
            "interaction_fc_total": res["interaction_fc_total"],
            "interaction_phi_C": res["interaction_phi_C"],
        }
    with open(output_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(serializable, f, ensure_ascii=False, indent=2)
    print(f"\nResults written to: {output_path}")
