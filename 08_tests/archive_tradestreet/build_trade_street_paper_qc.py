import csv
import math
import os
from collections import Counter
from datetime import datetime, timedelta

import holidays

ROOT = r"D:\matlab\C"
RAW_DIR = os.path.join(ROOT, "UCSD-Microgrid-Database", "UCSD-Microgrid-Database-master", "Data Files", "Trade Street Off-campus Microgrid")
OUT_DIR = os.path.join(ROOT, "base data")
INPUT_FORMAT = "%m/%d/%Y %H:%M"
OUTPUT_FORMAT = "%Y-%m-%d %H:%M:%S"
US_HOLIDAYS = holidays.US()


def parse_power(value):
    value = value.strip()
    if not value:
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except ValueError:
        return None


def day_type(timestamp):
    return "business" if timestamp.weekday() < 5 and timestamp.date() not in US_HOLIDAYS else "non_business"


def load_raw(filename):
    records = []
    with open(os.path.join(RAW_DIR, filename), encoding="utf-8-sig", newline="") as file:
        for source_row, row in enumerate(csv.DictReader(file), start=2):
            timestamp = datetime.strptime(row["DateTime"].strip(), INPUT_FORMAT)
            raw_power = row["RealPower"].strip()
            records.append({
                "timestamp": timestamp,
                "source_row": source_row,
                "raw_power": raw_power,
                "power": parse_power(raw_power),
            })
    return records


def build_raw_unique_map(records):
    grouped = {}
    for item in records:
        grouped.setdefault(item["timestamp"], []).append(item)
    raw_map = {}
    for timestamp, group in grouped.items():
        numeric = [item for item in group if item["power"] is not None]
        if len(group) == 1 and len(numeric) == 1:
            raw_map[timestamp] = {
                "power": numeric[0]["power"],
                "source_row": numeric[0]["source_row"],
                "status": "observed_raw_valid",
                "reason": "",
            }
        elif len(group) > 1:
            raw_map[timestamp] = {
                "power": None,
                "source_row": "",
                "status": "needs_imputation",
                "reason": "duplicate_timestamp_unresolved",
            }
        else:
            raw_map[timestamp] = {
                "power": None,
                "source_row": group[0]["source_row"],
                "status": "needs_imputation",
                "reason": "raw_nan_or_nonnumeric",
            }
    return raw_map


def nearest_prior_same_type_same_clock(target, raw_valid):
    candidate_day = target.date() - timedelta(days=1)
    while candidate_day >= min(raw_valid).date():
        candidate = datetime.combine(candidate_day, target.time())
        if day_type(candidate) == day_type(target) and candidate in raw_valid:
            return candidate
        candidate_day -= timedelta(days=1)
    return None


def nearest_prior_valid(target, raw_valid):
    candidates = [timestamp for timestamp in raw_valid if timestamp < target]
    return max(candidates) if candidates else None


def build_qc_stream(records, label, start, end):
    raw_map = build_raw_unique_map(records)
    raw_valid = {timestamp: item for timestamp, item in raw_map.items() if item["status"] == "observed_raw_valid"}
    rows = []
    qc_map = {}
    current = start
    while current <= end:
        raw_item = raw_map.get(current)
        target_day_type = day_type(current)
        if raw_item and raw_item["status"] == "observed_raw_valid":
            power = raw_item["power"]
            status = "observed_raw_valid"
            original_reason = ""
            method = "not_imputed"
            donor_timestamp = ""
            donor_source_row = ""
            donor_day_type = ""
        else:
            original_reason = raw_item["reason"] if raw_item else "missing_15min_grid_timestamp"
            donor = nearest_prior_same_type_same_clock(current, raw_valid)
            if donor is not None:
                donor_item = raw_valid[donor]
                power = donor_item["power"]
                status = "imputed_paper_same_time_same_day_type"
                method = "paper_persistence_same_time_same_day_type"
            else:
                donor = nearest_prior_valid(current, raw_valid)
                if donor is not None:
                    donor_item = raw_valid[donor]
                    power = donor_item["power"]
                    status = "imputed_paper_recent_prior_valid"
                    method = "paper_persistence_recent_prior_valid"
                else:
                    donor_item = None
                    power = ""
                    status = "not_imputable_no_prior_raw_donor"
                    method = "no_prior_raw_donor"
            if donor_item is None:
                donor_timestamp = ""
                donor_source_row = ""
                donor_day_type = ""
            else:
                donor_timestamp = donor.strftime(OUTPUT_FORMAT)
                donor_source_row = donor_item["source_row"]
                donor_day_type = day_type(donor)
        qc_map[current] = {
            "power": power,
            "status": status,
            "is_imputed": int(status.startswith("imputed_")),
        }
        rows.append([
            current.strftime(OUTPUT_FORMAT),
            power,
            status,
            int(status.startswith("imputed_")),
            original_reason,
            method,
            donor_timestamp,
            donor_source_row,
            target_day_type,
            donor_day_type,
            "local_civil_time_unresolved_no_UTC_conversion",
            "qc_sizing_dispatch_sensitivity_only" if status.startswith("imputed_") else "raw_observation",
        ])
        current += timedelta(minutes=15)

    output = os.path.join(OUT_DIR, label)
    with open(output, "w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow([
            "timestamp_local_civil_unresolved",
            "power_qc_kW",
            "value_status",
            "is_imputed",
            "original_issue_reason",
            "imputation_method",
            "donor_timestamp_local_civil_unresolved",
            "donor_source_row",
            "target_day_type_US_calendar",
            "donor_day_type_US_calendar",
            "timezone_status",
            "permitted_use",
        ])
        writer.writerows(rows)
    return qc_map, rows, output


def main():
    total_raw = load_raw("TradeStreetTotal.csv")
    pv_raw = load_raw("TradeStreetPV.csv")
    total_times = [item["timestamp"] for item in total_raw]
    pv_times = [item["timestamp"] for item in pv_raw]
    start = max(min(total_times), min(pv_times))
    end = min(max(total_times), max(pv_times))

    total_map, total_rows, total_output = build_qc_stream(total_raw, "07_TradeStreetTotal_paper_QC_layer.csv", start, end)
    pv_map, pv_rows, pv_output = build_qc_stream(pv_raw, "08_TradeStreetPV_paper_QC_layer.csv", start, end)

    reconstruction_rows = []
    counts = Counter()
    current = start
    while current <= end:
        total = total_map[current]
        pv = pv_map[current]
        usable = total["power"] != "" and pv["power"] != ""
        load = float(total["power"]) + float(pv["power"]) if usable else ""
        contains_imputation = bool(total["is_imputed"] or pv["is_imputed"])
        if not usable:
            use_status = "not_usable_no_prior_raw_donor"
        elif contains_imputation:
            use_status = "qc_sensitivity_only_contains_imputation"
        else:
            use_status = "raw_complete_eligible_for_dynamic_layer"
        counts[use_status] += 1
        reconstruction_rows.append([
            current.strftime(OUTPUT_FORMAT),
            total["power"],
            pv["power"],
            load,
            total["status"],
            pv["status"],
            int(total["is_imputed"]),
            int(pv["is_imputed"]),
            int(contains_imputation),
            use_status,
            "P_load_candidate=P_net+P_PV",
        ])
        current += timedelta(minutes=15)

    reconstruction_output = os.path.join(OUT_DIR, "09_TradeStreet_15min_paper_QC_netload_pv_load_candidate.csv")
    with open(reconstruction_output, "w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow([
            "timestamp_local_civil_unresolved",
            "p_net_qc_kW",
            "p_pv_qc_kW",
            "p_load_candidate_qc_kW_equals_p_net_plus_p_pv",
            "p_net_value_status",
            "p_pv_value_status",
            "p_net_is_imputed",
            "p_pv_is_imputed",
            "contains_imputation",
            "scenario_use_status",
            "reconstruction_formula",
        ])
        writer.writerows(reconstruction_rows)

    summary = os.path.join(OUT_DIR, "10_TradeStreet_paper_QC_summary.csv")
    total_count = Counter(row[2] for row in total_rows)
    pv_count = Counter(row[2] for row in pv_rows)
    with open(summary, "w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["metric", "value"])
        writer.writerows([
            ["qc_rule", "Paper persistence: nearest earlier same-time same-day-type raw valid donor; fallback nearest earlier raw valid donor"],
            ["donor_rule", "Only observed_raw_valid values may donate; imputed values never donate"],
            ["US_calendar", "holidays.US() with weekday/non-holiday defined as business"],
            ["timezone_policy", "Original local civil labels retained; timezone unresolved; no UTC conversion"],
            ["start_timestamp", start.strftime(OUTPUT_FORMAT)],
            ["end_timestamp", end.strftime(OUTPUT_FORMAT)],
            ["grid_records", len(reconstruction_rows)],
            ["total_observed_raw_valid", total_count["observed_raw_valid"]],
            ["total_imputed_same_time_same_day_type", total_count["imputed_paper_same_time_same_day_type"]],
            ["total_imputed_recent_prior_valid", total_count["imputed_paper_recent_prior_valid"]],
            ["total_not_imputable", total_count["not_imputable_no_prior_raw_donor"]],
            ["pv_observed_raw_valid", pv_count["observed_raw_valid"]],
            ["pv_imputed_same_time_same_day_type", pv_count["imputed_paper_same_time_same_day_type"]],
            ["pv_imputed_recent_prior_valid", pv_count["imputed_paper_recent_prior_valid"]],
            ["pv_not_imputable", pv_count["not_imputable_no_prior_raw_donor"]],
            ["raw_complete_eligible_for_dynamic_layer", counts["raw_complete_eligible_for_dynamic_layer"]],
            ["qc_sensitivity_only_contains_imputation", counts["qc_sensitivity_only_contains_imputation"]],
            ["not_usable_no_prior_raw_donor", counts["not_usable_no_prior_raw_donor"]],
            ["dynamic_layer_policy", "Only raw_complete_eligible_for_dynamic_layer may be used for the default dynamic degradation, ramp, start-stop and coupling-identification input"],
            ["qc_layer_policy", "Rows with imputation are permitted only for labelled sizing/dispatch sensitivity analysis"],
        ])

    print(total_output)
    print(pv_output)
    print(reconstruction_output)
    print(summary)


if __name__ == "__main__":
    main()
