"""Capacity-domain screening and continuous three-year admission logic."""
from dataclasses import asdict, dataclass
from itertools import product
from typing import Dict, List

import numpy as np


@dataclass(frozen=True)
class CapacityCandidate:
    candidate_id: str
    domain: str
    pv_kwp: float
    bat_kwh: float
    pemfc_kw: float
    pemel_kw: float
    h2_kg: float

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class EnergyScreenResult:
    passed: bool
    ratios: Dict[int, float]
    failed_years: List[int]


@dataclass
class YearCycleResult:
    year: int
    start_soc: float
    end_soc: float
    start_h2_kg: float
    end_h2_kg: float
    commands: np.ndarray
    state: dict


@dataclass
class ThreeYearCycleResult:
    converged: bool
    repetitions: int
    block_start_soc: float
    block_end_soc: float
    block_start_h2_kg: float
    block_end_h2_kg: float
    block_soc_delta: float
    block_h2_delta_fraction: float
    year_results: Dict[int, YearCycleResult]
    single_year_state_closure_required: bool = False


@dataclass(frozen=True)
class AdmissionResult:
    admissible: bool
    checks: Dict[str, bool]
    failure_reasons: List[str]


def enumerate_domain(name: str, axes: dict) -> List[CapacityCandidate]:
    rows = []
    values = product(
        axes["PV_kWp"],
        axes["BAT_kWh"],
        axes["PEMFC_kW"],
        axes["PEMEL_kW"],
        axes["H2_kg"],
    )
    for pv, bat, fc, el, h2 in values:
        cid = f"{name}__pv{pv}__bat{bat}__fc{fc}__el{el}__h2{h2}"
        rows.append(CapacityCandidate(cid, name, pv, bat, fc, el, h2))
    return rows


def energy_screen(
    candidate: CapacityCandidate,
    pv_energy_by_year: Dict[int, float],
    load_energy_by_year: Dict[int, float],
    min_fraction: float = 0.99,
) -> EnergyScreenResult:
    del candidate
    ratios = {
        int(year): float(pv_energy_by_year[year]) / max(float(load_energy_by_year[year]), 1e-12)
        for year in sorted(load_energy_by_year)
    }
    failed = [year for year, ratio in ratios.items() if ratio < min_fraction]
    return EnergyScreenResult(not failed, ratios, failed)


def solve_three_year_cycle(
    years: dict,
    ems_config,
    initial_soc: float,
    initial_h2_kg: float,
    dispatch_fn,
    tolerance: float = 0.001,
    max_repetitions: int = 50,
) -> ThreeYearCycleResult:
    """Repeat a continuous 2016-2018 block until block boundary states close."""
    next_soc = float(initial_soc)
    next_h2 = float(initial_h2_kg)
    h2_scale = max(float(getattr(ems_config, "h2_kg", initial_h2_kg)), 1e-12)
    final_year_results = {}
    converged = False
    block_start_soc = next_soc
    block_start_h2 = next_h2
    block_end_soc = next_soc
    block_end_h2 = next_h2
    block_soc_delta = float("inf")
    block_h2_delta_fraction = float("inf")

    for repetition in range(1, max_repetitions + 1):
        block_start_soc = next_soc
        block_start_h2 = next_h2
        soc = block_start_soc
        h2 = block_start_h2
        year_results = {}
        for year in (2016, 2017, 2018):
            item = years[year]
            commands, state = dispatch_fn(
                item["load"],
                item["pv"],
                ems_config,
                soc_initial=soc,
                h2_initial_kg=h2,
            )
            end_soc = float(state["soc_trace"][-1])
            end_h2 = float(state["h2_trace"][-1])
            year_results[year] = YearCycleResult(
                year, soc, end_soc, h2, end_h2, commands, state
            )
            soc, h2 = end_soc, end_h2
        block_end_soc = soc
        block_end_h2 = h2
        block_soc_delta = abs(block_end_soc - block_start_soc)
        block_h2_delta_fraction = abs(block_end_h2 - block_start_h2) / h2_scale
        final_year_results = year_results
        if block_soc_delta <= tolerance and block_h2_delta_fraction <= tolerance:
            converged = True
            break
        next_soc, next_h2 = block_end_soc, block_end_h2

    return ThreeYearCycleResult(
        converged=converged,
        repetitions=repetition,
        block_start_soc=block_start_soc,
        block_end_soc=block_end_soc,
        block_start_h2_kg=block_start_h2,
        block_end_h2_kg=block_end_h2,
        block_soc_delta=block_soc_delta,
        block_h2_delta_fraction=block_h2_delta_fraction,
        year_results=final_year_results,
    )


def evaluate_admission(metrics: dict) -> AdmissionResult:
    """Evaluate explicit gates; single-year SOC/H2 drift is diagnostic only."""
    yearly = metrics["yearly"]
    checks = {
        "cycle_converged": bool(metrics["cycle_converged"]),
        "yearly_lpsp": all(row["lpsp"] <= 0.01 for row in yearly.values()),
        "three_year_lpsp": metrics["three_year_lpsp"] <= 0.01,
        "yearly_critical_satisfaction": all(
            row["critical_satisfaction"] >= 0.99 for row in yearly.values()
        ),
        "three_year_critical_satisfaction": (
            metrics["three_year_critical_satisfaction"] >= 0.99
        ),
        "three_year_soc_closure": metrics["block_soc_delta"] <= 0.05,
        "three_year_h2_closure": metrics["block_h2_delta_fraction"] <= 0.05,
        "power_conservation": metrics["max_power_residual_fraction"] <= 1e-6,
        "h2_conservation": metrics["h2_residual_fraction"] <= 1e-6,
        "bounds": bool(metrics["bounds_pass"]),
        "minimum_stable_power": bool(metrics["minimum_stable_power_pass"]),
    }
    failed = [name for name, passed in checks.items() if not passed]
    return AdmissionResult(not failed, checks, failed)


def h2_lower_bound_diagnostics(
    timestamps,
    h2_trace,
    p_uns,
    h2_min_kg: float,
    dt_h: float,
) -> dict:
    h2 = np.asarray(h2_trace, dtype=float)
    uns = np.asarray(p_uns, dtype=float)
    mask = h2 <= float(h2_min_kg) + 1e-9
    indices = np.flatnonzero(mask)
    first = str(timestamps[indices[0]]) if indices.size else None
    return {
        "first_timestamp": first,
        "duration_h": float(indices.size * dt_h),
        "unserved_energy_kwh": float(np.sum(uns[mask]) * dt_h),
    }
