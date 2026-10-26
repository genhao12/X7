import sys
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "06_src"))

from simulation.capacity_gate import solve_three_year_cycle


def fake_dispatch(load, pv, cfg, soc_initial=None, h2_initial_kg=None):
    del pv, cfg
    target_soc, target_h2 = 0.6, 6.0
    end_soc = 0.5 * (soc_initial + target_soc)
    end_h2 = 0.5 * (h2_initial_kg + target_h2)
    n = len(load)
    return np.zeros((n, 3)), {
        "initial_soc": soc_initial,
        "initial_h2_kg": h2_initial_kg,
        "soc_trace": np.linspace(soc_initial, end_soc, n),
        "h2_trace": np.linspace(h2_initial_kg, end_h2, n),
        "p_uns": np.zeros(n),
        "p_cur": np.zeros(n),
        "p_aux": np.zeros(n),
        "p_bat_dis": np.zeros(n),
        "p_bat_ch": np.zeros(n),
        "power_residual_trace": np.zeros(n),
        "h2_residual": 0.0,
    }


def drifting_dispatch(load, pv, cfg, soc_initial=None, h2_initial_kg=None):
    del pv, cfg
    n = len(load)
    end_soc = soc_initial + 0.01
    return np.zeros((n, 3)), {
        "initial_soc": soc_initial,
        "initial_h2_kg": h2_initial_kg,
        "soc_trace": np.linspace(soc_initial, end_soc, n),
        "h2_trace": np.full(n, h2_initial_kg),
        "p_uns": np.zeros(n),
        "p_cur": np.zeros(n),
        "p_aux": np.zeros(n),
        "p_bat_dis": np.zeros(n),
        "p_bat_ch": np.zeros(n),
        "power_residual_trace": np.zeros(n),
        "h2_residual": 0.0,
    }


def test_cycle_closes_three_year_block_and_allows_single_year_inventory_movement():
    years = {y: {"load": np.ones(4), "pv": np.ones(4)} for y in (2016, 2017, 2018)}
    result = solve_three_year_cycle(
        years,
        object(),
        0.2,
        2.0,
        dispatch_fn=fake_dispatch,
        tolerance=0.001,
        max_repetitions=50,
    )
    assert result.converged
    assert result.block_soc_delta <= 0.001
    assert result.block_h2_delta_fraction <= 0.001
    assert len(result.year_results) == 3
    assert result.single_year_state_closure_required is False


def test_nonconvergent_cycle_stops_at_limit():
    years = {y: {"load": np.ones(4), "pv": np.ones(4)} for y in (2016, 2017, 2018)}
    result = solve_three_year_cycle(
        years,
        object(),
        0.2,
        2.0,
        dispatch_fn=drifting_dispatch,
        tolerance=0.001,
        max_repetitions=50,
    )
    assert not result.converged
    assert result.repetitions == 50
