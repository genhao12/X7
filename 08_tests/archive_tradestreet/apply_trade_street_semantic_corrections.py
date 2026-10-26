import csv
import math
import os
from collections import Counter
from datetime import datetime, timedelta

ROOT = r"D:\matlab\C"
BASE = os.path.join(ROOT, "base data")
RAW_BATTERY = os.path.join(ROOT, "UCSD-Microgrid-Database", "UCSD-Microgrid-Database-master", "Data Files", "Trade Street Off-campus Microgrid", "TradeStreetBattery.csv")
TS_FORMAT = "%Y-%m-%d %H:%M:%S"
RAW_TS_FORMAT = "%m/%d/%Y %H:%M"


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path, fields, rows):
    with open(path, "w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main():
    pv_source = read_csv(os.path.join(BASE, "03_TradeStreetPV_cleaning_layer.csv"))
    total_source = read_csv(os.path.join(BASE, "02_TradeStreetTotal_cleaning_layer.csv"))

    pv_output = []
    for row in pv_source:
        raw = row["p_pv_raw_kW"]
        model_value = ""
        adjustment_status = "not_applicable_not_numeric"
        model_eligibility = "not_eligible"
        if row["model_input_status"].startswith("eligible"):
            value = float(raw)
            if value < 0:
                model_value = 0.0
                adjustment_status = "pv_negative_offset_corrected_to_zero"
            else:
                model_value = value
                adjustment_status = "raw_nonnegative_retained"
            model_eligibility = "eligible_for_physical_pv_model"
        pv_output.append({
            "timestamp_local_civil_unresolved": row["timestamp_local_civil_unresolved"],
            "p_pv_raw_kW": raw,
            "p_pv_model_kW_nonnegative": model_value,
            "pv_model_adjustment_status": adjustment_status,
            "raw_record_count": row["raw_record_count"],
            "selected_source_row_for_audit": row["selected_source_row_for_audit"],
            "source_power_value_status": row["power_value_status"],
            "source_duplicate_resolution": row["duplicate_resolution"],
            "dst_calendar_status": row["dst_calendar_status"],
            "timezone_status": row["timezone_status"],
            "model_input_status": model_eligibility,
            "exclusion_reason": row["exclusion_reason"],
        })

    battery_groups = {}
    for source_row, row in enumerate(read_csv(RAW_BATTERY), start=2):
        timestamp = datetime.strptime(row["DateTime"].strip(), RAW_TS_FORMAT).strftime(TS_FORMAT)
        battery_groups.setdefault(timestamp, []).append((source_row, row["RealPower"].strip()))

    battery_output = []
    for timestamp in sorted(battery_groups):
        records = battery_groups[timestamp]
        is_unique = len(records) == 1
        raw_value = records[0][1]
        numeric = None
        try:
            numeric = float(raw_value)
            numeric_valid = math.isfinite(numeric)
        except ValueError:
            numeric_valid = False
        battery_output.append({
            "timestamp_local_civil_unresolved": timestamp,
            "p_bess_raw_kW_positive_charge_negative_discharge": raw_value if numeric_valid and is_unique else "",
            "raw_record_count": len(records),
            "selected_source_row_for_audit": records[0][0],
            "bess_power_value_status": "valid_numeric" if numeric_valid and is_unique else ("duplicate_timestamp_unusable" if not is_unique else "missing_nan_or_nonnumeric"),
            "bess_sign_convention": "positive_charge_negative_discharge_per_UCSD_paper",
            "model_input_status": "eligible_for_bess_boundary_closure" if numeric_valid and is_unique else "not_eligible",
            "exclusion_reason": "" if numeric_valid and is_unique else ("duplicate_timestamp_requires_source_level_resolution" if not is_unique else "missing_raw_power"),
        })

    total_by_time = {row["timestamp_local_civil_unresolved"]: row for row in total_source}
    pv_by_time = {row["timestamp_local_civil_unresolved"]: row for row in pv_output}
    bess_by_time = {row["timestamp_local_civil_unresolved"]: row for row in battery_output}

    start = datetime.strptime("2016-08-31 09:30:00", TS_FORMAT)
    end = datetime.strptime("2019-07-26 11:45:00", TS_FORMAT)
    joined = []
    time = start
    counts = Counter()
    closure_residuals = []
    while time <= end:
        timestamp = time.strftime(TS_FORMAT)
        total = total_by_time.get(timestamp)
        pv = pv_by_time.get(timestamp)
        bess = bess_by_time.get(timestamp)
        p_net = "" if total is None else total["p_net_raw_kW"]
        p_pv_raw = "" if pv is None else pv["p_pv_raw_kW"]
        p_pv_model = "" if pv is None else pv["p_pv_model_kW_nonnegative"]
        p_bess = "" if bess is None else bess["p_bess_raw_kW_positive_charge_negative_discharge"]

        base_eligible = total is not None and pv is not None and total["model_input_status"].startswith("eligible") and pv["model_input_status"].startswith("eligible")
        bess_eligible = bess is not None and bess["model_input_status"].startswith("eligible")
        pre_bess_candidate = ""
        building_candidate = ""
        building_status = "not_eligible"
        building_reason = ""

        if base_eligible:
            pre_bess_candidate = float(p_net) + float(p_pv_model)
        if base_eligible and bess_eligible:
            building_candidate = float(p_net) + float(p_pv_model) - float(p_bess)
            building_status = "eligible_for_building_load_model"
            closure_residuals.append(abs(building_candidate - float(p_net) - float(p_pv_model) + float(p_bess)))
        elif base_eligible:
            building_status = "not_eligible_bess_boundary_unresolved"
            building_reason = "missing_or_invalid_bess_measurement"
        elif total is None:
            building_reason = "missing_total_15min_grid_timestamp"
        elif pv is None:
            building_reason = "missing_pv_15min_grid_timestamp"
        else:
            building_reason = ";".join(value for value in [total["exclusion_reason"], pv["exclusion_reason"]] if value)

        counts[building_status] += 1
        joined.append({
            "timestamp_local_civil_unresolved": timestamp,
            "p_net_raw_kW": p_net,
            "p_pv_raw_kW": p_pv_raw,
            "p_pv_model_kW_nonnegative": p_pv_model,
            "p_bess_raw_kW_positive_charge_negative_discharge": p_bess,
            "p_net_plus_pv_model_pre_bess_candidate_kW": pre_bess_candidate,
            "p_building_load_candidate_kW_equals_p_net_plus_pv_model_minus_p_bess": building_candidate,
            "p_net_status": "missing_15min_grid_timestamp" if total is None else total["model_input_status"],
            "p_pv_status": "missing_15min_grid_timestamp" if pv is None else pv["model_input_status"],
            "p_bess_status": "missing_15min_grid_timestamp" if bess is None else bess["model_input_status"],
            "building_load_model_status": building_status,
            "timezone_status": "local_civil_time_unresolved_no_UTC_conversion",
            "exclusion_reason": building_reason,
        })
        time += timedelta(minutes=15)

    write_csv(
        os.path.join(BASE, "11_TradeStreetPV_model_semantic_layer.csv"),
        list(pv_output[0].keys()),
        pv_output,
    )
    write_csv(
        os.path.join(BASE, "12_TradeStreetBESS_boundary_layer.csv"),
        list(battery_output[0].keys()),
        battery_output,
    )
    write_csv(
        os.path.join(BASE, "13_TradeStreet_15min_building_load_semantic_candidate.csv"),
        list(joined[0].keys()),
        joined,
    )

    summary_rows = [
        ["rule_version", "v1.1"],
        ["pv_raw_field", "p_pv_raw_kW retained without alteration"],
        ["pv_model_field", "p_pv_model_kW_nonnegative = max(p_pv_raw_kW, 0) only for unique numeric source rows"],
        ["pv_negative_adjustment_rule", "All valid raw PV values below 0 kW are set to 0 kW only in p_pv_model_kW_nonnegative and marked pv_negative_offset_corrected_to_zero"],
        ["pv_negative_adjustment_count", sum(row["pv_model_adjustment_status"] == "pv_negative_offset_corrected_to_zero" for row in pv_output)],
        ["bess_sign_convention", "Positive BESS power = charging load; negative BESS power = discharging generation, per UCSD paper"],
        ["pre_bess_candidate_definition", "P_net + P_PV_model; not a pure building load because BESS is behind the meter"],
        ["building_load_definition", "P_building_candidate = P_net + P_PV_model - P_BESS"],
        ["building_load_eligible_rows", counts["eligible_for_building_load_model"]],
        ["building_load_bess_unresolved_rows", counts["not_eligible_bess_boundary_unresolved"]],
        ["building_load_not_eligible_rows", counts["not_eligible"]],
        ["building_load_closure_max_residual_kW", max(closure_residuals) if closure_residuals else ""],
        ["modeling_constraint", "Use p_building_load_candidate only where building_load_model_status=eligible_for_building_load_model; do not use pre-BESS candidate as pure building load"],
    ]
    with open(os.path.join(BASE, "14_TradeStreet_semantic_correction_summary.csv"), "w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["metric", "value"])
        writer.writerows(summary_rows)


if __name__ == "__main__":
    main()
