import sys
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "06_src"))

from ems.ems_bf_lite import EMSConfig, check_constraints, rule_based_dispatch


def test_dispatch_exposes_stepwise_power_and_exact_h2_residuals():
    cfg = EMSConfig(
        pv_kwp=10,
        bat_kwh=10,
        pemfc_kw=5,
        pemel_kw=5,
        h2_kg=5,
        p_aux_fraction=0.0,
    )
    commands, state = rule_based_dispatch(
        np.full(16, 3.0), np.full(16, 4.0), cfg
    )
    assert state["power_residual_trace"].shape == (16,)
    assert state["h2_leak_trace"].shape == (16,)
    assert np.max(np.abs(state["power_residual_trace"])) <= 1e-12
    assert state["h2_residual"] <= 1e-12
    report = check_constraints(commands, state, cfg)
    assert report["power_conservation_pass"]
    assert report["h2_conservation_pass"]
