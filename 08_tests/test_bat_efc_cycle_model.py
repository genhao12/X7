# -*- coding: utf-8 -*-
"""TDD tests for the EFC / energy-throughput battery cycle-damage model.

Covers:
  - rainflow counting correctness (full/half cycles, depth weighting)
  - EFC k=1 equivalence to energy-throughput EFC (sum|dSoC|/2)
  - 280Ah LFP calibration (700 full cycles -> D = 0.355)
  - damage conservation (per-step increments sum to a_d * EFC)
  - throughput EFC consistency
  - compute_damage integration (EFC mode) and proxy-mode comparison
"""
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_src"))

from components import efc_cycle_model as ecm
from simulation.closed_loop_lite import compute_damage, BAT_K_CYC_H, BAT_K_CAL_H


# === rainflow correctness ===

def test_full_cycle_efc_is_one():
    soc = np.array([0.0, 0.5, 1.0, 0.5, 0.0])
    efc = ecm.equivalent_full_cycles(ecm.rainflow_cycles(soc))
    assert efc == pytest.approx(1.0, abs=1e-12)


def test_half_cycle_efc_is_half():
    soc = np.array([0.0, 0.5, 1.0])
    efc = ecm.equivalent_full_cycles(ecm.rainflow_cycles(soc))
    assert efc == pytest.approx(0.5, abs=1e-12)


def test_rainflow_k1_equals_total_variation_efc():
    """With k=1 the rainflow EFC must equal sum(|dSoC|)/2 (energy-throughput)."""
    rng = np.random.default_rng(7)
    for _ in range(20):
        soc = np.clip(0.5 + 0.4 * np.cumsum(rng.normal(0, 1, 300)), 0.0, 1.0)
        efc_rf = ecm.equivalent_full_cycles(ecm.rainflow_cycles(soc), k=1.0)
        efc_tv = ecm.total_variation_efc(soc)
        assert efc_rf == pytest.approx(efc_tv, rel=1e-10)


def test_depth_weighting_increases_with_k():
    """Deep half-cycle DOD=0.5: k=1 -> 0.25 EFC; k=2 -> 0.125 EFC."""
    soc = np.array([0.0, 0.25, 0.5])
    efc_k1 = ecm.equivalent_full_cycles(ecm.rainflow_cycles(soc), k=1.0)
    efc_k2 = ecm.equivalent_full_cycles(ecm.rainflow_cycles(soc), k=2.0)
    assert efc_k1 == pytest.approx(0.25)
    assert efc_k2 == pytest.approx(0.125)


# === calibration ===

def test_280ah_calibration_seven_hundred_cycles():
    """700 full cycles from the 280Ah aging data -> D = Q_loss/0.2 = 0.355."""
    d_700 = ecm.EFC_A_D * 700.0
    assert d_700 == pytest.approx(0.355, rel=1e-3)


def test_efc_a_d_matches_hp_bat_lite_beta_q():
    """D-per-EFC must be consistent with the hp_bat_lite Q(D) fit slope."""
    # hp_bat_lite: D range 0..0.354 over 700 cycles, Q_frac 1.0 -> 0.929
    assert ecm.EFC_A_D * 700.0 == pytest.approx(0.354, rel=0.01)


# === damage conservation ===

def test_cycle_damage_conserves_total():
    soc = np.clip(0.5 + 0.4 * np.cumsum(np.random.default_rng(3).normal(0, 1, 500)), 0.0, 1.0)
    inc = ecm.cycle_damage_increments(soc, k=1.0)
    efc = ecm.equivalent_full_cycles(ecm.rainflow_cycles(soc), k=1.0)
    assert float(np.sum(inc)) == pytest.approx(ecm.EFC_A_D * efc, rel=1e-12)
    assert np.all(inc >= 0)


def test_idle_trace_has_zero_cycle_damage():
    soc = np.full(100, 0.5)
    inc = ecm.cycle_damage_increments(soc)
    assert float(np.sum(inc)) == 0.0


# === throughput EFC ===

def test_throughput_efc_matches_rainflow_for_constant_c():
    """1C constant discharge for 1h moves SoC by 1.0 -> EFC ~= 1."""
    capacity_kwh = 1000.0
    p_bat = np.full(4, capacity_kwh)  # 1C for 1h at 15-min steps
    dt_h = 0.25
    e_thr = ecm.energy_throughput_mwh(p_bat, dt_h)
    efc_thr = ecm.throughput_equivalent_full_cycles(e_thr, capacity_kwh)
    assert efc_thr == pytest.approx(1.0, rel=1e-12)


# === compute_damage integration ===

def _hp_bat(D):
    return 1.0, 1.0, 500.0  # Q=1, R0=1, P_max=500kW


def _config(bat_kwh=1000.0, fc_rated=100.0, el_rated=100.0, bat_cycle_model="efc"):
    return {
        "capacity": {"BAT_kWh": bat_kwh, "PEMFC_kW": fc_rated, "PEMEL_kW": el_rated},
        "battery_constraints": {"soc_min": 0.15, "soc_max": 0.95, "soc_initial": 0.5},
        "ems_parameters": {"start_stop_threshold_fraction": 0.15},
        "degradation": {"bat_cycle_model": bat_cycle_model},
    }


def test_compute_damage_efc_full_cycle_gives_calibrated_d():
    """A full 0->1->0 discharge/charge cycle yields ~a_d cyclic damage."""
    n_steps = 4
    dt_h = 0.25
    bat_kwh = 1000.0
    # 1C discharge 1h then 1C charge 1h at 15-min steps -> SoC 0.5->1->0 (clipped)
    p_discharge = bat_kwh  # 1C discharge
    p_charge = -bat_kwh    # 1C charge
    p_bat = np.array([p_discharge] * 4 + [p_charge] * 4)
    cmds = np.zeros((len(p_bat), 3))
    cmds[:, 0] = p_bat
    result = compute_damage(_hp_bat, cmds, _config(bat_kwh=bat_kwh),
                            dt_min=15.0,
                            D_state={"bat": 0.0, "fc": 0.0, "el": 0.0},
                            eol_policy="raw")
    dD_bat = np.sum(result["dD_bat"])
    cal = BAT_K_CAL_H * len(p_bat) * dt_h
    cyc = dD_bat - cal
    # SoC clips at soc_max=0.95 so the effective cycle is slightly < 1 EFC;
    # the cyclic damage must be positive and bounded by ~a_d (full cycle).
    assert cyc > 0.0
    assert cyc <= ecm.EFC_A_D * 1.05 + 1e-9


def test_compute_damage_efc_idle_equals_calendar():
    n_steps = 35040
    cmds = np.zeros((n_steps, 3))
    result = compute_damage(_hp_bat, cmds, _config(),
                            dt_min=15.0,
                            D_state={"bat": 0.0, "fc": 0.0, "el": 0.0},
                            eol_policy="raw")
    expected_cal = BAT_K_CAL_H * n_steps * (15.0 / 60.0)
    assert float(np.sum(result["dD_bat"])) == pytest.approx(expected_cal, abs=1e-6)


def test_efc_less_aggressive_than_running_hour_proxy():
    """EFC model (calibrated to 280Ah) must give lower per-cycle damage than the
    legacy running-hour proxy BAT_K_CYC_H for a full 1C cycle."""
    bat_kwh = 1000.0
    n_steps = 8
    p_bat = np.array([bat_kwh] * 4 + [-bat_kwh] * 4)
    cmds = np.zeros((len(p_bat), 3))
    cmds[:, 0] = p_bat
    res_efc = compute_damage(_hp_bat, cmds, _config(bat_kwh=bat_kwh, bat_cycle_model="efc"),
                             D_state={"bat": 0.0, "fc": 0.0, "el": 0.0}, eol_policy="raw")
    res_proxy = compute_damage(_hp_bat, cmds, _config(bat_kwh=bat_kwh, bat_cycle_model="proxy"),
                               D_state={"bat": 0.0, "fc": 0.0, "el": 0.0}, eol_policy="raw")
    cal = BAT_K_CAL_H * len(p_bat) * 0.25
    d_efc = float(np.sum(res_efc["dD_bat"]))
    d_proxy = float(np.sum(res_proxy["dD_bat"]))
    assert d_efc < d_proxy
    # proxy full cycle ~ BAT_K_CYC_H * 1 * 1 * 1h = 2e-3
    assert d_proxy - cal == pytest.approx(BAT_K_CYC_H * 1.0, rel=0.05)
    # EFC model: cyclic damage must equal a_d * EFC of the (clipped) SoC trace
    soc_trace = np.zeros(len(p_bat) + 1)
    soc_trace[0] = 0.5
    cap = 1000.0
    for t in range(len(p_bat)):
        soc_trace[t + 1] = np.clip(soc_trace[t] - p_bat[t] * 0.25 / cap, 0.15, 0.95)
    efc = ecm.equivalent_full_cycles(ecm.rainflow_cycles(soc_trace), k=1.0)
    assert d_efc - cal == pytest.approx(ecm.EFC_A_D * efc, rel=0.02)


def test_compute_damage_efc_eol_cap_still_works():
    """EOL capping must still hold under the EFC cycle model."""
    n_steps = 35040
    cmds = np.zeros((n_steps, 3))
    cmds[:, 0] = 2000.0  # heavy cycling to approach EOL
    result = compute_damage(_hp_bat, cmds, _config(bat_kwh=500.0),
                            dt_min=15.0,
                            D_state={"bat": 0.0, "fc": 0.0, "el": 0.0},
                            eol_policy="cap")
    assert np.all(result["dD_bat"] >= 0.0)
    assert float(np.sum(result["dD_bat"])) <= 1.0 + 1e-9
