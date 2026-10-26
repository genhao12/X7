import csv
import math
import os
from collections import Counter
from datetime import datetime, timedelta

ROOT = r"D:\matlab\C"
RAW_DIR = os.path.join(ROOT, "UCSD-Microgrid-Database", "UCSD-Microgrid-Database-master", "Data Files", "Trade Street Off-campus Microgrid")
OUT_DIR = os.path.join(ROOT, "base data")
INPUT_FORMAT = "%m/%d/%Y %H:%M"
OUTPUT_FORMAT = "%Y-%m-%d %H:%M:%S"


def second_sunday_of_march(year):
    day = datetime(year, 3, 8)
    return day + timedelta(days=(6 - day.weekday()) % 7)


def first_sunday_of_november(year):
    day = datetime(year, 11, 1)
    return day + timedelta(days=(6 - day.weekday()) % 7)


def dst_calendar_status(timestamp):
    spring = second_sunday_of_march(timestamp.year)
    fall = first_sunday_of_november(timestamp.year)
    if timestamp.date() == spring.date() and timestamp.hour == 2:
        return "DST_spring_forward_candidate"
    if timestamp.date() == fall.date() and timestamp.hour == 1:
        return "DST_fall_back_candidate"
    return "not_DST_transition_candidate"


def parse_power(value):
    value = value.strip()
    if not value:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def load_raw(filename, role):
    path = os.path.join(RAW_DIR, filename)
    records = []
    with open(path, encoding="utf-8-sig", newline="") as file:
        for source_row, row in enumerate(csv.DictReader(file), start=2):
            timestamp = datetime.strptime(row["DateTime"].strip(), INPUT_FORMAT)
            raw_power = row["RealPower"].strip()
            records.append({
                "timestamp": timestamp,
                "raw_power": raw_power,
                "numeric_power": parse_power(raw_power),
                "source_row": source_row,
                "role": role,
            })
    return records


def clean_stream(records, stream_name, power_column):
    grouped = {}
    for record in records:
        grouped.setdefault(record["timestamp"], []).append(record)

    cleaned = {}
    audit_rows = []
    for timestamp in sorted(grouped):
        group = sorted(grouped[timestamp], key=lambda item: item["source_row"])
        numeric_records = [item for item in group if item["numeric_power"] is not None]
        raw_record_count = len(group)
        duplicate = raw_record_count > 1
        selected = group[0]
        values = {item["numeric_power"] for item in numeric_records}

        if duplicate:
            duplicate_resolution = "not_resolved_retained_for_audit"
            power_value_status = "duplicate_timestamp_unusable"
            model_input_status = "not_eligible"
            exclusion_reason = "duplicate_timestamp_requires_source_level_resolution"
            selected_power = ""
        elif selected["numeric_power"] is None:
            duplicate_resolution = "not_applicable"
            power_value_status = "missing_nan_or_nonnumeric"
            model_input_status = "not_eligible"
            exclusion_reason = "missing_raw_power"
            selected_power = ""
        else:
            duplicate_resolution = "not_applicable"
            power_value_status = "valid_numeric"
            model_input_status = "eligible_pending_timezone_resolution"
            exclusion_reason = ""
            selected_power = selected["numeric_power"]

        transition_status = dst_calendar_status(timestamp)
        # Source paper and supplied scripts do not document a timezone or DST policy.
        # Therefore transition candidates are flagged for audit only, not excluded automatically.

        cleaned[timestamp] = {
            "timestamp": timestamp,
            "power": selected_power,
            "raw_record_count": raw_record_count,
            "selected_source_row": selected["source_row"],
            "power_value_status": power_value_status,
            "duplicate_resolution": duplicate_resolution,
            "dst_calendar_status": transition_status,
            "model_input_status": model_input_status,
            "exclusion_reason": exclusion_reason,
        }
        audit_rows.append([
            timestamp.strftime(OUTPUT_FORMAT),
            selected_power,
            raw_record_count,
            selected["source_row"],
            power_value_status,
            duplicate_resolution,
            transition_status,
            "local_civil_time_unresolved_no_UTC_conversion",
            model_input_status,
            exclusion_reason,
        ])

    output = os.path.join(OUT_DIR, stream_name)
    with open(output, "w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow([
            "timestamp_local_civil_unresolved",
            power_column,
            "raw_record_count",
            "selected_source_row_for_audit",
            "power_value_status",
            "duplicate_resolution",
            "dst_calendar_status",
            "timezone_status",
            "model_input_status",
            "exclusion_reason",
        ])
        writer.writerows(audit_rows)
    return cleaned, output


def stream_status(record, prefix):
    if record is None:
        return "missing_15min_grid_timestamp", f"missing_{prefix}_15min_grid_timestamp"
    return record["model_input_status"], record["exclusion_reason"]


def main():
    total_raw = load_raw("TradeStreetTotal.csv", "net_load")
    pv_raw = load_raw("TradeStreetPV.csv", "pv")

    total_clean, total_output = clean_stream(total_raw, "02_TradeStreetTotal_cleaning_layer.csv", "p_net_raw_kW")
    pv_clean, pv_output = clean_stream(pv_raw, "03_TradeStreetPV_cleaning_layer.csv", "p_pv_raw_kW")

    start = max(min(total_clean), min(pv_clean))
    end = min(max(total_clean), max(pv_clean))
    grid_rows = []
    timestamp = start
    status_count = Counter()
    while timestamp <= end:
        total = total_clean.get(timestamp)
        pv = pv_clean.get(timestamp)
        total_status, total_reason = stream_status(total, "total")
        pv_status, pv_reason = stream_status(pv, "pv")
        transition_status = dst_calendar_status(timestamp)
        eligible = total_status == "eligible_pending_timezone_resolution" and pv_status == "eligible_pending_timezone_resolution"
        load_candidate = ""
        load_status = "not_eligible"
        reason = ";".join(value for value in [total_reason, pv_reason] if value)
        if eligible:
            load_candidate = float(total["power"]) + float(pv["power"])
            load_status = "eligible_pending_timezone_resolution"
        # Keep DST transition candidates usable if the underlying source values pass
        # all data-quality checks. The source does not document timezone/DST semantics.

        status_count[load_status] += 1
        grid_rows.append([
            timestamp.strftime(OUTPUT_FORMAT),
            "" if total is None else total["power"],
            "" if pv is None else pv["power"],
            load_candidate,
            total_status,
            pv_status,
            transition_status,
            "local_civil_time_unresolved_no_UTC_conversion",
            load_status,
            reason,
        ])
        timestamp += timedelta(minutes=15)

    reconstruction_output = os.path.join(OUT_DIR, "04_TradeStreet_15min_netload_pv_load_candidate.csv")
    with open(reconstruction_output, "w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow([
            "timestamp_local_civil_unresolved",
            "p_net_raw_kW",
            "p_pv_raw_kW",
            "p_load_candidate_kW_equals_p_net_plus_p_pv",
            "p_net_status",
            "p_pv_status",
            "dst_calendar_status",
            "timezone_status",
            "model_input_status",
            "exclusion_reason",
        ])
        writer.writerows(grid_rows)

    def raw_summary(records):
        grouped = Counter(record["timestamp"] for record in records)
        return {
            "raw_records": len(records),
            "unique_timestamps": len(grouped),
            "duplicate_timestamp_count": sum(count > 1 for count in grouped.values()),
            "duplicate_extra_record_count": sum(count - 1 for count in grouped.values() if count > 1),
            "missing_nan_or_nonnumeric_count": sum(record["numeric_power"] is None for record in records),
        }

    summary = raw_summary(total_raw)
    pv_summary = raw_summary(pv_raw)
    quality_output = os.path.join(OUT_DIR, "05_TradeStreet_unified_cleaning_rules_and_summary.csv")
    rows = [
        ["rule_version", "v1.0"],
        ["time_grid", "15-minute local civil timestamp labels; no inferred UTC conversion"],
        ["timezone_policy", "Timezone/DST metadata absent from source; timestamps retained without UTC conversion"],
        ["DST_policy", "US DST calendar candidates are flagged for audit only; they are not automatically treated as gaps or excluded because source timezone/DST semantics are undocumented"],
        ["duplicate_policy", "Any duplicate timestamp is retained in audit source but excluded from model-ready stream; no arbitrary averaging or row selection"],
        ["missing_policy", "NaN, blank, and absent 15-minute timestamps remain missing and are excluded; no interpolation, forward fill, smoothing, or zero fill"],
        ["net_load_definition", "TradeStreetTotal treated as P_net = P_load - P_PV based on dataset documentation"],
        ["load_reconstruction", "Candidate P_load = P_net + P_PV only when both inputs are unique, numeric, and share the same timestamp; DST candidates remain separately flagged pending source timezone confirmation"],
        ["total_raw_records", summary["raw_records"]],
        ["total_unique_timestamps", summary["unique_timestamps"]],
        ["total_duplicate_timestamp_count", summary["duplicate_timestamp_count"]],
        ["total_duplicate_extra_record_count", summary["duplicate_extra_record_count"]],
        ["total_missing_nan_or_nonnumeric_count", summary["missing_nan_or_nonnumeric_count"]],
        ["pv_raw_records", pv_summary["raw_records"]],
        ["pv_unique_timestamps", pv_summary["unique_timestamps"]],
        ["pv_duplicate_timestamp_count", pv_summary["duplicate_timestamp_count"]],
        ["pv_duplicate_extra_record_count", pv_summary["duplicate_extra_record_count"]],
        ["pv_missing_nan_or_nonnumeric_count", pv_summary["missing_nan_or_nonnumeric_count"]],
        ["common_grid_start", start.strftime(OUTPUT_FORMAT)],
        ["common_grid_end", end.strftime(OUTPUT_FORMAT)],
        ["common_grid_records", len(grid_rows)],
        ["candidate_load_eligible_pending_timezone_resolution", status_count["eligible_pending_timezone_resolution"]],
        ["candidate_load_not_eligible", status_count["not_eligible"]],
        ["power_closure_check", "For every eligible row, p_load_candidate - p_net_raw - p_pv_raw = 0 by construction"],
    ]
    with open(quality_output, "w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["metric", "value"])
        writer.writerows(rows)

    print(total_output)
    print(pv_output)
    print(reconstruction_output)
    print(quality_output)


if __name__ == "__main__":
    main()
