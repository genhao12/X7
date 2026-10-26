import sys
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_src"))

from ems.ems_stage2b import EMSState, Stage2BEMSConfig, dispatch_stage2b
from simulation.stage2b_capacity_gate import (
    Stage2BCycleResult,
    Stage2BYearResult,
    aggregate_cycle_metrics,
    check_stage2b_conservation,
    evaluate_stage2b_admission,
    normalized_violation,
)


def passing_metrics():
    return {
        "cycle_converged": True,
        "yearly": {
            year: {"lpsp": 0.005, "critical_satisfaction": 0.995}
            for year in (2016, 2017, 2018)
        },
        "three_year_lpsp": 0.005,
        "block_soc_delta": 0.01,
        "block_h2_delta_fraction": 0.01,
        "machine_state_boundary_mismatch": 0,
        "fc_el_overlap_count": 0,
        "short_run_count": 0,
        "el_critical_displacement_kwh": 0.0,
        "invalid_dump_count": 0,
        "bounds_pass": True,
        "max_power_residual_kw": 1e-8,
        "max_h2_residual_kg": 1e-8,
    }


def test_random_dispatch_is_stepwise_conservative():
    rng = np.random.RandomState(20260803)
    load = rng.uniform(5.0, 150.0, 512)
    pv = rng.uniform(0.0, 300.0, 512)
    cfg = Stage2BEMSConfig(320, 1200, 160, 300, 3000)
    for strategy in ("battery_first", "balanced", "hydrogen_first"):
        out = dispatch_stage2b(
            load,
            pv,
            cfg,
            strategy=strategy,
            initial_state=EMSState(0.6, 1800.0),
        )
        report = check_stage2b_conservation(out)
        assert report["power_pass"]
        assert report["h2_pass"]
        assert report["max_power_residual_kw"] <= 1e-6
        assert report["max_h2_residual_kg"] <= 1e-6
        assert np.all(out.trace["p_dump"] >= 0.0)


def test_violation_score_checks_all_four_classes():
    base = passing_metrics()
    assert normalized_violation(base).maximum == 0.0
    mutations = (
        ("three_year_lpsp", 0.02),
        ("block_soc_delta", 0.10),
        ("short_run_count", 1),
        ("max_power_residual_kw", 2e-6),
    )
    for field, value in mutations:
        broken = passing_metrics()
        broken[field] = value
        assert normalized_violation(broken).maximum > 0.0


def test_admission_requires_every_state_machine_and_conservation_gate():
    decision = evaluate_stage2b_admission(passing_metrics())
    assert decision.admissible
    assert set(decision.checks) >= {
        "cycle_converged",
        "yearly_lpsp",
        "three_year_lpsp",
        "critical_satisfaction",
        "soc_h2_closure",
        "machine_state_closure",
        "fc_el_mutual_exclusion",
        "minimum_run_segments",
        "el_critical_priority",
        "valid_dump",
        "power_conservation",
        "h2_conservation",
        "bounds",
    }


def test_each_gate_can_independently_block_admission():
    fields = {
        "cycle_converged": False,
        "machine_state_boundary_mismatch": 1,
        "fc_el_overlap_count": 1,
        "short_run_count": 1,
        "el_critical_displacement_kwh": 0.25,
        "invalid_dump_count": 1,
        "bounds_pass": False,
        "max_h2_residual_kg": 2e-6,
    }
    for field, value in fields.items():
        broken = passing_metrics()
        broken[field] = value
        assert not evaluate_stage2b_admission(broken).admissible


def test_cycle_aggregation_uses_periodic_runs_and_all_state_gates():
    cfg = Stage2BEMSConfig(320, 1200, 160, 300, 3000)
    state = EMSState(0.5, 1500.0)
    years = {}
    results = {}
    for year in (2016, 2017, 2018):
        load = np.array([10.0, 10.0])
        years[year] = {"load": load, "pv": load.copy()}
        trace = {
            "soc": np.full(2, state.soc), "h2_kg": np.full(2, state.h2_kg),
            "p_uns": np.zeros(2), "p_cur": np.zeros(2), "p_dump": np.zeros(2),
            "power_residual_kw": np.zeros(2), "h2_residual_kg": np.zeros(2),
            "fc_on": np.zeros(2, dtype=bool), "el_on": np.zeros(2, dtype=bool),
        }
        dispatch = type("D", (), {})()
        dispatch.commands = np.zeros((2, 3))
        dispatch.trace = trace
        dispatch.final_state = state
        dispatch.metrics = {
            "el_critical_displacement_kwh": 0.0,
            "invalid_dump_count": 0,
            "emergency_trip_count": 0,
        }
        results[year] = Stage2BYearResult(year, state, state, dispatch)
    cycle = Stage2BCycleResult(True, 1, state, state, 0.0, 0.0, 0, results)
    metrics = aggregate_cycle_metrics(cycle, years, cfg)
    assert metrics["three_year_lpsp"] == 0.0
    assert metrics["short_run_count"] == 0
    assert metrics["bounds_pass"] is True
    assert evaluate_stage2b_admission(metrics).admissible
