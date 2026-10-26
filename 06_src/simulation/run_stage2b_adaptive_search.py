"""Stage2b deterministic adaptive capacity search runner."""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Mapping

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "06_src") not in sys.path:
    sys.path.insert(0, str(ROOT / "06_src"))

from components.annual_input_contract import (
    PVInputContract,
    load_evaluation_years,
    scale_pv_available,
)
from ems.ems_stage2b import EMSState, Stage2BEMSConfig, dispatch_stage2b
from simulation.stage2b_capacity_gate import (
    CapacityCandidate,
    aggregate_cycle_metrics,
    evaluate_stage2b_admission,
    freeze_representatives,
    make_sobol_pool,
    map_sobol_capacities,
    normalized_violation,
    select_adaptive_batch,
    should_stop,
    solve_stage2b_three_year_cycle,
)
from simulation.run_stage2b_ems_audit import atomic_json
from utils.build_stage2b_governance import build_downstream_gate


def assert_audit_precedes_search(
    manifest_path: Path,
    input_hash: str,
    config_hash: str,
    code_hash: str,
) -> None:
    try:
        payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("search requires a completed audit manifest") from exc
    expected = {
        "status": "complete",
        "input_hash": input_hash,
        "config_hash": config_hash,
        "code_hash": code_hash,
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        raise RuntimeError("search requires a completed audit with matching hashes")


def tier2_activation_allowed(
    tier1_admitted_count: int, bottleneck_summary: Mapping[str, object]
) -> bool:
    triggers = bottleneck_summary.get("equipment_upper_bound_trigger", [])
    return int(tier1_admitted_count) < 5 and bool(triggers)


def _ems_config(candidate: CapacityCandidate, cfg: Mapping[str, object]) -> Stage2BEMSConfig:
    ems = cfg["ems"]
    aux = cfg["auxiliary"]
    return Stage2BEMSConfig(
        candidate.pv_kwp,
        candidate.bat_kwh,
        candidate.pemfc_kw,
        candidate.pemel_kw,
        candidate.h2_kg,
        soc_min=float(ems["soc_min"]),
        soc_max=float(ems["soc_max"]),
        dt_min=float(ems["dt_min"]),
        h2_min_fraction=float(ems["h2_min_fraction"]),
        h2_max_fraction=float(ems["h2_max_fraction"]),
        h2_leakage_per_day=float(ems["h2_leakage_per_day"]),
        min_stable_load_fc=float(ems["fc_min_stable_fraction"]),
        min_stable_load_el=float(ems["el_min_stable_fraction"]),
        minimum_run_steps=int(ems["minimum_run_steps"]),
        standby_kw=float(aux["standby_kW"]),
        pv_aux_fraction=float(aux["pv_fraction_rated_when_on"]),
        bat_aux_fraction=float(aux["bat_fraction_rated_when_on"]),
        fc_aux_fraction=float(aux["fc_fraction_rated_when_on"]),
        el_aux_fraction=float(aux["el_fraction_rated_when_on"]),
        eta_fc=float(ems["fc_efficiency_lhv"]),
        eta_el=float(ems["el_efficiency_lhv"]),
        bat_c_rate=float(ems["battery_c_rate"]),
    )


def evaluate_capacity_candidate(
    candidate: CapacityCandidate,
    years: Mapping[int, Mapping[str, np.ndarray]],
    cfg: Mapping[str, object],
) -> dict:
    """Evaluate one Sobol capacity through energy, coarse policy and periodic fine gates."""
    ems_cfg = _ems_config(candidate, cfg)
    contract = PVInputContract(
        reference_kw=float(cfg["pv_source"]["reference_capacity_kw"])
    )
    prepared = {
        year: {
            "load": np.asarray(years[year]["load"], dtype=float),
            "pv": scale_pv_available(
                np.asarray(years[year]["pv_raw"], dtype=float),
                candidate.pv_kwp,
                contract,
            ),
        }
        for year in (2016, 2017, 2018)
    }
    dt_h = ems_cfg.dt_h
    total_load = sum(float(np.sum(row["load"]) * dt_h) for row in prepared.values())
    total_pv = sum(float(np.sum(row["pv"]) * dt_h) for row in prepared.values())
    total_steps = sum(len(row["load"]) for row in prepared.values())
    lower_bound = total_load + ems_cfg.standby_kw * total_steps * dt_h
    energy_pass = total_pv + 1e-9 >= lower_bound
    necessary = {
        "passed": bool(energy_pass),
        "pv_energy_kwh": total_pv,
        "load_plus_standby_lower_bound_kwh": lower_bound,
        "ratio": total_pv / max(lower_bound, 1e-12),
    }
    strategies = tuple(cfg["ems"]["strategies"])
    coarse = {}
    initial = EMSState(0.50, ems_cfg.h2_kg * 0.50)
    for strategy in strategies:
        if energy_pass:
            result = dispatch_stage2b(
                prepared[2017]["load"],
                prepared[2017]["pv"],
                ems_cfg,
                strategy=strategy,
                initial_state=initial,
            )
            lpsp = float(
                np.sum(result.trace["p_uns"])
                / max(np.sum(prepared[2017]["load"]), 1e-12)
            )
            coarse[strategy] = {
                "lpsp": lpsp,
                "terminal_soc": result.final_state.soc,
                "terminal_h2_kg": result.final_state.h2_kg,
                "state_machine_violation_count": (
                    int(result.metrics["fc_el_overlap_count"])
                    + int(result.metrics["short_run_count"])
                    + int(result.metrics["invalid_dump_count"])
                ),
            }
        else:
            coarse[strategy] = {
                "lpsp": 1.0,
                "terminal_soc": initial.soc,
                "terminal_h2_kg": initial.h2_kg,
                "state_machine_violation_count": 0,
                "not_run_reason": "necessary_energy_failed",
            }
    fine_strategy = min(
        strategies,
        key=lambda name: (
            coarse[name]["state_machine_violation_count"],
            coarse[name]["lpsp"],
            -coarse[name]["terminal_h2_kg"],
            name,
        ),
    )

    if energy_pass:
        cycle = solve_stage2b_three_year_cycle(
            prepared,
            ems_cfg,
            initial,
            dispatch_stage2b,
            tolerance=float(cfg["cycle"]["soc_h2_solver_tolerance"]),
            max_repetitions=int(cfg["cycle"]["max_repetitions"]),
            strategy=fine_strategy,
        )
        metrics = aggregate_cycle_metrics(cycle, prepared, ems_cfg)
        decision = evaluate_stage2b_admission(metrics)
        score = normalized_violation(metrics)
    else:
        gap = max(0.0, 1.0 - necessary["ratio"])
        metrics = {
            "cycle_converged": False,
            "yearly": {
                year: {"lpsp": 1.0, "critical_satisfaction": 0.0}
                for year in (2016, 2017, 2018)
            },
            "three_year_lpsp": 1.0,
            "block_soc_delta": 0.0,
            "block_h2_delta_fraction": 0.0,
            "machine_state_boundary_mismatch": 0,
            "fc_el_overlap_count": 0,
            "short_run_count": 0,
            "el_critical_displacement_kwh": 0.0,
            "invalid_dump_count": 0,
            "bounds_pass": True,
            "max_power_residual_kw": 0.0,
            "max_h2_residual_kg": 0.0,
            "energy_gap_fraction": gap,
        }
        decision = evaluate_stage2b_admission(metrics)
        score = normalized_violation(metrics)

    sensitivity = {}
    if decision.admissible:
        for multiplier in (0.8, 1.2):
            cycle_s = solve_stage2b_three_year_cycle(
                prepared,
                ems_cfg,
                initial,
                dispatch_stage2b,
                tolerance=float(cfg["cycle"]["soc_h2_solver_tolerance"]),
                max_repetitions=int(cfg["cycle"]["max_repetitions"]),
                strategy=fine_strategy,
                aux_multiplier=multiplier,
            )
            metrics_s = aggregate_cycle_metrics(cycle_s, prepared, ems_cfg)
            sensitivity[str(multiplier)] = {
                "metrics": metrics_s,
                "admission": asdict(evaluate_stage2b_admission(metrics_s)),
            }

    state_checks = (
        int(metrics.get("fc_el_overlap_count", 1)) == 0
        and int(metrics.get("short_run_count", 1)) == 0
        and float(metrics.get("el_critical_displacement_kwh", 1.0)) == 0.0
        and int(metrics.get("invalid_dump_count", 1)) == 0
    )
    conservation = (
        float(metrics.get("max_power_residual_kw", float("inf"))) <= 1e-6
        and float(metrics.get("max_h2_residual_kg", float("inf"))) <= 1e-6
    )
    closure = (
        bool(metrics.get("cycle_converged", False))
        and float(metrics.get("block_soc_delta", 1.0)) <= 0.05
        and float(metrics.get("block_h2_delta_fraction", 1.0)) <= 0.05
        and int(metrics.get("machine_state_boundary_mismatch", 1)) == 0
    )
    return {
        "candidate_id": candidate.candidate_id,
        "domain": candidate.domain,
        "sobol_index": candidate.sobol_index,
        "pv_kwp": candidate.pv_kwp,
        "bat_kwh": candidate.bat_kwh,
        "pemfc_kw": candidate.pemfc_kw,
        "pemel_kw": candidate.pemel_kw,
        "h2_kg": candidate.h2_kg,
        "normalized_coordinates": list(candidate.normalized_coordinates),
        "necessary_energy": necessary,
        "coarse_strategies": coarse,
        "fine_strategy": fine_strategy,
        "metrics": metrics,
        "admission": asdict(decision),
        "admissible": decision.admissible,
        "violation": asdict(score),
        "auxiliary_sensitivity": sensitivity,
        "state_closure_pass": closure,
        "state_machine_checks_pass": state_checks,
        "conservation_pass": conservation,
        "paper_citable": bool(decision.admissible),
        "paper_citable_scope": "synthetic_reconstructed_scenario_only",
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stage2b_code_hash() -> str:
    digest = hashlib.sha256()
    base_files = (
        "06_src/ems/ems_stage2b.py",
        "06_src/simulation/stage2b_capacity_gate.py",
        "06_src/simulation/run_stage2b_ems_audit.py",
        "06_src/simulation/run_stage2b_adaptive_search.py",
    )
    stage2c_files = (
        "06_src/simulation/run_stage2c_ems_audit.py",
        "06_src/simulation/run_stage2c_adaptive_search.py",
    )
    for relative in base_files:
        path = ROOT / relative
        digest.update(relative.encode("utf-8"))
        digest.update(path.read_bytes())
    for relative in stage2c_files:
        path = ROOT / relative
        if path.exists():
            digest.update(relative.encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


_WORKER_YEARS = None
_WORKER_CONFIG = None


def _worker_init(years, config) -> None:
    global _WORKER_YEARS, _WORKER_CONFIG
    _WORKER_YEARS = years
    _WORKER_CONFIG = config


def _worker_evaluate(candidate: CapacityCandidate) -> dict:
    return evaluate_capacity_candidate(candidate, _WORKER_YEARS, _WORKER_CONFIG)


def _admission_margin(result: Mapping[str, object]) -> float:
    metrics = result["metrics"]
    yearly = metrics["yearly"]
    margins = [
        0.01 - max(float(row["lpsp"]) for row in yearly.values()),
        0.01 - float(metrics["three_year_lpsp"]),
        0.05 - float(metrics["block_soc_delta"]),
        0.05 - float(metrics["block_h2_delta_fraction"]),
        1e-6 - float(metrics["max_power_residual_kw"]),
        1e-6 - float(metrics["max_h2_residual_kg"]),
    ]
    return float(min(margins))


def _best_violation(results) -> dict:
    names = ("lpsp", "closure", "state_machine", "conservation")
    return {
        name: min(float(row["violation"][name]) for row in results)
        for name in names
    }


def _evaluate_batch(
    candidates,
    years,
    cfg,
    checkpoint_dir: Path,
    hashes: dict,
    max_workers: int,
) -> list:
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    completed = []
    pending = []
    for candidate in candidates:
        path = checkpoint_dir / f"{candidate.candidate_id}.json"
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                payload = None
            if payload and payload.get("status") == "complete" and all(
                payload.get(key) == value for key, value in hashes.items()
            ):
                completed.append(payload)
                continue
        pending.append(candidate)

    if pending and max_workers > 1:
        with ProcessPoolExecutor(
            max_workers=max_workers,
            initializer=_worker_init,
            initargs=(years, cfg),
        ) as executor:
            futures = {
                executor.submit(_worker_evaluate, candidate): candidate
                for candidate in pending
            }
            for future in as_completed(futures):
                candidate = futures[future]
                result = future.result()
                payload = {"status": "complete", **hashes, **result}
                atomic_json(checkpoint_dir / f"{candidate.candidate_id}.json", payload)
                completed.append(payload)
    else:
        for candidate in pending:
            result = evaluate_capacity_candidate(candidate, years, cfg)
            payload = {"status": "complete", **hashes, **result}
            atomic_json(checkpoint_dir / f"{candidate.candidate_id}.json", payload)
            completed.append(payload)
    return completed


def _search_tier(
    tier: str,
    cfg: dict,
    years,
    result_root: Path,
    hashes: dict,
    max_workers: int,
    candidate_limit: int = None,
) -> tuple:
    pool = make_sobol_pool(tier, cfg)
    candidates = map_sobol_capacities(pool, tier, cfg)
    pool_size = len(candidates)
    limit = min(pool_size, candidate_limit) if candidate_limit is not None else pool_size
    evaluated = {}
    history = []
    next_indices = list(range(min(int(cfg["search"]["initial_count"]), limit)))
    batch_number = 0
    checkpoint_dir = result_root / "search" / tier / "checkpoints"
    batch_dir = result_root / "search" / tier / "batches"
    batch_dir.mkdir(parents=True, exist_ok=True)

    while next_indices:
        batch_number += 1
        batch_candidates = [candidates[index] for index in next_indices]
        before_admissions = sum(bool(row["admissible"]) for row in evaluated.values())
        batch_results = _evaluate_batch(
            batch_candidates, years, cfg, checkpoint_dir, hashes, max_workers
        )
        for row in batch_results:
            evaluated[int(row["sobol_index"])] = row
        after_admissions = sum(bool(row["admissible"]) for row in evaluated.values())
        history.append(
            {
                "batch_number": batch_number,
                "batch_complete": True,
                "evaluated_count": len(evaluated),
                "new_admissions": after_admissions - before_admissions,
                "best": _best_violation(list(evaluated.values())),
                "sobol_indices": sorted(next_indices),
            }
        )
        atomic_json(batch_dir / f"batch_{batch_number:03d}.json", history[-1])
        if candidate_limit is not None and len(evaluated) >= limit:
            break
        if should_stop(history, after_admissions, len(evaluated), pool_size):
            break
        remaining_budget = limit - len(evaluated)
        if remaining_budget <= 0:
            break
        score_map = {
            index: np.array(
                [
                    row["violation"]["lpsp"],
                    row["violation"]["closure"],
                    row["violation"]["state_machine"],
                    row["violation"]["conservation"],
                ],
                dtype=float,
            )
            for index, row in evaluated.items()
        }
        selected = select_adaptive_batch(
            pool,
            sorted(evaluated),
            score_map,
            exploit_count=int(cfg["search"]["exploit_count"]),
            coverage_count=int(cfg["search"]["coverage_count"]),
            neighbors=int(cfg["search"]["violation_predictor_neighbors"]),
        )
        next_indices = [item.sobol_index for item in selected[:remaining_budget]]

    rows = list(evaluated.values())
    admitted = [row for row in rows if bool(row["admissible"])]
    summary = {
        "tier": tier,
        "evaluated_count": len(rows),
        "admitted_count": len(admitted),
        "batch_count": len(history),
        "best_violation": _best_violation(rows),
        "history": history,
    }
    atomic_json(result_root / "search" / tier / "tier_summary.json", summary)
    return summary, rows, admitted


def run_search(
    config_path: Path,
    data_path: Path,
    result_root: Path,
    *,
    years=None,
    candidate_limit: int = None,
    max_workers: int = None,
    allow_tier2: bool = True,
) -> dict:
    """Run Tier1 and the audited conditional Tier2 search with resumable checkpoints."""
    config_path = Path(config_path)
    data_path = Path(data_path)
    result_root = Path(result_root)
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    input_hash = _sha256(data_path)
    config_hash = _sha256(config_path)
    code_hash = stage2b_code_hash()
    assert_audit_precedes_search(
        result_root / "audit" / "audit_manifest.json",
        input_hash,
        config_hash,
        code_hash,
    )
    if years is None:
        loaded = load_evaluation_years(data_path)
        years = {
            year: {
                "load": frame["load_kw"].to_numpy(dtype=float),
                "pv_raw": frame["pv_raw_kw"].to_numpy(dtype=float),
            }
            for year, frame in loaded.items()
        }
    hashes = {
        "input_hash": input_hash,
        "config_hash": config_hash,
        "code_hash": code_hash,
    }
    workers = int(max_workers if max_workers is not None else cfg["parallel"]["max_workers"])
    tier_summaries = {}
    all_rows = []
    all_admitted = []
    tier1_summary, tier1_rows, tier1_admitted = _search_tier(
        "tier1", cfg, years, result_root, hashes, workers, candidate_limit
    )
    tier_summaries["tier1"] = tier1_summary
    all_rows.extend(tier1_rows)
    all_admitted.extend(tier1_admitted)

    bottleneck_path = result_root / "audit" / "bottleneck_summary.json"
    bottleneck = (
        json.loads(bottleneck_path.read_text(encoding="utf-8"))
        if bottleneck_path.exists()
        else {"equipment_upper_bound_trigger": []}
    )
    tier2_allowed = (
        allow_tier2
        and candidate_limit is None
        and tier2_activation_allowed(len(tier1_admitted), bottleneck)
    )
    if tier2_allowed:
        tier2_summary, tier2_rows, tier2_admitted = _search_tier(
            "tier2", cfg, years, result_root, hashes, workers, None
        )
        tier_summaries["tier2"] = tier2_summary
        all_rows.extend(tier2_rows)
        all_admitted.extend(tier2_admitted)

    for row in all_admitted:
        row["admission_margin"] = _admission_margin(row)
    representatives = freeze_representatives(all_admitted, cfg) if all_admitted else []
    verified_payload = {
        "schema_version": "rectification_v2_stage2b.admissions.1",
        "candidates": all_admitted,
        "representatives": representatives,
    }
    atomic_json(result_root / "search" / "verified_admissions.json", verified_payload)
    gate = build_downstream_gate(all_admitted)
    atomic_json(result_root / "governance" / "downstream_gate.json", gate)
    summary = {
        "schema_version": "rectification_v2_stage2b.search.1",
        "status": "complete",
        **hashes,
        "tier_summaries": tier_summaries,
        "tier2_activated": tier2_allowed,
        "evaluated_total": len(all_rows),
        "verified_admitted_count": len(all_admitted),
        "representative_count": len(representatives),
        "stage3_basic_degradation_allowed": gate["stage3_basic_degradation_allowed"],
        "robust_six_path_planning_allowed": gate["robust_six_path_planning_allowed"],
        "frozen_modules_executed": [],
    }
    atomic_json(result_root / "search" / "search_summary.json", summary)
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--data", type=Path, default=ROOT / "02_data_processed" / "tradestreet_pipeline" / "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv")
    parser.add_argument("--result-root", type=Path, default=ROOT / "07_results" / "rectification_v2_stage2b")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    run_search(args.config, args.data, args.result_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "assert_audit_precedes_search", "evaluate_capacity_candidate", "main",
    "run_search", "stage2b_code_hash", "tier2_activation_allowed",
]
