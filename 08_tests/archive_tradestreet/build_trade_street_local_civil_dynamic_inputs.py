from __future__ import annotations

import csv
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(r"D:\matlab\C\base data")
SOURCE = BASE / "34_TradeStreet_15min_building_load_local_civil_primary_UTC_mapping_layer.csv"
CATALOG = BASE / "37_TradeStreet_local_civil_dynamic_degradation_continuous_window_catalog.csv"
CATALOG_SUMMARY = BASE / "38_TradeStreet_local_civil_dynamic_degradation_window_summary.csv"
REGISTRY = BASE / "39_TradeStreet_local_civil_dynamic_degradation_test_window_registry.csv"
REGISTRY_SUMMARY = BASE / "40_TradeStreet_local_civil_dynamic_degradation_test_window_summary.csv"
SIM_SUMMARY = BASE / "44_TradeStreet_local_civil_dynamic_simulation_input_rules_and_summary.csv"
FMT = "%Y-%m-%d %H:%M:%S"
STEP = timedelta(minutes=15)
ELIGIBLE = "eligible_for_building_load_dynamic_input"
TESTS = [
    ("benchmark_24h", 24, 97, "41_TradeStreet_local_civil_dynamic_degradation_benchmark_24h.csv", "45_TradeStreet_local_civil_dynamic_simulation_input_24h.csv"),
    ("benchmark_7d", 168, 673, "42_TradeStreet_local_civil_dynamic_degradation_benchmark_7d.csv", "46_TradeStreet_local_civil_dynamic_simulation_input_7d.csv"),
    ("benchmark_35d", 840, 3361, "43_TradeStreet_local_civil_dynamic_degradation_benchmark_35d.csv", "47_TradeStreet_local_civil_dynamic_simulation_input_35d.csv"),
]


def load(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_summary(path: Path, pairs: list[tuple[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["metric", "value"])
        writer.writerows(pairs)


rows = load(SOURCE)
eligible = [
    row for row in rows
    if row["building_load_dynamic_admission_status"] == ELIGIBLE
    and row["utc_mapping_admission_status"].startswith("eligible_")
]
for row in eligible:
    row["_local"] = datetime.strptime(row["timestamp_local_civil"], FMT)

# Measurement continuity is deliberately defined on the declared primary source
# civil axis.  UTC remains a derived join coordinate; fall-back/spring behavior
# cannot silently alter the source's 15-minute sample ordering.
windows: list[list[dict[str, str]]] = []
current: list[dict[str, str]] = []
for row in eligible:
    if not current or row["_local"] - current[-1]["_local"] == STEP:
        current.append(row)
    else:
        windows.append(current)
        current = [row]
if current:
    windows.append(current)

catalog_rows: list[dict[str, str]] = []
for i, window in enumerate(windows, start=1):
    first, last = window[0], window[-1]
    elapsed_hours = (last["_local"] - first["_local"]).total_seconds() / 3600
    classes = sorted({row["dst_local_time_classification"] for row in window})
    offsets = sorted({row["utc_offset"] for row in window if row["utc_offset"]})
    catalog_rows.append({
        "window_id": f"local_civil_raw_window_{i:04d}",
        "source_layer": SOURCE.name,
        "admission_status_required": ELIGIBLE,
        "timestamp_axis": "timestamp_local_civil",
        "local_civil_start": first["timestamp_local_civil"],
        "local_civil_end": last["timestamp_local_civil"],
        "derived_utc_start": first["timestamp_utc_iso8601"],
        "derived_utc_end": last["timestamp_utc_iso8601"],
        "record_count_15min": str(len(window)),
        "elapsed_hours_local_civil": f"{elapsed_hours:.2f}",
        "elapsed_days_local_civil": f"{elapsed_hours / 24:.6f}",
        "utc_offset_set": ";".join(offsets),
        "dst_local_time_classification_set": ";".join(classes),
        "contains_DST_ambiguous_local_label": "1" if "ambiguous_fall_back_local_time" in classes else "0",
        "contains_imputation": "0",
        "time_grid_rule": "strict_consecutive_15min_local_civil_primary_axis",
        "allowed_use": "dynamic_degradation_ramp_start_stop_coupling_identification",
        "timezone_evidence_status": "physical_consistency_supported_local_civil_interpretation_pending_source_confirmation",
    })
write(CATALOG, catalog_rows)

buckets = Counter()
for row in catalog_rows:
    hours = float(row["elapsed_hours_local_civil"])
    buckets["under_24h" if hours < 24 else "24h_to_under_7d" if hours < 168 else "7d_to_under_30d" if hours < 720 else "30d_or_more"] += 1
longest = max(catalog_rows, key=lambda row: int(row["record_count_15min"]))
write_summary(CATALOG_SUMMARY, [
    ("rule_version", "v4.0_local_civil_primary_dynamic_continuous_window_catalog"),
    ("source_file", SOURCE.name),
    ("timestamp_axis", "timestamp_local_civil"),
    ("continuity_rule", "Adjacent eligible rows must differ by exactly 15 minutes on timestamp_local_civil; this is the declared primary measurement axis"),
    ("derived_utc_policy", "Derived UTC values are retained for joins but are not used to repair, split or extend local-civil measurement continuity"),
    ("source_row_count", str(len(rows))),
    ("source_eligible_dynamic_rows", str(len(eligible))),
    ("window_count", str(len(catalog_rows))),
    ("eligible_rows_reconstructed_from_windows", str(sum(int(row["record_count_15min"]) for row in catalog_rows))),
    ("longest_window_id", longest["window_id"]),
    ("longest_window_record_count_15min", longest["record_count_15min"]),
    ("longest_window_elapsed_hours_local_civil", longest["elapsed_hours_local_civil"]),
    ("longest_window_local_civil_start", longest["local_civil_start"]),
    ("longest_window_local_civil_end", longest["local_civil_end"]),
    ("windows_under_24h", str(buckets["under_24h"])),
    ("windows_24h_to_under_7d", str(buckets["24h_to_under_7d"])),
    ("windows_7d_to_under_30d", str(buckets["7d_to_under_30d"])),
    ("windows_30d_or_more", str(buckets["30d_or_more"])),
    ("windows_containing_DST_ambiguous_local_label", str(sum(row["contains_DST_ambiguous_local_label"] == "1" for row in catalog_rows))),
    ("imputation_policy", "No imputed data; all windows use original raw-complete admitted rows only"),
    ("timezone_evidence_status", "physical_consistency_supported_local_civil_interpretation_pending_source_confirmation"),
])

# Use the longest unambiguous, no-DST-ambiguity window with at least 35 days.
qualified = [
    row for row in catalog_rows
    if int(row["record_count_15min"]) >= 3361
    and row["contains_DST_ambiguous_local_label"] == "0"
]
if not qualified:
    raise ValueError("No >=35-day unambiguous local-civil window available")
selected = max(qualified, key=lambda row: int(row["record_count_15min"]))
selected_start = datetime.strptime(selected["local_civil_start"], FMT)
source_by_local = {row["timestamp_local_civil"]: row for row in eligible}
selected_rows: list[dict[str, str]] = []
current_local = selected_start
for _ in range(int(selected["record_count_15min"])):
    row = source_by_local.get(current_local.strftime(FMT))
    if row is None:
        raise ValueError(f"Catalog segment does not reconstruct from source at {current_local}")
    selected_rows.append(row)
    current_local += STEP

registry_rows: list[dict[str, str]] = []
sim_summary = [
    ("rule_version", "v4.0_local_civil_primary_right_endpoint_15min_average_dynamic_simulation_input"),
    ("source_interpretation", "Original Trade Street timestamp text is primary America/Los_Angeles local civil measurement time; UTC is derived for traceable joins"),
    ("simulation_interval_convention", "For [t_(k-1), t_k] on local civil axis, use right-endpoint observed sample at t_k as preceding-15-minute average power"),
    ("initial_anchor_policy", "First row is an initial anchor only; it creates no simulated interval or energy contribution"),
    ("time_step_hours", "0.25"),
    ("time_step_seconds", "900"),
    ("power_unit", "kW"),
    ("energy_unit", "kWh"),
    ("building_load_boundary", "P_building=P_net+P_pv_model-P_bess; BESS positive is charging load and negative is discharge output"),
    ("pv_model_policy", "Use p_pv_model_kW_nonnegative; raw PV remains traceable"),
    ("imputation_policy", "No imputed data; all intervals originate from eligible_for_building_load_dynamic_input rows"),
    ("timestamp_axis", "timestamp_local_civil"),
    ("timezone_evidence_status", "physical_consistency_supported_local_civil_interpretation_pending_source_confirmation"),
]
for test_id, duration_hours, expected_rows, benchmark_name, simulation_name in TESTS:
    segment = selected_rows[:expected_rows]
    if len(segment) != expected_rows:
        raise ValueError(f"Insufficient rows for {test_id}")
    first, last = segment[0], segment[-1]
    elapsed = (datetime.strptime(last["timestamp_local_civil"], FMT) - datetime.strptime(first["timestamp_local_civil"], FMT)).total_seconds() / 3600
    if elapsed != duration_hours:
        raise ValueError(f"Local civil duration mismatch for {test_id}: {elapsed}")
    write(BASE / benchmark_name, [{key: value for key, value in row.items() if key != "_local"} for row in segment])
    registry_rows.append({
        "test_window_id": test_id,
        "benchmark_design": "nested_duration_benchmark_same_local_civil_start",
        "source_window_id": selected["window_id"],
        "source_catalog_file": CATALOG.name,
        "source_data_file": SOURCE.name,
        "export_file": benchmark_name,
        "timestamp_axis": "timestamp_local_civil",
        "local_civil_start": first["timestamp_local_civil"],
        "local_civil_end": last["timestamp_local_civil"],
        "derived_utc_start": first["timestamp_utc_iso8601"],
        "derived_utc_end": last["timestamp_utc_iso8601"],
        "target_elapsed_hours_local_civil": str(duration_hours),
        "actual_elapsed_hours_local_civil": f"{elapsed:.2f}",
        "record_count_15min": str(len(segment)),
        "inclusive_endpoint_rule": "record_count=elapsed_hours*4+1",
        "contains_DST_ambiguous_local_label": "0",
        "contains_imputation": "0",
        "source_admission_status": ELIGIBLE,
        "selection_rule": "Use first exact-duration segment of the longest >=35-day original-complete admitted local-civil window without ambiguous fall-back labels; nested segments isolate duration effect and are not independent holdout samples",
        "allowed_use": "dynamic_degradation_ramp_start_stop_coupling_identification_duration_benchmark",
        "not_allowed_use": "independent_train_test_generalization_claim",
        "timezone_evidence_status": "physical_consistency_supported_local_civil_interpretation_pending_source_confirmation",
    })
    simulation_rows: list[dict[str, str]] = []
    for index in range(1, len(segment)):
        left, right = segment[index - 1], segment[index]
        local_start = datetime.strptime(left["timestamp_local_civil"], FMT)
        local_end = datetime.strptime(right["timestamp_local_civil"], FMT)
        if local_end - local_start != STEP:
            raise ValueError(f"Non-15-minute local civil gap in {test_id} at {index}")
        p_net = float(right["p_net_raw_kW"])
        p_pv = float(right["p_pv_model_kW_nonnegative"])
        p_bess = float(right["p_bess_raw_kW_positive_charge_negative_discharge"])
        p_load = float(right["p_building_load_candidate_kW_equals_p_net_plus_pv_model_minus_p_bess"])
        if abs(p_load - p_net - p_pv + p_bess) > 1e-9:
            raise ValueError(f"Power closure failure in {test_id} at {index}")
        simulation_rows.append({
            "test_window_id": test_id,
            "simulation_step_index_1based": str(index),
            "interval_start_local_civil": left["timestamp_local_civil"],
            "interval_end_local_civil": right["timestamp_local_civil"],
            "interval_start_utc_derived": left["timestamp_utc_iso8601"],
            "interval_end_utc_derived": right["timestamp_utc_iso8601"],
            "dt_hours": "0.25",
            "dt_seconds": "900",
            "power_sample_timestamp_local_civil": right["timestamp_local_civil"],
            "power_sample_semantics": "right_endpoint_preceding_15min_average_kW_on_local_civil_primary_axis",
            "p_net_kW": right["p_net_raw_kW"],
            "p_pv_model_kW": right["p_pv_model_kW_nonnegative"],
            "p_bess_kW_positive_charge_negative_discharge": right["p_bess_raw_kW_positive_charge_negative_discharge"],
            "p_building_load_kW": right["p_building_load_candidate_kW_equals_p_net_plus_pv_model_minus_p_bess"],
            "e_net_kWh": f"{p_net * 0.25:.12g}",
            "e_pv_model_kWh": f"{p_pv * 0.25:.12g}",
            "e_bess_kWh_positive_charge_negative_discharge": f"{p_bess * 0.25:.12g}",
            "e_building_load_kWh": f"{p_load * 0.25:.12g}",
            "total_outlier_candidate_flag": right["total_outlier_candidate_flag"],
            "pv_outlier_candidate_flag": right["pv_outlier_candidate_flag"],
            "bess_rating_screen_flag": right["bess_rating_screen_flag"],
            "source_admission_status": right["building_load_dynamic_admission_status"],
            "source_timestamp_local_civil": right["timestamp_local_civil"],
            "utc_mapping_admission_status": right["utc_mapping_admission_status"],
            "timezone_evidence_status": right["timezone_evidence_status"],
        })
    write(BASE / simulation_name, simulation_rows)
    sim_summary.extend([
        (f"{test_id}_source_file", benchmark_name),
        (f"{test_id}_simulation_input_file", simulation_name),
        (f"{test_id}_source_anchor_rows", str(len(segment))),
        (f"{test_id}_simulation_steps", str(len(simulation_rows))),
        (f"{test_id}_elapsed_hours_local_civil", f"{len(simulation_rows) * 0.25:.2f}"),
        (f"{test_id}_interval_start_local_civil", simulation_rows[0]["interval_start_local_civil"]),
        (f"{test_id}_interval_end_local_civil", simulation_rows[-1]["interval_end_local_civil"]),
    ])

write(REGISTRY, registry_rows)
write_summary(REGISTRY_SUMMARY, [
    ("rule_version", "v4.0_local_civil_primary_nested_duration_benchmark_window_freeze"),
    ("source_catalog_file", CATALOG.name),
    ("selected_source_window_id", selected["window_id"]),
    ("selected_source_window_record_count_15min", selected["record_count_15min"]),
    ("selected_source_window_elapsed_hours_local_civil", selected["elapsed_hours_local_civil"]),
    ("source_window_selection", "Longest >=35-day original-complete admitted local-civil window without ambiguous fall-back labels"),
    ("benchmark_design", "Nested 24h, 7d and 35d segments share one local-civil start to isolate duration effects; not independent holdout samples"),
    ("benchmark_count", str(len(registry_rows))),
    ("imputation_policy", "No imputed values; all exported rows require eligible_for_building_load_dynamic_input"),
    ("dst_policy", "Selected benchmark has no ambiguous fall-back local labels; spring-forward nonexistent labels are never mapped or imputed"),
    ("timestamp_axis", "timestamp_local_civil"),
    ("timezone_evidence_status", "physical_consistency_supported_local_civil_interpretation_pending_source_confirmation"),
])
write_summary(SIM_SUMMARY, sim_summary)

print(f"eligible_rows={len(eligible)}; windows={len(catalog_rows)}")
print(f"selected={selected['window_id']}; rows={selected['record_count_15min']}; local={selected['local_civil_start']}..{selected['local_civil_end']}")
for row in registry_rows:
    print(f"{row['test_window_id']}; rows={row['record_count_15min']}; local={row['local_civil_start']}..{row['local_civil_end']}")
