from __future__ import annotations

import csv
from pathlib import Path

BASE = Path(r"D:\matlab\C\base data")
REGISTER = BASE / "52_TradeStreet_offgrid_optimization_study_design_register.csv"
SUMMARY = BASE / "53_TradeStreet_offgrid_optimization_design_gate_summary.csv"

with REGISTER.open("r", encoding="utf-8-sig", newline="") as handle:
    rows = list(csv.DictReader(handle))
with SUMMARY.open("r", encoding="utf-8-sig", newline="") as handle:
    summary = {row["metric"]: row["value"] for row in csv.DictReader(handle)}
errors = []
expected_values = [
    ("Operating mode", "off_grid_optimization", "confirmed"),
    ("Objective priority 1", "minimize_battery_degradation", "conditionally_confirmed"),
    ("Objective priority 2", "minimize_total_cost", "confirmed"),
    ("Objective priority 3", "minimize_unmet_load", "requires_reliability_gate"),
    ("Objective priority 4", "minimize_PV_curtailment", "confirmed"),
    ("Objective priority 5", "minimize_carbon_emissions", "conditionally_confirmed"),
]
if len(rows) != len(expected_values):
    errors.append(f"row_count={len(rows)}")
for row, expected in zip(rows, expected_values):
    if (row["design_item"], row["user_confirmed_value"], row["status"]) != expected:
        errors.append(f"row={row.get('design_item')}")
if summary.get("operating_mode") != "off_grid_optimization":
    errors.append("operating_mode")
if "Unmet load cannot remain only a third-priority soft objective" not in summary.get("critical_consistency_finding", ""):
    errors.append("reliability_gate")
if "Choose the off-grid reliability criterion" not in summary.get("next_required_user_feedback", ""):
    errors.append("feedback_gate")
print("STRICT_OFFGRID_STUDY_DESIGN_VALIDATION")
print(f"rows={len(rows)}; errors={errors}")
raise SystemExit(1 if errors else 0)
