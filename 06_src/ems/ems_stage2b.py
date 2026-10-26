"""Stage2b rule EMS with causal forecasts and complete carried machine state."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional

import numpy as np


LHV_H2_KWH_PER_KG = 33.3
EPS = 1e-9


@dataclass(frozen=True)
class Stage2BEMSConfig:
    pv_kwp: float
    bat_kwh: float
    pemfc_kw: float
    pemel_kw: float
    h2_kg: float
    soc_min: float = 0.15
    soc_max: float = 0.95
    dt_min: float = 15.0
    h2_min_fraction: float = 0.10
    h2_max_fraction: float = 1.0
    h2_leakage_per_day: float = 0.0005
    min_stable_load_fc: float = 0.15
    min_stable_load_el: float = 0.15
    minimum_run_steps: int = 5
    standby_kw: float = 0.5
    pv_aux_fraction: float = 0.015
    bat_aux_fraction: float = 0.010
    fc_aux_fraction: float = 0.030
    el_aux_fraction: float = 0.025
    eta_fc: float = 0.50
    eta_el: float = 0.70
    bat_c_rate: float = 0.5

    def __post_init__(self) -> None:
        positive = (
            self.pv_kwp,
            self.bat_kwh,
            self.pemfc_kw,
            self.pemel_kw,
            self.h2_kg,
            self.dt_min,
            self.minimum_run_steps,
        )
        if any(value <= 0 for value in positive):
            raise ValueError("capacities, timestep and minimum run steps must be positive")
        if not 0 <= self.soc_min < self.soc_max <= 1:
            raise ValueError("invalid SOC bounds")
        if not 0 <= self.h2_min_fraction < self.h2_max_fraction <= 1:
            raise ValueError("invalid H2 bounds")

    @property
    def dt_h(self) -> float:
        return self.dt_min / 60.0

    @property
    def bat_power_kw(self) -> float:
        return self.bat_kwh * self.bat_c_rate


@dataclass(frozen=True)
class EMSState:
    soc: float
    h2_kg: float
    fc_on: bool = False
    fc_steps_remaining: int = 0
    el_on: bool = False
    el_steps_remaining: int = 0


@dataclass(frozen=True)
class ForecastWindow:
    load_kw: np.ndarray
    pv_kw: np.ndarray
    mode: str
    paper_citable: bool


@dataclass
class DispatchResult:
    commands: np.ndarray
    trace: Dict[str, np.ndarray]
    final_state: EMSState
    metrics: Dict[str, object]


ForecastProvider = Callable[[np.ndarray, np.ndarray, int, int], ForecastWindow]


def causal_persistence_forecast(
    load_kw: np.ndarray, pv_kw: np.ndarray, index: int, horizon: int
) -> ForecastWindow:
    """Repeat only the current observation; never read future array elements."""
    return ForecastWindow(
        load_kw=np.full(horizon, float(load_kw[index])),
        pv_kw=np.full(horizon, float(pv_kw[index])),
        mode="causal_persistence",
        paper_citable=True,
    )


def perfect_forecast(
    load_kw: np.ndarray, pv_kw: np.ndarray, index: int, horizon: int
) -> ForecastWindow:
    """Return actual future values as a non-citable diagnostic upper bound."""
    n = len(load_kw)
    positions = np.minimum(np.arange(index, index + horizon), n - 1)
    return ForecastWindow(
        load_kw=np.asarray(load_kw, dtype=float)[positions],
        pv_kw=np.asarray(pv_kw, dtype=float)[positions],
        mode="perfect_forecast_upper_bound",
        paper_citable=False,
    )


def positive_run_lengths(values: Iterable[float], tolerance: float = EPS) -> List[int]:
    runs: List[int] = []
    current = 0
    for active in np.asarray(values, dtype=float) > tolerance:
        if active:
            current += 1
        elif current:
            runs.append(current)
            current = 0
    if current:
        runs.append(current)
    return runs


def _validate_state(state: EMSState, cfg: Stage2BEMSConfig) -> None:
    h2_min = cfg.h2_kg * cfg.h2_min_fraction
    h2_max = cfg.h2_kg * cfg.h2_max_fraction
    if not cfg.soc_min - EPS <= state.soc <= cfg.soc_max + EPS:
        raise ValueError("initial SOC outside bounds")
    if not h2_min - EPS <= state.h2_kg <= h2_max + EPS:
        raise ValueError("initial H2 outside bounds")
    if state.fc_on and state.el_on:
        raise ValueError("FC and EL cannot both be on")
    if state.fc_steps_remaining < 0 or state.el_steps_remaining < 0:
        raise ValueError("remaining run steps cannot be negative")


def _can_start_fc(
    window: ForecastWindow, cfg: Stage2BEMSConfig, h2_kg: float, aux_multiplier: float
) -> bool:
    h2_min = cfg.h2_kg * cfg.h2_min_fraction
    p_min = cfg.pemfc_kw * cfg.min_stable_load_fc
    fuel = p_min * cfg.dt_h * cfg.minimum_run_steps / (cfg.eta_fc * LHV_H2_KWH_PER_KG)
    leakage_margin = (
        h2_kg * cfg.h2_leakage_per_day / 24.0 * cfg.dt_h * cfg.minimum_run_steps
    )
    pv_aux = np.where(window.pv_kw > EPS, cfg.pv_aux_fraction * cfg.pv_kwp * aux_multiplier, 0.0)
    predicted_net = window.pv_kw - window.load_kw - pv_aux - cfg.standby_kw
    return bool(h2_kg - h2_min >= fuel + leakage_margin - EPS and predicted_net[0] < -EPS)


def _can_start_el(
    window: ForecastWindow,
    cfg: Stage2BEMSConfig,
    h2_kg: float,
    soc: float,
    aux_multiplier: float,
) -> bool:
    h2_max = cfg.h2_kg * cfg.h2_max_fraction
    p_min = cfg.pemel_kw * cfg.min_stable_load_el
    produced = p_min * cfg.dt_h * cfg.minimum_run_steps * cfg.eta_el / LHV_H2_KWH_PER_KG
    pv_aux = np.where(window.pv_kw > EPS, cfg.pv_aux_fraction * cfg.pv_kwp * aux_multiplier, 0.0)
    el_aux = cfg.el_aux_fraction * cfg.pemel_kw * aux_multiplier
    predicted_surplus = window.pv_kw - window.load_kw - pv_aux - cfg.standby_kw
    battery_aux = cfg.bat_aux_fraction * cfg.bat_power_kw * aux_multiplier
    zero_pv_backup_kw = (
        window.load_kw + cfg.standby_kw + p_min + el_aux + battery_aux
    )
    backup_energy_kwh = float(np.sum(zero_pv_backup_kw) * cfg.dt_h)
    available_battery_energy_kwh = max(0.0, (soc - cfg.soc_min) * cfg.bat_kwh)
    return bool(
        h2_max - h2_kg >= produced - EPS
        and np.all(predicted_surplus >= p_min + el_aux - EPS)
        and np.all(zero_pv_backup_kw <= cfg.bat_power_kw + EPS)
        and available_battery_energy_kwh >= backup_energy_kwh - EPS
    )


def dispatch_stage2b(
    load_kw: np.ndarray,
    pv_kw: np.ndarray,
    config: Stage2BEMSConfig,
    strategy: str = "battery_first",
    initial_state: Optional[EMSState] = None,
    forecast_provider: ForecastProvider = causal_persistence_forecast,
    aux_multiplier: float = 1.0,
    degradation: Optional[Dict[str, float]] = None,
) -> DispatchResult:
    """Dispatch one continuous segment with explicit same-step machine states.

    strategy:
      - battery_first / hydrogen_first / balanced: 优先级规则调度（默认）
      - degradation_aware: 按当前设备退化状态 D 调整优先级——退化越重的设备越少启用
        （需传 degradation={'bat': D_bat, 'fc': D_fc, 'el': D_el}，缺省设备按 D=0 处理）
    """
    load = np.asarray(load_kw, dtype=float)
    pv = np.asarray(pv_kw, dtype=float)
    if load.ndim != 1 or pv.ndim != 1 or len(load) != len(pv):
        raise ValueError("load and PV must be equal-length one-dimensional arrays")
    if np.any(load < 0) or np.any(pv < 0):
        raise ValueError("load and PV must be nonnegative")
    if strategy not in {"battery_first", "hydrogen_first", "balanced", "degradation_aware"}:
        raise ValueError("unsupported Stage2b strategy")
    if aux_multiplier <= 0:
        raise ValueError("auxiliary multiplier must be positive")
    deg = degradation or {}
    d_bat = max(0.0, min(1.0, float(deg.get("bat", 0.0))))
    d_fc = max(0.0, min(1.0, float(deg.get("fc", 0.0))))
    d_el = max(0.0, min(1.0, float(deg.get("el", 0.0))))

    cfg = config
    state = initial_state or EMSState(0.50, cfg.h2_kg * 0.50)
    _validate_state(state, cfg)
    n = len(load)
    commands = np.zeros((n, 3), dtype=float)
    names = (
        "soc", "h2_kg", "p_uns", "p_cur", "p_dump", "p_aux_pv",
        "p_aux_bat", "p_aux_fc", "p_aux_el", "p_aux_standby", "p_aux_total",
        "power_residual_kw", "h2_residual_kg", "h2_prod_kg", "h2_cons_kg",
        "h2_leak_kg", "fc_on", "el_on", "fc_steps_remaining",
        "el_steps_remaining", "load_met", "battery_absorption_headroom_kw",
        "battery_supply_headroom_kw", "emergency_trip",
    )
    trace = {name: np.zeros(n, dtype=bool if name in {"fc_on", "el_on", "load_met"} else float) for name in names}

    soc = float(state.soc)
    h2 = float(state.h2_kg)
    fc_on = bool(state.fc_on and state.fc_steps_remaining > 0)
    el_on = bool(state.el_on and state.el_steps_remaining > 0)
    fc_remaining = int(state.fc_steps_remaining if fc_on else 0)
    el_remaining = int(state.el_steps_remaining if el_on else 0)
    forecast_mode = ""
    forecast_paper_citable = True
    el_displacement_kwh = 0.0

    h2_min = cfg.h2_kg * cfg.h2_min_fraction
    h2_max = cfg.h2_kg * cfg.h2_max_fraction
    fc_min = cfg.pemfc_kw * cfg.min_stable_load_fc
    el_min = cfg.pemel_kw * cfg.min_stable_load_el
    bat_aux_on = cfg.bat_aux_fraction * cfg.bat_power_kw * aux_multiplier
    fc_aux_on = cfg.fc_aux_fraction * cfg.pemfc_kw * aux_multiplier
    el_aux_on = cfg.el_aux_fraction * cfg.pemel_kw * aux_multiplier

    for t in range(n):
        h2_before = h2
        leak = 0.0
        if h2 > h2_min:
            requested = cfg.h2_leakage_per_day * h2 / 24.0 * cfg.dt_h
            h2 = max(h2 - requested, h2_min)
            leak = h2_before - h2

        p_aux_pv = cfg.pv_aux_fraction * cfg.pv_kwp * aux_multiplier if pv[t] > EPS else 0.0
        r = float(pv[t] - load[t] - p_aux_pv - cfg.standby_kw)
        p_bat = p_fc = p_el = 0.0
        p_aux_bat = p_aux_fc = p_aux_el = 0.0
        prod = cons = 0.0
        emergency_trip = 0.0
        window = forecast_provider(load, pv, t, cfg.minimum_run_steps)
        forecast_mode = window.mode
        forecast_paper_citable = forecast_paper_citable and window.paper_citable

        forced_fc = fc_on and fc_remaining > 0
        forced_el = el_on and el_remaining > 0

        def battery_discharge(deficit: float, limit: Optional[float] = None) -> float:
            nonlocal soc, p_bat, p_aux_bat, r
            current_discharge = max(p_bat, 0.0)
            if p_bat < -EPS:
                return deficit
            energy_cap = max(0.0, (soc - cfg.soc_min) * cfg.bat_kwh / cfg.dt_h)
            cap = min(max(0.0, cfg.bat_power_kw - current_discharge), energy_cap)
            if limit is not None:
                cap = min(cap, limit)
            auxiliary_increment = bat_aux_on if current_discharge <= EPS else 0.0
            if deficit <= EPS or cap <= auxiliary_increment + EPS:
                return deficit
            p_dis = min(cap, deficit + auxiliary_increment)
            if p_dis <= auxiliary_increment + EPS:
                return deficit
            p_bat += p_dis
            p_aux_bat = bat_aux_on
            soc = max(cfg.soc_min, soc - p_dis * cfg.dt_h / cfg.bat_kwh)
            r += p_dis - auxiliary_increment
            return max(0.0, -r)

        def battery_charge(surplus: float, limit: Optional[float] = None) -> float:
            nonlocal soc, p_bat, p_aux_bat, r
            current_charge = max(-p_bat, 0.0)
            if p_bat > EPS:
                return surplus
            energy_cap = max(0.0, (cfg.soc_max - soc) * cfg.bat_kwh / cfg.dt_h)
            cap = min(max(0.0, cfg.bat_power_kw - current_charge), energy_cap)
            if limit is not None:
                cap = min(cap, limit)
            auxiliary_increment = bat_aux_on if current_charge <= EPS else 0.0
            if surplus <= auxiliary_increment + EPS or cap <= EPS:
                return surplus
            p_ch = min(cap, surplus - auxiliary_increment)
            if p_ch <= EPS:
                return surplus
            p_bat -= p_ch
            p_aux_bat = bat_aux_on
            soc = min(cfg.soc_max, soc + p_ch * cfg.dt_h / cfg.bat_kwh)
            r -= p_ch + auxiliary_increment
            return max(0.0, r)

        def reset_battery_dispatch() -> None:
            nonlocal soc, p_bat, p_aux_bat, r
            if p_bat > EPS:
                soc = min(cfg.soc_max, soc + p_bat * cfg.dt_h / cfg.bat_kwh)
                r -= p_bat - p_aux_bat
            elif p_bat < -EPS:
                charge = -p_bat
                soc = max(cfg.soc_min, soc - charge * cfg.dt_h / cfg.bat_kwh)
                r += charge + p_aux_bat
            p_bat = 0.0
            p_aux_bat = 0.0

        def run_fc(starting: bool = False) -> bool:
            nonlocal h2, p_fc, p_aux_fc, r, cons, fc_on, fc_remaining
            committed_after_step = (
                cfg.minimum_run_steps - 1 if starting else max(0, fc_remaining - 1)
            )
            minimum_future_fuel = (
                fc_min
                * cfg.dt_h
                * committed_after_step
                / (cfg.eta_fc * LHV_H2_KWH_PER_KG)
            )
            future_leakage_margin = (
                h2 * cfg.h2_leakage_per_day / 24.0 * cfg.dt_h * committed_after_step
            )
            dispatchable_h2 = max(
                0.0, h2 - h2_min - minimum_future_fuel - future_leakage_margin
            )
            h2_power = dispatchable_h2 * cfg.eta_fc * LHV_H2_KWH_PER_KG / cfg.dt_h
            if h2_power < fc_min - EPS:
                return False
            required = max(fc_min, -r + fc_aux_on if r < 0 else fc_min)
            p_fc = min(cfg.pemfc_kw, h2_power, required)
            if p_fc < fc_min - EPS:
                p_fc = 0.0
                return False
            p_aux_fc = fc_aux_on
            cons = p_fc * cfg.dt_h / (cfg.eta_fc * LHV_H2_KWH_PER_KG)
            h2 = max(h2_min, h2 - cons)
            r += p_fc - p_aux_fc
            if starting:
                fc_on = True
                fc_remaining = cfg.minimum_run_steps
            return True

        def can_power_forced_el() -> bool:
            deficit_after = max(0.0, el_min + el_aux_on - r)
            if deficit_after <= EPS:
                return True
            energy_cap = max(0.0, (soc - cfg.soc_min) * cfg.bat_kwh / cfg.dt_h)
            net_bat = max(0.0, min(cfg.bat_power_kw, energy_cap) - bat_aux_on)
            return net_bat >= deficit_after - EPS

        def run_el(starting: bool = False) -> bool:
            nonlocal h2, p_el, p_aux_el, r, prod, el_on, el_remaining
            committed_after_step = (
                cfg.minimum_run_steps - 1 if starting else max(0, el_remaining - 1)
            )
            minimum_future_headroom = (
                el_min
                * cfg.dt_h
                * committed_after_step
                * cfg.eta_el
                / LHV_H2_KWH_PER_KG
            )
            dispatchable_headroom = max(0.0, h2_max - h2 - minimum_future_headroom)
            h2_power = (
                dispatchable_headroom
                * LHV_H2_KWH_PER_KG
                / (cfg.eta_el * cfg.dt_h)
            )
            if h2_power < el_min - EPS or not can_power_forced_el():
                return False
            available = max(el_min, r - el_aux_on)
            p_el = min(cfg.pemel_kw, h2_power, max(el_min, available))
            if p_el < el_min - EPS:
                p_el = 0.0
                return False
            p_aux_el = el_aux_on
            prod = p_el * cfg.dt_h * cfg.eta_el / LHV_H2_KWH_PER_KG
            h2 = min(h2_max, h2 + prod)
            r -= p_el + p_aux_el
            if starting:
                el_on = True
                el_remaining = cfg.minimum_run_steps
            return True

        if forced_fc:
            if not run_fc():
                emergency_trip = 1.0
                fc_on = False
                fc_remaining = 0
            if r < -EPS:
                battery_discharge(-r)
            elif r > EPS:
                battery_charge(r)
        elif forced_el:
            if not run_el():
                emergency_trip = 1.0
                el_on = False
                el_remaining = 0
            elif r < -EPS:
                battery_discharge(-r)
            elif r > EPS:
                battery_charge(r)
            if r < -EPS and p_el > 0:
                el_displacement_kwh += (-r) * cfg.dt_h
        elif r < -EPS:
            if strategy == "hydrogen_first":
                if _can_start_fc(window, cfg, h2, aux_multiplier):
                    run_fc(starting=True)
                if r < -EPS:
                    battery_discharge(-r)
                elif r > EPS:
                    battery_charge(r)
            elif strategy == "balanced":
                battery_discharge(-r, limit=max(0.0, -r / 2.0 + bat_aux_on))
                if r < -EPS and _can_start_fc(window, cfg, h2, aux_multiplier):
                    reset_battery_dispatch()
                    run_fc(starting=True)
                if r < -EPS:
                    battery_discharge(-r)
                elif r > EPS:
                    battery_charge(r)
            elif strategy == "degradation_aware":
                # 缺电：优先用退化较轻的设备——FC 比电池健康（d_fc < d_bat）且可启动时先用 FC
                if d_fc < d_bat and _can_start_fc(window, cfg, h2, aux_multiplier):
                    run_fc(starting=True)
                    if r < -EPS:
                        battery_discharge(-r)
                    elif r > EPS:
                        battery_charge(r)
                else:
                    battery_discharge(-r)
                    if r < -EPS and _can_start_fc(window, cfg, h2, aux_multiplier):
                        reset_battery_dispatch()
                        run_fc(starting=True)
                        if r < -EPS:
                            battery_discharge(-r)
                        elif r > EPS:
                            battery_charge(r)
            else:
                battery_discharge(-r)
                if r < -EPS and _can_start_fc(window, cfg, h2, aux_multiplier):
                    reset_battery_dispatch()
                    run_fc(starting=True)
                    if r < -EPS:
                        battery_discharge(-r)
                    elif r > EPS:
                        battery_charge(r)
        elif r > EPS:
            if strategy == "hydrogen_first":
                if _can_start_el(window, cfg, h2, soc, aux_multiplier):
                    run_el(starting=True)
                if r < -EPS:
                    battery_discharge(-r)
                elif r > EPS:
                    battery_charge(r)
            elif strategy == "balanced":
                battery_charge(r, limit=max(0.0, r / 2.0 - bat_aux_on))
                if r > EPS:
                    reset_battery_dispatch()
                    if _can_start_el(window, cfg, h2, soc, aux_multiplier):
                        run_el(starting=True)
                if r < -EPS:
                    battery_discharge(-r)
                elif r > EPS:
                    battery_charge(r)
            elif strategy == "degradation_aware":
                # 余电：优先充电（退化最重设备优先吸纳）→ 电化学存储先满则启动 EL（EL 比电池健康时更倾向产氢）
                if d_el < d_bat:
                    # EL 较健康：先启动 EL 产氢，剩余余电充电
                    if _can_start_el(window, cfg, h2, soc, aux_multiplier):
                        run_el(starting=True)
                    if r > EPS:
                        battery_charge(r)
                    elif r < -EPS:
                        battery_discharge(-r)
                else:
                    # 电池较健康：先充电，电池满后再 EL
                    battery_charge(r)
                    if r > EPS:
                        reset_battery_dispatch()
                        if _can_start_el(window, cfg, h2, soc, aux_multiplier):
                            run_el(starting=True)
                        if r < -EPS:
                            battery_discharge(-r)
                        elif r > EPS:
                            battery_charge(r)
            else:
                battery_charge(r)
                if r > EPS:
                    reset_battery_dispatch()
                    if _can_start_el(window, cfg, h2, soc, aux_multiplier):
                        run_el(starting=True)
                    if r < -EPS:
                        battery_discharge(-r)
                    elif r > EPS:
                        battery_charge(r)

        p_uns = max(0.0, -r)
        surplus = max(0.0, r)
        p_cur = min(surplus, pv[t])
        p_dump = max(0.0, surplus - p_cur)
        p_bat_dis = max(p_bat, 0.0)
        p_bat_ch = max(-p_bat, 0.0)

        active_fc = p_fc > EPS
        active_el = p_el > EPS
        if active_fc:
            fc_remaining = max(0, fc_remaining - 1)
            if fc_remaining == 0:
                fc_on = False
        else:
            fc_on = False
            fc_remaining = 0
        if active_el:
            el_remaining = max(0, el_remaining - 1)
            if el_remaining == 0:
                el_on = False
        else:
            el_on = False
            el_remaining = 0

        p_aux_total = p_aux_pv + p_aux_bat + p_aux_fc + p_aux_el + cfg.standby_kw
        power_residual = (
            pv[t] + p_fc + p_bat_dis + p_uns
            - load[t] - p_el - p_bat_ch - p_cur - p_dump - p_aux_total
        )
        h2_residual = (h2 - h2_before) - (prod - cons - leak)
        absorption_headroom = min(
            max(0.0, cfg.bat_power_kw - p_bat_ch),
            max(0.0, (cfg.soc_max - soc) * cfg.bat_kwh / cfg.dt_h),
        )
        supply_headroom = min(
            max(0.0, cfg.bat_power_kw - p_bat_dis),
            max(0.0, (soc - cfg.soc_min) * cfg.bat_kwh / cfg.dt_h),
        )

        commands[t] = (p_bat, p_fc, p_el)
        values = {
            "soc": soc, "h2_kg": h2, "p_uns": p_uns, "p_cur": p_cur,
            "p_dump": p_dump, "p_aux_pv": p_aux_pv, "p_aux_bat": p_aux_bat,
            "p_aux_fc": p_aux_fc, "p_aux_el": p_aux_el,
            "p_aux_standby": cfg.standby_kw, "p_aux_total": p_aux_total,
            "power_residual_kw": power_residual, "h2_residual_kg": h2_residual,
            "h2_prod_kg": prod, "h2_cons_kg": cons, "h2_leak_kg": leak,
            "fc_on": active_fc, "el_on": active_el,
            "fc_steps_remaining": fc_remaining, "el_steps_remaining": el_remaining,
            "load_met": p_uns <= EPS,
            "battery_absorption_headroom_kw": absorption_headroom,
            "battery_supply_headroom_kw": supply_headroom,
            "emergency_trip": emergency_trip,
        }
        for name, value in values.items():
            trace[name][t] = value

    final = EMSState(soc, h2, fc_on, fc_remaining, el_on, el_remaining)
    invalid_dump = (
        (trace["p_dump"] > EPS)
        & (~trace["load_met"] | (trace["battery_absorption_headroom_kw"] > EPS))
    )
    fc_runs = positive_run_lengths(commands[:, 1])
    el_runs = positive_run_lengths(commands[:, 2])
    metrics = {
        "forecast_mode": forecast_mode,
        "forecast_paper_citable": bool(forecast_paper_citable),
        "fc_el_overlap_count": int(np.sum(trace["fc_on"] & trace["el_on"])),
        "fc_run_lengths_steps": fc_runs,
        "el_run_lengths_steps": el_runs,
        "short_run_count": int(sum(length < cfg.minimum_run_steps for length in fc_runs + el_runs)),
        "el_critical_displacement_kwh": float(el_displacement_kwh),
        "invalid_dump_count": int(np.sum(invalid_dump)),
        "emergency_trip_count": int(np.sum(trace["emergency_trip"])),
        "max_power_residual_kw": float(np.max(np.abs(trace["power_residual_kw"]))) if n else 0.0,
        "max_h2_residual_kg": float(np.max(np.abs(trace["h2_residual_kg"]))) if n else 0.0,
    }
    return DispatchResult(commands, trace, final, metrics)


__all__ = [
    "DispatchResult", "EMSState", "ForecastWindow", "Stage2BEMSConfig",
    "causal_persistence_forecast", "dispatch_stage2b", "perfect_forecast",
    "positive_run_lengths",
]
