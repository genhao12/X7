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
ADMISSION_IN = BASE / "17_TradeStreet_15min_building_load_dynamic_admission_layer.csv"
SCOPE_IN = BASE / "18_TradeStreet_dynamic_input_scope_layer.csv"
ADMISSION_OUT = BASE / "34_TradeStreet_15min_building_load_local_civil_primary_UTC_mapping_layer.csv"
SCOPE_OUT = BASE / "35_TradeStreet_dynamic_input_scope_local_civil_primary_UTC_mapping_layer.csv"
SUMMARY_OUT = BASE / "36_TradeStreet_local_civil_primary_UTC_mapping_rules_and_summary.csv"
ELIGIBLE = "eligible_for_building_load_dynamic_input"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def local_mapping(timestamp_text: str) -> dict[str, str]:
    """Map a source civil label to UTC without silently repairing DST anomalies."""
    local_naive = datetime.strptime(timestamp_text, FMT)
    candidates = []
    for fold in (0, 1):
        local = local_naive.replace(tzinfo=LA, fold=fold)
        utc = local.astimezone(UTC)
        roundtrip = utc.astimezone(LA)
        if roundtrip.replace(tzinfo=None) == local_naive:
            candidates.append((fold, local, utc, roundtrip))

    base = {
        "timestamp_source_text": timestamp_text,
        "timestamp_local_civil": timestamp_text,
        "timezone_local": "America/Los_Angeles",
        "source_timestamp_interpretation": "source_timestamp_interpreted_as_America_Los_Angeles_local_civil_time_based_on_PV_daylight_physical_consistency",
        "timezone_evidence_status": "physical_consistency_supported_local_civil_interpretation_pending_source_confirmation",
    }
    if not candidates:
        return {
            **base,
            "timestamp_utc": "",
            "timestamp_utc_iso8601": "",
            "utc_offset": "",
            "dst_status": "nonexistent_local_civil_time_spring_forward",
            "dst_local_time_classification": "nonexistent_local_time_no_valid_UTC_mapping",
            "dst_fold_resolution": "not_applicable_no_UTC_mapping",
            "utc_mapping_admission_status": "not_eligible_nonexistent_local_civil_time",
            "local_date": local_naive.strftime("%Y-%m-%d"),
            "local_clock_time": local_naive.strftime("%H:%M:%S"),
            "local_weekday_iso": str(local_naive.isoweekday()),
            "local_day_type_weekday_weekend": "weekday" if local_naive.weekday() < 5 else "weekend",
        }

    if len(candidates) == 2 and candidates[0][2] != candidates[1][2]:
        # The source has one row for this ambiguous label.  Fold 0 is the first
        # (daylight-time) occurrence.  The alternate standard-time occurrence is
        # not fabricated; the choice is exposed in every affected output row.
        fold, local, utc, _ = next(item for item in candidates if item[0] == 0)
        classification = "ambiguous_fall_back_local_time"
        resolution = "fold_0_first_occurrence_PDT_selected; alternate_fold_1_not_fabricated"
        mapping_status = "eligible_UTC_mapping_with_explicit_fall_back_fold_0_rule"
    else:
        fold, local, utc, _ = candidates[0]
        classification = "unambiguous_local_time"
        resolution = "not_applicable_unambiguous_local_time"
        mapping_status = "eligible_UTC_mapping_unambiguous_local_time"

    offset = local.strftime("%z")
    offset = offset[:3] + ":" + offset[3:]
    dst_active = bool(local.dst() and local.dst() != timedelta(0))
    return {
        **base,
        "timestamp_utc": utc.strftime(FMT),
        "timestamp_utc_iso8601": utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "utc_offset": offset,
        "dst_status": "PDT_DST_active_UTC_minus_07" if dst_active else "PST_standard_UTC_minus_08",
        "dst_local_time_classification": classification,
        "dst_fold_resolution": resolution,
        "utc_mapping_admission_status": mapping_status,
        "local_date": local_naive.strftime("%Y-%m-%d"),
        "local_clock_time": local_naive.strftime("%H:%M:%S"),
        "local_weekday_iso": str(local_naive.isoweekday()),
        "local_day_type_weekday_weekend": "weekday" if local_naive.weekday() < 5 else "weekend",
    }


def main() -> None:
    admission = read_csv(ADMISSION_IN)
    scope = read_csv(SCOPE_IN)
    scope_by_timestamp = {row["timestamp_local_civil_unresolved"]: row for row in scope}
    if len(admission) != len(scope) or len(scope_by_timestamp) != len(scope):
        raise ValueError("Input layers are not one-to-one by source local civil timestamp")

    admission_rows: list[dict[str, str]] = []
    scope_rows: list[dict[str, str]] = []
    local_classes: Counter[str] = Counter()
    mapping_statuses: Counter[str] = Counter()
    offsets: Counter[str] = Counter()
    utc_values: list[datetime] = []

    for row in admission:
        source_timestamp = row["timestamp_local_civil_unresolved"]
        mapping = local_mapping(source_timestamp)
        scope_row = scope_by_timestamp[source_timestamp]
        local_classes[mapping["dst_local_time_classification"]] += 1
        mapping_statuses[mapping["utc_mapping_admission_status"]] += 1
        if mapping["utc_offset"]:
            offsets[mapping["utc_offset"]] += 1
            utc_values.append(datetime.strptime(mapping["timestamp_utc"], FMT))

        admission_rows.append({
            **mapping,
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
        scope_rows.append({
            **mapping,
            "total_pv_raw_completeness_status": scope_row["total_pv_raw_completeness_status"],
            "total_pv_outlier_review_status": scope_row["total_pv_outlier_review_status"],
            "bess_boundary_status": scope_row["bess_boundary_status"],
            "building_load_dynamic_admission_status": scope_row["building_load_dynamic_admission_status"],
            "permitted_use": scope_row["permitted_use"],
            "reason": scope_row["reason"],
        })

    if len(set(utc_values)) != len(utc_values):
        raise ValueError("Local-civil mapping did not produce unique mapped UTC timestamps")
    write_csv(ADMISSION_OUT, admission_rows)
    write_csv(SCOPE_OUT, scope_rows)

    eligible_utc_rows = sum(
        row["building_load_dynamic_admission_status"] == ELIGIBLE
        and row["utc_mapping_admission_status"].startswith("eligible_")
        for row in admission_rows
    )
    summary = [
        ("rule_version", "v4.0_local_civil_primary_UTC_mapping"),
        ("source_files_preserved", "17 and 18 remain immutable source admission/scope layers; 20-33 remain frozen audit artifacts of the rejected UTC-primary interpretation"),
        ("source_timestamp_interpretation", "Original timestamp text is interpreted as America/Los_Angeles local civil time because PV daylight behavior is physically consistent under this interpretation; source metadata confirmation remains required"),
        ("iana_timezone", "America/Los_Angeles"),
        ("timezone_evidence_status", "physical_consistency_supported_local_civil_interpretation_pending_source_confirmation"),
        ("local_civil_primary_policy", "timestamp_local_civil is the primary recorded measurement label for local calendar, solar-time analysis and source ordering"),
        ("utc_mapping_policy", "timestamp_utc is a derived, auditable unique mapping for UTC joins and elapsed-time simulation only when utc_mapping_admission_status starts with eligible_"),
        ("fall_back_policy", "For a single source row at an ambiguous fall-back local label, map with PEP495 fold=0 (first PDT occurrence); do not fabricate the alternate fold=1 PST record; flag every such row"),
        ("spring_forward_policy", "A source label that is nonexistent in America/Los_Angeles has no valid UTC mapping, is retained with blank UTC fields and excluded from UTC-continuous inputs; it is never imputed or silently shifted"),
        ("source_rows", str(len(admission_rows))),
        ("mapped_UTC_rows", str(len(utc_values))),
        ("unique_mapped_UTC_rows", str(len(set(utc_values)))),
        ("unambiguous_local_time_rows", str(local_classes["unambiguous_local_time"])),
        ("ambiguous_fall_back_local_time_rows", str(local_classes["ambiguous_fall_back_local_time"])),
        ("nonexistent_spring_forward_local_time_rows", str(local_classes["nonexistent_local_time_no_valid_UTC_mapping"])),
        ("PDT_rows_UTC_minus_07", str(offsets["-07:00"])),
        ("PST_rows_UTC_minus_08", str(offsets["-08:00"])),
        ("eligible_building_load_rows_after_UTC_mapping", str(eligible_utc_rows)),
        ("derived_file_34", ADMISSION_OUT.name),
        ("derived_file_35", SCOPE_OUT.name),
    ]
    with SUMMARY_OUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["metric", "value"])
        writer.writerows(summary)

    print(f"source_rows={len(admission_rows)}")
    print(f"mapped_UTC_rows={len(utc_values)}; unique_mapped_UTC_rows={len(set(utc_values))}")
    print(f"local_time_classification_counts={dict(local_classes)}")
    print(f"eligible_building_load_rows_after_UTC_mapping={eligible_utc_rows}")


if __name__ == "__main__":
    main()
