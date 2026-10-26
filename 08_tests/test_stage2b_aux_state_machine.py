import sys
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_src"))

from ems.ems_stage2b import (
    EMSState,
    Stage2BEMSConfig,
    dispatch_stage2b,
    positive_run_lengths,
)


def test_auxiliary_flags_follow_same_step_power():
    cfg = Stage2BEMSConfig(100, 10, 20, 20, 20)
    out = dispatch_stage2b(
        np.array([10.0, 10.0]),
        np.array([0.0, 30.0]),
        cfg,
        initial_state=EMSState(cfg.soc_max, cfg.h2_kg),
    )
    assert out.trace["p_aux_pv"].tolist() == [0.0, 1.5]
    assert np.all(out.trace["p_aux_bat"][out.commands[:, 0] == 0] == 0.0)
    assert np.array_equal(out.trace["fc_on"], out.commands[:, 1] > 0)
    assert np.array_equal(out.trace["el_on"], out.commands[:, 2] > 0)


def test_fc_start_step_counts_as_first_of_five_steps():
    cfg = Stage2BEMSConfig(100, 1, 20, 20, 20)
    out = dispatch_stage2b(
        np.full(5, 50.0),
        np.zeros(5),
        cfg,
        initial_state=EMSState(cfg.soc_min, 18.0),
    )
    assert positive_run_lengths(out.commands[:, 1]) == [5]
    assert out.trace["fc_steps_remaining"].tolist() == [4, 3, 2, 1, 0]


def test_el_start_step_counts_as_first_of_five_steps():
    cfg = Stage2BEMSConfig(100, 100, 20, 20, 20)
    out = dispatch_stage2b(
        np.zeros(5),
        np.full(5, 80.0),
        cfg,
        initial_state=EMSState(cfg.soc_max, cfg.h2_kg * cfg.h2_min_fraction),
    )
    assert positive_run_lengths(out.commands[:, 2]) == [5]
    assert out.trace["el_steps_remaining"].tolist() == [4, 3, 2, 1, 0]


def test_fc_and_el_are_mutually_exclusive():
    cfg = Stage2BEMSConfig(100, 1, 20, 20, 20)
    out = dispatch_stage2b(
        np.array([50.0] * 5 + [0.0] * 5),
        np.array([0.0] * 5 + [80.0] * 5),
        cfg,
        initial_state=EMSState(cfg.soc_min, 10.0),
    )
    assert np.sum(out.trace["fc_on"] & out.trace["el_on"]) == 0
    assert all(n >= 5 for n in positive_run_lengths(out.commands[:, 1]))
    assert all(n >= 5 for n in positive_run_lengths(out.commands[:, 2]))


def test_el_never_displaces_critical_load():
    cfg = Stage2BEMSConfig(100, 1, 20, 20, 20)
    out = dispatch_stage2b(
        np.full(6, 50.0),
        np.zeros(6),
        cfg,
        initial_state=EMSState(cfg.soc_min, 10.0),
    )
    assert out.metrics["el_critical_displacement_kwh"] == 0.0
    assert np.all(out.commands[:, 2] == 0.0)


def test_dump_occurs_only_after_load_and_battery_absorption_are_unavailable():
    cfg = Stage2BEMSConfig(100, 1, 20, 20, 20)
    out = dispatch_stage2b(
        np.zeros(1),
        np.zeros(1),
        cfg,
        initial_state=EMSState(
            cfg.soc_max,
            18.0,
            fc_on=True,
            fc_steps_remaining=5,
        ),
    )
    assert out.trace["p_dump"][0] > 0.0
    assert out.trace["load_met"][0]
    assert out.trace["battery_absorption_headroom_kw"][0] <= 1e-9
    assert out.metrics["invalid_dump_count"] == 0


def test_fc_dispatch_preserves_minimum_fuel_for_all_committed_steps():
    cfg = Stage2BEMSConfig(100, 1, 100, 20, 2, h2_leakage_per_day=0.0)
    h2_min = cfg.h2_kg * cfg.h2_min_fraction
    minimum_fuel = (
        cfg.pemfc_kw
        * cfg.min_stable_load_fc
        * cfg.dt_h
        * cfg.minimum_run_steps
        / (cfg.eta_fc * 33.3)
    )
    out = dispatch_stage2b(
        np.full(5, 100.0),
        np.zeros(5),
        cfg,
        strategy="hydrogen_first",
        initial_state=EMSState(cfg.soc_min, h2_min + minimum_fuel),
    )
    assert positive_run_lengths(out.commands[:, 1]) == [5]
    assert out.metrics["emergency_trip_count"] == 0


def test_el_dispatch_preserves_tank_headroom_for_all_committed_steps():
    cfg = Stage2BEMSConfig(100, 100, 20, 100, 2, h2_leakage_per_day=0.0)
    h2_max = cfg.h2_kg * cfg.h2_max_fraction
    minimum_production = (
        cfg.pemel_kw
        * cfg.min_stable_load_el
        * cfg.dt_h
        * cfg.minimum_run_steps
        * cfg.eta_el
        / 33.3
    )
    out = dispatch_stage2b(
        np.zeros(5),
        np.full(5, 100.0),
        cfg,
        strategy="hydrogen_first",
        initial_state=EMSState(cfg.soc_max, h2_max - minimum_production),
    )
    assert positive_run_lengths(out.commands[:, 2]) == [5]
    assert out.metrics["emergency_trip_count"] == 0


def test_el_does_not_start_without_five_step_power_and_soc_reserve():
    cfg = Stage2BEMSConfig(100, 1, 20, 20, 20, h2_leakage_per_day=0.0)
    out = dispatch_stage2b(
        np.zeros(5),
        np.array([80.0, 0.0, 0.0, 0.0, 0.0]),
        cfg,
        strategy="hydrogen_first",
        initial_state=EMSState(cfg.soc_max, cfg.h2_kg * cfg.h2_min_fraction),
    )
    assert np.all(out.commands[:, 2] == 0.0)
    assert out.metrics["short_run_count"] == 0
    assert out.metrics["emergency_trip_count"] == 0


def test_dump_is_valid_when_battery_power_limit_is_fully_used():
    cfg = Stage2BEMSConfig(100, 10, 100, 20, 20, h2_leakage_per_day=0.0)
    out = dispatch_stage2b(
        np.zeros(1),
        np.zeros(1),
        cfg,
        initial_state=EMSState(
            cfg.soc_min,
            18.0,
            fc_on=True,
            fc_steps_remaining=5,
        ),
    )
    assert out.commands[0, 0] == -cfg.bat_power_kw
    assert out.trace["p_dump"][0] > 0.0
    assert out.trace["battery_absorption_headroom_kw"][0] <= 1e-9
    assert out.metrics["invalid_dump_count"] == 0


def test_fc_start_replans_prior_battery_discharge_before_power_allocation():
    cfg = Stage2BEMSConfig(100, 100, 100, 20, 20, h2_leakage_per_day=0.0)
    out = dispatch_stage2b(
        np.full(5, 14.5),
        np.zeros(5),
        cfg,
        strategy="battery_first",
        initial_state=EMSState(cfg.soc_min + 0.015, 18.0),
    )
    assert out.commands[0, 1] > 0.0
    assert out.commands[0, 0] >= 0.0
    assert out.trace["p_dump"][0] == 0.0
    assert abs(out.trace["power_residual_kw"][0]) <= 1e-6
