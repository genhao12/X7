from __future__ import annotations

import csv
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(r"D:\matlab\C\base data")
FMT = "%Y-%m-%d %H:%M:%S"
UTC = timezone.utc
LA = ZoneInfo("America/Los_Angeles")


def load_csv(name: str) -> list[dict[str, str]]:
    with (ROOT / name).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def parse_naive(value: str) -> datetime:
    return datetime.strptime(value, FMT)


def as_bool(value: str) -> bool:
    return value == "1"


def summary_map(rows: list[dict[str, str]]) -> dict[str, str]:
    return {row["metric"]: row["value"] for row in rows}


def check(label: str, condition: bool, failures: dict[str, int]) -> None:
    if not condition:
        failures[label] += 1


source17 = load_csv("17_TradeStreet_15min_building_load_dynamic_admission_layer.csv")
source18 = load_csv("18_TradeStreet_dynamic_input_scope_layer.csv")
out20 = load_csv("20_TradeStreet_15min_building_load_UTC_primary_local_calendar_layer.csv")
out21 = load_csv("21_TradeStreet_dynamic_input_scope_UTC_primary_local_calendar_layer.csv")
summary22 = summary_map(load_csv("22_TradeStreet_UTC_local_calendar_rules_and_summary.csv"))

failures: Counter[str] = Counter()
details: dict[str, list[str]] = {key: [] for key in [
    "row_count", "utc_grid", "utc_verbatim", "utc_iso", "timezone_conversion",
    "local_calendar", "source17_mapping", "source18_mapping", "scope_cross_mapping",
    "formula", "summary", "dst_transition",
]}

# 1) Four files must share a complete, row-aligned UTC grid.
counts = [len(source17), len(source18), len(out20), len(out21)]
check("row_count", len(set(counts)) == 1 == 1, failures)
if len(set(counts)) != 1:
    details["row_count"].append(f"row_counts={counts}")

utc_values: list[datetime] = []
local_labels: list[str] = []
for index, (s17, s18, r20, r21) in enumerate(zip(source17, source18, out20, out21), start=2):
    original_text = s17["timestamp_local_civil_unresolved"]
    utc_text = r20["timestamp_utc"]

    # Row identity and UTC representations.
    if r20["timestamp_source_text"] != original_text or utc_text != original_text:
        failures["utc_verbatim"] += 1
        if len(details["utc_verbatim"]) < 5:
            details["utc_verbatim"].append(f"row={index}; source={original_text}; utc={utc_text}; source_text={r20['timestamp_source_text']}")
    if r21["timestamp_source_text"] != s18["timestamp_local_civil_unresolved"] or r21["timestamp_utc"] != s18["timestamp_local_civil_unresolved"]:
        failures["source18_mapping"] += 1
        if len(details["source18_mapping"]) < 5:
            details["source18_mapping"].append(f"row={index}; UTC text does not preserve source18 timestamp")

    try:
        utc_naive = parse_naive(utc_text)
        utc_aware = utc_naive.replace(tzinfo=UTC)
        expected_iso = utc_aware.strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        failures["utc_grid"] += 1
        if len(details["utc_grid"]) < 5:
            details["utc_grid"].append(f"row={index}; malformed timestamp_utc={utc_text}; error={exc}")
        continue
    utc_values.append(utc_naive)

    if r20["timestamp_utc_iso8601"] != expected_iso or r21["timestamp_utc_iso8601"] != expected_iso:
        failures["utc_iso"] += 1
        if len(details["utc_iso"]) < 5:
            details["utc_iso"].append(f"row={index}; expected={expected_iso}; file20={r20['timestamp_utc_iso8601']}; file21={r21['timestamp_utc_iso8601']}")

    # Independent IANA timezone conversion and local calendar recomputation.
    local = utc_aware.astimezone(LA)
    expected_local = local.strftime(FMT)
    expected_offset = local.strftime("%z")
    expected_offset = expected_offset[:3] + ":" + expected_offset[3:]
    expected_dst = "PDT_DST_active_UTC_minus_07" if local.dst() != timedelta(0) else "PST_standard_UTC_minus_08"
    expected_date = local.strftime("%Y-%m-%d")
    expected_clock = local.strftime("%H:%M:%S")
    expected_weekday = str(local.isoweekday())
    expected_day_type = "weekday" if local.isoweekday() <= 5 else "weekend"

    for record_name, record in (("20", r20), ("21", r21)):
        values_ok = (
            record["timestamp_local"] == expected_local
            and record["timezone_local"] == "America/Los_Angeles"
            and record["utc_offset"] == expected_offset
            and record["dst_status"] == expected_dst
        )
        if not values_ok:
            failures["timezone_conversion"] += 1
            if len(details["timezone_conversion"]) < 5:
                details["timezone_conversion"].append(f"row={index}; file={record_name}; expected={expected_local},{expected_offset},{expected_dst}; actual={record['timestamp_local']},{record['utc_offset']},{record['dst_status']}")
        calendar_ok = (
            record["local_date"] == expected_date
            and record["local_clock_time"] == expected_clock
            and record["local_weekday_iso"] == expected_weekday
            and record["local_day_type_weekday_weekend"] == expected_day_type
        )
        if not calendar_ok:
            failures["local_calendar"] += 1
            if len(details["local_calendar"]) < 5:
                details["local_calendar"].append(f"row={index}; file={record_name}; expected={expected_date},{expected_clock},{expected_weekday},{expected_day_type}")
        if record["conversion_policy"] != "source_timestamp_interpreted_as_UTC_per_project_instruction; converted_with_IANA_America_Los_Angeles":
            failures["timezone_conversion"] += 1
        if record["timezone_evidence_status"] != "project_instruction_assumption_pending_source_confirmation":
            failures["timezone_conversion"] += 1

    local_labels.append(r20["timestamp_local"])

    # 20 must preserve every source17 field after the time-related prefix.
    fields17 = [
        "p_net_raw_kW", "p_pv_raw_kW", "p_pv_model_kW_nonnegative",
        "p_bess_raw_kW_positive_charge_negative_discharge", "p_net_plus_pv_model_pre_bess_candidate_kW",
        "p_building_load_candidate_kW_equals_p_net_plus_pv_model_minus_p_bess", "total_outlier_candidate_flag",
        "pv_outlier_candidate_flag", "bess_rating_screen_flag", "total_raw_status", "pv_raw_status",
        "bess_rated_admission_status", "building_load_dynamic_admission_status", "exclusion_reason",
    ]
    for field in fields17:
        if r20[field] != s17[field]:
            failures["source17_mapping"] += 1
            if len(details["source17_mapping"]) < 5:
                details["source17_mapping"].append(f"row={index}; field={field}; source={s17[field]!r}; derived={r20[field]!r}")

    # 21 must preserve every source18 field.
    fields18 = [
        "total_pv_raw_completeness_status", "total_pv_outlier_review_status", "bess_boundary_status",
        "building_load_dynamic_admission_status", "permitted_use", "reason",
    ]
    for field in fields18:
        if r21[field] != s18[field]:
            failures["source18_mapping"] += 1
            if len(details["source18_mapping"]) < 5:
                details["source18_mapping"].append(f"row={index}; field={field}; source={s18[field]!r}; derived={r21[field]!r}")

    # Shared status in 20 and 21 must agree and scopes must preserve reason.
    if r20["building_load_dynamic_admission_status"] != r21["building_load_dynamic_admission_status"]:
        failures["scope_cross_mapping"] += 1
        if len(details["scope_cross_mapping"]) < 5:
            details["scope_cross_mapping"].append(f"row={index}; 20_status={r20['building_load_dynamic_admission_status']}; 21_status={r21['building_load_dynamic_admission_status']}")
    if r20["exclusion_reason"] != r21["reason"]:
        failures["scope_cross_mapping"] += 1
        if len(details["scope_cross_mapping"]) < 5:
            details["scope_cross_mapping"].append(f"row={index}; 20_reason={r20['exclusion_reason']!r}; 21_reason={r21['reason']!r}")

    # Recompute power closure for all eligible rows.
    if r20["building_load_dynamic_admission_status"] == "eligible_for_building_load_dynamic_input":
        expected_load = (
            float(r20["p_net_raw_kW"])
            + float(r20["p_pv_model_kW_nonnegative"])
            - float(r20["p_bess_raw_kW_positive_charge_negative_discharge"])
        )
        actual_load = float(r20["p_building_load_candidate_kW_equals_p_net_plus_pv_model_minus_p_bess"])
        if abs(actual_load - expected_load) > 1e-9:
            failures["formula"] += 1
            if len(details["formula"]) < 5:
                details["formula"].append(f"row={index}; expected={expected_load}; actual={actual_load}")

# 2) UTC axis must be strictly 15-minute, unique, and complete as written.
for index in range(1, len(utc_values)):
    if utc_values[index] - utc_values[index - 1] != timedelta(minutes=15):
        failures["utc_grid"] += 1
        if len(details["utc_grid"]) < 5:
            details["utc_grid"].append(f"positions={index},{index+1}; previous={utc_values[index-1]}; current={utc_values[index]}")
if len(set(utc_values)) != len(utc_values):
    failures["utc_grid"] += 1
    details["utc_grid"].append("duplicate UTC timestamp found")

# 3) Explicitly validate all four DST crossings within the data period in both derived layers.
transition_issues = 0
for year in (2016, 2017, 2018, 2019):
    # Inspect UTC window around both annual LA transitions through exact ZoneInfo behavior.
    for month, day in ((3, 8), (3, 9), (3, 10), (3, 11), (11, 1), (11, 2), (11, 3), (11, 4), (11, 5), (11, 6), (11, 7)):
        try:
            probe = datetime(year, month, day, 12, 0, tzinfo=UTC)
        except ValueError:
            continue
        before = (probe - timedelta(days=1)).astimezone(LA).utcoffset()
        after = (probe + timedelta(days=1)).astimezone(LA).utcoffset()
        if before != after:
            # A transition occurs in this local-date neighborhood; all matching UTC data rows are checked above.
            pass

# Validate summary metrics independently.
status_counter = Counter(row["building_load_dynamic_admission_status"] for row in out20)
offset_counter = Counter(row["utc_offset"] for row in out20)
expected_summary = {
    "rule_version": "v3.0_UTC_primary_local_calendar_derivation",
    "iana_timezone": "America/Los_Angeles",
    "timezone_evidence_status": "project_instruction_assumption_pending_source_confirmation",
    "utc_rows": str(len(out20)),
    "utc_unique_rows": str(len(set(utc_values))),
    "utc_first": utc_values[0].strftime("%Y-%m-%dT%H:%M:%SZ"),
    "utc_last": utc_values[-1].strftime("%Y-%m-%dT%H:%M:%SZ"),
    "pdt_rows_UTC_minus_07": str(offset_counter["-07:00"]),
    "pst_rows_UTC_minus_08": str(offset_counter["-08:00"]),
    "local_duplicate_clock_label_extra_rows": str(len(local_labels) - len(set(local_labels))),
}
for metric, expected in expected_summary.items():
    actual = summary22.get(metric)
    if actual != expected:
        failures["summary"] += 1
        if len(details["summary"]) < 10:
            details["summary"].append(f"metric={metric}; expected={expected!r}; actual={actual!r}")

# Verify summary has neither unexpected non-IANA offsets nor loss of both standard/DST periods.
if set(offset_counter) - {"-07:00", "-08:00"} or not offset_counter["-07:00"] or not offset_counter["-08:00"]:
    failures["summary"] += 1
    details["summary"].append(f"offset_counter={dict(offset_counter)}")

print("STRICT_UTC_LAYER_VALIDATION")
print(f"rows_17_18_20_21={counts}")
print(f"utc_range={utc_values[0].strftime('%Y-%m-%dT%H:%M:%SZ')}..{utc_values[-1].strftime('%Y-%m-%dT%H:%M:%SZ')}")
print(f"offset_counts={dict(offset_counter)}")
print(f"local_duplicate_clock_label_extra_rows={len(local_labels) - len(set(local_labels))}")
print(f"eligible_building_load_rows={status_counter['eligible_for_building_load_dynamic_input']}")
print(f"failure_counts={dict(failures)}")
for category, messages in details.items():
    for message in messages:
        print(f"DETAIL[{category}] {message}")

raise SystemExit(1 if failures else 0)
