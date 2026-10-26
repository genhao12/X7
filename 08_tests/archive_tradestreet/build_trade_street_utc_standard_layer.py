import csv
import os
from collections import Counter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ROOT = r"D:\matlab\C"
BASE = os.path.join(ROOT, "base data")
FMT = "%Y-%m-%d %H:%M:%S"
UTC = timezone.utc
LOCAL_ZONE = ZoneInfo("America/Los_Angeles")


def read_csv(filename):
    with open(os.path.join(BASE, filename), encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(filename, fields, rows):
    with open(os.path.join(BASE, filename), "w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def local_fields(timestamp_text):
    parsed_utc = datetime.strptime(timestamp_text, FMT).replace(tzinfo=UTC)
    local = parsed_utc.astimezone(LOCAL_ZONE)
    offset_seconds = int(local.utcoffset().total_seconds())
    offset_hours = offset_seconds // 3600
    dst_active = bool(local.dst() and local.dst().total_seconds() != 0)
    return {
        "timestamp_source_text": timestamp_text,
        "timestamp_utc": timestamp_text,
        "timestamp_utc_iso8601": parsed_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "timestamp_local": local.strftime("%Y-%m-%d %H:%M:%S"),
        "timezone_local": "America/Los_Angeles",
        "utc_offset": f"{offset_hours:+03d}:00",
        "dst_status": "PDT_DST_active_UTC_minus_07" if dst_active else "PST_standard_UTC_minus_08",
        "local_date": local.strftime("%Y-%m-%d"),
        "local_clock_time": local.strftime("%H:%M:%S"),
        "local_weekday_iso": str(local.isoweekday()),
        "local_day_type_weekday_weekend": "weekday" if local.weekday() < 5 else "weekend",
        "conversion_policy": "source_timestamp_interpreted_as_UTC_per_project_instruction; converted_with_IANA_America_Los_Angeles",
        "timezone_evidence_status": "project_instruction_assumption_pending_source_confirmation",
    }


def main():
    admission = read_csv("17_TradeStreet_15min_building_load_dynamic_admission_layer.csv")
    scope = read_csv("18_TradeStreet_dynamic_input_scope_layer.csv")
    scope_by_source = {row["timestamp_local_civil_unresolved"]: row for row in scope}

    admission_rows = []
    scope_rows = []
    dst_counts = Counter()
    local_labels = Counter()
    source_utc = []
    for row in admission:
        source_timestamp = row["timestamp_local_civil_unresolved"]
        time_data = local_fields(source_timestamp)
        source_utc.append(time_data["timestamp_utc_iso8601"])
        local_labels[time_data["timestamp_local"]] += 1
        dst_counts[time_data["dst_status"]] += 1
        admission_rows.append({
            **time_data,
            "p_net_raw_kW": row["p_net_raw_kW"],
            "p_pv_raw_kW": row["p_pv_raw_kW"],
            "p_pv_model_kW_nonnegative": row["p_pv_model_kW_nonnegative"],
            "p_bess_raw_kW_positive_charge_negative_discharge": row["p_bess_raw_kW_positive_charge_negative_discharge"],
            "p_net_plus_pv_model_pre_bess_candidate_kW": row["p_net_plus_pv_model_pre_bess_candidate_kW"],
            "p_building_load_candidate_kW_equals_p_net_plus_pv_model_minus_p_bess": row["p_building_load_candidate_kW_equals_p_net_plus_pv_model_minus_p_bess"],
            "total_outlier_candidate_flag": row["total_outlier_candidate_flag"],
            "pv_outlier_candidate_flag": row["pv_outlier_candidate_flag"],
            "bess_rating_screen_flag": row["bess_rating_screen_flag"],
            "total_raw_status": row["total_raw_status"],
            "pv_raw_status": row["pv_raw_status"],
            "bess_rated_admission_status": row["bess_rated_admission_status"],
            "building_load_dynamic_admission_status": row["building_load_dynamic_admission_status"],
            "exclusion_reason": row["exclusion_reason"],
        })
        scope_row = scope_by_source[source_timestamp]
        scope_rows.append({
            **time_data,
            "total_pv_raw_completeness_status": scope_row["total_pv_raw_completeness_status"],
            "total_pv_outlier_review_status": scope_row["total_pv_outlier_review_status"],
            "bess_boundary_status": scope_row["bess_boundary_status"],
            "building_load_dynamic_admission_status": scope_row["building_load_dynamic_admission_status"],
            "permitted_use": scope_row["permitted_use"],
            "reason": scope_row["reason"],
        })

    write_csv("20_TradeStreet_15min_building_load_UTC_primary_local_calendar_layer.csv", list(admission_rows[0].keys()), admission_rows)
    write_csv("21_TradeStreet_dynamic_input_scope_UTC_primary_local_calendar_layer.csv", list(scope_rows[0].keys()), scope_rows)

    local_duplicate_count = sum(count - 1 for count in local_labels.values() if count > 1)
    summary = [
        {"metric": "rule_version", "value": "v3.0_UTC_primary_local_calendar_derivation"},
        {"metric": "source_files_preserved", "value": "17 and 18 are preserved; 20 and 21 are derived UTC-primary layers"},
        {"metric": "source_timestamp_interpretation", "value": "The previous timestamp_local_civil_unresolved text is frozen as UTC by explicit project instruction and retained verbatim in timestamp_utc; timestamp_utc_iso8601 is a separate equivalent representation"},
        {"metric": "utc_primary_policy", "value": "Use timestamp_utc as the dynamic-simulation master axis and for joining UTC external data"},
        {"metric": "local_calendar_policy", "value": "Use timestamp_local only for local weekday/weekend, daily load profiles, local solar-hour analysis and local calendar reporting"},
        {"metric": "iana_timezone", "value": "America/Los_Angeles"},
        {"metric": "dst_rule", "value": "ZoneInfo IANA rules: PDT UTC-07 while DST active; PST UTC-08 otherwise"},
        {"metric": "timezone_evidence_status", "value": "project_instruction_assumption_pending_source_confirmation"},
        {"metric": "utc_rows", "value": len(admission_rows)},
        {"metric": "utc_unique_rows", "value": len(set(source_utc))},
        {"metric": "utc_first", "value": source_utc[0]},
        {"metric": "utc_last", "value": source_utc[-1]},
        {"metric": "pdt_rows_UTC_minus_07", "value": dst_counts["PDT_DST_active_UTC_minus_07"]},
        {"metric": "pst_rows_UTC_minus_08", "value": dst_counts["PST_standard_UTC_minus_08"]},
        {"metric": "local_duplicate_clock_label_extra_rows", "value": local_duplicate_count},
        {"metric": "fall_back_handling", "value": "Repeated local civil labels remain distinguishable through unique timestamp_utc and utc_offset; no local timestamp deduplication is applied"},
        {"metric": "spring_forward_handling", "value": "Local 02:00-02:45 structural absence on DST-start days is a timezone conversion property, not an imputable UTC-grid gap"},
        {"metric": "conversion_reproducibility", "value": "Python zoneinfo with IANA America/Los_Angeles; timestamp_utc uses ISO 8601 Z suffix"},
    ]
    with open(os.path.join(BASE, "22_TradeStreet_UTC_local_calendar_rules_and_summary.csv"), "w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["metric", "value"])
        writer.writeheader()
        writer.writerows(summary)


if __name__ == "__main__":
    main()
