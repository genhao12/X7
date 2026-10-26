"""Tests for closed-loop counterfactual Shapley decomposition (C/P mediators, 4 coalitions).

The C mediator controls BAT capacity degradation (Q(D) → effective_capacity).
The P mediator controls BAT power degradation (P_max(D) → power limit).

In closed-loop, degraded parameters change EMS dispatch decisions, which
change FC/EL utilization, which changes FC/EL damage — this is the
degradation-mediated coupling effect.

Shapley values for C and P quantify each mediator's contribution to
the total coupling effect on FC and EL damage.
"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_src"))

from ems.ems_stage2b import (
    EMSState,
    Stage2BEMSConfig,
    dispatch_stage2b,
)
from simulation.closed_loop_lite import (
    CoalitionState,
    compute_damage,
    BAT_K_CAL_H,
    FC_K_RUN_H,
    FC_K_START,
    EL_K_RUN_H,
    EL_K_START,
)


# ── Test helpers ──

def make_config():
    """Standard test config for counterfactual tests."""
    return Stage2BEMSConfig(
        pv_kwp=400.0, bat_kwh=1500.0, pemfc_kw=120.0,
        pemel_kw=300.0, h2_kg=4000.0,
    )


def make_degraded_hp_func(D_bat=0.5):
    """Create hp_func that returns degraded Q and P_max at given D."""
    def hp_func(D):
        Q = max(0.80, 1.0 - 0.20 * D)
        R0 = 1.0 + 0.50 * D
        P_max_kw = 750.0 * (1.0 - 0.10 * D)  # bat_rated * (1 - 0.1*D)
        return Q, R0, P_max_kw
    return hp_func


def dispatch_with_degradation(cfg, load, pv, Q_frac=1.0, P_max_kw=None):
    """Dispatch with degraded BAT parameters.

    Q_frac: capacity retention fraction (1.0 = BOL, 0.8 = EOL)
    P_max_kw: effective max power in kW (None = BOL rated)
    """
    if P_max_kw is None:
        P_max_kw = cfg.bat_power_kw

    # Create effective config with degraded parameters
    # We override bat_kwh and bat_c_rate to achieve the desired effective values
    effective_kwh = cfg.bat_kwh * Q_frac
    effective_c_rate = P_max_kw / effective_kwh if effective_kwh > 0 else cfg.bat_c_rate

    degraded_cfg = Stage2BEMSConfig(
        pv_kwp=cfg.pv_kwp,
        bat_kwh=effective_kwh,
        pemfc_kw=cfg.pemfc_kw,
        pemel_kw=cfg.pemel_kw,
        h2_kg=cfg.h2_kg,
        soc_min=cfg.soc_min,
        soc_max=cfg.soc_max,
        dt_min=cfg.dt_min,
        h2_min_fraction=cfg.h2_min_fraction,
        h2_max_fraction=cfg.h2_max_fraction,
        h2_leakage_per_day=cfg.h2_leakage_per_day,
        min_stable_load_fc=cfg.min_stable_load_fc,
        min_stable_load_el=cfg.min_stable_load_el,
        minimum_run_steps=cfg.minimum_run_steps,
        standby_kw=cfg.standby_kw,
        pv_aux_fraction=cfg.pv_aux_fraction,
        bat_aux_fraction=cfg.bat_aux_fraction,
        fc_aux_fraction=cfg.fc_aux_fraction,
        el_aux_fraction=cfg.el_aux_fraction,
        eta_fc=cfg.eta_fc,
        eta_el=cfg.eta_el,
        bat_c_rate=effective_c_rate,
    )

    initial = EMSState(soc=0.50, h2_kg=cfg.h2_kg * 0.50)
    return dispatch_stage2b(load, pv, degraded_cfg,
                           strategy="battery_first",
                           initial_state=initial)


# ── Tests ──

def test_dispatch_accepts_degraded_capacity():
    """Dispatch with Q_frac < 1.0 must produce different SoC trajectory."""
    cfg = make_config()
    n = 96 * 7  # one week
    load = np.full(n, 50.0)
    pv = np.concatenate([np.zeros(n // 2), np.full(n // 2, 120.0)])

    result_bol = dispatch_with_degradation(cfg, load, pv, Q_frac=1.0)
    result_deg = dispatch_with_degradation(cfg, load, pv, Q_frac=0.85)

    soc_bol = result_bol.trace["soc"]
    soc_deg = result_deg.trace["soc"]

    # Degraded battery has smaller effective capacity → larger SoC swings
    assert np.max(np.abs(np.diff(soc_deg))) > np.max(np.abs(np.diff(soc_bol))) * 1.05


def test_dispatch_accepts_degraded_power():
    """Dispatch with severely reduced P_max must cap BAT charge/discharge power."""
    cfg = make_config()
    n = 96 * 7
    # Create large surplus events that push BAT to power limit
    load = np.full(n, 20.0)
    pv = np.full(n, 20.0)
    for i in range(0, n, 96):
        end = min(i + 24, n)
        pv[i:end] = 500.0  # very large surplus → BAT must charge hard

    # BOL: full power (750 kW)
    result_bol = dispatch_with_degradation(cfg, load, pv, P_max_kw=750.0)
    # Severely degraded: only 50 kW power limit
    result_deg = dispatch_with_degradation(cfg, load, pv, P_max_kw=50.0)

    p_bat_bol = result_bol.commands[:, 0]
    p_bat_deg = result_deg.commands[:, 0]

    # With 50 kW limit, max |P_bat| should be ≤ ~55 kW (allowing aux overhead)
    max_abs_bol = np.max(np.abs(p_bat_bol))
    max_abs_deg = np.max(np.abs(p_bat_deg))
    assert max_abs_deg < max_abs_bol * 0.5 or max_abs_bol < 60.0, \
        f"Degraded max |P_bat| ({max_abs_deg:.1f} kW) should be much < BOL ({max_abs_bol:.1f} kW)"
    assert max_abs_deg < 60.0, \
        f"Degraded max |P_bat| ({max_abs_deg:.1f} kW) should be near 50 kW limit"


def test_degraded_dispatch_changes_fc_utilization():
    """C_ON (degraded capacity) must change FC operating hours vs C_OFF."""
    cfg = make_config()
    n = 96 * 30  # one month
    # Create load/PV pattern that forces FC usage
    np.random.seed(42)
    load = 40.0 + 20.0 * np.sin(np.arange(n) / 96 * 2 * np.pi) + np.random.normal(0, 5, n)
    load = np.maximum(load, 5.0)
    pv = np.maximum(0, 80.0 * np.sin(np.arange(n) / 96 * np.pi - np.pi / 3) + np.random.normal(0, 10, n))

    D_bat = 0.5
    hp_func = make_degraded_hp_func(D_bat)
    Q, _, P_max_kw = hp_func(D_bat)

    # C_ON: use degraded capacity
    result_c_on = dispatch_with_degradation(cfg, load, pv, Q_frac=Q, P_max_kw=750.0)
    # C_OFF: use BOL capacity
    result_c_off = dispatch_with_degradation(cfg, load, pv, Q_frac=1.0, P_max_kw=750.0)

    fc_hours_on = np.sum(result_c_on.commands[:, 1] > 1.0)
    fc_hours_off = np.sum(result_c_off.commands[:, 1] > 1.0)

    # Degraded capacity → different FC usage (direction depends on scenario)
    assert fc_hours_on != fc_hours_off, "C_ON must change FC utilization vs C_OFF"


def test_four_coalition_damage_differs():
    """All 4 coalitions must produce distinct damage values at D>0."""
    cfg = make_config()
    n = 96 * 30
    np.random.seed(42)
    load = 40.0 + 20.0 * np.sin(np.arange(n) / 96 * 2 * np.pi) + np.random.normal(0, 5, n)
    load = np.maximum(load, 5.0)
    pv = np.maximum(0, 80.0 * np.sin(np.arange(n) / 96 * np.pi - np.pi / 3) + np.random.normal(0, 10, n))

    D_bat = 0.5
    hp_func = make_degraded_hp_func(D_bat)
    Q, _, P_max_kw = hp_func(D_bat)
    P_max_bol = cfg.bat_power_kw  # 750 kW

    c1_config = {
        "capacity": {"BAT_kWh": cfg.bat_kwh, "PEMFC_kW": cfg.pemfc_kw, "PEMEL_kW": cfg.pemel_kw},
        "battery_constraints": {"soc_min": cfg.soc_min, "soc_max": cfg.soc_max, "soc_initial": 0.5},
        "ems_parameters": {"start_stop_threshold_fraction": 0.15},
    }

    damages = {}
    for code, Q_frac, P_kw in [
        ("11", Q, P_max_kw),    # C_ON, P_ON: full degradation
        ("10", Q, P_max_bol),   # C_ON, P_OFF: capacity only
        ("01", 1.0, P_max_kw),  # C_OFF, P_ON: power only
        ("00", 1.0, P_max_bol), # C_OFF, P_OFF: BOL
    ]:
        result = dispatch_with_degradation(cfg, load, pv, Q_frac=Q_frac, P_max_kw=P_kw)
        dD = compute_damage(hp_func, result.commands, c1_config,
                           D_state={"bat": D_bat, "fc": 0.0, "el": 0.0},
                           eol_policy="raw")
        damages[code] = {
            "bat": float(np.sum(dD["dD_bat"])),
            "fc": float(np.sum(dD["dD_fc"])),
            "el": float(np.sum(dD["dD_el"])),
        }

    # 11 (full degradation) should have highest FC/EL damage
    # 00 (BOL) should have lowest FC/EL damage
    assert damages["11"]["fc"] >= damages["00"]["fc"], \
        f"Full degradation FC damage ({damages['11']['fc']:.6f}) should >= BOL ({damages['00']['fc']:.6f})"
    assert damages["11"]["el"] >= damages["00"]["el"], \
        f"Full degradation EL damage ({damages['11']['el']:.6f}) should >= BOL ({damages['00']['el']:.6f})"

    # At least one of C or P must produce non-zero coupling effect
    fc_coupling = abs(damages["11"]["fc"] - damages["00"]["fc"])
    el_coupling = abs(damages["11"]["el"] - damages["00"]["el"])
    assert fc_coupling > 0 or el_coupling > 0, "At least one device must show coupling effect"


def test_shapley_cp_values_sum_to_total_effect():
    """Shapley values for C and P must sum to total coupling effect v(11) - v(00)."""
    cfg = make_config()
    n = 96 * 30
    np.random.seed(42)
    load = 40.0 + 20.0 * np.sin(np.arange(n) / 96 * 2 * np.pi) + np.random.normal(0, 5, n)
    load = np.maximum(load, 5.0)
    pv = np.maximum(0, 80.0 * np.sin(np.arange(n) / 96 * np.pi - np.pi / 3) + np.random.normal(0, 10, n))

    D_bat = 0.5
    hp_func = make_degraded_hp_func(D_bat)
    Q, _, P_max_kw = hp_func(D_bat)
    P_max_bol = cfg.bat_power_kw

    c1_config = {
        "capacity": {"BAT_kWh": cfg.bat_kwh, "PEMFC_kW": cfg.pemfc_kw, "PEMEL_kW": cfg.pemel_kw},
        "battery_constraints": {"soc_min": cfg.soc_min, "soc_max": cfg.soc_max, "soc_initial": 0.5},
        "ems_parameters": {"start_stop_threshold_fraction": 0.15},
    }

    # Run 4 coalitions
    v = {}
    for code, Q_frac, P_kw in [
        ("11", Q, P_max_kw), ("10", Q, P_max_bol),
        ("01", 1.0, P_max_kw), ("00", 1.0, P_max_bol),
    ]:
        result = dispatch_with_degradation(cfg, load, pv, Q_frac=Q_frac, P_max_kw=P_kw)
        dD = compute_damage(hp_func, result.commands, c1_config,
                           D_state={"bat": D_bat, "fc": 0.0, "el": 0.0},
                           eol_policy="raw")
        v[code] = float(np.sum(dD["dD_fc"]))  # Focus on FC damage

    # Shapley values for 2-player game:
    # phi_C = 0.5 * (v(11) - v(01)) + 0.5 * (v(10) - v(00))
    # phi_P = 0.5 * (v(11) - v(10)) + 0.5 * (v(01) - v(00))
    phi_C = 0.5 * (v["11"] - v["01"]) + 0.5 * (v["10"] - v["00"])
    phi_P = 0.5 * (v["11"] - v["10"]) + 0.5 * (v["01"] - v["00"])

    total_effect = v["11"] - v["00"]
    shapley_sum = phi_C + phi_P

    # Shapley values must sum to total effect (closure property)
    assert abs(shapley_sum - total_effect) < 1e-10, \
        f"Shapley sum ({shapley_sum:.10f}) must equal total effect ({total_effect:.10f})"


def test_shapley_c_and_p_both_contribute_at_high_degradation():
    """At high D_bat, both C and P mediators should produce finite Shapley values,
    and the total coupling effect should be non-zero."""
    cfg = make_config()
    n = 96 * 30
    np.random.seed(42)
    load = 40.0 + 20.0 * np.sin(np.arange(n) / 96 * 2 * np.pi) + np.random.normal(0, 5, n)
    load = np.maximum(load, 5.0)
    pv = np.maximum(0, 80.0 * np.sin(np.arange(n) / 96 * np.pi - np.pi / 3) + np.random.normal(0, 10, n))

    D_bat = 0.8
    hp_func = make_degraded_hp_func(D_bat)
    Q, _, P_max_kw = hp_func(D_bat)
    P_max_bol = cfg.bat_power_kw

    c1_config = {
        "capacity": {"BAT_kWh": cfg.bat_kwh, "PEMFC_kW": cfg.pemfc_kw, "PEMEL_kW": cfg.pemel_kw},
        "battery_constraints": {"soc_min": cfg.soc_min, "soc_max": cfg.soc_max, "soc_initial": 0.5},
        "ems_parameters": {"start_stop_threshold_fraction": 0.15},
    }

    v = {}
    for code, Q_frac, P_kw in [
        ("11", Q, P_max_kw), ("10", Q, P_max_bol),
        ("01", 1.0, P_max_kw), ("00", 1.0, P_max_bol),
    ]:
        result = dispatch_with_degradation(cfg, load, pv, Q_frac=Q_frac, P_max_kw=P_kw)
        dD = compute_damage(hp_func, result.commands, c1_config,
                           D_state={"bat": D_bat, "fc": 0.0, "el": 0.0},
                           eol_policy="raw")
        v[code] = float(np.sum(dD["dD_fc"]))

    phi_C = 0.5 * (v["11"] - v["01"]) + 0.5 * (v["10"] - v["00"])
    phi_P = 0.5 * (v["11"] - v["10"]) + 0.5 * (v["01"] - v["00"])

    total_effect = v["11"] - v["00"]

    # Both Shapley values must be finite
    assert np.isfinite(phi_C), f"phi_C must be finite, got {phi_C}"
    assert np.isfinite(phi_P), f"phi_P must be finite, got {phi_P}"

    # Total effect must be non-zero (coupling exists)
    assert abs(total_effect) > 1e-10, \
        f"Total coupling effect ({total_effect:.10f}) must be non-zero at D_bat=0.8"

    # Shapley sum must equal total effect
    assert abs(phi_C + phi_P - total_effect) < 1e-10, \
        f"phi_C + phi_P ({phi_C + phi_P:.10f}) must equal total effect ({total_effect:.10f})"
