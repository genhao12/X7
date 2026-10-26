import sys
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_src"))

from ems.ems_stage2b import DispatchResult, EMSState, Stage2BEMSConfig
from simulation.stage2b_capacity_gate import solve_stage2b_three_year_cycle


def carrying_dispatch(load, pv, cfg, initial_state=None, **kwargs):
    del pv, cfg, kwargs
    delta_soc = float(load[0])
    delta_h2 = float(load[1])
    end = EMSState(
        initial_state.soc + delta_soc,
        initial_state.h2_kg + delta_h2,
        initial_state.fc_on,
        initial_state.fc_steps_remaining,
        initial_state.el_on,
        initial_state.el_steps_remaining,
    )
    n = len(load)
    trace = {
        "soc": np.linspace(initial_state.soc, end.soc, n),
        "h2_kg": np.linspace(initial_state.h2_kg, end.h2_kg, n),
        "p_uns": np.zeros(n),
        "p_cur": np.zeros(n),
        "p_dump": np.zeros(n),
        "power_residual_kw": np.zeros(n),
        "h2_residual_kg": np.zeros(n),
        "fc_on": np.full(n, initial_state.fc_on),
        "el_on": np.full(n, initial_state.el_on),
    }
    return DispatchResult(np.zeros((n, 3)), trace, end, {
        "fc_el_overlap_count": 0,
        "short_run_count": 0,
        "el_critical_displacement_kwh": 0.0,
        "invalid_dump_count": 0,
    })


def test_three_year_cycle_carries_complete_state_and_allows_annual_drift():
    years = {
        2016: {"load": np.array([0.10, 1.0, 0.0]), "pv": np.zeros(3)},
        2017: {"load": np.array([-0.04, -0.4, 0.0]), "pv": np.zeros(3)},
        2018: {"load": np.array([-0.06, -0.6, 0.0]), "pv": np.zeros(3)},
    }
    cfg = Stage2BEMSConfig(320, 1200, 160, 300, 3000)
    initial = EMSState(0.5, 1500.0, fc_on=True, fc_steps_remaining=4)
    result = solve_stage2b_three_year_cycle(
        years, cfg, initial, carrying_dispatch, tolerance=0.001, max_repetitions=50
    )
    assert result.converged
    assert result.year_results[2017].start_state == result.year_results[2016].end_state
    assert result.year_results[2018].start_state == result.year_results[2017].end_state
    assert result.block_start_state.fc_steps_remaining == result.block_end_state.fc_steps_remaining
    assert result.block_soc_delta <= 0.001
    assert result.block_h2_delta_fraction <= 0.001
    assert any(
        row.end_state.soc != row.start_state.soc
        for row in result.year_results.values()
    )


def drifting_dispatch(load, pv, cfg, initial_state=None, **kwargs):
    del load, pv, cfg, kwargs
    end = EMSState(initial_state.soc + 0.01, initial_state.h2_kg)
    trace = {
        "soc": np.array([end.soc]), "h2_kg": np.array([end.h2_kg]),
        "p_uns": np.zeros(1), "p_cur": np.zeros(1), "p_dump": np.zeros(1),
        "power_residual_kw": np.zeros(1), "h2_residual_kg": np.zeros(1),
        "fc_on": np.zeros(1, dtype=bool), "el_on": np.zeros(1, dtype=bool),
    }
    return DispatchResult(np.zeros((1, 3)), trace, end, {})


def test_nonconvergent_complete_state_cycle_stops_at_limit():
    years = {year: {"load": np.zeros(1), "pv": np.zeros(1)} for year in (2016, 2017, 2018)}
    cfg = Stage2BEMSConfig(320, 1200, 160, 300, 3000)
    result = solve_stage2b_three_year_cycle(
        years, cfg, EMSState(0.2, 300.0), drifting_dispatch,
        tolerance=0.001, max_repetitions=7,
    )
    assert not result.converged
    assert result.repetitions == 7
