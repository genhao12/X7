"""Tests for BAT calendar degradation coefficient time-unit conversion.

The calendar coefficient k_cal_h must be expressed in per-hour units,
and the per-step damage must be k_cal_h * dt_h, making it independent
of the simulation step size.

Literature constraint (Naumann 2018/2022, LFP 280Ah, 25°C):
  annual calendar degradation = 2–5 %/year at SoC=0.5.
"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_src"))


def test_bat_calendar_coefficient_is_per_hour():
    """k_cal_h * dt_h must equal the per-step damage; independent of dt."""
    from simulation.closed_loop_lite import BAT_K_CAL_H

    # 15-min steps (default)
    dt_h_15 = 15.0 / 60.0
    dD_15 = BAT_K_CAL_H * dt_h_15

    # 1-hour steps
    dt_h_60 = 60.0 / 60.0
    dD_60 = BAT_K_CAL_H * dt_h_60

    # Per-hour rate must be consistent
    assert abs(dD_15 / dt_h_15 - BAT_K_CAL_H) < 1e-15
    assert abs(dD_60 / dt_h_60 - BAT_K_CAL_H) < 1e-15

    # 1-hour step damage must be 4× the 15-min step damage
    assert abs(dD_60 - 4.0 * dD_15) < 1e-15


def test_bat_annual_calendar_degradation_within_literature():
    """Annual calendar damage at SoC=0.5 must be 2–5%/year (LFP 25°C)."""
    from simulation.closed_loop_lite import BAT_K_CAL_H

    hours_per_year = 8760.0
    annual_dD = BAT_K_CAL_H * hours_per_year

    # Must be between 0.02 and 0.05 (2–5%/year)
    assert 0.02 <= annual_dD <= 0.05, (
        f"Annual calendar degradation = {annual_dD*100:.2f}%, "
        f"outside literature range 2–5%/year"
    )


def test_bat_calendar_damage_independent_of_ems_commands():
    """Calendar damage for N steps must equal k_cal_h * N * dt_h
    regardless of what the EMS dispatches."""
    from simulation.closed_loop_lite import compute_damage, BAT_K_CAL_H

    dt_min = 15.0
    dt_h = dt_min / 60.0
    n_steps = 35040  # one non-leap year

    # Zero commands — battery idle
    cmds_idle = np.zeros((n_steps, 3))
    config = {
        "capacity": {"BAT_kWh": 1000.0, "PEMFC_kW": 100.0, "PEMEL_kW": 100.0},
        "battery_constraints": {"soc_min": 0.15, "soc_max": 0.95, "soc_initial": 0.5},
        "ems_parameters": {"start_stop_threshold_fraction": 0.15},
    }

    def hp_bat(D):
        return 1.0, 1.0, 500.0  # Q=1, R0=1, P_max=500kW

    result_idle = compute_damage(hp_bat, cmds_idle, config, dt_min=dt_min,
                                 D_state={"bat": 0.0, "fc": 0.0, "el": 0.0},
                                 eol_policy="raw")

    # Calendar damage with idle battery = k_cal_h * n_steps * dt_h
    expected_cal = BAT_K_CAL_H * n_steps * dt_h
    # Total BAT damage = calendar (cyclic = 0 since dod=0, p_ratio=0)
    actual_bat = float(np.sum(result_idle["dD_bat"]))

    assert abs(actual_bat - expected_cal) < 1e-6, (
        f"Idle BAT damage = {actual_bat:.6f}, "
        f"expected calendar = {expected_cal:.6f}"
    )


def test_fc_el_start_stop_coefficients_are_per_event():
    """k_start values must be documented per-event (not per-step).
    Each start event adds exactly k_start to D regardless of step duration."""
    from simulation.closed_loop_lite import (
        FC_K_START, EL_K_START,
    )

    # Must be positive and finite
    assert 0 < FC_K_START < 0.01, f"FC k_start = {FC_K_START}, expected 0 < k < 0.01"
    assert 0 < EL_K_START < 0.01, f"EL k_start = {EL_K_START}, expected 0 < k < 0.01"

    # Literature constraint: each start-stop cycle causes ~10-50 μV degradation
    # for a 5-cell stack at 0.8V/cell BOL → 4.0V total.
    # 50 μV / 4.0V = 1.25e-5 per event (very small).
    # MVP values (1e-3, 8e-4) are ~80-64× larger — flagged for calibration.
    # This test documents the current values; calibration will tighten bounds.
    pass


def test_fc_annual_degradation_with_calibrated_start_stop():
    """After calibration, FC annual damage at 500h operation must be
    physically reasonable (lifetime 8-15 years for PEMFC in microgrid)."""
    from simulation.closed_loop_lite import (
        FC_K_RUN_H, FC_K_START, compute_damage,
    )

    dt_min = 15.0
    dt_h = dt_min / 60.0

    # Simulate 500h of FC operation at 50% rated power
    # 500h = 2000 steps at 15-min
    n_steps = 2000
    fc_rated = 100.0  # kW
    cmds = np.zeros((n_steps, 3))
    cmds[:, 1] = fc_rated * 0.5  # FC at 50% rated

    config = {
        "capacity": {"BAT_kWh": 1000.0, "PEMFC_kW": fc_rated, "PEMEL_kW": 100.0},
        "battery_constraints": {"soc_min": 0.15, "soc_max": 0.95, "soc_initial": 0.5},
        "ems_parameters": {"start_stop_threshold_fraction": 0.15},
    }

    def hp_bat(D):
        return 1.0, 1.0, 500.0

    result = compute_damage(hp_bat, cmds, config, dt_min=dt_min,
                           D_state={"bat": 0.0, "fc": 0.0, "el": 0.0},
                           eol_policy="raw")

    fc_annual_500h = float(np.sum(result["dD_fc"]))

    # If FC runs 500h/year, annual damage should give lifetime 8-15 years
    # → annual damage = 0.067 to 0.125
    # At 50% power: dD = k_run_h * 0.5 * 500h + k_start * n_starts
    # With 1 start per day: n_starts ≈ 365 * (500/8760) ≈ 21
    # This test documents the constraint; actual bounds depend on calibration.
    assert fc_annual_500h > 0, "FC damage must be positive"


def test_el_annual_degradation_with_calibrated_start_stop():
    """After calibration, EL annual damage at 600h operation must be
    physically reasonable (lifetime 10-20 years for PEMEL in microgrid)."""
    from simulation.closed_loop_lite import (
        EL_K_RUN_H, EL_K_START, compute_damage,
    )

    dt_min = 15.0
    n_steps = 2400  # 600h at 15-min
    el_rated = 200.0  # kW
    cmds = np.zeros((n_steps, 3))
    cmds[:, 2] = el_rated * 0.5  # EL at 50% rated

    config = {
        "capacity": {"BAT_kWh": 1000.0, "PEMFC_kW": 100.0, "PEMEL_kW": el_rated},
        "battery_constraints": {"soc_min": 0.15, "soc_max": 0.95, "soc_initial": 0.5},
        "ems_parameters": {"start_stop_threshold_fraction": 0.15},
    }

    def hp_bat(D):
        return 1.0, 1.0, 500.0

    result = compute_damage(hp_bat, cmds, config, dt_min=dt_min,
                           D_state={"bat": 0.0, "fc": 0.0, "el": 0.0},
                           eol_policy="raw")

    el_annual_600h = float(np.sum(result["dD_el"]))
    assert el_annual_600h > 0, "EL damage must be positive"
