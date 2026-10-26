from __future__ import annotations

import csv
from pathlib import Path

BASE = Path(r"D:\matlab\C\base data")
OUT = BASE / "52_TradeStreet_offgrid_optimization_study_design_register.csv"
SUMMARY = BASE / "53_TradeStreet_offgrid_optimization_design_gate_summary.csv"

rows = [
    {
        "design_item": "Operating mode",
        "user_confirmed_value": "off_grid_optimization",
        "status": "confirmed",
        "model_implication": "Grid import and grid export variables are fixed to zero; supply must come from PV, BESS and hydrogen subsystem, subject to declared unmet-load policy.",
        "validation_note": "Consistent with an islanded microgrid study; Trade Street observed net-load data remain an exogenous reconstruction input, not evidence that historic operation was off-grid.",
        "required_follow_up": "",
    },
    {
        "design_item": "Objective priority 1",
        "user_confirmed_value": "minimize_battery_degradation",
        "status": "conditionally_confirmed",
        "model_implication": "Use as first lexicographic objective only inside a reliability-feasible set.",
        "validation_note": "Without a reliability hard constraint, minimizing degradation can favor withholding BESS energy and increasing unmet load.",
        "required_follow_up": "Declare the reliability hard constraint or explicit allowable unmet-load limit before optimization formulation.",
    },
    {
        "design_item": "Objective priority 2", "user_confirmed_value": "minimize_total_cost", "status": "confirmed", "model_implication": "Second lexicographic objective after the reliability-feasible and degradation-minimal solution set is established.", "validation_note": "Requires cost definitions for CAPEX/OPEX, fuel, replacement, curtailment and unmet-load treatment.", "required_follow_up": "Provide cost scope and financial assumptions when parameter evidence is supplied.",
    },
    {
        "design_item": "Objective priority 3", "user_confirmed_value": "minimize_unmet_load", "status": "requires_reliability_gate", "model_implication": "Cannot safely remain only a lower-priority soft objective in an off-grid design if priority 1 is battery degradation.", "validation_note": "Must be elevated to a hard feasibility requirement or bounded metric before lexicographic objectives 1-5 are solved.", "required_follow_up": "Choose a reliability criterion: zero unserved energy, maximum EENS, maximum LOLP, or critical/noncritical load-service rule.",
    },
    {
        "design_item": "Objective priority 4", "user_confirmed_value": "minimize_PV_curtailment", "status": "confirmed", "model_implication": "Fourth lexicographic objective after reliability, degradation, cost and residual unmet-load criterion are resolved.", "validation_note": "Requires PV curtailment feasibility and inverter constraints.", "required_follow_up": "Provide PV/inverter design constraints.",
    },
    {
        "design_item": "Objective priority 5", "user_confirmed_value": "minimize_carbon_emissions", "status": "conditionally_confirmed", "model_implication": "In strict off-grid operation, operational grid-electricity emissions are zero; remaining emissions must be explicitly scoped (e.g., hydrogen supply, backup fuel, lifecycle emissions).", "validation_note": "A carbon objective is undefined until its boundary is stated.", "required_follow_up": "Declare whether carbon means operational fuel emissions, purchased-hydrogen emissions, lifecycle emissions, or another documented boundary.",
    },
]
with OUT.open("w", encoding="utf-8", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)

summary = [
    ("rule_version", "v1.0_offgrid_optimization_study_design_register"),
    ("operating_mode", "off_grid_optimization"),
    ("user_objective_order", "1 battery_degradation; 2 total_cost; 3 unmet_load; 4 PV_curtailment; 5 carbon_emissions"),
    ("critical_consistency_finding", "Unmet load cannot remain only a third-priority soft objective when battery degradation is first; reliability must be a hard constraint or explicitly bounded before lexicographic optimization."),
    ("optimization_not_yet_permitted", "No state equation, dispatch optimization or degradation calculation may be executed until reliability criterion and missing component parameters are traceably resolved."),
    ("next_required_user_feedback", "Choose the off-grid reliability criterion and its numerical threshold; then provide BESS and hydrogen-system parameter evidence."),
]
with SUMMARY.open("w", encoding="utf-8", newline="") as handle:
    writer = csv.writer(handle)
    writer.writerow(["metric", "value"])
    writer.writerows(summary)

print("created_offgrid_design_register=6")
