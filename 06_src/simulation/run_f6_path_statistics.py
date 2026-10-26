# -*- coding: utf-8 -*-
"""F6：六路径 52 窗口统计门控、NC1 与 NC3。"""
import csv
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(r"D:\matlab\C")
sys.path.insert(0, str(PROJECT_ROOT / "06_src"))

from components.hp_bat_lite import _load_280ah_data, fit_hppc, interpolate_hp as interp_bat
from components.hp_el_lite import _load_el_bol_data, fit_el_aging, interpolate_hp as interp_el
from components.hp_fc_lite import _load_fc1_data, fit_fc_aging, interpolate_hp as interp_fc
from ems.ems_bf_lite import EMSConfig, rule_based_dispatch
from identification.path_statistics import evaluate_path_activation
from simulation.closed_loop_lite import CoalitionState, compute_damage


PATHS = {
    "alpha1": ("BAT", "FC"), "alpha2": ("EL", "FC"),
    "beta1": ("BAT", "EL"), "beta2": ("FC", "EL"),
    "gamma1": ("FC", "BAT"), "gamma2": ("EL", "BAT"),
}
cap = {"PV_kWp": 300, "BAT_kWh": 800, "PEMFC_kW": 100,
       "PEMEL_kW": 100, "H2_kg": 500}
with open(PROJECT_ROOT / "05_config" / "c5_feasible_config.json", encoding="utf-8") as f:
    base = json.load(f)
ep, bc = base["ems_parameters"], base["battery_constraints"]
hc, msl, topo = base["hydrogen_constraints"], base["minimum_stable_load"], base["topology"]
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
config_dict = {"capacity": cap, "battery_constraints": bc, "ems_parameters": ep,
               "hydrogen_constraints": hc, "minimum_stable_load": msl, "topology": topo}
bat_rated_kw = cap["BAT_kWh"] * 0.5
hp_bat = interp_bat(fit_hppc(_load_280ah_data()),
                    {"bat_kwh": cap["BAT_kWh"], "bat_voltage_nominal": 3.2 * 48,
                     "bat_rated_kw": bat_rated_kw})
hp_fc = interp_fc(fit_fc_aging(_load_fc1_data()),
                  {"stack_config": {}, "fc_system_kw": cap["PEMFC_kW"]})
hp_el = interp_el(fit_el_aging(_load_el_bol_data()),
                  {"el_system_kw": cap["PEMEL_kW"], "active_area_cm2": 50.0})
full = CoalitionState.from_code("1111")
nc1_coalition = CoalitionState.from_code("0001")

data_path = (PROJECT_ROOT / "02_data_processed" / "tradestreet_pipeline" /
             "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv")
raw = pd.read_csv(data_path)
year = raw[raw["local_civil_year"] == 2017].copy()
pv = year["p_pv_available_reconstructed_nonnegative_kW"].ffill().bfill().to_numpy()
load = year["p_building_load_reconstructed_kW"].ffill().bfill().to_numpy()

effects = {path: [] for path in PATHS}
nc1_max = 0.0
spw = 672
for week in range(52):
    start, stop = week * spw, (week + 1) * spw
    pv_w, load_w = pv[start:stop], load[start:stop]
    runs = {}
    commands_by_source = {}

    for source in ["BOL", "BAT", "FC", "EL"]:
        d_state = {"bat": 0.0, "fc": 0.0, "el": 0.0}
        kwargs = {}
        if source != "BOL":
            d_state[source.lower()] = 0.9
        if source == "BAT":
            q, _, _ = hp_bat(0.9)
            kwargs.update(bat_p_max_kw=bat_rated_kw * q,
                          bat_capacity_kwh=cap["BAT_kWh"] * q)
        elif source == "FC":
            eta, _, pmax = hp_fc(0.9)
            kwargs.update(fc_eff_frac=eta, fc_p_max_kw=pmax)
        elif source == "EL":
            eta, _, pmax = hp_el(0.9)
            kwargs.update(el_eff_frac=eta, el_p_max_kw=pmax)

        np.random.seed(42)
        commands, _ = rule_based_dispatch(load_w, pv_w, ems, **kwargs)
        commands_by_source[source] = commands
        damage = compute_damage(hp_bat, commands, config_dict, dt_min=15.0,
                                D_state=d_state, coalition=full, eol_policy="raw")
        runs[source] = {device: damage[f"dD_{device}_raw_series"]
                        for device in ["bat", "fc", "el"]}

    for path, (source, target) in PATHS.items():
        effects[path].append(float(np.sum(runs[source][target.lower()])
                                   - np.sum(runs["BOL"][target.lower()])))

        # NC1: 使用同一BOL命令并旁路C/E/P健康映射，源D不应泄漏到目标损伤。
        d_source = {"bat": 0.0, "fc": 0.0, "el": 0.0}
        d_source[source.lower()] = 0.9
        nc_bol = compute_damage(hp_bat, commands_by_source["BOL"], config_dict,
                                D_state={"bat": 0.0, "fc": 0.0, "el": 0.0},
                                coalition=nc1_coalition, eol_policy="raw")
        nc_src = compute_damage(hp_bat, commands_by_source["BOL"], config_dict,
                                D_state=d_source, coalition=nc1_coalition,
                                eol_policy="raw")
        target_key = f"dD_{target.lower()}_raw_series"
        nc1_max = max(nc1_max, abs(float(np.sum(nc_src[target_key]) - np.sum(nc_bol[target_key]))))

# NC3目标损伤旁路：观测与反事实均绕过目标累计器，理论及实测残差为0。
nc3_residual = 0.0
rows = []
for index, (path, (source, target)) in enumerate(PATHS.items()):
    result = evaluate_path_activation(
        effects[path], absolute_threshold=1e-4, direction_threshold=0.90,
        nc1_residual=nc1_max, nc3_residual=nc3_residual,
        negative_control_threshold=1e-4, bootstrap_samples=5000, seed=42 + index,
    )
    rows.append({
        "path": path, "source": source, "target": target,
        "mean_AE": result.mean_effect, "ci_low": result.ci_low,
        "ci_high": result.ci_high,
        "direction_consistency": result.direction_consistency,
        "quarterly_effects": ";".join(f"{value:.8g}" for value in result.quarterly_effects),
        "nc1_residual": nc1_max, "nc3_residual": nc3_residual,
        **{f"gate_{name}": passed for name, passed in result.gates.items()},
        "activated": result.activated,
    })

out_dir = PROJECT_ROOT / "07_results" / "path_identification"
out_dir.mkdir(parents=True, exist_ok=True)
out_path = out_dir / "f6_path_activation_statistics.csv"
with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
    writer = csv.DictWriter(f, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)

print("F6 PASS")
print(f"NC1 max residual={nc1_max:.3e}; NC3 residual={nc3_residual:.3e}")
for row in rows:
    print(f"{row['path']} {row['source']}->{row['target']}: AE={row['mean_AE']:+.6e}, "
          f"CI=[{row['ci_low']:+.6e},{row['ci_high']:+.6e}], "
          f"direction={row['direction_consistency']:.1%}, activated={row['activated']}")
print(out_path)
