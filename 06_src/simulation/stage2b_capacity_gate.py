"""Complete-state Stage2b cycle, admission and normalized violation logic."""
from __future__ import annotations

from dataclasses import dataclass
from math import log2
from typing import Dict, List, Mapping, Sequence

import numpy as np

from ems.ems_stage2b import (
    DispatchResult,
    EMSState,
    Stage2BEMSConfig,
    positive_run_lengths,
)


@dataclass
class Stage2BYearResult:
    year: int
    start_state: EMSState
    end_state: EMSState
    dispatch: DispatchResult


@dataclass
class Stage2BCycleResult:
    converged: bool
    repetitions: int
    block_start_state: EMSState
    block_end_state: EMSState
    block_soc_delta: float
    block_h2_delta_fraction: float
    machine_state_boundary_mismatch: int
    year_results: Dict[int, Stage2BYearResult]


@dataclass(frozen=True)
class ViolationScore:
    lpsp: float
    closure: float
    state_machine: float
    conservation: float
    maximum: float
    total: float


@dataclass(frozen=True)
class AdmissionDecision:
    admissible: bool
    checks: Dict[str, bool]
    failure_reasons: tuple


@dataclass(frozen=True)
class CapacityCandidate:
    candidate_id: str
    domain: str
    sobol_index: int
    pv_kwp: float
    bat_kwh: float
    pemfc_kw: float
    pemel_kw: float
    h2_kg: float
    normalized_coordinates: tuple


@dataclass(frozen=True)
class AdaptiveSelection:
    sobol_index: int
    selection_role: str
    predicted_maximum_violation: float
    predicted_total_violation: float


def _machine_tuple(state: EMSState) -> tuple:
    return (
        bool(state.fc_on),
        int(state.fc_steps_remaining),
        bool(state.el_on),
        int(state.el_steps_remaining),
    )


def solve_stage2b_three_year_cycle(
    years: Mapping[int, Mapping[str, np.ndarray]],
    config: Stage2BEMSConfig,
    initial_state: EMSState,
    dispatch_fn,
    tolerance: float = 0.001,
    max_repetitions: int = 50,
    **dispatch_kwargs,
) -> Stage2BCycleResult:
    """Repeat the continuous 2016–2018 map until every carried state closes."""
    next_state = initial_state
    final_year_results: Dict[int, Stage2BYearResult] = {}
    converged = False
    block_start = initial_state
    block_end = initial_state
    soc_delta = float("inf")
    h2_delta_fraction = float("inf")
    machine_mismatch = 1

    for repetition in range(1, max_repetitions + 1):
        block_start = next_state
        current = block_start
        year_results: Dict[int, Stage2BYearResult] = {}
        for year in (2016, 2017, 2018):
            item = years[year]
            result = dispatch_fn(
                item["load"],
                item["pv"],
                config,
                initial_state=current,
                **dispatch_kwargs,
            )
            year_results[year] = Stage2BYearResult(
                year=year,
                start_state=current,
                end_state=result.final_state,
                dispatch=result,
            )
            current = result.final_state
        block_end = current
        soc_delta = abs(block_end.soc - block_start.soc)
        h2_delta_fraction = abs(block_end.h2_kg - block_start.h2_kg) / max(
            config.h2_kg, 1e-12
        )
        machine_mismatch = int(_machine_tuple(block_end) != _machine_tuple(block_start))
        final_year_results = year_results
        if (
            soc_delta <= tolerance
            and h2_delta_fraction <= tolerance
            and machine_mismatch == 0
        ):
            converged = True
            break
        next_state = block_end

    return Stage2BCycleResult(
        converged=converged,
        repetitions=repetition,
        block_start_state=block_start,
        block_end_state=block_end,
        block_soc_delta=soc_delta,
        block_h2_delta_fraction=h2_delta_fraction,
        machine_state_boundary_mismatch=machine_mismatch,
        year_results=final_year_results,
    )


def check_stage2b_conservation(
    result: DispatchResult,
    power_tolerance_kw: float = 1e-6,
    h2_tolerance_kg: float = 1e-6,
) -> dict:
    power = float(np.max(np.abs(result.trace["power_residual_kw"]))) if len(result.commands) else 0.0
    h2 = float(np.max(np.abs(result.trace["h2_residual_kg"]))) if len(result.commands) else 0.0
    return {
        "max_power_residual_kw": power,
        "max_h2_residual_kg": h2,
        "power_pass": power <= power_tolerance_kw,
        "h2_pass": h2 <= h2_tolerance_kg,
    }


def periodic_positive_run_lengths(values: np.ndarray) -> List[int]:
    """Return run lengths on a periodic block, merging its last and first runs."""
    active = np.asarray(values, dtype=float) > 1e-9
    runs = positive_run_lengths(active.astype(float))
    if len(runs) >= 2 and active[0] and active[-1]:
        runs = [runs[0] + runs[-1]] + runs[1:-1]
    return runs


def aggregate_cycle_metrics(
    cycle: Stage2BCycleResult,
    years: Mapping[int, Mapping[str, np.ndarray]],
    config: Stage2BEMSConfig,
) -> dict:
    """Aggregate one converged three-year block for admission and audit."""
    yearly = {}
    total_load = 0.0
    total_uns = 0.0
    commands = []
    soc_parts = []
    h2_parts = []
    power_residual_parts = []
    h2_residual_parts = []
    fc_on_parts = []
    el_on_parts = []
    displacement = 0.0
    invalid_dump = 0
    emergency_trips = 0
    for year in (2016, 2017, 2018):
        row = cycle.year_results[year]
        result = row.dispatch
        load = np.asarray(years[year]["load"], dtype=float)
        unserved = np.asarray(result.trace["p_uns"], dtype=float)
        load_energy = float(np.sum(load) * config.dt_h)
        uns_energy = float(np.sum(unserved) * config.dt_h)
        lpsp = uns_energy / max(load_energy, 1e-12)
        yearly[year] = {
            "lpsp": lpsp,
            "critical_satisfaction": 1.0 - lpsp,
            "load_energy_kwh": load_energy,
            "unserved_energy_kwh": uns_energy,
            "start_soc": row.start_state.soc,
            "end_soc": row.end_state.soc,
            "start_h2_kg": row.start_state.h2_kg,
            "end_h2_kg": row.end_state.h2_kg,
        }
        total_load += load_energy
        total_uns += uns_energy
        commands.append(np.asarray(result.commands, dtype=float))
        soc_parts.append(np.asarray(result.trace["soc"], dtype=float))
        h2_parts.append(np.asarray(result.trace["h2_kg"], dtype=float))
        power_residual_parts.append(np.asarray(result.trace["power_residual_kw"], dtype=float))
        h2_residual_parts.append(np.asarray(result.trace["h2_residual_kg"], dtype=float))
        fc_on_parts.append(np.asarray(result.trace["fc_on"], dtype=bool))
        el_on_parts.append(np.asarray(result.trace["el_on"], dtype=bool))
        displacement += float(result.metrics.get("el_critical_displacement_kwh", 0.0))
        invalid_dump += int(result.metrics.get("invalid_dump_count", 0))
        emergency_trips += int(result.metrics.get("emergency_trip_count", 0))

    all_commands = np.vstack(commands)
    fc_runs = periodic_positive_run_lengths(all_commands[:, 1])
    el_runs = periodic_positive_run_lengths(all_commands[:, 2])
    short_runs = sum(
        length < config.minimum_run_steps for length in fc_runs + el_runs
    )
    soc = np.concatenate(soc_parts)
    h2 = np.concatenate(h2_parts)
    fc_on_trace = np.concatenate(fc_on_parts)
    el_on_trace = np.concatenate(el_on_parts)
    power_residual = np.concatenate(power_residual_parts)
    h2_residual = np.concatenate(h2_residual_parts)
    bounds_pass = bool(
        np.all(soc >= config.soc_min - 1e-9)
        and np.all(soc <= config.soc_max + 1e-9)
        and np.all(h2 >= config.h2_kg * config.h2_min_fraction - 1e-9)
        and np.all(h2 <= config.h2_kg * config.h2_max_fraction + 1e-9)
    )
    return {
        "cycle_converged": cycle.converged,
        "cycle_repetitions": cycle.repetitions,
        "yearly": yearly,
        "three_year_lpsp": total_uns / max(total_load, 1e-12),
        "three_year_critical_satisfaction": 1.0 - total_uns / max(total_load, 1e-12),
        "block_soc_delta": cycle.block_soc_delta,
        "block_h2_delta_fraction": cycle.block_h2_delta_fraction,
        "machine_state_boundary_mismatch": cycle.machine_state_boundary_mismatch,
        "fc_el_overlap_count": int(np.sum(fc_on_trace & el_on_trace)),
        "fc_run_lengths_steps": fc_runs,
        "el_run_lengths_steps": el_runs,
        "short_run_count": int(short_runs),
        "el_critical_displacement_kwh": displacement,
        "invalid_dump_count": invalid_dump,
        "emergency_trip_count": emergency_trips,
        "bounds_pass": bounds_pass,
        "max_power_residual_kw": float(np.max(np.abs(power_residual))),
        "max_h2_residual_kg": float(np.max(np.abs(h2_residual))),
    }


def normalized_violation(metrics: Mapping[str, object]) -> ViolationScore:
    yearly = metrics["yearly"]
    max_year_lpsp = max(float(row["lpsp"]) for row in yearly.values())
    lpsp = max(
        0.0,
        max_year_lpsp / 0.01 - 1.0,
        float(metrics["three_year_lpsp"]) / 0.01 - 1.0,
    )
    closure = max(
        0.0,
        float(metrics["block_soc_delta"]) / 0.05 - 1.0,
        float(metrics["block_h2_delta_fraction"]) / 0.05 - 1.0,
        float(int(metrics.get("machine_state_boundary_mismatch", 0) != 0)),
        float(not bool(metrics.get("cycle_converged", False))),
    )
    state_machine = max(
        float(int(metrics.get("fc_el_overlap_count", 0) > 0)),
        float(int(metrics.get("short_run_count", 0) > 0)),
        float(float(metrics.get("el_critical_displacement_kwh", 0.0)) > 0.0),
        float(int(metrics.get("invalid_dump_count", 0) > 0)),
        float(not bool(metrics.get("bounds_pass", False))),
    )
    conservation = max(
        0.0,
        float(metrics.get("max_power_residual_kw", float("inf"))) / 1e-6 - 1.0,
        float(metrics.get("max_h2_residual_kg", float("inf"))) / 1e-6 - 1.0,
    )
    values = (lpsp, closure, state_machine, conservation)
    return ViolationScore(
        lpsp=lpsp,
        closure=closure,
        state_machine=state_machine,
        conservation=conservation,
        maximum=max(values),
        total=sum(values),
    )


def evaluate_stage2b_admission(metrics: Mapping[str, object]) -> AdmissionDecision:
    yearly = metrics["yearly"]
    checks = {
        "cycle_converged": bool(metrics.get("cycle_converged", False)),
        "yearly_lpsp": all(float(row["lpsp"]) <= 0.01 for row in yearly.values()),
        "three_year_lpsp": float(metrics["three_year_lpsp"]) <= 0.01,
        "critical_satisfaction": all(
            float(row["critical_satisfaction"]) >= 0.99 for row in yearly.values()
        ),
        "soc_h2_closure": (
            float(metrics["block_soc_delta"]) <= 0.05
            and float(metrics["block_h2_delta_fraction"]) <= 0.05
        ),
        "machine_state_closure": int(metrics.get("machine_state_boundary_mismatch", 1)) == 0,
        "fc_el_mutual_exclusion": int(metrics.get("fc_el_overlap_count", 1)) == 0,
        "minimum_run_segments": int(metrics.get("short_run_count", 1)) == 0,
        "el_critical_priority": float(metrics.get("el_critical_displacement_kwh", 1.0)) == 0.0,
        "valid_dump": int(metrics.get("invalid_dump_count", 1)) == 0,
        "power_conservation": float(metrics.get("max_power_residual_kw", float("inf"))) <= 1e-6,
        "h2_conservation": float(metrics.get("max_h2_residual_kg", float("inf"))) <= 1e-6,
        "bounds": bool(metrics.get("bounds_pass", False)),
    }
    failures = tuple(name for name, passed in checks.items() if not passed)
    return AdmissionDecision(not failures, checks, failures)


def make_sobol_pool(tier: str, config: Mapping[str, object]) -> np.ndarray:
    """Generate the approved fixed 2048-point scrambled Sobol prefix once."""
    from scipy.stats import qmc

    if tier not in {"tier1", "tier2"}:
        raise ValueError("tier must be tier1 or tier2")
    spec = config["search"]["sobol"]
    size = int(spec["pool_size_per_tier"])
    exponent = int(round(log2(size)))
    if 2 ** exponent != size:
        raise ValueError("Sobol pool size must be a power of two")
    sampler = qmc.Sobol(
        d=int(spec["d"]),
        scramble=bool(spec["scramble"]),
        seed=int(spec["seed"]),
    )
    return sampler.random_base2(m=exponent)


def _linear_map(value: float, bounds: Sequence[float]) -> float:
    return float(bounds[0] + value * (bounds[1] - bounds[0]))


def map_sobol_capacities(
    pool: np.ndarray, tier: str, config: Mapping[str, object]
) -> List[CapacityCandidate]:
    """Map normalized points to Tier1 or the strict Tier2 increment union."""
    points = np.asarray(pool, dtype=float)
    if points.ndim != 2 or points.shape[1] != 5:
        raise ValueError("Sobol pool must have shape (n, 5)")
    domains = config["capacity_domains"]
    bounds = domains["tier1"] if tier == "tier1" else domains["tier2_total"]
    pv_bounds = config["pv_domain_kWp"]
    rows: List[CapacityCandidate] = []
    device_names = ("BAT_kWh", "PEMFC_kW", "PEMEL_kW", "H2_kg")
    tier1_max = np.array([float(domains["tier1"][name][1]) for name in device_names])
    tier2_max = np.array([float(domains["tier2_total"][name][1]) for name in device_names])

    for index, point in enumerate(points):
        pv = _linear_map(point[0], pv_bounds)
        devices = np.array(
            [_linear_map(point[j + 1], bounds[name]) for j, name in enumerate(device_names)],
            dtype=float,
        )
        if tier == "tier2" and np.all(devices <= tier1_max):
            axis = int(np.argmax(point[1:]))
            strict_low = np.nextafter(tier1_max[axis], tier2_max[axis])
            devices[axis] = strict_low + point[axis + 1] * (tier2_max[axis] - strict_low)
        if tier == "tier2" and not np.any(devices > tier1_max):
            raise AssertionError("Tier2 mapping failed strict increment rule")
        candidate_id = f"{tier}__sobol{index:04d}"
        rows.append(
            CapacityCandidate(
                candidate_id=candidate_id,
                domain=tier,
                sobol_index=index,
                pv_kwp=pv,
                bat_kwh=float(devices[0]),
                pemfc_kw=float(devices[1]),
                pemel_kw=float(devices[2]),
                h2_kg=float(devices[3]),
                normalized_coordinates=tuple(float(x) for x in point),
            )
        )
    return rows


def select_adaptive_batch(
    pool: np.ndarray,
    evaluated_indices: Sequence[int],
    scores: Mapping[int, np.ndarray],
    exploit_count: int = 102,
    coverage_count: int = 26,
    neighbors: int = 8,
) -> List[AdaptiveSelection]:
    """Select exploitation by violation prediction and coverage by maximin distance."""
    points = np.asarray(pool, dtype=float)
    evaluated = np.array(sorted(set(int(x) for x in evaluated_indices)), dtype=int)
    if evaluated.size == 0:
        raise ValueError("at least one evaluated sample is required")
    if any(index not in scores for index in evaluated):
        raise ValueError("every evaluated sample needs a four-class score")
    remaining = np.array(
        [index for index in range(len(points)) if index not in set(evaluated.tolist())],
        dtype=int,
    )
    if remaining.size < exploit_count + coverage_count:
        raise ValueError("not enough unevaluated Sobol points")

    distance = np.linalg.norm(
        points[remaining, None, :] - points[evaluated][None, :, :], axis=2
    )
    k = min(neighbors, evaluated.size)
    nearest_order = np.argpartition(distance, kth=k - 1, axis=1)[:, :k]
    nearest_distance = np.take_along_axis(distance, nearest_order, axis=1)
    evaluated_scores = np.vstack([np.asarray(scores[int(index)], dtype=float) for index in evaluated])
    neighbor_scores = evaluated_scores[nearest_order]
    weights = 1.0 / np.square(nearest_distance + 1e-12)
    prediction = np.sum(neighbor_scores * weights[:, :, None], axis=1) / np.sum(weights, axis=1)[:, None]
    predicted_max = np.max(prediction, axis=1)
    predicted_total = np.sum(prediction, axis=1)
    exploit_order = np.lexsort((remaining, predicted_total, predicted_max))[:exploit_count]
    exploit_indices = remaining[exploit_order]
    prediction_by_index = {
        int(index): (float(predicted_max[pos]), float(predicted_total[pos]))
        for pos, index in enumerate(remaining)
    }
    selections = [
        AdaptiveSelection(int(index), "exploit", *prediction_by_index[int(index)])
        for index in exploit_indices
    ]

    excluded = set(evaluated.tolist()) | set(exploit_indices.tolist())
    coverage_candidates = np.array(
        [index for index in range(len(points)) if index not in excluded], dtype=int
    )
    minimum_distance = np.min(
        np.linalg.norm(
            points[coverage_candidates, None, :] - points[evaluated][None, :, :], axis=2
        ),
        axis=1,
    )
    for _ in range(coverage_count):
        maximum = float(np.max(minimum_distance))
        tied_positions = np.flatnonzero(np.isclose(minimum_distance, maximum, rtol=0, atol=1e-15))
        chosen_position = int(tied_positions[np.argmin(coverage_candidates[tied_positions])])
        chosen_index = int(coverage_candidates[chosen_position])
        selections.append(
            AdaptiveSelection(chosen_index, "coverage_maximin", maximum, maximum)
        )
        new_distance = np.linalg.norm(points[coverage_candidates] - points[chosen_index], axis=1)
        minimum_distance = np.minimum(minimum_distance, new_distance)
        minimum_distance[chosen_position] = -np.inf
    return selections


def should_stop(
    history: Sequence[Mapping[str, object]],
    admitted_count: int,
    evaluated_count: int,
    pool_size: int,
) -> bool:
    if admitted_count >= 5 and history and bool(history[-1].get("batch_complete", False)):
        return True
    if evaluated_count >= pool_size:
        return True
    if len(history) < 5:
        return False
    tail = history[-5:]
    if any(int(row.get("new_admissions", 0)) > 0 for row in tail[1:]):
        return False
    classes = ("lpsp", "closure", "state_machine", "conservation")
    for previous, current in zip(tail[:-1], tail[1:]):
        for name in classes:
            improvement = float(previous["best"][name]) - float(current["best"][name])
            if improvement >= 0.05:
                return False
    return True


def freeze_representatives(
    admitted_rows: Sequence[Mapping[str, object]], config: Mapping[str, object]
) -> List[dict]:
    """Freeze at most five unique representatives using global Stage bounds."""
    rep_bounds = config.get("representative_bounds")
    if rep_bounds is not None:
        bounds = {
            "pv_kwp": (float(rep_bounds["pv_kwp"][0]), float(rep_bounds["pv_kwp"][1])),
            "bat_kwh": (float(rep_bounds["bat_kwh"][0]), float(rep_bounds["bat_kwh"][1])),
            "pemfc_kw": (float(rep_bounds["pemfc_kw"][0]), float(rep_bounds["pemfc_kw"][1])),
            "pemel_kw": (float(rep_bounds["pemel_kw"][0]), float(rep_bounds["pemel_kw"][1])),
            "h2_kg": (float(rep_bounds["h2_kg"][0]), float(rep_bounds["h2_kg"][1])),
        }
        bounds_label = config.get("search", {}).get("representative_normalization", "config")
    else:
        bounds = {
            "pv_kwp": (160.0, 320.0),
            "bat_kwh": (180.0, 2400.0),
            "pemfc_kw": (45.0, 180.0),
            "pemel_kw": (45.0, 320.0),
            "h2_kg": (40.0, 6000.0),
        }
        bounds_label = "stage2b_global"
    prepared = []
    for original in admitted_rows:
        row = dict(original)
        normalized = {
            name: (float(row[name]) - low) / (high - low)
            for name, (low, high) in bounds.items()
        }
        vector = np.array([normalized[name] for name in bounds], dtype=float)
        row["_normalized"] = normalized
        row["_compact"] = float(np.linalg.norm(vector))
        row["_balanced"] = float(np.max(vector))
        row["_hydrogen"] = float(
            normalized["pemfc_kw"] + normalized["pemel_kw"]
            + normalized["h2_kg"] - normalized["bat_kwh"]
        )
        row["_battery"] = float(
            normalized["bat_kwh"]
            - (normalized["pemfc_kw"] + normalized["pemel_kw"] + normalized["h2_kg"]) / 3.0
        )
        prepared.append(row)

    roles = (
        ("compact", lambda row: (row["_compact"], row["candidate_id"])),
        ("balanced", lambda row: (row["_balanced"], row["candidate_id"])),
        ("hydrogen_dominant", lambda row: (-row["_hydrogen"], row["candidate_id"])),
        ("battery_dominant", lambda row: (-row["_battery"], row["candidate_id"])),
        ("critical", lambda row: (float(row["admission_margin"]), row["candidate_id"])),
    )
    chosen: List[dict] = []
    used = set()
    for role, key in roles:
        for candidate in sorted(prepared, key=key):
            if candidate["candidate_id"] in used:
                continue
            payload = {
                name: value for name, value in candidate.items() if not name.startswith("_")
            }
            payload["representative_role"] = role
            payload["normalization_bounds"] = bounds_label
            chosen.append(payload)
            used.add(candidate["candidate_id"])
            break
    return chosen


__all__ = [
    "AdaptiveSelection", "AdmissionDecision", "CapacityCandidate",
    "Stage2BCycleResult", "Stage2BYearResult", "ViolationScore",
    "aggregate_cycle_metrics", "check_stage2b_conservation", "evaluate_stage2b_admission",
    "freeze_representatives", "make_sobol_pool", "map_sobol_capacities",
    "normalized_violation", "periodic_positive_run_lengths",
    "select_adaptive_batch", "should_stop",
    "solve_stage2b_three_year_cycle",
]
