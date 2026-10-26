from __future__ import annotations

import csv
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(r"D:\matlab\C\base data")
FMT = "%Y-%m-%d %H:%M:%S"
LA = ZoneInfo("America/Los_Angeles")
UTC = timezone.utc
ELIGIBLE = "eligible_for_building_load_dynamic_input"


def load(name: str) -> list[dict[str, str]]:
    with (BASE / name).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def remap(timestamp_text: str) -> dict[str, str]:
    naive = datetime.strptime(timestamp_text, FMT)
    candidates = []
    for fold in (0, 1):
        local = naive.replace(tzinfo=LA, fold=fold)
        utc = local.astimezone(UTC)
        if utc.astimezone(LA).replace(tzinfo=None) == naive:
            candidates.append((fold, local, utc))
    if not candidates:
        return {"class": "nonexistent_local_time_no_valid_UTC_mapping", "utc": "", "offset": "", "dst": ""}
    if len(candidates) == 2 and candidates[0][2] != candidates[1][2]:
        fold, local, utc = next(item for item in candidates if item[0] == 0)
        classification = "ambiguous_fall_back_local_time"
    else:
        fold, local, utc = candidates[0]
        classification = "unambiguous_local_time"
    offset = local.strftime("%z")
    return {
        "class": classification,
        "utc": utc.strftime(FMT),
        "offset": offset[:3] + ":" + offset[3:],
        "dst": "PDT_DST_active_UTC_minus_07" if local.dst() and local.dst() != timedelta(0) else "PST_standard_UTC_minus_08",
    }


source17 = load("17_TradeStreet_15min_building_load_dynamic_admission_layer.csv")
source18 = load("18_TradeStreet_dynamic_input_scope_layer.csv")
out34 = load("34_TradeStreet_15min_building_load_local_civil_primary_UTC_mapping_layer.csv")
out35 = load("35_TradeStreet_dynamic_input_scope_local_civil_primary_UTC_mapping_layer.csv")
summary = {row["metric"]: row["value"] for row in load("36_TradeStreet_local_civil_primary_UTC_mapping_rules_and_summary.csv")}
errors: Counter[str] = Counter()
details: list[str] = []

if not (len(source17) == len(source18) == len(out34) == len(out35)):
    errors["row_count"] += 1
    details.append(f"counts={[len(source17), len(source18), len(out34), len(out35)]}")

utc_values: list[datetime] = []
classes: Counter[str] = Counter()
offsets: Counter[str] = Counter()
eligible_mapped = 0
fields17 = [
    field for field in source17[0]
    if field not in {"timestamp_local_civil_unresolved", "timezone_status"}
]
fields18 = [field for field in source18[0] if field != "timestamp_local_civil_unresolved"]
for i, (s17, s18, r34, r35) in enumerate(zip(source17, source18, out34, out35), start=2):
    text = s17["timestamp_local_civil_unresolved"]
    expected = remap(text)
    for label, record in (("34", r34), ("35", r35)):
        if record["timestamp_source_text"] != text or record["timestamp_local_civil"] != text:
            errors["source_identity"] += 1
        if record["dst_local_time_classification"] != expected["class"]:
            errors["dst_classification"] += 1
        if record["timestamp_utc"] != expected["utc"]:
            errors["utc_mapping"] += 1
        if expected["class"] == "nonexistent_local_time_no_valid_UTC_mapping":
            offset_dst_ok = (
                record["utc_offset"] == ""
                and record["dst_status"] == "nonexistent_local_civil_time_spring_forward"
            )
        else:
            offset_dst_ok = record["utc_offset"] == expected["offset"] and record["dst_status"] == expected["dst"]
        if not offset_dst_ok:
            errors["offset_dst"] += 1
        if record["timezone_local"] != "America/Los_Angeles":
            errors["timezone"] += 1
        if record["source_timestamp_interpretation"] != "source_timestamp_interpreted_as_America_Los_Angeles_local_civil_time_based_on_PV_daylight_physical_consistency":
            errors["policy"] += 1
        if record["timezone_evidence_status"] != "physical_consistency_supported_local_civil_interpretation_pending_source_confirmation":
            errors["policy"] += 1
    for field in fields17:
        if r34[field] != s17[field]:
            errors["source17_field_mapping"] += 1
    for field in fields18:
        if r35[field] != s18[field]:
            errors["source18_field_mapping"] += 1
    if r34["building_load_dynamic_admission_status"] != r35["building_load_dynamic_admission_status"] or r34["exclusion_reason"] != r35["reason"]:
        errors["cross_layer_scope"] += 1

    classes[expected["class"]] += 1
    if expected["utc"]:
        utc_values.append(datetime.strptime(expected["utc"], FMT))
        offsets[expected["offset"]] += 1
    if r34["building_load_dynamic_admission_status"] == ELIGIBLE and r34["utc_mapping_admission_status"].startswith("eligible_"):
        eligible_mapped += 1

if len(utc_values) != len(set(utc_values)):
    errors["utc_uniqueness"] += 1
if any(right <= left for left, right in zip(utc_values, utc_values[1:])):
    errors["utc_order"] += 1
if any(right - left not in (timedelta(minutes=15), timedelta(minutes=75)) for left, right in zip(utc_values, utc_values[1:])):
    errors["unexpected_utc_gap"] += 1
# Any 75-minute gap must directly follow the explicitly selected fold-0 fall-back time.
for left, right in zip(out34, out34[1:]):
    if left["timestamp_utc"] and right["timestamp_utc"]:
        delta = datetime.strptime(right["timestamp_utc"], FMT) - datetime.strptime(left["timestamp_utc"], FMT)
        if delta == timedelta(minutes=75) and left["dst_local_time_classification"] != "ambiguous_fall_back_local_time":
            errors["fall_back_gap_provenance"] += 1

expected_summary = {
    "rule_version": "v4.0_local_civil_primary_UTC_mapping",
    "source_rows": str(len(out34)),
    "mapped_UTC_rows": str(len(utc_values)),
    "unique_mapped_UTC_rows": str(len(set(utc_values))),
    "unambiguous_local_time_rows": str(classes["unambiguous_local_time"]),
    "ambiguous_fall_back_local_time_rows": str(classes["ambiguous_fall_back_local_time"]),
    "nonexistent_spring_forward_local_time_rows": str(classes["nonexistent_local_time_no_valid_UTC_mapping"]),
    "PDT_rows_UTC_minus_07": str(offsets["-07:00"]),
    "PST_rows_UTC_minus_08": str(offsets["-08:00"]),
    "eligible_building_load_rows_after_UTC_mapping": str(eligible_mapped),
}
for key, expected_value in expected_summary.items():
    if summary.get(key) != expected_value:
        errors["summary"] += 1
        details.append(f"summary {key}: expected={expected_value}; actual={summary.get(key)}")

print("STRICT_LOCAL_CIVIL_PRIMARY_TIME_LAYER_VALIDATION")
print(f"rows_17_18_34_35={[len(source17), len(source18), len(out34), len(out35)]}")
print(f"local_time_classes={dict(classes)}")
print(f"utc_mapped_rows={len(utc_values)}; unique={len(set(utc_values))}; offsets={dict(offsets)}")
print(f"eligible_mapped_building_load_rows={eligible_mapped}")
print(f"errors={dict(errors)}")
for line in details[:10]:
    print(f"DETAIL {line}")
raise SystemExit(1 if errors else 0)
