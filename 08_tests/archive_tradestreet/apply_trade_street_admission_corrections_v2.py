import csv
import math
import os
from collections import Counter
from datetime import datetime, timedelta

ROOT = r"D:\matlab\C"
BASE = os.path.join(ROOT, "base data")
TS_FORMAT = "%Y-%m-%d %H:%M:%S"


def read_csv(filename):
    with open(os.path.join(BASE, filename), encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(filename, fields, rows):
    with open(os.path.join(BASE, filename), "w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def finite_number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def gaussian_smoothed_grid(source_map, start, end, sigma=2.0):
    radius = int(math.ceil(4 * sigma))
    kernel = [math.exp(-0.5 * (offset / sigma) ** 2) for offset in range(-radius, radius + 1)]
    points = []
    current = start
    while current <= end:
        points.append(current)
        current += timedelta(minutes=15)
    values = [source_map.get(point) for point in points]
    smooth = {}
    for index, timestamp in enumerate(points):
        numerator = 0.0
        denominator = 0.0
        for local_index, weight in enumerate(kernel):
            neighbor_index = index + local_index - radius
            if 0 <= neighbor_index < len(values) and values[neighbor_index] is not None:
                numerator += weight * values[neighbor_index]
                denominator += weight
        smooth[timestamp] = numerator / denominator if denominator else None
    return smooth


def build_candidate_layer(total_rows, pv_rows, bess_rows):
    total_valid = {}
    for row in total_rows:
        value = finite_number(row["p_net_raw_kW"])
        if row["model_input_status"].startswith("eligible") and value is not None:
            total_valid[datetime.strptime(row["timestamp_local_civil_unresolved"], TS_FORMAT)] = value
    pv_valid = {}
    for row in pv_rows:
        value = finite_number(row["p_pv_raw_kW"])
        if row["model_input_status"].startswith("eligible") and value is not None:
            pv_valid[datetime.strptime(row["timestamp_local_civil_unresolved"], TS_FORMAT)] = value
    bess_valid = {}
    for row in bess_rows:
        value = finite_number(row["p_bess_raw_kW_positive_charge_negative_discharge"])
        if row["model_input_status"].startswith("eligible") and value is not None:
            bess_valid[datetime.strptime(row["timestamp_local_civil_unresolved"], TS_FORMAT)] = value

    total_start, total_end = min(total_valid), max(total_valid)
    pv_start, pv_end = min(pv_valid), max(pv_valid)
    total_smooth = gaussian_smoothed_grid(total_valid, total_start, total_end, sigma=2.0)
    pv_smooth = gaussian_smoothed_grid(pv_valid, pv_start, pv_end, sigma=1.0)
    pv_smoothed_max = max(value for value in pv_smooth.values() if value is not None)

    total_desc = sorted(total_valid, reverse=True)
    total_forward_mean_abs = {}
    for index, timestamp in enumerate(total_desc):
        window = total_desc[index:index + 672]
        if len(window) == 672:
            total_forward_mean_abs[timestamp] = sum(abs(total_valid[item]) for item in window) / 672.0
        else:
            total_forward_mean_abs[timestamp] = None

    result = []
    candidate_map = {"total": {}, "pv": {}, "bess": {}}

    for stream, rows, power_field in [
        ("total_net_meter", total_rows, "p_net_raw_kW"),
        ("pv", pv_rows, "p_pv_raw_kW"),
        ("bess", bess_rows, "p_bess_raw_kW_positive_charge_negative_discharge"),
    ]:
        for row in rows:
            timestamp_text = row["timestamp_local_civil_unresolved"]
            timestamp = datetime.strptime(timestamp_text, TS_FORMAT)
            raw_value = finite_number(row[power_field])
            raw_eligible = row["model_input_status"].startswith("eligible") and raw_value is not None
            smooth_value = ""
            threshold = ""
            rule = ""
            candidate = False
            screening_status = "not_screened_not_raw_eligible"
            rationale = row.get("exclusion_reason", "")

            if raw_eligible and stream == "total_net_meter":
                smooth = total_smooth.get(timestamp)
                mean_abs = total_forward_mean_abs.get(timestamp)
                smooth_value = "" if smooth is None else smooth
                threshold = "" if mean_abs is None else 3.5 * mean_abs
                rule = "gaussian_sigma_2_residual_gt_3p5_times_672_observation_mean_abs"
                if smooth is None or mean_abs is None:
                    screening_status = "not_screened_insufficient_forward_672_observations"
                    rationale = "insufficient_future_in_original_descending_order_observations_for_paper_code_inspired_threshold"
                else:
                    candidate = abs(raw_value - smooth) > 3.5 * mean_abs
                    screening_status = "candidate_requires_manual_review" if candidate else "screened_no_candidate"
                    rationale = "abs_raw_minus_gaussian_exceeds_calibrated_threshold" if candidate else ""
            elif raw_eligible and stream == "pv":
                smooth = pv_smooth.get(timestamp)
                smooth_value = "" if smooth is None else smooth
                threshold = 1.1 * pv_smoothed_max
                rule = "paper_pv_raw_lt_minus3_or_raw_gt_1p1_times_smoothed_max_or_night_raw_gt_1"
                night = timestamp.hour in [21, 22, 23, 0, 1, 2, 3, 4]
                reasons = []
                if raw_value < -3.0:
                    reasons.append("raw_below_minus_3kW")
                if raw_value > 1.1 * pv_smoothed_max:
                    reasons.append("raw_above_1p1_times_smoothed_max")
                if raw_value > 1.0 and night:
                    reasons.append("nighttime_raw_above_1kW")
                candidate = bool(reasons)
                screening_status = "candidate_requires_manual_review" if candidate else "screened_no_candidate"
                rationale = ";".join(reasons)
            elif raw_eligible and stream == "bess":
                threshold = 200.0
                rule = "paper_bess_absolute_power_gt_200kW_trade_street_rating"
                candidate = abs(raw_value) > 200.0
                screening_status = "candidate_requires_manual_review" if candidate else "screened_no_candidate"
                rationale = "absolute_power_exceeds_200kW_trade_street_rating" if candidate else ""

            candidate_map["total" if stream == "total_net_meter" else stream][timestamp_text] = candidate
            result.append({
                "stream": stream,
                "timestamp_local_civil_unresolved": timestamp_text,
                "raw_power_kW": row[power_field],
                "source_model_input_status": row["model_input_status"],
                "gaussian_expected_power_kW": smooth_value,
                "screening_threshold_kW": threshold,
                "screening_rule": rule,
                "outlier_candidate_flag": int(candidate),
                "outlier_review_status": screening_status,
                "candidate_rationale": rationale,
                "review_decision": "pending_manual_review" if candidate else "not_required_no_candidate",
                "reviewer": "",
                "review_note": "",
                "automatic_replacement_policy": "no_automatic_replacement_in_this_layer",
            })
    return result, candidate_map, pv_smoothed_max


def main():
    total_rows = read_csv("02_TradeStreetTotal_cleaning_layer.csv")
    pv_rows = read_csv("03_TradeStreetPV_cleaning_layer.csv")
    bess_rows = read_csv("12_TradeStreetBESS_boundary_layer.csv")
    candidate_rows, candidate_map, pv_smoothed_max = build_candidate_layer(total_rows, pv_rows, bess_rows)
    write_csv("15_TradeStreet_outlier_candidate_review_layer.csv", list(candidate_rows[0].keys()), candidate_rows)

    bess_v2 = []
    bess_counts = Counter()
    for row in bess_rows:
        timestamp = row["timestamp_local_civil_unresolved"]
        value = finite_number(row["p_bess_raw_kW_positive_charge_negative_discharge"])
        is_raw_valid = row["model_input_status"].startswith("eligible") and value is not None
        is_over_rating = is_raw_valid and abs(value) > 200.0
        if is_over_rating:
            admission = "not_eligible_bess_over_trade_street_rating"
            reason = "absolute_raw_bess_power_exceeds_200kW_trade_street_rating"
        elif is_raw_valid:
            admission = "eligible_for_bess_boundary_closure_within_200kW_rating"
            reason = ""
        else:
            admission = "not_eligible_raw_bess_quality"
            reason = row["exclusion_reason"] or "missing_or_invalid_bess_measurement"
        bess_counts[admission] += 1
        bess_v2.append({
            "timestamp_local_civil_unresolved": timestamp,
            "p_bess_raw_kW_positive_charge_negative_discharge": row["p_bess_raw_kW_positive_charge_negative_discharge"],
            "raw_record_count": row["raw_record_count"],
            "selected_source_row_for_audit": row["selected_source_row_for_audit"],
            "bess_power_value_status": row["bess_power_value_status"],
            "bess_sign_convention": row["bess_sign_convention"],
            "original_boundary_status": row["model_input_status"],
            "bess_rating_kW": 200.0,
            "bess_rating_screen_flag": int(is_over_rating),
            "outlier_review_status": "pending_manual_review" if is_over_rating else ("not_required_no_candidate" if is_raw_valid else "not_applicable_not_raw_valid"),
            "bess_boundary_admission_status": admission,
            "exclusion_reason": reason,
        })
    write_csv("16_TradeStreetBESS_boundary_rated_admission_layer.csv", list(bess_v2[0].keys()), bess_v2)

    total_by_time = {row["timestamp_local_civil_unresolved"]: row for row in total_rows}
    pv_by_time = {row["timestamp_local_civil_unresolved"]: row for row in read_csv("11_TradeStreetPV_model_semantic_layer.csv")}
    bess_by_time = {row["timestamp_local_civil_unresolved"]: row for row in bess_v2}
    start = datetime.strptime("2016-08-31 09:30:00", TS_FORMAT)
    end = datetime.strptime("2019-07-26 11:45:00", TS_FORMAT)
    building_rows = []
    dynamic_rows = []
    building_counts = Counter()
    current = start
    while current <= end:
        timestamp = current.strftime(TS_FORMAT)
        total = total_by_time.get(timestamp)
        pv = pv_by_time.get(timestamp)
        bess = bess_by_time.get(timestamp)
        total_ok = total is not None and total["model_input_status"].startswith("eligible")
        pv_ok = pv is not None and pv["model_input_status"].startswith("eligible")
        total_candidate = candidate_map["total"].get(timestamp, False)
        pv_candidate = candidate_map["pv"].get(timestamp, False)
        bess_ok = bess is not None and bess["bess_boundary_admission_status"] == "eligible_for_bess_boundary_closure_within_200kW_rating"
        p_net = "" if total is None else total["p_net_raw_kW"]
        p_pv_raw = "" if pv is None else pv["p_pv_raw_kW"]
        p_pv_model = "" if pv is None else pv["p_pv_model_kW_nonnegative"]
        p_bess = "" if bess is None else bess["p_bess_raw_kW_positive_charge_negative_discharge"]
        pre_bess = float(p_net) + float(p_pv_model) if total_ok and pv_ok else ""
        building = float(p_net) + float(p_pv_model) - float(p_bess) if total_ok and pv_ok and bess is not None and finite_number(p_bess) is not None else ""

        if not total_ok or not pv_ok:
            status = "not_eligible_total_or_pv_raw_quality"
            reason = ";".join(item for item in ["missing_or_invalid_total" if not total_ok else "", "missing_or_invalid_pv" if not pv_ok else ""] if item)
        elif total_candidate or pv_candidate:
            status = "pending_manual_review_total_or_pv_outlier_candidate"
            reason = ";".join(item for item in ["total_outlier_candidate" if total_candidate else "", "pv_outlier_candidate" if pv_candidate else ""] if item)
        elif bess is None:
            status = "not_eligible_bess_boundary_unresolved"
            reason = "missing_bess_15min_grid_timestamp"
        elif bess["bess_boundary_admission_status"] == "not_eligible_bess_over_trade_street_rating":
            status = "not_eligible_bess_over_trade_street_rating"
            reason = bess["exclusion_reason"]
        elif not bess_ok:
            status = "not_eligible_bess_boundary_unresolved"
            reason = bess["exclusion_reason"]
        else:
            status = "eligible_for_building_load_dynamic_input"
            reason = ""
        building_counts[status] += 1
        building_rows.append({
            "timestamp_local_civil_unresolved": timestamp,
            "p_net_raw_kW": p_net,
            "p_pv_raw_kW": p_pv_raw,
            "p_pv_model_kW_nonnegative": p_pv_model,
            "p_bess_raw_kW_positive_charge_negative_discharge": p_bess,
            "p_net_plus_pv_model_pre_bess_candidate_kW": pre_bess,
            "p_building_load_candidate_kW_equals_p_net_plus_pv_model_minus_p_bess": building,
            "total_outlier_candidate_flag": int(total_candidate),
            "pv_outlier_candidate_flag": int(pv_candidate),
            "bess_rating_screen_flag": "" if bess is None else bess["bess_rating_screen_flag"],
            "total_raw_status": "missing_15min_grid_timestamp" if total is None else total["model_input_status"],
            "pv_raw_status": "missing_15min_grid_timestamp" if pv is None else pv["model_input_status"],
            "bess_rated_admission_status": "missing_15min_grid_timestamp" if bess is None else bess["bess_boundary_admission_status"],
            "building_load_dynamic_admission_status": status,
            "timezone_status": "local_civil_time_unresolved_no_UTC_conversion",
            "exclusion_reason": reason,
        })
        dynamic_rows.append({
            "timestamp_local_civil_unresolved": timestamp,
            "total_pv_raw_completeness_status": "raw_total_pv_complete" if total_ok and pv_ok else "total_or_pv_not_raw_complete",
            "total_pv_outlier_review_status": "pending_manual_review" if total_candidate or pv_candidate else "no_candidate_or_not_applicable",
            "bess_boundary_status": "missing_15min_grid_timestamp" if bess is None else bess["bess_boundary_admission_status"],
            "building_load_dynamic_admission_status": status,
            "permitted_use": "building_load_dynamic_degradation_ramp_startstop_input" if status == "eligible_for_building_load_dynamic_input" else "not_permitted_for_building_load_dynamic_input",
            "reason": reason,
        })
        current += timedelta(minutes=15)
    write_csv("17_TradeStreet_15min_building_load_dynamic_admission_layer.csv", list(building_rows[0].keys()), building_rows)
    write_csv("18_TradeStreet_dynamic_input_scope_layer.csv", list(dynamic_rows[0].keys()), dynamic_rows)

    summary_rows = [
        {"metric": "rule_version", "value": "v2.0_incremental_admission_correction"},
        {"metric": "original_files_preserved", "value": "06-14 are not overwritten; 15-19 provide additive correction layers"},
        {"metric": "outlier_policy", "value": "Candidates are flags requiring manual review; this correction layer performs no automatic outlier replacement"},
        {"metric": "total_candidate_count", "value": sum(row["stream"] == "total_net_meter" and int(row["outlier_candidate_flag"]) == 1 for row in candidate_rows)},
        {"metric": "pv_candidate_count", "value": sum(row["stream"] == "pv" and int(row["outlier_candidate_flag"]) == 1 for row in candidate_rows)},
        {"metric": "bess_over_200kW_candidate_count", "value": sum(row["stream"] == "bess" and int(row["outlier_candidate_flag"]) == 1 for row in candidate_rows)},
        {"metric": "pv_smoothed_max_kW_sigma_1", "value": pv_smoothed_max},
        {"metric": "bess_within_rating_rows", "value": bess_counts["eligible_for_bess_boundary_closure_within_200kW_rating"]},
        {"metric": "bess_over_rating_rows", "value": bess_counts["not_eligible_bess_over_trade_street_rating"]},
        {"metric": "building_load_dynamic_eligible_rows", "value": building_counts["eligible_for_building_load_dynamic_input"]},
        {"metric": "building_load_pending_total_or_pv_outlier_review_rows", "value": building_counts["pending_manual_review_total_or_pv_outlier_candidate"]},
        {"metric": "building_load_bess_over_rating_rows", "value": building_counts["not_eligible_bess_over_trade_street_rating"]},
        {"metric": "building_load_bess_unresolved_rows", "value": building_counts["not_eligible_bess_boundary_unresolved"]},
        {"metric": "building_load_total_or_pv_quality_ineligible_rows", "value": building_counts["not_eligible_total_or_pv_raw_quality"]},
        {"metric": "dynamic_scope_correction", "value": "Raw Total/PV completeness alone is not building-load dynamic eligibility. Building-load input additionally requires a raw-valid, within-rating BESS record and no pending Total/PV outlier review."},
        {"metric": "building_load_formula", "value": "P_building_candidate=P_net+P_PV_model-P_BESS; only rows with eligible_for_building_load_dynamic_input may be used."},
    ]
    with open(os.path.join(BASE, "19_TradeStreet_admission_correction_summary.csv"), "w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["metric", "value"])
        writer.writeheader()
        writer.writerows(summary_rows)


if __name__ == "__main__":
    main()
