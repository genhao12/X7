# -*- coding: utf-8 -*-
"""F8：仅在连续年度状态、LPSP≤1%且库存闭合时进行跨年验证。"""
import csv
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(r"D:\matlab\C")
sys.path.insert(0, str(PROJECT_ROOT / "06_src"))

from ems.ems_bf_lite import EMSConfig, rule_based_dispatch


with open(PROJECT_ROOT / "05_config" / "c5_feasible_config.json", encoding="utf-8") as f:
    base = json.load(f)
ep, bc = base["ems_parameters"], base["battery_constraints"]
hc, msl, topo = base["hydrogen_constraints"], base["minimum_stable_load"], base["topology"]
configs = {
    "C5_H2_500": {"PV_kWp": 300, "BAT_kWh": 800, "PEMFC_kW": 100,
                   "PEMEL_kW": 100, "H2_kg": 500},
    "C5_H2_1000": {"PV_kWp": 300, "BAT_kWh": 800, "PEMFC_kW": 100,
                    "PEMEL_kW": 100, "H2_kg": 1000},
}

data_path = (PROJECT_ROOT / "02_data_processed" / "tradestreet_pipeline" /
             "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv")
raw = pd.read_csv(data_path)
rows = []
for config_name, cap in configs.items():
    ems = EMSConfig(
        pv_kwp=cap["PV_kWp"], bat_kwh=cap["BAT_kWh"], pemfc_kw=cap["PEMFC_kW"],
        pemel_kw=cap["PEMEL_kW"], h2_kg=cap["H2_kg"], soc_min=bc["soc_min"],
        soc_max=bc["soc_max"], soc_initial=bc["soc_initial"],
        h2_min_fraction=hc["m_min_fraction"], h2_max_fraction=hc["m_max_fraction"],
        h2_initial_fraction=1.0, h2_leakage_per_day=hc["leakage_rate_per_day"],
        start_stop_threshold=ep["start_stop_threshold_fraction"],
        minimum_run_time_min=float(ep["minimum_run_time"]),
        critical_load_fraction=ep["critical_load_fraction"],
        p_aux_fraction=topo["P_aux_fraction_of_installed_power"],
        min_stable_load_fc=msl["PEMFC"], min_stable_load_el=msl["PEMEL"], dt_min=15.0,
    )
    for calendar_year in [2016, 2017, 2018]:
        data = raw[raw["local_civil_year"] == calendar_year]
        pv = data["p_pv_available_reconstructed_nonnegative_kW"].ffill().bfill().to_numpy()
        load = data["p_building_load_reconstructed_kW"].ffill().bfill().to_numpy()
        np.random.seed(42)
        _, state = rule_based_dispatch(load, pv, ems)
        dt_h = ems.dt_min / 60.0
        load_energy = float(np.sum(load) * dt_h)
        unserved = float(np.sum(state["p_uns"]) * dt_h)
        lpsp = unserved / load_energy
        final_h2 = float(state["h2_trace"][-1])
        initial_h2 = float(state["initial_h2_kg"])
        h2_change_fraction = (final_h2 - initial_h2) / initial_h2
        final_soc = float(state["soc_trace"][-1])
        reliable = lpsp <= 0.01
        cyclic = abs(h2_change_fraction) <= 0.05
        rows.append({
            "config": config_name, "year": calendar_year,
            "lpsp": lpsp, "lpsp_pct": lpsp * 100,
            "initial_h2_kg": initial_h2, "final_h2_kg": final_h2,
            "h2_change_pct": h2_change_fraction * 100,
            "final_soc": final_soc, "reliability_pass": reliable,
            "cyclic_h2_pass": cyclic, "admissible": reliable and cyclic,
        })

admissible = [row for row in rows if row["admissible"]]
configs_all_three = [
    name for name in configs
    if sum(row["admissible"] for row in rows if row["config"] == name) == 3
]
summary = {
    "protocol": "continuous full-year dispatch; no weekly SOC/H2 resets",
    "admission": "LPSP<=1% and |H2(T)-H2(0)|/H2(0)<=5%",
    "admissible_config_years": admissible,
    "configs_admissible_all_three_years": configs_all_three,
    "crossyear_validation_pass": bool(configs_all_three),
    "conclusion": (
        "cross-year validation available" if configs_all_three else
        "no configuration is admissible in all three years; cross-year path stability is not established"
    ),
}

out_dir = PROJECT_ROOT / "07_results" / "crossyear"
out_dir.mkdir(parents=True, exist_ok=True)
table_path = out_dir / "f8_crossyear_admissibility.csv"
summary_path = out_dir / "f8_crossyear_summary.json"
with open(table_path, "w", newline="", encoding="utf-8-sig") as f:
    writer = csv.DictWriter(f, fieldnames=list(rows[0]))
    writer.writeheader(); writer.writerows(rows)
with open(summary_path, "w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)

print("F8 COMPLETE")
for row in rows:
    print(f"{row['config']} {row['year']}: LPSP={row['lpsp_pct']:.2f}%, "
          f"H2 change={row['h2_change_pct']:.1f}%, admissible={row['admissible']}")
print(f"cross-year PASS={summary['crossyear_validation_pass']}")
print(table_path)
print(summary_path)
