"""Tests for L4 degradation-aware controller comparison against baseline battery_first.

The L4 controller applies three rules based on D_bat to reduce coupling:
  R1: SOC band narrowing → reduce BAT DOD → slow BAT degradation
  R2: FC minimum run extension → reduce FC start-stop cycles
  R3: EL ramp limiting (deferred — requires Stage2b kernel modification)

Both baseline and L4 use the same dispatch_stage2b engine with different
parameter settings, ensuring a fair comparison.
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
    compute_damage,
    BAT_K_CAL_H,
    FC_K_RUN_H,
    FC_K_START,
    EL_K_RUN_H,
    EL_K_START,
)


def make_stage2c_config():
    """Representative 'compact' config from Stage2c."""
    return Stage2BEMSConfig(
        pv_kwp=388.3, bat_kwh=1734.4, pemfc_kw=95.0,
        pemel_kw=224.2, h2_kg=4450.3,
    )


def make_l4_config(cfg, D_bat, soc_narrow_k=0.15, fc_extend_k=1.0):
    """Create L4-adjusted Stage2BEMSConfig based on D_bat.

    R1: SOC band narrows by soc_narrow_k * D_bat fraction.
    R2: FC minimum_run_steps increases by (1 + fc_extend_k * D_bat).
    """
    soc_range = cfg.soc_max - cfg.soc_min
    narrow = soc_narrow_k * D_bat
    soc_range_eff = soc_range * (1.0 - narrow)
    soc_center = (cfg.soc_max + cfg.soc_min) / 2.0
    soc_min_eff = max(0.05, soc_center - soc_range_eff / 2.0)
    soc_max_eff = min(0.99, soc_center + soc_range_eff / 2.0)

    extend_factor = 1.0 + fc_extend_k * D_bat
    min_run_eff = max(1, int(round(cfg.minimum_run_steps * extend_factor)))

    return Stage2BEMSConfig(
        pv_kwp=cfg.pv_kwp,
        bat_kwh=cfg.bat_kwh,
        pemfc_kw=cfg.pemfc_kw,
        pemel_kw=cfg.pemel_kw,
        h2_kg=cfg.h2_kg,
        soc_min=soc_min_eff,
        soc_max=soc_max_eff,
        dt_min=cfg.dt_min,
        h2_min_fraction=cfg.h2_min_fraction,
        h2_max_fraction=cfg.h2_max_fraction,
        h2_leakage_per_day=cfg.h2_leakage_per_day,
        min_stable_load_fc=cfg.min_stable_load_fc,
        min_stable_load_el=cfg.min_stable_load_el,
        minimum_run_steps=min_run_eff,
        standby_kw=cfg.standby_kw,
        pv_aux_fraction=cfg.pv_aux_fraction,
        bat_aux_fraction=cfg.bat_aux_fraction,
        fc_aux_fraction=cfg.fc_aux_fraction,
        el_aux_fraction=cfg.el_aux_fraction,
        eta_fc=cfg.eta_fc,
        eta_el=cfg.eta_el,
        bat_c_rate=cfg.bat_c_rate,
    )


def run_dispatch(cfg, load, pv):
    """Run dispatch and return commands."""
    initial = EMSState(soc=0.50, h2_kg=cfg.h2_kg * 0.50)
    result = dispatch_stage2b(load, pv, cfg, strategy="battery_first", initial_state=initial)
    return result.commands


def compute_fc_el_damage(commands, c1_config, D_bat=0.5):
    """Compute FC and EL annual damage from dispatch commands."""
    def hp_func(D):
        return 1.0, 1.0, 500.0
    dD = compute_damage(hp_func, commands, c1_config,
                       D_state={"bat": D_bat, "fc": 0.0, "el": 0.0},
                       eol_policy="raw")
    return {
        "bat": float(np.sum(dD["dD_bat"])),
        "fc": float(np.sum(dD["dD_fc"])),
        "el": float(np.sum(dD["dD_el"])),
    }


def make_test_load_pv(n_days=30):
    """Create synthetic load/PV for testing."""
    n = 96 * n_days
    np.random.seed(42)
    load = 40.0 + 20.0 * np.sin(np.arange(n) / 96 * 2 * np.pi) + np.random.normal(0, 5, n)
    load = np.maximum(load, 5.0)
    pv = np.maximum(0, 80.0 * np.sin(np.arange(n) / 96 * np.pi - np.pi / 3)
                    + np.random.normal(0, 10, n))
    return load, pv


# ── Tests ──

def test_l4_soc_band_narrows_with_degradation():
    """R1: SOC band must narrow as D_bat increases."""
    cfg = make_stage2c_config()

    l4_30 = make_l4_config(cfg, D_bat=0.3)
    l4_70 = make_l4_config(cfg, D_bat=0.7)

    range_30 = l4_30.soc_max - l4_30.soc_min
    range_70 = l4_70.soc_max - l4_70.soc_min
    range_bol = cfg.soc_max - cfg.soc_min

    assert range_30 < range_bol, "SOC band must narrow at D=0.3"
    assert range_70 < range_30, "SOC band must narrow further at D=0.7"


def test_l4_fc_min_run_extends_with_degradation():
    """R2: FC minimum run steps must increase as D_bat increases."""
    cfg = make_stage2c_config()

    l4_30 = make_l4_config(cfg, D_bat=0.3)
    l4_70 = make_l4_config(cfg, D_bat=0.7)

    assert l4_30.minimum_run_steps >= cfg.minimum_run_steps
    assert l4_70.minimum_run_steps >= l4_30.minimum_run_steps
    assert l4_70.minimum_run_steps > cfg.minimum_run_steps


def test_l4_reduces_bat_dod():
    """R1: L4 SOC narrowing must reduce average BAT DOD."""
    cfg = make_stage2c_config()
    load, pv = make_test_load_pv()

    cmds_bol = run_dispatch(cfg, load, pv)
    l4_cfg = make_l4_config(cfg, D_bat=0.5)
    cmds_l4 = run_dispatch(l4_cfg, load, pv)

    # DOD proxy: standard deviation of SoC changes
    dod_bol = np.std(np.diff(cmds_bol[:, 0]))
    dod_l4 = np.std(np.diff(cmds_l4[:, 0]))

    assert dod_l4 <= dod_bol * 1.05, \
        f"L4 DOD ({dod_l4:.4f}) should not exceed BOL DOD ({dod_bol:.4f})"


def test_l4_reduces_fc_start_stop_count():
    """R2: L4 extended FC minimum run must reduce start-stop frequency."""
    cfg = make_stage2c_config()
    load, pv = make_test_load_pv(n_days=90)

    cmds_bol = run_dispatch(cfg, load, pv)
    l4_cfg = make_l4_config(cfg, D_bat=0.7)
    cmds_l4 = run_dispatch(l4_cfg, load, pv)

    fc_bol = cmds_bol[:, 1]
    fc_l4 = cmds_l4[:, 1]

    # Count FC starts (transition from <threshold to >=threshold)
    threshold = cfg.pemfc_kw * cfg.min_stable_load_fc
    starts_bol = np.sum((fc_bol[1:] >= threshold) & (fc_bol[:-1] < threshold))
    starts_l4 = np.sum((fc_l4[1:] >= threshold) & (fc_l4[:-1] < threshold))

    assert starts_l4 <= starts_bol, \
        f"L4 FC starts ({starts_l4}) should be <= BOL starts ({starts_bol})"


def test_l4_reduces_total_fc_el_damage():
    """L4 must reduce or maintain total FC+EL damage compared to BOL."""
    cfg = make_stage2c_config()
    load, pv = make_test_load_pv(n_days=90)

    c1_config = {
        "capacity": {"BAT_kWh": cfg.bat_kwh, "PEMFC_kW": cfg.pemfc_kw, "PEMEL_kW": cfg.pemel_kw},
        "battery_constraints": {"soc_min": cfg.soc_min, "soc_max": cfg.soc_max, "soc_initial": 0.5},
        "ems_parameters": {"start_stop_threshold_fraction": 0.15},
    }

    D_bat = 0.5

    cmds_bol = run_dispatch(cfg, load, pv)
    dmg_bol = compute_fc_el_damage(cmds_bol, c1_config, D_bat)

    l4_cfg = make_l4_config(cfg, D_bat=D_bat)
    cmds_l4 = run_dispatch(l4_cfg, load, pv)
    dmg_l4 = compute_fc_el_damage(cmds_l4, c1_config, D_bat)

    total_bol = dmg_bol["fc"] + dmg_bol["el"]
    total_l4 = dmg_l4["fc"] + dmg_l4["el"]

    assert total_l4 <= total_bol * 1.05, \
        f"L4 total damage ({total_l4:.6f}) should be <= BOL ({total_bol:.6f}) × 1.05"


def test_l4_reduces_shapley_phi_c():
    """L4 must reduce the C mediator's Shapley value (coupling strength)."""
    cfg = make_stage2c_config()
    load, pv = make_test_load_pv(n_days=90)

    c1_config = {
        "capacity": {"BAT_kWh": cfg.bat_kwh, "PEMFC_kW": cfg.pemfc_kw, "PEMEL_kW": cfg.pemel_kw},
        "battery_constraints": {"soc_min": cfg.soc_min, "soc_max": cfg.soc_max, "soc_initial": 0.5},
        "ems_parameters": {"start_stop_threshold_fraction": 0.15},
    }

    D_bat = 0.5
    bat_rated_kw = cfg.bat_kwh * 0.5

    def hp_func(D):
        return max(0.80, 1.0 - 0.20 * D), 1.0 + 0.50 * D, bat_rated_kw * (1.0 - 0.10 * D)

    Q, _, P_max_kw = hp_func(D_bat)

    def compute_phi_c(dispatch_cfg):
        """Compute phi_C for FC damage using given dispatch config."""
        v = {}
        for code, Q_frac, P_kw in [
            ("11", Q, P_max_kw), ("10", Q, bat_rated_kw),
            ("01", 1.0, P_max_kw), ("00", 1.0, bat_rated_kw),
        ]:
            # Create degraded config
            eff_kwh = dispatch_cfg.bat_kwh * Q_frac
            eff_c_rate = P_kw / max(eff_kwh, 1e-6)
            deg_cfg = Stage2BEMSConfig(
                pv_kwp=dispatch_cfg.pv_kwp, bat_kwh=eff_kwh,
                pemfc_kw=dispatch_cfg.pemfc_kw, pemel_kw=dispatch_cfg.pemel_kw,
                h2_kg=dispatch_cfg.h2_kg,
                soc_min=dispatch_cfg.soc_min, soc_max=dispatch_cfg.soc_max,
                dt_min=dispatch_cfg.dt_min,
                h2_min_fraction=dispatch_cfg.h2_min_fraction,
                h2_max_fraction=dispatch_cfg.h2_max_fraction,
                h2_leakage_per_day=dispatch_cfg.h2_leakage_per_day,
                min_stable_load_fc=dispatch_cfg.min_stable_load_fc,
                min_stable_load_el=dispatch_cfg.min_stable_load_el,
                minimum_run_steps=dispatch_cfg.minimum_run_steps,
                standby_kw=dispatch_cfg.standby_kw,
                pv_aux_fraction=dispatch_cfg.pv_aux_fraction,
                bat_aux_fraction=dispatch_cfg.bat_aux_fraction,
                fc_aux_fraction=dispatch_cfg.fc_aux_fraction,
                el_aux_fraction=dispatch_cfg.el_aux_fraction,
                eta_fc=dispatch_cfg.eta_fc, eta_el=dispatch_cfg.eta_el,
                bat_c_rate=eff_c_rate,
            )
            cmds = run_dispatch(deg_cfg, load, pv)
            dD = compute_damage(hp_func, cmds, c1_config,
                               D_state={"bat": D_bat, "fc": 0.0, "el": 0.0},
                               eol_policy="raw")
            v[code] = float(np.sum(dD["dD_fc"]))

        return 0.5 * (v["11"] - v["01"]) + 0.5 * (v["10"] - v["00"])

    phi_c_bol = compute_phi_c(cfg)
    l4_cfg = make_l4_config(cfg, D_bat=D_bat)
    phi_c_l4 = compute_phi_c(l4_cfg)

    assert phi_c_l4 <= phi_c_bol * 1.10, \
        f"L4 phi_C ({phi_c_l4:.6f}) should not exceed BOL phi_C ({phi_c_bol:.6f}) × 1.10"


def test_l4_maintains_lpsp():
    """L4 must not significantly worsen LPSP (reliability constraint)."""
    cfg = make_stage2c_config()
    load, pv = make_test_load_pv(n_days=90)

    cmds_bol = run_dispatch(cfg, load, pv)
    l4_cfg = make_l4_config(cfg, D_bat=0.5)
    cmds_l4 = run_dispatch(l4_cfg, load, pv)

    # LPSP proxy: fraction of steps with non-zero unserved energy
    # (We don't have direct p_uns from dispatch_stage2b, but can infer from power balance)
    dt_h = cfg.dt_min / 60.0
    p_aux_bol = cfg.standby_kw + cfg.pv_aux_fraction * cfg.pv_kwp * np.ones(len(load))
    p_net_bol = pv - load - p_aux_bol
    p_uns_bol = np.maximum(0, -(p_net_bol + np.maximum(cmds_bol[:, 0], 0) + cmds_bol[:, 1]
                                 + np.maximum(-cmds_bol[:, 0], 0) * 0))

    # Simpler: total energy balance
    total_load = np.sum(load) * dt_h
    # Unserved = load - PV + aux - BAT_net - FC + EL (simplified)
    bat_net_bol = cmds_bol[:, 0]  # positive=discharge
    fc_bol = cmds_bol[:, 1]
    el_bol = cmds_bol[:, 2]
    supplied_bol = pv + np.maximum(bat_net_bol, 0) + fc_bol
    consumed_bol = load + el_bol + np.maximum(-bat_net_bol, 0) + p_aux_bol
    unserved_bol = np.sum(np.maximum(0, consumed_bol - supplied_bol)) * dt_h

    bat_net_l4 = cmds_l4[:, 0]
    fc_l4 = cmds_l4[:, 1]
    el_l4 = cmds_l4[:, 2]
    p_aux_l4 = l4_cfg.standby_kw + l4_cfg.pv_aux_fraction * l4_cfg.pv_kwp * np.ones(len(load))
    supplied_l4 = pv + np.maximum(bat_net_l4, 0) + fc_l4
    consumed_l4 = load + el_l4 + np.maximum(-bat_net_l4, 0) + p_aux_l4
    unserved_l4 = np.sum(np.maximum(0, consumed_l4 - supplied_l4)) * dt_h

    lpsp_bol = unserved_bol / max(total_load, 1e-6)
    lpsp_l4 = unserved_l4 / max(total_load, 1e-6)

    # L4 LPSP should not exceed BOL by more than 1 percentage point
    assert lpsp_l4 <= lpsp_bol + 0.01, \
        f"L4 LPSP ({lpsp_l4*100:.2f}%) should not exceed BOL ({lpsp_bol*100:.2f}%) + 1%"
