from __future__ import annotations

import csv
from pathlib import Path

BASE = Path(r"D:\matlab\C\base data")
REGISTER = "50_TradeStreet_dynamic_model_parameter_evidence_and_run_gate.csv"
SUMMARY = "51_TradeStreet_dynamic_model_readiness_summary.csv"
REQUIRED_COLUMNS = [
    "parameter_or_evidence_item", "model_subsystem", "required_for", "status", "value_or_definition",
    "evidence_source", "allowed_use_now", "blocking_reason_if_unavailable", "required_resolution",
]
VALID_STATUS = {"available_from_verified_data", "partial_verified_evidence", "missing_source_evidence", "missing_study_design_decision"}

with (BASE / REGISTER).open("r", encoding="utf-8-sig", newline="") as handle:
    rows = list(csv.DictReader(handle))
with (BASE / SUMMARY).open("r", encoding="utf-8-sig", newline="") as handle:
    summary = {row["metric"]: row["value"] for row in csv.DictReader(handle)}
errors = []
if list(rows[0]) != REQUIRED_COLUMNS:
    errors.append("register_schema")
if len(rows) != 13:
    errors.append(f"register_count={len(rows)}")
if any(row["status"] not in VALID_STATUS for row in rows):
    errors.append("invalid_status")
if any(not row["parameter_or_evidence_item"].strip() or not row["evidence_source"].strip() or not row["allowed_use_now"].strip() for row in rows):
    errors.append("incomplete_register_row")
for row in rows:
    if row["status"] != "available_from_verified_data":
        if not row["blocking_reason_if_unavailable"].strip() or not row["required_resolution"].strip():
            errors.append(f"unresolved_item_missing_gate={row['parameter_or_evidence_item']}")
    if row["status"] == "missing_source_evidence" and row["value_or_definition"] not in {"not populated", "not inferable from a finite power window", "not inferable from electrical time series", "PV time series is available; controllable curtailment boundary is not"}:
        errors.append(f"unexpected_missing_value={row['parameter_or_evidence_item']}")

counts = {status: sum(row["status"] == status for row in rows) for status in VALID_STATUS}
expected = {
    "verified_interface_file": "48_TradeStreet_local_civil_dynamic_model_input_interface_35d.csv",
    "verified_contract_file": "49_TradeStreet_local_civil_dynamic_model_preparation_contract.csv",
    "interval_count": "3360",
    "available_verified_items": str(counts["available_from_verified_data"]),
    "partial_verified_evidence_items": str(counts["partial_verified_evidence"]),
    "missing_source_evidence_items": str(counts["missing_source_evidence"]),
    "missing_study_design_decisions": str(counts["missing_study_design_decision"]),
    "observed_BESS_rating_screen_evidence": "available: 200 kW for observed Trade Street BESS data screening only; not sufficient to define redesigned-dispatch limits",
    "redesigned_PV_hydrogen_BESS_dispatch_readiness": "blocked: hydrogen asset parameters, BESS efficiency/design constraints and operating-mode design decision are not supplied",
    "battery_degradation_readiness": "blocked: chemistry, usable capacity, SOC/temperature history and calibrated degradation law are not supplied",
}
for key, value in expected.items():
    if summary.get(key) != value:
        errors.append(f"summary {key}")

print("STRICT_PARAMETER_EVIDENCE_REGISTER_VALIDATION")
print(f"rows={len(rows)}; status_counts={counts}")
print(f"errors={errors}")
raise SystemExit(1 if errors else 0)
