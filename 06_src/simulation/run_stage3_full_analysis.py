"""Stage3 degradation coupling, Shapley decomposition, sensitivity and economics runner.

Takes the 5 representative configurations from Stage2c and performs:
1. Open-loop annual damage computation (BOL dispatch → damage model)
2. Closed-loop lifetime estimation (iterative D update with dispatch feedback)
3. 16-state Shapley decomposition for each path
4. Auxiliary power sensitivity (0.8×/1.2×)
5. NPC/LCOE economic evaluation
"""
import json
import sys
import numpy as np
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "06_src") not in sys.path:
    sys.path.insert(0, str(ROOT / "06_src"))

from ems.ems_stage2b import (
    Stage2BEMSConfig, EMSState, dispatch_stage2b,
    causal_persistence_forecast,
)
from components.annual_input_contract import load_evaluation_years, scale_pv_available
from simulation.stage2b_capacity_gate import solve_stage2b_three_year_cycle
from simulation.closed_loop_lite import compute_damage, CoalitionState


# ── Default hp_func stubs (identity maps returning BOL values) ──

def hp_bat_default(D: float, bat_kwh: float = 135.0) -> Tuple[float, float, float]:
    """BAT health-performance: D → (Q_fraction, R0_Ohm, P_max_kW).
    
    Note: compute_damage uses min(p_max_bat, bat_rated_kw) as absolute kW,
    so P_max must be returned as kW, not a ratio.
    """
    Q = max(0.80, 1.0 - 0.20 * D)       # 20% capacity loss at EOL
    R0 = 1.0 + 0.50 * D                   # 50% impedance growth at EOL
    bat_rated = bat_kwh * 0.5
    P_max = bat_rated * (1.0 - 0.10 * D)  # absolute kW: 10% power derate at EOL
    return Q, R0, P_max


def hp_fc_default(D: float) -> Tuple[float, float, float]:
    """FC health-performance: D → (eta_fraction, R_hfr_Ohm, P_max_kW_ratio)."""
    eta = max(0.90, 1.0 - 0.10 * D)       # 10% efficiency loss at EOL
    R_hfr = 1.0 + 0.20 * D                # 20% HFR growth at EOL
    P_max = 1.0 - 0.05 * D                # 5% power derate at EOL
    return eta, R_hfr, P_max


def hp_el_default(D: float) -> Tuple[float, float, float]:
    """EL health-performance: D → (eta_fraction, R_ohm_ratio, P_max_kW_ratio)."""
    eta = max(0.90, 1.0 - 0.10 * D)       # 10% efficiency loss at EOL
    R_ohm = 1.0 + 0.30 * D                # 30% resistance growth at EOL
    P_max = 1.0 - 0.05 * D                # 5% power derate at EOL
    return eta, R_ohm, P_max


# ── Core functions ──

def run_three_year_cycle(prepared, cfg_ems, strategy="battery_first",
                         aux_multiplier=1.0, max_reps=50):
    """Run 2016-2017-2018 cycle until convergence."""
    initial = EMSState(soc=0.50, h2_kg=cfg_ems.h2_kg * 0.50)
    cycle = solve_stage2b_three_year_cycle(
        prepared, cfg_ems, initial, dispatch_stage2b,
        tolerance=0.001, max_repetitions=max_reps,
        strategy=strategy, aux_multiplier=aux_multiplier,
    )
    return cycle


def extract_commands(cycle) -> Dict[int, np.ndarray]:
    """Extract per-year EMS command arrays from cycle result."""
    cmds = {}
    for year in (2016, 2017, 2018):
        yr = cycle.year_results[year]
        cmds[year] = np.asarray(yr.dispatch.commands, dtype=float)
    return cmds


def compute_annual_damage(cmds_year: np.ndarray, config: dict,
                          D_state: dict = None,
                          coalition: CoalitionState = None,
                          eta_degrade: float = 0.10) -> dict:
    """Compute damage increments for one year of dispatch."""
    bat_kwh = config.get('capacity', {}).get('BAT_kWh', 135.0)
    
    def hp_bat_closure(D):
        return hp_bat_default(D, bat_kwh=bat_kwh)
    
    damage = compute_damage(
        hp_func=hp_bat_closure,
        ems_commands=cmds_year,
        config=config,
        dt_min=15.0,
        D_state=D_state,
        coalition=coalition,
        eta_degrade=eta_degrade,
    )
    return {
        'dD_bat': float(np.sum(damage['dD_bat'])),
        'dD_fc': float(np.sum(damage['dD_fc'])),
        'dD_el': float(np.sum(damage['dD_el'])),
        'dD_bat_array': damage['dD_bat'],
        'dD_fc_array': damage['dD_fc'],
        'dD_el_array': damage['dD_el'],
    }


def estimate_lifetime(annual_damage: float, d_min: float = 0.0) -> float:
    """Estimate device lifetime in years from annual damage increment (legacy)."""
    if annual_damage <= 1e-12:
        return float('inf')
    remaining = 1.0 - d_min
    return remaining / annual_damage


def compute_lifetime_with_events(
    cmds_by_year: Dict[int, np.ndarray],
    c1_config: dict,
    hp_bat_func,
    horizon_years: int = 20,
    downtime_steps: Optional[Dict[str, int]] = None,
) -> dict:
    """Compute device lifetimes using EOL event model with downtime.

    Tiles the 3-year dispatch cycle over the full horizon and uses
    advance_replacement_events_with_downtime to get exact replacement
    counts and event times.

    Args:
        cmds_by_year: {year: commands array} for years 2016/2017/2018
        c1_config: C1 config dict
        hp_bat_func: BAT health-performance function
        horizon_years: evaluation horizon (default 20)
        downtime_steps: {device: steps offline after replacement}

    Returns:
        dict with lifetime_years, replacement_counts, events per device
    """
    from simulation.eol_events import advance_replacement_events_with_downtime

    if downtime_steps is None:
        downtime_steps = {"bat": 96, "fc": 48, "el": 48}  # 24h/12h/12h at 15-min steps

    dt_h = 0.25  # 15-min steps
    years_cycle = [2016, 2017, 2018]
    steps_per_cycle = sum(len(cmds_by_year[y]) for y in years_cycle)

    # Compute raw per-step damage for one 3-year cycle
    cycle_damage = {"bat": [], "fc": [], "el": []}
    for year in years_cycle:
        cmds = cmds_by_year[year]
        dD = compute_annual_damage(cmds, c1_config, D_state={'bat': 0.0, 'fc': 0.0, 'el': 0.0})
        # We need per-step arrays, not sums. Recompute with raw policy.
        def hp_func(D):
            return hp_bat_func(D, bat_kwh=c1_config['capacity']['BAT_kWh'])
        dD_raw = compute_damage(hp_func, cmds, c1_config,
                                D_state={"bat": 0.0, "fc": 0.0, "el": 0.0},
                                eol_policy="raw")
        cycle_damage["bat"].append(dD_raw["dD_bat"])
        cycle_damage["fc"].append(dD_raw["dD_fc"])
        cycle_damage["el"].append(dD_raw["dD_el"])

    # Concatenate one cycle
    bat_cycle = np.concatenate(cycle_damage["bat"])
    fc_cycle = np.concatenate(cycle_damage["fc"])
    el_cycle = np.concatenate(cycle_damage["el"])

    # Tile over horizon
    n_cycles = int(np.ceil(horizon_years / 3))
    bat_tiled = np.tile(bat_cycle, n_cycles)
    fc_tiled = np.tile(fc_cycle, n_cycles)
    el_tiled = np.tile(el_cycle, n_cycles)

    # Truncate to exact horizon steps
    steps_per_year = len(bat_cycle) // 3
    total_steps = steps_per_year * horizon_years
    bat_tiled = bat_tiled[:total_steps]
    fc_tiled = fc_tiled[:total_steps]
    el_tiled = el_tiled[:total_steps]

    result = advance_replacement_events_with_downtime(
        damage_increments={"bat": bat_tiled, "fc": fc_tiled, "el": el_tiled},
        initial_state={"bat": 0.0, "fc": 0.0, "el": 0.0},
        dt_hours=dt_h,
        downtime_steps=downtime_steps,
    )

    # Compute lifetimes from first replacement event time
    lifetimes = {}
    for device in ["bat", "fc", "el"]:
        device_events = [e for e in result.events if e.device == device]
        if device_events:
            # Lifetime = time of first replacement
            lifetimes[device] = device_events[0].time_hours / 8760.0
        else:
            # No replacement in horizon → lifetime > horizon
            lifetimes[device] = float(horizon_years)

    return {
        "lifetime_years": lifetimes,
        "replacement_counts": result.replacement_counts,
        "events": [
            {"device": e.device, "time_hours": e.time_hours,
             "step_index": e.step_index, "replacement_number": e.replacement_number}
            for e in result.events
        ],
        "downtime_steps_used": downtime_steps,
    }


def build_c1_config(rep: dict) -> dict:
    """Build C1-compatible config dict from representative payload."""
    return {
        'capacity': {
            'PV_kWp': rep['pv_kwp'],
            'BAT_kWh': rep['bat_kwh'],
            'PEMFC_kW': rep['pemfc_kw'],
            'PEMEL_kW': rep['pemel_kw'],
            'H2_kg': rep['h2_kg'],
        },
        'battery_constraints': {
            'soc_min': 0.15,
            'soc_max': 0.95,
            'soc_initial': 0.50,
            'soh_eol': 0.80,
        },
        'ems_parameters': {
            'start_stop_threshold_fraction': 0.15,
        },
    }


def dispatch_with_degraded_bat(
    cfg_ems: Stage2BEMSConfig,
    load: np.ndarray,
    pv: np.ndarray,
    Q_frac: float = 1.0,
    P_max_kw: Optional[float] = None,
    strategy: str = "battery_first",
) -> "DispatchResult":
    """Dispatch with degraded BAT parameters (closed-loop counterfactual).

    Q_frac: capacity retention (1.0 = BOL, 0.8 = EOL).
    P_max_kw: effective max power. None = BOL rated.
    """
    effective_kwh = cfg_ems.bat_kwh * Q_frac
    effective_c_rate = (P_max_kw or cfg_ems.bat_power_kw) / max(effective_kwh, 1e-6)

    degraded_cfg = Stage2BEMSConfig(
        pv_kwp=cfg_ems.pv_kwp,
        bat_kwh=effective_kwh,
        pemfc_kw=cfg_ems.pemfc_kw,
        pemel_kw=cfg_ems.pemel_kw,
        h2_kg=cfg_ems.h2_kg,
        soc_min=cfg_ems.soc_min,
        soc_max=cfg_ems.soc_max,
        dt_min=cfg_ems.dt_min,
        h2_min_fraction=cfg_ems.h2_min_fraction,
        h2_max_fraction=cfg_ems.h2_max_fraction,
        h2_leakage_per_day=cfg_ems.h2_leakage_per_day,
        min_stable_load_fc=cfg_ems.min_stable_load_fc,
        min_stable_load_el=cfg_ems.min_stable_load_el,
        minimum_run_steps=cfg_ems.minimum_run_steps,
        standby_kw=cfg_ems.standby_kw,
        pv_aux_fraction=cfg_ems.pv_aux_fraction,
        bat_aux_fraction=cfg_ems.bat_aux_fraction,
        fc_aux_fraction=cfg_ems.fc_aux_fraction,
        el_aux_fraction=cfg_ems.el_aux_fraction,
        eta_fc=cfg_ems.eta_fc,
        eta_el=cfg_ems.eta_el,
        bat_c_rate=effective_c_rate,
    )
    initial = EMSState(soc=0.50, h2_kg=cfg_ems.h2_kg * 0.50)
    return dispatch_stage2b(
        load, pv, degraded_cfg,
        strategy=strategy,
        initial_state=initial,
    )


def run_counterfactual_shapley_cp(
    prepared: dict,
    cfg_ems: Stage2BEMSConfig,
    c1_config: dict,
    hp_bat_func,
    D_levels: List[float] = [0.3, 0.5, 0.7],
) -> dict:
    """Run closed-loop counterfactual Shapley decomposition for C/P mediators.

    For each D_level, runs 4 coalitions (CC/PP ∈ {ON,OFF}²) on year 2017
    and computes Shapley values for C and P on FC/EL damage.

    C mediator: BAT capacity degradation Q(D) → effective_capacity
    P mediator: BAT power degradation P_max(D) → power limit
    """
    bat_rated_kw = cfg_ems.bat_kwh * 0.5  # 0.5C rated power
    year = 2017
    load = prepared[year]["load"]
    pv = prepared[year]["pv"]

    all_results = {}
    for D_bat in D_levels:
        Q, _, P_max_kw = hp_bat_func(D_bat, bat_kwh=cfg_ems.bat_kwh)

        # 4 coalitions: (C, P) ∈ {(ON,ON), (ON,OFF), (OFF,ON), (OFF,OFF)}
        coalition_dispatches = {
            "11": (Q, P_max_kw),       # C_ON, P_ON: full degradation
            "10": (Q, bat_rated_kw),   # C_ON, P_OFF: capacity only
            "01": (1.0, P_max_kw),     # C_OFF, P_ON: power only
            "00": (1.0, bat_rated_kw), # C_OFF, P_OFF: BOL
        }

        damages = {}
        for code, (Q_frac, P_kw) in coalition_dispatches.items():
            result = dispatch_with_degraded_bat(cfg_ems, load, pv, Q_frac=Q_frac, P_max_kw=P_kw)
            bat_kwh_eff = cfg_ems.bat_kwh * Q_frac
            dD = compute_annual_damage(
                result.commands, c1_config,
                D_state={'bat': D_bat, 'fc': 0.0, 'el': 0.0},
            )
            damages[code] = {
                'bat': dD['dD_bat'],
                'fc': dD['dD_fc'],
                'el': dD['dD_el'],
            }

        # Shapley values for 2-player game
        # phi_C = 0.5*(v(11)-v(01)) + 0.5*(v(10)-v(00))
        # phi_P = 0.5*(v(11)-v(10)) + 0.5*(v(01)-v(00))
        shapley = {}
        for target in ('fc', 'el'):
            v = {code: damages[code][target] for code in damages}
            phi_C = 0.5 * (v["11"] - v["01"]) + 0.5 * (v["10"] - v["00"])
            phi_P = 0.5 * (v["11"] - v["10"]) + 0.5 * (v["01"] - v["00"])
            total = v["11"] - v["00"]
            shapley[target] = {
                'phi_C': phi_C,
                'phi_P': phi_P,
                'total_effect': total,
                'closure_error': abs(phi_C + phi_P - total),
            }

        all_results[str(D_bat)] = {
            'D_bat': D_bat,
            'Q_frac': Q,
            'P_max_kw': P_max_kw,
            'damages': damages,
            'shapley': shapley,
        }

        print(f"    Shapley(D={D_bat}):")
        for target in ('fc', 'el'):
            s = shapley[target]
            print(f"      {target.upper()}: phi_C={s['phi_C']:+.6f}  phi_P={s['phi_P']:+.6f}  "
                  f"total={s['total_effect']:+.6f}  closure={s['closure_error']:.2e}")

    return all_results


def make_l4_adjusted_config(cfg_ems, D_bat, soc_narrow_k=0.15, fc_extend_k=1.0):
    """Create L4-adjusted Stage2BEMSConfig based on D_bat.

    R1: SOC band narrows by soc_narrow_k * D_bat fraction.
    R2: FC minimum_run_steps increases by (1 + fc_extend_k * D_bat).
    R3: EL ramp limiting (deferred — requires kernel modification).
    """
    soc_range = cfg_ems.soc_max - cfg_ems.soc_min
    narrow = soc_narrow_k * D_bat
    soc_range_eff = soc_range * (1.0 - narrow)
    soc_center = (cfg_ems.soc_max + cfg_ems.soc_min) / 2.0
    soc_min_eff = max(0.05, soc_center - soc_range_eff / 2.0)
    soc_max_eff = min(0.99, soc_center + soc_range_eff / 2.0)

    extend_factor = 1.0 + fc_extend_k * D_bat
    min_run_eff = max(1, int(round(cfg_ems.minimum_run_steps * extend_factor)))

    return Stage2BEMSConfig(
        pv_kwp=cfg_ems.pv_kwp, bat_kwh=cfg_ems.bat_kwh,
        pemfc_kw=cfg_ems.pemfc_kw, pemel_kw=cfg_ems.pemel_kw,
        h2_kg=cfg_ems.h2_kg,
        soc_min=soc_min_eff, soc_max=soc_max_eff,
        dt_min=cfg_ems.dt_min,
        h2_min_fraction=cfg_ems.h2_min_fraction,
        h2_max_fraction=cfg_ems.h2_max_fraction,
        h2_leakage_per_day=cfg_ems.h2_leakage_per_day,
        min_stable_load_fc=cfg_ems.min_stable_load_fc,
        min_stable_load_el=cfg_ems.min_stable_load_el,
        minimum_run_steps=min_run_eff,
        standby_kw=cfg_ems.standby_kw,
        pv_aux_fraction=cfg_ems.pv_aux_fraction,
        bat_aux_fraction=cfg_ems.bat_aux_fraction,
        fc_aux_fraction=cfg_ems.fc_aux_fraction,
        el_aux_fraction=cfg_ems.el_aux_fraction,
        eta_fc=cfg_ems.eta_fc, eta_el=cfg_ems.eta_el,
        bat_c_rate=cfg_ems.bat_c_rate,
    )


def run_l4_comparison(
    prepared: dict,
    cfg_ems: Stage2BEMSConfig,
    c1_config: dict,
    hp_bat_func,
    D_bat: float = 0.5,
) -> dict:
    """Compare L4 vs baseline battery_first at given D_bat.

    Runs both controllers, computes damage and Shapley values,
    and reports the reduction in coupling strength.
    """
    year = 2017
    load = prepared[year]["load"]
    pv = prepared[year]["pv"]
    bat_rated_kw = cfg_ems.bat_kwh * 0.5

    Q, _, P_max_kw = hp_bat_func(D_bat, bat_kwh=cfg_ems.bat_kwh)

    l4_cfg = make_l4_adjusted_config(cfg_ems, D_bat)

    results = {}
    for label, dispatch_cfg in [("baseline", cfg_ems), ("L4", l4_cfg)]:
        # Run 4 coalitions for Shapley
        coalition_params = {
            "11": (Q, P_max_kw),
            "10": (Q, bat_rated_kw),
            "01": (1.0, P_max_kw),
            "00": (1.0, bat_rated_kw),
        }

        damages = {}
        for code, (Q_frac, P_kw) in coalition_params.items():
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
            result = dispatch_with_degraded_bat(
                dispatch_cfg, load, pv, Q_frac=Q_frac, P_max_kw=P_kw
            )
            dD = compute_annual_damage(
                result.commands, c1_config,
                D_state={'bat': D_bat, 'fc': 0.0, 'el': 0.0},
            )
            damages[code] = {
                'bat': dD['dD_bat'],
                'fc': dD['dD_fc'],
                'el': dD['dD_el'],
            }

        # Shapley values
        shapley = {}
        for target in ('fc', 'el'):
            v = {code: damages[code][target] for code in damages}
            phi_C = 0.5 * (v["11"] - v["01"]) + 0.5 * (v["10"] - v["00"])
            phi_P = 0.5 * (v["11"] - v["10"]) + 0.5 * (v["01"] - v["00"])
            total = v["11"] - v["00"]
            shapley[target] = {
                'phi_C': phi_C, 'phi_P': phi_P,
                'total_effect': total,
                'closure_error': abs(phi_C + phi_P - total),
            }

        results[label] = {
            'damages': damages,
            'shapley': shapley,
            'l4_adjustments': {
                'soc_min': dispatch_cfg.soc_min,
                'soc_max': dispatch_cfg.soc_max,
                'minimum_run_steps': dispatch_cfg.minimum_run_steps,
            } if label == "L4" else None,
        }

    # Compute reductions
    baseline_fc_total = results["baseline"]["shapley"]["fc"]["total_effect"]
    l4_fc_total = results["L4"]["shapley"]["fc"]["total_effect"]
    baseline_phi_c_fc = results["baseline"]["shapley"]["fc"]["phi_C"]
    l4_phi_c_fc = results["L4"]["shapley"]["fc"]["phi_C"]

    fc_reduction = (baseline_fc_total - l4_fc_total) / max(abs(baseline_fc_total), 1e-12) * 100
    phi_c_reduction = (baseline_phi_c_fc - l4_phi_c_fc) / max(abs(baseline_phi_c_fc), 1e-12) * 100

    print(f"    L4 vs Baseline (D_bat={D_bat}):")
    print(f"      FC total coupling: {baseline_fc_total:+.6f} → {l4_fc_total:+.6f} ({fc_reduction:+.1f}%)")
    print(f"      FC phi_C:          {baseline_phi_c_fc:+.6f} → {l4_phi_c_fc:+.6f} ({phi_c_reduction:+.1f}%)")

    return {
        'D_bat': D_bat,
        'baseline': results['baseline'],
        'L4': results['L4'],
        'fc_total_reduction_pct': fc_reduction,
        'phi_c_reduction_pct': phi_c_reduction,
    }


# ── Stage3 main pipeline ──

def run_stage3_degradation(
    config_path: Path,
    data_path: Path,
    admissions_path: Path,
    result_root: Path,
    aux_multipliers: Tuple[float, ...] = (0.8, 1.0, 1.2),
) -> dict:
    """Execute full Stage3 pipeline: degradation + sensitivity + economics."""

    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    admissions = json.loads(admissions_path.read_text(encoding="utf-8"))
    representatives = admissions.get("representatives", [])
    if not representatives:
        raise ValueError("no representative configurations found")

    loaded = load_evaluation_years(data_path)
    ref_kw = float(cfg["pv_source"]["reference_capacity_kw"])

    results = {
        "schema_version": "stage3_degradation.1",
        "representatives": [],
    }

    for rep in representatives:
        role = rep["representative_role"]
        pv_kwp = float(rep["pv_kwp"])
        bat_kwh = float(rep["bat_kwh"])
        pemfc_kw = float(rep["pemfc_kw"])
        pemel_kw = float(rep["pemel_kw"])
        h2_kg = float(rep["h2_kg"])

        print(f"\n{'='*70}")
        print(f"Representative: {role}")
        print(f"  PV={pv_kwp:.1f} BAT={bat_kwh:.1f} FC={pemfc_kw:.1f} EL={pemel_kw:.1f} H2={h2_kg:.1f}")

        cfg_ems = Stage2BEMSConfig(
            pv_kwp=pv_kwp, bat_kwh=bat_kwh, pemfc_kw=pemfc_kw,
            pemel_kw=pemel_kw, h2_kg=h2_kg,
        )

        # Prepare PV data
        prepared = {}
        for year, frame in loaded.items():
            pv_raw = frame["pv_raw_kw"].to_numpy(dtype=float)
            pv_scaled = scale_pv_available(pv_raw, nominal_kw=pv_kwp)
            prepared[year] = {
                "load": frame["load_kw"].to_numpy(dtype=float),
                "pv": pv_scaled,
            }

        c1_config = build_c1_config(rep)

        # ── 1. Run 3-year cycle ──
        cycle = run_three_year_cycle(prepared, cfg_ems)
        cmds = extract_commands(cycle)

        # ── 2. Open-loop annual damage (BOL dispatch) ──
        annual_damages = {}
        for year in (2016, 2017, 2018):
            dmg = compute_annual_damage(cmds[year], c1_config, D_state={'bat': 0.0, 'fc': 0.0, 'el': 0.0})
            annual_damages[year] = {
                'dD_bat': dmg['dD_bat'],
                'dD_fc': dmg['dD_fc'],
                'dD_el': dmg['dD_el'],
            }

        # Average annual damage (3-year)
        avg_dD_bat = np.mean([annual_damages[y]['dD_bat'] for y in (2016, 2017, 2018)])
        avg_dD_fc = np.mean([annual_damages[y]['dD_fc'] for y in (2016, 2017, 2018)])
        avg_dD_el = np.mean([annual_damages[y]['dD_el'] for y in (2016, 2017, 2018)])

        # Legacy lifetime estimate (for reference)
        life_bat_legacy = estimate_lifetime(avg_dD_bat)
        life_fc_legacy = estimate_lifetime(avg_dD_fc)
        life_el_legacy = estimate_lifetime(avg_dD_el)

        print(f"\n  --- Open-loop annual damage (BOL dispatch) ---")
        for year in (2016, 2017, 2018):
            d = annual_damages[year]
            print(f"    {year}: BAT={d['dD_bat']:.6f}  FC={d['dD_fc']:.6f}  EL={d['dD_el']:.6f}")
        print(f"    Avg:  BAT={avg_dD_bat:.6f}  FC={avg_dD_fc:.6f}  EL={avg_dD_el:.6f}")
        print(f"  --- Lifetime (legacy D_EOL=1.0) ---")
        print(f"    BAT: {life_bat_legacy:.1f} years  FC: {life_fc_legacy:.1f} years  EL: {life_el_legacy:.1f} years")

        # ── 2b. EOL event model with downtime ──
        print(f"\n  --- EOL Event Model (20-year horizon, with downtime) ---")
        eol_result = compute_lifetime_with_events(
            cmds_by_year=cmds,
            c1_config=c1_config,
            hp_bat_func=hp_bat_default,
            horizon_years=20,
            downtime_steps={"bat": 96, "fc": 48, "el": 48},  # 24h/12h/12h
        )
        life_bat = eol_result["lifetime_years"]["bat"]
        life_fc = eol_result["lifetime_years"]["fc"]
        life_el = eol_result["lifetime_years"]["el"]
        rep_counts = eol_result["replacement_counts"]
        print(f"    BAT: {life_bat:.1f} years, {rep_counts['bat']} replacements")
        print(f"    FC:  {life_fc:.1f} years, {rep_counts['fc']} replacements")
        print(f"    EL:  {life_el:.1f} years, {rep_counts['el']} replacements")

        # ── 3. Closed-loop counterfactual Shapley (C/P mediators, 4 coalitions) ──
        shapley_results = run_counterfactual_shapley_cp(
            prepared, cfg_ems, c1_config, hp_bat_default,
            D_levels=[0.3, 0.5, 0.7],
        )

        # ── 3b. L4 degradation-aware controller comparison ──
        print(f"\n  --- L4 Controller Comparison ---")
        l4_results = run_l4_comparison(
            prepared, cfg_ems, c1_config, hp_bat_default,
            D_bat=0.5,
        )

        # ── 4. Sensitivity: aux multipliers (full gate check) ──
        from simulation.stage2b_capacity_gate import aggregate_cycle_metrics, evaluate_stage2b_admission
        sensitivity = {}
        for mult in aux_multipliers:
            if mult == 1.0:
                continue
            cycle_s = run_three_year_cycle(prepared, cfg_ems, aux_multiplier=mult)
            cmds_s = extract_commands(cycle_s)
            metrics_s = aggregate_cycle_metrics(cycle_s, prepared, cfg_ems)
            admission_s = evaluate_stage2b_admission(metrics_s)

            yearly_lpsp = {
                str(y): metrics_s["yearly"][y]["lpsp"]
                for y in (2016, 2017, 2018)
            }
            sensitivity[str(mult)] = {
                "three_year_lpsp": metrics_s["three_year_lpsp"],
                "yearly_lpsp": yearly_lpsp,
                "converged": cycle_s.converged,
                "soc_closure": metrics_s["block_soc_delta"],
                "h2_closure": metrics_s["block_h2_delta_fraction"],
                "short_run_count": metrics_s["short_run_count"],
                "invalid_dump_count": metrics_s["invalid_dump_count"],
                "fc_el_overlap_count": metrics_s["fc_el_overlap_count"],
                "max_power_residual": metrics_s["max_power_residual_kw"],
                "max_h2_residual": metrics_s["max_h2_residual_kg"],
                "admissible": admission_s.admissible,
                "failed_gates": list(admission_s.failure_reasons),
            }
            gate_status = "PASS" if admission_s.admissible else f"FAIL({','.join(admission_s.failure_reasons)})"
            print(f"  Aux {mult}×: LPSP 3yr={metrics_s['three_year_lpsp']*100:.4f}%  "
                  f"SOC_δ={metrics_s['block_soc_delta']:.4f}  H2_δ={metrics_s['block_h2_delta_fraction']:.4f}  "
                  f"dump={metrics_s['invalid_dump_count']}  gate={gate_status}")

        # ── 5. Economics: NPC/LCOE ──
        economics = compute_economics(rep, annual_damages, eol_result)

        rep_result = {
            "role": role,
            "pv_kwp": pv_kwp,
            "bat_kwh": bat_kwh,
            "pemfc_kw": pemfc_kw,
            "pemel_kw": pemel_kw,
            "h2_kg": h2_kg,
            "annual_damage": {str(y): annual_damages[y] for y in (2016, 2017, 2018)},
            "avg_annual_damage": {"bat": avg_dD_bat, "fc": avg_dD_fc, "el": avg_dD_el},
            "lifetime_years": {"bat": life_bat, "fc": life_fc, "el": life_el},
            "eol_events": eol_result,
            "shapley": shapley_results,
            "l4_comparison": l4_results,
            "aux_sensitivity": sensitivity,
            "economics": economics,
        }
        results["representatives"].append(rep_result)

    return results


def run_shapley_decomposition(cmds: Dict[int, np.ndarray], c1_config: dict) -> dict:
    """Run 16 coalition states and compute Shapley values for each device."""
    from simulation.closed_loop_lite import CoalitionState
    from identification.mediation_lite import compute_shapley, coalition_loop

    all_states = CoalitionState.all_16_states()
    coalition_damages = {}  # code → {bat, fc, el}

    for state in all_states:
        code = state.code
        total_dD = {'bat': 0.0, 'fc': 0.0, 'el': 0.0}
        for year in (2016, 2017, 2018):
            dmg = compute_annual_damage(
                cmds[year], c1_config,
                D_state={'bat': 0.0, 'fc': 0.0, 'el': 0.0},
                coalition=state,
            )
            total_dD['bat'] += dmg['dD_bat']
            total_dD['fc'] += dmg['dD_fc']
            total_dD['el'] += dmg['dD_el']
        coalition_damages[code] = total_dD

    # Compute Shapley for each device target
    shapley_results = {}
    for target in ('bat', 'fc', 'el'):
        ae_values = {code: vals[target] for code, vals in coalition_damages.items()}
        coalition_values = coalition_loop(ae_values)
        shapley_vals = {}
        for group in ('C', 'E', 'P', 'I'):
            phi = compute_shapley(coalition_values, mediator=group)
            shapley_vals[group] = phi
        shapley_results[target] = {
            "coalition_values": coalition_values,
            "shapley_values": shapley_vals,
            "total_damage": sum(ae_values.values()) / 16.0,
            "full_on_damage": ae_values.get("1111", 0.0),
            "full_off_damage": ae_values.get("0000", 0.0),
        }
        phi_sum = sum(shapley_vals.values())
        print(f"    Shapley({target}): C={shapley_vals['C']:.6f} E={shapley_vals['E']:.6f} "
              f"P={shapley_vals['P']:.6f} I={shapley_vals['I']:.6f} sum={phi_sum:.6f}")

    return shapley_results


def compute_economics(rep: dict, annual_damages: dict,
                      eol_result: dict) -> dict:
    """Compute NPC and LCOE using EOL event-based replacement counts."""
    from economics.npc_lite import EconomicConfig, compute_equipment_capex

    pv_kwp = float(rep['pv_kwp'])
    bat_kwh = float(rep['bat_kwh'])
    pemfc_kw = float(rep['pemfc_kw'])
    pemel_kw = float(rep['pemel_kw'])
    h2_kg = float(rep['h2_kg'])

    econ_cfg = EconomicConfig(
        pv_kwp=pv_kwp, bat_kwh=bat_kwh, pemfc_kw=pemfc_kw,
        pemel_kw=pemel_kw, h2_kg=h2_kg,
    )

    capex = compute_equipment_capex(econ_cfg)
    total_capex = capex.get('total_initial', 0)

    # Annual energy (from Stage 2c results)
    metrics = rep.get('metrics', {})
    yearly = metrics.get('yearly', {})
    annual_load_kwh = np.mean([yearly[str(y)].get('load_energy_kwh', 500000) for y in (2016, 2017, 2018)])
    annual_unserved_kwh = np.mean([yearly[str(y)].get('unserved_energy_kwh', 0) for y in (2016, 2017, 2018)])
    annual_served_kwh = annual_load_kwh - annual_unserved_kwh

    # Replacement schedule from EOL event model
    horizon = econ_cfg.horizon_years
    discount = econ_cfg.discount_rate

    # Extract replacement years from events
    replacements = {'BAT': [], 'PEMFC': [], 'PEMEL': []}
    device_map = {"bat": "BAT", "fc": "PEMFC", "el": "PEMEL"}
    for event in eol_result.get("events", []):
        dev_key = device_map.get(event["device"])
        if dev_key:
            yr = int(event["time_hours"] / 8760.0)
            if 0 < yr < horizon and yr not in replacements[dev_key]:
                replacements[dev_key].append(yr)
    for key in replacements:
        replacements[key].sort()

    # NPC calculation
    npc = total_capex
    annual_opex = total_capex * econ_cfg.general_opex_fraction
    for y in range(1, horizon + 1):
        pv_factor = 1.0 / (1.0 + discount) ** y
        npc += annual_opex * pv_factor
        if y in replacements.get('BAT', []):
            npc += capex.get('BAT', 0) * econ_cfg.bat_replacement_fraction * pv_factor
        if y in replacements.get('PEMFC', []):
            npc += capex.get('PEMFC', 0) * econ_cfg.pemfc_replacement_fraction * pv_factor
        if y in replacements.get('PEMEL', []):
            npc += capex.get('PEMEL', 0) * econ_cfg.pemel_replacement_fraction * pv_factor

    # LCOE
    crf = discount * (1 + discount) ** horizon / ((1 + discount) ** horizon - 1) if discount > 0 else 1.0 / horizon
    annual_cost = npc * crf
    lcoe = annual_cost / max(annual_served_kwh, 1e-6)

    result = {
        "capex_breakdown": {k: v for k, v in capex.items() if isinstance(v, (int, float))},
        "total_capex_cny": total_capex,
        "npc_cny": npc,
        "annual_opex_cny": annual_opex,
        "lcoe_cny_per_kwh": lcoe,
        "lcoe_usd_per_kwh": lcoe / econ_cfg.exchange_rate,
        "annual_served_kwh": annual_served_kwh,
        "replacements": replacements,
        "horizon_years": horizon,
        "discount_rate": discount,
    }

    print(f"  --- Economics ---")
    print(f"    Total CAPEX: {total_capex/1000:.0f} kCNY")
    print(f"    NPC: {npc/1000:.0f} kCNY")
    print(f"    LCOE: {lcoe:.4f} CNY/kWh ({lcoe/econ_cfg.exchange_rate:.4f} USD/kWh)")
    print(f"    Replacements: BAT={len(replacements['BAT'])} FC={len(replacements['PEMFC'])} EL={len(replacements['PEMEL'])}")

    return result


def main():
    config_path = ROOT / "05_config" / "rectification_v2_stage2c.json"
    data_path = ROOT / "02_data_processed" / "tradestreet_pipeline" / "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv"
    admissions_path = ROOT / "07_results" / "rectification_v2_stage2c" / "search" / "verified_admissions.json"
    result_root = ROOT / "07_results" / "rectification_v2_stage2c"

    results = run_stage3_degradation(config_path, data_path, admissions_path, result_root)

    # Write results
    output_path = result_root / "stage3" / "degradation_summary.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nResults written to: {output_path}")

    # Print summary table
    print(f"\n{'='*90}")
    print(f"{'Role':<18} {'BAT life':>10} {'FC life':>10} {'EL life':>10} {'NPC(kCNY)':>12} {'LCOE(CNY/kWh)':>15} {'LCOE(USD/kWh)':>15}")
    print(f"{'-'*90}")
    for rep in results["representatives"]:
        life = rep["lifetime_years"]
        econ = rep["economics"]
        print(f"{rep['role']:<18} {life['bat']:>9.1f}y {life['fc']:>9.1f}y {life['el']:>9.1f}y "
              f"{econ['npc_cny']/1000:>11.0f} {econ['lcoe_cny_per_kwh']:>14.4f} {econ['lcoe_usd_per_kwh']:>14.4f}")


if __name__ == "__main__":
    main()
