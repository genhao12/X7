# -*- coding: utf-8 -*-
"""EFC vs legacy running-hour proxy comparison for the paper/report.

For each frozen representative: dispatch one year (2017, BOL dispatch) then
compute_damage under bat_cycle_model='efc' and 'proxy'. Reports annual cyclic
damage, calendar-only damage and the implied lifetime (1/D_annual). The result
shows how much less aggressive the 280Ah-calibrated EFC model is vs the legacy
proxy (which is ~4x more aggressive per full cycle).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(r"d:\matlab\C")
sys.path.insert(0, str(ROOT / "06_src"))

import numpy as np
from ems.ems_stage2b import Stage2BEMSConfig, EMSState, dispatch_stage2b
from components.annual_input_contract import load_evaluation_years, scale_pv_available
from simulation.closed_loop_lite import compute_damage, BAT_K_CAL_H
from simulation.run_stage3_full_analysis import build_c1_config


def main() -> int:
    data_path = ROOT / "02_data_processed" / "tradestreet_pipeline" / \
        "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv"
    admissions_path = ROOT / "07_results" / "rectification_v2_stage2c" / "search" / "verified_admissions.json"
    loaded = load_evaluation_years(data_path)
    frame = loaded[2017]
    pv_raw = frame["pv_raw_kw"].to_numpy(dtype=float)
    load = frame["load_kw"].to_numpy(dtype=float)
    admissions = json.loads(admissions_path.read_text(encoding="utf-8"))

    out = {"method": "BOL dispatch single-year 2017 comparison",
           "bat_cycle_model_efc": "rainflow+Woehler k=1, a_d=5.0714e-4 D/EFC (280Ah)",
           "bat_cycle_model_proxy": "legacy dD_cyc=BAT_K_CYC_H*DOD*p_ratio*dt, BAT_K_CYC_H=2e-3/h",
           "representatives": []}

    for rep in admissions["representatives"]:
        role = rep["representative_role"]
        cfg_ems = Stage2BEMSConfig(
            pv_kwp=float(rep["pv_kwp"]), bat_kwh=float(rep["bat_kwh"]),
            pemfc_kw=float(rep["pemfc_kw"]), pemel_kw=float(rep["pemel_kw"]),
            h2_kg=float(rep["h2_kg"]))
        pv_scaled = scale_pv_available(pv_raw, nominal_kw=float(rep["pv_kwp"]))
        initial = EMSState(soc=0.50, h2_kg=cfg_ems.h2_kg * 0.50)
        dispatch = dispatch_stage2b(load, pv_scaled, cfg_ems, strategy="battery_first", initial_state=initial)
        cmds = dispatch.commands
        n = len(cmds)
        c1 = build_c1_config(rep)

        hp = lambda D: (1.0, 1.0, 500.0)  # hp_bat placeholder: Q=1, R0=1, P_max=500kW

        def run(model):
            cfg = dict(c1)
            cfg.setdefault("degradation", {})["bat_cycle_model"] = model
            res = compute_damage(hp, cmds, cfg, dt_min=15.0,
                                 D_state={"bat": 0.0, "fc": 0.0, "el": 0.0},
                                 eol_policy="raw")
            dD = np.sum(res["dD_bat"])
            cal = BAT_K_CAL_H * n * 0.25
            return {"total": float(dD), "cyclic": float(dD - cal), "calendar": float(cal)}

        efc = run("efc")
        proxy = run("proxy")
        lifetime_efc = 1.0 / efc["total"] if efc["total"] > 0 else float("inf")
        lifetime_proxy = 1.0 / proxy["total"] if proxy["total"] > 0 else float("inf")
        out["representatives"].append({
            "role": role,
            "efc": efc,
            "proxy": proxy,
            "lifetime_years_efc": round(lifetime_efc, 2),
            "lifetime_years_proxy": round(lifetime_proxy, 2),
            "ratio_proxy_over_efc": round(proxy["cyclic"] / efc["cyclic"], 2) if efc["cyclic"] > 0 else None,
        })
        print(f"{role:18s} efc D/yr={efc['cyclic']:.4f} (life {lifetime_efc:.1f}y) | "
              f"proxy D/yr={proxy['cyclic']:.4f} (life {lifetime_proxy:.1f}y) | "
              f"proxy/efc cyclic={out['representatives'][-1]['ratio_proxy_over_efc']}")

    out_path = ROOT / "07_results" / "rectification_v2_stage2c" / "stage3" / "efc_vs_proxy_comparison.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("written:", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
