# -*- coding: utf-8 -*-
"""F4：2017基准年的逐时间步 EOL 与更换事件验证。"""
import csv
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(r"D:\matlab\C")
sys.path.insert(0, str(PROJECT_ROOT / "06_src"))

from components.hp_bat_lite import _load_280ah_data, fit_hppc, interpolate_hp
from ems.ems_bf_lite import EMSConfig, rule_based_dispatch
from simulation.closed_loop_lite import CoalitionState, compute_damage
from simulation.eol_events import advance_replacement_events, operating_hour_damage


with open(PROJECT_ROOT / "05_config" / "c6_compact_config.json", encoding="utf-8") as f:
    base = json.load(f)
with open(PROJECT_ROOT / "05_config" / "fc_recalibration_f2.json", encoding="utf-8") as f:
    fc_cal = json.load(f)["fc_recalibration_f2"]

cap = {"PV_kWp": 300, "BAT_kWh": 800, "PEMFC_kW": 100,
       "PEMEL_kW": 100, "H2_kg": 500}
bc = base["battery_constraints"]
hc = base["hydrogen_constraints"]
ep = base["ems_parameters"]
msl = base["minimum_stable_load"]
topo = base["topology"]
dt_hours = 0.25

ems = EMSConfig(
    pv_kwp=cap["PV_kWp"], bat_kwh=cap["BAT_kWh"],
    pemfc_kw=cap["PEMFC_kW"], pemel_kw=cap["PEMEL_kW"], h2_kg=cap["H2_kg"],
    soc_min=bc["soc_min"], soc_max=bc["soc_max"], soc_initial=bc["soc_initial"],
    h2_min_fraction=hc["m_min_fraction"], h2_max_fraction=hc["m_max_fraction"],
    h2_initial_fraction=1.0, h2_leakage_per_day=hc["leakage_rate_per_day"],
    start_stop_threshold=ep["start_stop_threshold_fraction"],
    minimum_run_time_min=float(ep["minimum_run_time"]),
    critical_load_fraction=ep["critical_load_fraction"],
    p_aux_fraction=topo["P_aux_fraction_of_installed_power"],
    min_stable_load_fc=msl["PEMFC"], min_stable_load_el=msl["PEMEL"],
    dt_min=15.0,
)
config_dict = {
    "capacity": cap, "battery_constraints": bc, "ems_parameters": ep,
    "hydrogen_constraints": hc, "minimum_stable_load": msl, "topology": topo,
}

bat_rated_kw = cap["BAT_kWh"] * 0.5
hp_bat = interpolate_hp(
    fit_hppc(_load_280ah_data()),
    {"bat_kwh": cap["BAT_kWh"], "bat_voltage_nominal": 3.2 * 48,
     "bat_rated_kw": bat_rated_kw},
)
coalition = CoalitionState.from_code("1111")
eol_hours_fc = float(fc_cal["calibration_result"]["EOL_time_hours"])

data_path = (PROJECT_ROOT / "02_data_processed" / "tradestreet_pipeline" /
             "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv")
raw = pd.read_csv(data_path)
year = raw[raw["local_civil_year"] == 2017].copy()
pv = year["p_pv_available_reconstructed_nonnegative_kW"].ffill().bfill().to_numpy()
load = year["p_building_load_reconstructed_kW"].ffill().bfill().to_numpy()

steps_per_week = 672
n_weeks = 52
state = {"bat": 0.0, "fc": 0.0, "el": 0.0}
counts = {"bat": 0, "fc": 0, "el": 0}
events = []
raw_damage = {"bat": 0.0, "fc": 0.0, "el": 0.0}
fc_operating_hours = 0.0
fc_equivalent_full_load_hours = 0.0
load_energy_kwh = 0.0
unserved_energy_kwh = 0.0
soc_state = float(bc["soc_initial"])
h2_state = float(cap["H2_kg"])

for week in range(n_weeks):
    start = week * steps_per_week
    stop = start + steps_per_week
    dispatch_kwargs = {}
    if state["bat"] > 0:
        q_frac, _, _ = hp_bat(state["bat"])
        dispatch_kwargs["bat_p_max_kw"] = bat_rated_kw * q_frac
        dispatch_kwargs["bat_capacity_kwh"] = cap["BAT_kWh"] * q_frac

    np.random.seed(42)
    commands, dispatch_state = rule_based_dispatch(
        load[start:stop], pv[start:stop], ems,
        soc_initial=soc_state, h2_initial_kg=h2_state, **dispatch_kwargs,
    )
    soc_state = float(dispatch_state["soc_trace"][-1])
    h2_state = float(dispatch_state["h2_trace"][-1])
    damage = compute_damage(
        hp_bat, commands, config_dict, dt_min=15.0, D_state=state,
        coalition=coalition, eol_policy="raw",
    )
    fc_damage = operating_hour_damage(
        commands[:, 1], cap["PEMFC_kW"], eol_hours_fc, dt_hours,
        active_threshold_fraction=ep["start_stop_threshold_fraction"],
    )
    fc_operating_hours += float(np.count_nonzero(fc_damage) * dt_hours)
    fc_equivalent_full_load_hours += float(
        np.sum(commands[:, 1]) * dt_hours / cap["PEMFC_kW"]
    )
    load_energy_kwh += float(np.sum(load[start:stop]) * dt_hours)
    unserved_energy_kwh += float(np.sum(dispatch_state["p_uns"]) * dt_hours)

    increments = {
        "bat": damage["dD_bat_raw_series"],
        "fc": fc_damage,
        "el": damage["dD_el_raw_series"],
    }
    advanced = advance_replacement_events(
        increments, initial_state=state, dt_hours=dt_hours,
        start_time_hours=week * 168.0,
        initial_replacement_counts=counts,
    )
    state = advanced.final_state
    counts = advanced.replacement_counts
    events.extend(advanced.events)
    for device in raw_damage:
        raw_damage[device] += advanced.raw_damage_totals[device]

expected_fc_replacements = int(fc_operating_hours // eol_hours_fc)
assert counts["fc"] == expected_fc_replacements
assert all(0.0 <= value < 1.0 for value in state.values())
assert all(events[i].time_hours <= events[i + 1].time_hours
           for i in range(len(events) - 1))

out_dir = PROJECT_ROOT / "07_results" / "lifetime"
out_dir.mkdir(parents=True, exist_ok=True)
event_path = out_dir / "f4_replacement_events_2017.csv"
summary_path = out_dir / "f4_replacement_summary_2017.json"

with open(event_path, "w", newline="", encoding="utf-8-sig") as f:
    writer = csv.DictWriter(f, fieldnames=[
        "device", "time_hours", "day", "step_index", "replacement_number",
    ])
    writer.writeheader()
    for event in events:
        writer.writerow({
            "device": event.device.upper(),
            "time_hours": round(event.time_hours, 6),
            "day": round(event.time_hours / 24.0, 6),
            "step_index": event.step_index,
            "replacement_number": event.replacement_number,
        })

summary = {
    "protocol": "F4 dynamic lifetime with instantaneous EOL replacement",
    "baseline": "C5-H2-500, UCSD 2017, 15-min",
    "fc_calibration": "F2 voltage-based scenario calibration",
    "fc_eol_operating_hours": eol_hours_fc,
    "fc_operating_hours": fc_operating_hours,
    "fc_equivalent_full_load_hours": fc_equivalent_full_load_hours,
    "annual_load_energy_kwh": load_energy_kwh,
    "annual_delivered_energy_kwh": load_energy_kwh - unserved_energy_kwh,
    "lpsp": unserved_energy_kwh / load_energy_kwh,
    "admissible": (unserved_energy_kwh / load_energy_kwh) <= 0.01,
    "final_soc": soc_state,
    "final_h2_kg": h2_state,
    "replacement_counts": counts,
    "final_damage_state": state,
    "raw_damage_totals": raw_damage,
    "event_count": len(events),
    "assumption": "replacement is instantaneous; downtime and logistics are excluded",
}
with open(summary_path, "w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)

print("F4 EVENT MODEL PASS")
print(f"FC operating hours={fc_operating_hours:.2f}, EOL={eol_hours_fc:.0f}h, replacements={counts['fc']}")
print(f"FC equivalent full-load hours={fc_equivalent_full_load_hours:.2f}")
print(f"BAT/FC/EL replacements={counts['bat']}/{counts['fc']}/{counts['el']}")
print(f"final D={state}")
print(f"system admissible={summary['admissible']} (LPSP={summary['lpsp']*100:.2f}%)")
print(event_path)
print(summary_path)
