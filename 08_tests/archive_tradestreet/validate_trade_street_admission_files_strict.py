import csv
import math
from collections import Counter, defaultdict
from datetime import datetime

ROOT = r"D:\matlab\C"
BASE = ROOT + r"\base data"
RAW = ROOT + r"\UCSD-Microgrid-Database\UCSD-Microgrid-Database-master\Data Files\Trade Street Off-campus Microgrid\TradeStreetBattery.csv"
FMT = "%Y-%m-%d %H:%M:%S"
RAW_FMT = "%m/%d/%Y %H:%M"


def load(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def number(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def same_number(a, b, tol=1e-10):
    x, y = number(a), number(b)
    return x is not None and y is not None and abs(x-y) <= tol

raw = load(RAW)
r16 = load(BASE + r"\16_TradeStreetBESS_boundary_rated_admission_layer.csv")
r17 = load(BASE + r"\17_TradeStreet_15min_building_load_dynamic_admission_layer.csv")
r18 = load(BASE + r"\18_TradeStreet_dynamic_input_scope_layer.csv")
r19 = {row["metric"]: row["value"] for row in load(BASE + r"\19_TradeStreet_admission_correction_summary.csv")}
r02 = load(BASE + r"\02_TradeStreetTotal_cleaning_layer.csv")
r11 = load(BASE + r"\11_TradeStreetPV_model_semantic_layer.csv")
r15 = load(BASE + r"\15_TradeStreet_outlier_candidate_review_layer.csv")

issues = []

def check(name, condition, detail=""):
    if not condition:
        issues.append((name, detail))

# A. Raw BESS -> file 16 provenance and rating admission
raw_groups = defaultdict(list)
for source_row, row in enumerate(raw, start=2):
    timestamp = datetime.strptime(row["DateTime"].strip(), RAW_FMT).strftime(FMT)
    raw_groups[timestamp].append((source_row, row["RealPower"].strip()))
map16 = {row["timestamp_local_civil_unresolved"]: row for row in r16}
check("16_timestamp_set_exact", set(map16) == set(raw_groups), f"16={len(map16)}, raw_unique={len(raw_groups)}")
check("16_unique_timestamp_rows", len(map16) == len(r16), f"rows={len(r16)}, unique={len(map16)}")
for timestamp, group in raw_groups.items():
    row = map16.get(timestamp)
    if row is None:
        continue
    expected_count = len(group)
    selected = int(row["selected_source_row_for_audit"])
    expected_value = dict(group).get(selected)
    raw_is_unique_finite = len(group) == 1 and number(group[0][1]) is not None
    over = raw_is_unique_finite and abs(number(group[0][1])) > 200.0
    expected_status = (
        "not_eligible_bess_over_trade_street_rating" if over else
        "eligible_for_bess_boundary_closure_within_200kW_rating" if raw_is_unique_finite else
        "not_eligible_raw_bess_quality"
    )
    check("16_raw_record_count", int(row["raw_record_count"]) == expected_count, timestamp)
    check("16_selected_source_row", expected_value is not None, timestamp)
    if raw_is_unique_finite:
        check("16_power_exact", same_number(row["p_bess_raw_kW_positive_charge_negative_discharge"], group[0][1]), timestamp)
    else:
        check("16_unusable_power_blank", row["p_bess_raw_kW_positive_charge_negative_discharge"] == "", timestamp)
    check("16_rating_constant", same_number(row["bess_rating_kW"], "200"), timestamp)
    check("16_rating_flag", int(row["bess_rating_screen_flag"]) == int(over), timestamp)
    check("16_admission_status", row["bess_boundary_admission_status"] == expected_status, f"{timestamp}: {row['bess_boundary_admission_status']} != {expected_status}")
    if over:
        check("16_overrating_reason", row["exclusion_reason"] == "absolute_raw_bess_power_exceeds_200kW_trade_street_rating", timestamp)

# B. Grids and sequencing
for label, rows in [("17", r17), ("18", r18)]:
    timestamps = [datetime.strptime(row["timestamp_local_civil_unresolved"], FMT) for row in rows]
    check(label + "_no_duplicate_timestamps", len(timestamps) == len(set(timestamps)), f"rows={len(timestamps)}")
    check(label + "_strict_ascending", all(timestamps[i] < timestamps[i+1] for i in range(len(timestamps)-1)))
    check(label + "_strict_15min_grid", all((timestamps[i+1] - timestamps[i]).total_seconds() == 900 for i in range(len(timestamps)-1)))
check("17_18_same_grid", [row["timestamp_local_civil_unresolved"] for row in r17] == [row["timestamp_local_civil_unresolved"] for row in r18])

# C. Cross-layer row mapping and deterministic admission logic
m02 = {row["timestamp_local_civil_unresolved"]: row for row in r02}
m11 = {row["timestamp_local_civil_unresolved"]: row for row in r11}
m16 = {row["timestamp_local_civil_unresolved"]: row for row in r16}
candidates = defaultdict(dict)
for row in r15:
    candidates[row["timestamp_local_civil_unresolved"]][row["stream"]] = row
m18 = {row["timestamp_local_civil_unresolved"]: row for row in r18}

for row in r17:
    timestamp = row["timestamp_local_civil_unresolved"]
    total, pv, bess = m02.get(timestamp), m11.get(timestamp), m16.get(timestamp)
    total_ok = total is not None and total["model_input_status"].startswith("eligible") and number(total["p_net_raw_kW"]) is not None
    pv_ok = pv is not None and pv["model_input_status"].startswith("eligible") and number(pv["p_pv_model_kW_nonnegative"]) is not None
    total_cand = candidates.get(timestamp, {}).get("total_net_meter", {}).get("outlier_candidate_flag") == "1"
    pv_cand = candidates.get(timestamp, {}).get("pv", {}).get("outlier_candidate_flag") == "1"
    bess_ok = bess is not None and bess["bess_boundary_admission_status"] == "eligible_for_bess_boundary_closure_within_200kW_rating"

    check("17_total_value_map", row["p_net_raw_kW"] == ("" if total is None else total["p_net_raw_kW"]), timestamp)
    check("17_pv_raw_map", row["p_pv_raw_kW"] == ("" if pv is None else pv["p_pv_raw_kW"]), timestamp)
    check("17_pv_model_map", row["p_pv_model_kW_nonnegative"] == ("" if pv is None else pv["p_pv_model_kW_nonnegative"]), timestamp)
    check("17_bess_value_map", row["p_bess_raw_kW_positive_charge_negative_discharge"] == ("" if bess is None else bess["p_bess_raw_kW_positive_charge_negative_discharge"]), timestamp)
    check("17_total_candidate_map", int(row["total_outlier_candidate_flag"]) == int(total_cand), timestamp)
    check("17_pv_candidate_map", int(row["pv_outlier_candidate_flag"]) == int(pv_cand), timestamp)
    check("17_bess_rating_map", row["bess_rating_screen_flag"] == ("" if bess is None else bess["bess_rating_screen_flag"]), timestamp)

    if total_ok and pv_ok:
        check("17_prebess_formula", same_number(row["p_net_plus_pv_model_pre_bess_candidate_kW"], number(total["p_net_raw_kW"]) + number(pv["p_pv_model_kW_nonnegative"])), timestamp)
    else:
        check("17_prebess_blank_when_base_invalid", row["p_net_plus_pv_model_pre_bess_candidate_kW"] == "", timestamp)
    if total_ok and pv_ok and bess is not None and number(bess["p_bess_raw_kW_positive_charge_negative_discharge"]) is not None:
        expected_building = number(total["p_net_raw_kW"]) + number(pv["p_pv_model_kW_nonnegative"]) - number(bess["p_bess_raw_kW_positive_charge_negative_discharge"])
        check("17_building_formula", same_number(row["p_building_load_candidate_kW_equals_p_net_plus_pv_model_minus_p_bess"], expected_building), timestamp)
    else:
        check("17_building_blank_when_no_numeric_bess_or_base_invalid", row["p_building_load_candidate_kW_equals_p_net_plus_pv_model_minus_p_bess"] == "", timestamp)

    if not total_ok or not pv_ok:
        expected_admission = "not_eligible_total_or_pv_raw_quality"
    elif total_cand or pv_cand:
        expected_admission = "pending_manual_review_total_or_pv_outlier_candidate"
    elif bess is None:
        expected_admission = "not_eligible_bess_boundary_unresolved"
    elif bess["bess_boundary_admission_status"] == "not_eligible_bess_over_trade_street_rating":
        expected_admission = "not_eligible_bess_over_trade_street_rating"
    elif not bess_ok:
        expected_admission = "not_eligible_bess_boundary_unresolved"
    else:
        expected_admission = "eligible_for_building_load_dynamic_input"
    check("17_admission_logic", row["building_load_dynamic_admission_status"] == expected_admission, f"{timestamp}: {row['building_load_dynamic_admission_status']} != {expected_admission}")

    r18row = m18[timestamp]
    expected_scope = "raw_total_pv_complete" if total_ok and pv_ok else "total_or_pv_not_raw_complete"
    expected_review = "pending_manual_review" if total_cand or pv_cand else "no_candidate_or_not_applicable"
    expected_bess = "missing_15min_grid_timestamp" if bess is None else bess["bess_boundary_admission_status"]
    expected_permit = "building_load_dynamic_degradation_ramp_startstop_input" if expected_admission == "eligible_for_building_load_dynamic_input" else "not_permitted_for_building_load_dynamic_input"
    check("18_total_pv_scope", r18row["total_pv_raw_completeness_status"] == expected_scope, timestamp)
    check("18_review_scope", r18row["total_pv_outlier_review_status"] == expected_review, timestamp)
    check("18_bess_scope", r18row["bess_boundary_status"] == expected_bess, timestamp)
    check("18_admission_map", r18row["building_load_dynamic_admission_status"] == expected_admission, timestamp)
    check("18_permitted_use", r18row["permitted_use"] == expected_permit, timestamp)

# D. Candidate flags obey their own recorded numerical screen
for row in r15:
    power = number(row["raw_power_kW"])
    candidate = row["outlier_candidate_flag"] == "1"
    stream = row["stream"]
    if power is None or not row["source_model_input_status"].startswith("eligible"):
        check("15_noneligible_not_candidate", not candidate, row["timestamp_local_civil_unresolved"])
    elif stream == "bess":
        check("15_bess_candidate_rule", candidate == (abs(power) > 200.0), row["timestamp_local_civil_unresolved"])
    elif stream == "total_net_meter" and row["screening_threshold_kW"] != "":
        check("15_total_candidate_rule", candidate == (abs(power - number(row["gaussian_expected_power_kW"])) > number(row["screening_threshold_kW"])), row["timestamp_local_civil_unresolved"])
    elif stream == "pv":
        expected = power < -3.0 or power > number(row["screening_threshold_kW"]) or (power > 1.0 and datetime.strptime(row["timestamp_local_civil_unresolved"], FMT).hour in [21, 22, 23, 0, 1, 2, 3, 4])
        check("15_pv_candidate_rule", candidate == expected, row["timestamp_local_civil_unresolved"])

# E. Recompute summary values from actual corrected layers
admission_count = Counter(row["building_load_dynamic_admission_status"] for row in r17)
summary_expected = {
    "total_candidate_count": sum(row["stream"] == "total_net_meter" and row["outlier_candidate_flag"] == "1" for row in r15),
    "pv_candidate_count": sum(row["stream"] == "pv" and row["outlier_candidate_flag"] == "1" for row in r15),
    "bess_over_200kW_candidate_count": sum(row["stream"] == "bess" and row["outlier_candidate_flag"] == "1" for row in r15),
    "bess_within_rating_rows": sum(row["bess_boundary_admission_status"] == "eligible_for_bess_boundary_closure_within_200kW_rating" for row in r16),
    "bess_over_rating_rows": sum(row["bess_boundary_admission_status"] == "not_eligible_bess_over_trade_street_rating" for row in r16),
    "building_load_dynamic_eligible_rows": admission_count["eligible_for_building_load_dynamic_input"],
    "building_load_pending_total_or_pv_outlier_review_rows": admission_count["pending_manual_review_total_or_pv_outlier_candidate"],
    "building_load_bess_over_rating_rows": admission_count["not_eligible_bess_over_trade_street_rating"],
    "building_load_bess_unresolved_rows": admission_count["not_eligible_bess_boundary_unresolved"],
    "building_load_total_or_pv_quality_ineligible_rows": admission_count["not_eligible_total_or_pv_raw_quality"],
}
for metric, expected in summary_expected.items():
    check("19_" + metric, r19.get(metric) == str(expected), f"summary={r19.get(metric)}, recomputed={expected}")
check("19_admission_total", sum(summary_expected[x] for x in [
    "building_load_dynamic_eligible_rows",
    "building_load_pending_total_or_pv_outlier_review_rows",
    "building_load_bess_over_rating_rows",
    "building_load_bess_unresolved_rows",
    "building_load_total_or_pv_quality_ineligible_rows",
]) == len(r17))

print("RAW_BESS_RECORDS", len(raw))
print("RAW_BESS_UNIQUE_TIMESTAMPS", len(raw_groups))
print("FILE16_ROWS", len(r16))
print("FILE17_ROWS", len(r17))
print("FILE18_ROWS", len(r18))
print("FILE15_ROWS", len(r15))
print("ADMISSION_COUNTS", dict(admission_count))
print("SUMMARY_EXPECTED", summary_expected)
print("ISSUE_COUNT", len(issues))
for name, detail in issues[:100]:
    print("ISSUE", name, detail)
