from __future__ import annotations

import csv
from pathlib import Path

BASE = Path(r"D:\matlab\C\base data")
SPEC_OUT = BASE / "54_TradeStreet_phaseA_and_20year_evaluation_rule_freeze.csv"
GATE_OUT = BASE / "55_TradeStreet_formal_evaluation_data_and_parameter_gate.csv"

spec_rows = [
    {"category": "load_reliability", "item": "Critical-load definition", "status": "frozen_user_design", "rule_or_value": "All reconstructed building load is critical load; no noncritical-load fraction or separate allocation", "unit": "policy", "model_implication": "E_critical=E_load", "open_gate": "none"},
    {"category": "load_reliability", "item": "Reliability hard constraint", "status": "frozen_user_design", "rule_or_value": "Critical-load satisfaction=1-E_uns/E_load>=0.99; LPSP=E_uns/E_load<=0.01; the two are identical under all-load-critical definition", "unit": "fraction", "model_implication": "one energy-based reliability constraint; retain both labels in reporting", "open_gate": "none"},
    {"category": "phase_A", "item": "35-day data role", "status": "frozen_user_design", "rule_or_value": "Use 35-day 15-minute window only for operating feasibility, boundary behavior and code tests", "unit": "scope", "model_implication": "Do not calculate 20-year NPC, LCOE, degradation or replacements from this window", "open_gate": "none"},
    {"category": "formal_evaluation", "item": "Native annual data requirement", "status": "frozen_user_design", "rule_or_value": "Use native 15-minute complete actual years; nonleap year=35,040 points and leap year=35,136 points", "unit": "points/year", "model_implication": "select at least three actual meteorology-load years before 20-year simulation", "open_gate": "existing dynamic-admission data contains zero complete actual years"},
    {"category": "formal_evaluation", "item": "20-year chronology", "status": "frozen_user_design", "rule_or_value": "Cycle at least three selected actual meteorology-load years in fixed order to 20 years; transfer end-of-year SOC, hydrogen inventory and degradation states continuously", "unit": "20 years", "model_implication": "no annual reset of physical or health states", "open_gate": "requires complete selected annual series"},
    {"category": "hydrogen_storage", "item": "Leakage continuous-time rate", "status": "frozen_user_design", "rule_or_value": "lambda=-ln(0.9995) per day", "unit": "1/day", "model_implication": "use exact exponential state propagation", "open_gate": "none"},
    {"category": "hydrogen_storage", "item": "15-minute retention factor", "status": "frozen_user_design", "rule_or_value": "0.9995^(1/96)=0.9999947903777193", "unit": "fraction/15min", "model_implication": "do not use linear leakage approximation", "open_gate": "none"},
    {"category": "hydrogen_storage", "item": "Exact discrete inventory update", "status": "frozen_user_design", "rule_or_value": "m_(t+1)=m_t*exp(-lambda*dt)+(q/lambda)*(1-exp(-lambda*dt)); q=m_dot_EL-m_dot_FC constant over time step", "unit": "kg", "model_implication": "apply bounds after update and stop electrolyzer at m_max", "open_gate": "PEM production/consumption maps required"},
    {"category": "performance_degradation", "item": "Frozen end-of-life performance criteria", "status": "frozen_user_design", "rule_or_value": "BESS EOL SOH=0.80; PEMFC rated-point voltage degradation=10%; PEMEL specific hydrogen energy consumption increase=10%", "unit": "fraction", "model_implication": "replacement/health trigger definitions", "open_gate": "device-specific degradation laws and coefficients required"},
    {"category": "operating_constraints", "item": "Frozen equipment and auxiliary rules", "status": "frozen_user_design", "rule_or_value": "PEMFC and PEMEL minimum stable load=15% rated power; hydrogen leakage=0.05%/day; auxiliary power=2% of total installed rated power", "unit": "fraction", "model_implication": "include auxiliary demand in electrical balance; define total installed rated power explicitly", "open_gate": "clarify whether auxiliary denominator includes PV DC, PV converter AC, BESS converter, EL and FC ratings"},
    {"category": "initial_states", "item": "Frozen normal and low-initial-state scenarios", "status": "frozen_user_design", "rule_or_value": "Normal initial battery SOC and hydrogen SOC=0.60; low initial battery SOC and hydrogen SOC=0.20", "unit": "fraction", "model_implication": "run scenario pair after SOC bounds and storage capacities are frozen", "open_gate": "battery SOC bounds required to convert SOC to energy"},
    {"category": "economics", "item": "Frozen financial assumptions", "status": "frozen_user_design", "rule_or_value": "Evaluation period=20 years; real discount rate=6%; real price escalation=0%; residual value=0", "unit": "years; fraction/year", "model_implication": "discounted cash-flow convention is fixed", "open_gate": "CAPEX, replacement and O&M values required; define NPC base-year and LCOE energy denominator"},
    {"category": "parameter_gate", "item": "Parameters prohibited from silent imputation", "status": "hard_gate", "rule_or_value": "Battery chemistry, SOC bounds, charge/discharge efficiency, temperature and degradation coefficients; FC/EL efficiency maps, startup and ramp rates; PV converter rating; CAPEX, replacement cost and O&M", "unit": "parameter set", "model_implication": "formal degradation/economic evaluation is prohibited until each item has a traceable single source in parameter table", "open_gate": "all listed evidence items"},
]

with SPEC_OUT.open("w", encoding="utf-8", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=list(spec_rows[0]))
    writer.writeheader()
    writer.writerows(spec_rows)

gate_rows = [
    {"gate_id": "A0", "evaluation_scope": "Phase A 35-day feasibility/boundary/code test", "status": "permitted_with_existing_data", "required_evidence": "48 and 54 frozen rules; unverified device efficiency/degradation/economic parameters may not be used", "current_finding": "35-day interface is verified for code/constraint testing only", "next_required_action": "build only symbolic/parameterized state-equation code and unit tests after auxiliary-power denominator is clarified"},
    {"gate_id": "B1", "evaluation_scope": "Formal 20-year reliability chronology", "status": "blocked_no_complete_actual_years", "required_evidence": "at least three native complete actual meteorology-load years with each year fully admitted", "current_finding": "2016=6,493 admitted points of 35,136; 2017=33,011 of 35,040; 2018=24,793 through 2018-09-19; zero complete actual years", "next_required_action": "obtain additional raw data or define and validate a separate complete-year reconstruction protocol; do not fill silently"},
    {"gate_id": "B2", "evaluation_scope": "Formal battery/PEM degradation and replacements", "status": "blocked_parameter_sources", "required_evidence": "manufacturer or single cited source for chemistry, SOC limits, efficiencies, temperatures, aging/efficiency-degradation laws and coefficients", "current_finding": "EOL thresholds are frozen but laws/coefficients are absent", "next_required_action": "select equipment/technology source and populate traceable parameter table"},
    {"gate_id": "B3", "evaluation_scope": "Formal 20-year NPC/LCOE", "status": "blocked_parameter_sources_and_chronology", "required_evidence": "complete-year chronology plus CAPEX, replacements, O&M and LCOE denominator convention", "current_finding": "discount rate/escalation/residual value are frozen; cost amounts and annual chronology unavailable", "next_required_action": "complete B1 and collect a single traceable economic source per component"},
    {"gate_id": "B4", "evaluation_scope": "Formal off-grid lexicographic optimization", "status": "blocked_open_definitions_and_parameters", "required_evidence": "all B1-B3 items plus PV converter rating and auxiliary-power denominator", "current_finding": "objective hierarchy is frozen; physical state and cost inputs are incomplete", "next_required_action": "freeze all parameter sources, then formulate stages 1-5 without relaxing earlier-stage optima"},
]
with GATE_OUT.open("w", encoding="utf-8", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=list(gate_rows[0]))
    writer.writeheader()
    writer.writerows(gate_rows)

print(f"rule_rows={len(spec_rows)}; gate_rows={len(gate_rows)}")
