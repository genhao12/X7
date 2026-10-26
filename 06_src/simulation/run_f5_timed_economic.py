# -*- coding: utf-8 -*-
"""F5：基于 F4 更换事件时间的 20 年 NPC/LCOE 修正。"""
import csv
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(r"D:\matlab\C")
sys.path.insert(0, str(PROJECT_ROOT / "06_src"))

from economics.npc_lite import (
    EconomicConfig,
    build_timed_replacement_schedule,
    compute_npc_timed,
)


with open(PROJECT_ROOT / "07_results" / "lifetime" /
          "f4_replacement_summary_2017.json", encoding="utf-8") as f:
    f4 = json.load(f)
with open(PROJECT_ROOT / "05_config" / "economic_parameters_v1.0.json",
          encoding="utf-8") as f:
    params = json.load(f)

cap = {"PV_kWp": 300.0, "BAT_kWh": 800.0, "PEMFC_kW": 100.0,
       "PEMEL_kW": 100.0, "H2_kg": 500.0}
cfg = EconomicConfig(
    discount_rate=params["discount"]["rate"],
    horizon_years=params["horizon"]["years"],
    residual_fraction=params["residual_value"]["fraction_of_capex"],
    exchange_rate=params["currency"]["exchange_rate_usd_to_cny"],
    pv_unit_cost=params["capex"]["PV"]["unit_cost_cny_per_kWp"],
    bat_unit_cost=params["capex"]["BAT"]["unit_cost_cny_per_kWh"],
    pemfc_unit_cost=params["capex"]["PEMFC"]["unit_cost_cny_per_kW"],
    pemel_unit_cost=params["capex"]["PEMEL"]["unit_cost_cny_per_kW"],
    h2_tank_unit_cost=params["capex"]["H2_tank"]["unit_cost_cny_per_kg"],
    bat_replacement_fraction=params["capex"]["BAT"]["replacement_cost_fraction_of_capex"],
    pemfc_replacement_fraction=params["capex"]["PEMFC"]["replacement_cost_fraction_of_capex"],
    pemel_replacement_fraction=params["capex"]["PEMEL"]["replacement_cost_fraction_of_capex"],
    power_electronics_fraction=params["capex"]["power_electronics"]["cost_fraction_of_total_equipment_capex"],
    power_electronics_life=params["capex"]["power_electronics"]["design_life_years"],
    installation_fraction=params["capex"]["installation_and_engineering"]["cost_fraction_of_equipment_capex"],
    pv_kwp=cap["PV_kWp"], bat_kwh=cap["BAT_kWh"], pemfc_kw=cap["PEMFC_kW"],
    pemel_kw=cap["PEMEL_kW"], h2_kg=cap["H2_kg"],
)

annual_damage = {
    "BAT": float(f4["raw_damage_totals"]["bat"]),
    "PEMFC": float(f4["raw_damage_totals"]["fc"]),
    "PEMEL": float(f4["raw_damage_totals"]["el"]),
}
schedule = build_timed_replacement_schedule(
    annual_damage,
    horizon_years=cfg.horizon_years,
    power_electronics_life=cfg.power_electronics_life,
)
annual_energy_mwh = float(f4["annual_delivered_energy_kwh"]) / 1000.0
result = compute_npc_timed(
    cfg, schedule, annual_energy_delivered_mwh=annual_energy_mwh,
    label="C5-H2-500_F5",
)

assert result["replacement_counts"]["PEMFC"] == 2
assert result["replacement_counts"]["BAT"] == 2
assert result["replacement_counts"]["PEMEL"] == 3
assert all(schedule[device][i] < schedule[device][i + 1]
           for device in ["BAT", "PEMFC", "PEMEL"]
           for i in range(len(schedule[device]) - 1))

out_dir = PROJECT_ROOT / "07_results" / "economic"
out_dir.mkdir(parents=True, exist_ok=True)
summary_path = out_dir / "f5_timed_economic_summary.json"
events_path = out_dir / "f5_timed_replacement_schedule.csv"

serializable = {
    "protocol": "F5 exact-time replacement-event discounting",
    "baseline": f4["baseline"],
    "annual_damage": annual_damage,
    "annual_delivered_energy_mwh": annual_energy_mwh,
    "replacement_counts": result["replacement_counts"],
    "npc_cny": result["npc_cny"],
    "npc_million_cny": result["npc_million_cny"],
    "lcoe_cny_per_kwh": result["lcoe_cny_per_kwh"],
    "capex_pv_cny": result["capex_pv_cny"],
    "opex_pv_cny": result["opex_pv_cny"],
    "replacement_pv_cny": result["replacement_pv_cny"],
    "admissible": bool(f4["lpsp"] <= 0.01),
    "admission_failure": (
        None if f4["lpsp"] <= 0.01
        else f"annual LPSP={f4['lpsp']*100:.2f}% exceeds 1%; NPC/LCOE is scenario-only"
    ),
    "schedule_years": schedule,
    "correction": {
        "old_fc_annual_hours": 6462,
        "old_fc_20yr_replacements": 69,
        "old_error": "15-minute steps were treated as hours (dt=0.25 h omitted)",
        "f4_fc_active_hours": f4["fc_operating_hours"],
        "f4_fc_equivalent_full_load_hours": f4["fc_equivalent_full_load_hours"],
    },
    "scope": "scenario economics; literature costs, no downtime/logistics",
}
with open(summary_path, "w", encoding="utf-8") as f:
    json.dump(serializable, f, ensure_ascii=False, indent=2)

with open(events_path, "w", newline="", encoding="utf-8-sig") as f:
    writer = csv.DictWriter(f, fieldnames=["device", "replacement_number", "time_years"])
    writer.writeheader()
    for device, times in schedule.items():
        for number, time_years in enumerate(times, start=1):
            writer.writerow({"device": device, "replacement_number": number,
                             "time_years": round(time_years, 8)})

print("F5 COMPLETE (NOT ADMISSIBLE)" if f4["lpsp"] > 0.01 else "F5 PASS")
print(f"20y replacements BAT/FC/EL={result['replacement_counts']['BAT']}/"
      f"{result['replacement_counts']['PEMFC']}/{result['replacement_counts']['PEMEL']}")
print(f"NPC={result['npc_million_cny']:.3f} million CNY")
print(f"LCOE={result['lcoe_cny_per_kwh']:.3f} CNY/kWh")
print(summary_path)
print(events_path)
