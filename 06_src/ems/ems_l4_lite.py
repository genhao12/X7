# -*- coding: utf-8 -*-
"""
M3 ems_l4_lite — L4耦合感知EMS模块
功能：基于D_BAT自适应调整EMS调度规则, 减少FC/EL耦合应力
依赖：M4 ems_bf_lite (EMSConfig), M2 hp_bat_lite

设计依据:
    识别到的耦合路径:
        alpha1 (BAT->FC): BAT退化→容量缩减→更多FC调度→FC应力增加
        beta1  (BAT->EL): BAT退化→容量缩减→更多EL调度→EL应力增加

    L4 EMS通过三条规则主动打破耦合链:
        R1: SOC带缩减   — 减少BAT深充深放 → 降低DOD → 减缓BAT退化 → 减弱耦合源
        R2: FC最小运行延长 — 减少FC启停次数 → 降低FC启停损伤
        R3: EL爬坡限制   — 限制EL功率变化率 → 降低EL热应力

启动条件 (已验证满足):
    TE >= 5%  (BAT-only TE = 6.59%) ✓
    Bias_X >= 10% (D1 = 15.59%)     ✓

输入：load_kw, pv_kw, EMSConfig, D_bat, L4Config
输出：commands (N_steps, 3) [P_BAT, P_FC, P_EL] in kW, state dict
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "06_src"))

import numpy as np
from typing import Dict, Any, Tuple, Optional
from dataclasses import dataclass
from ems.ems_bf_lite import EMSConfig


@dataclass
class L4Config:
    """L4耦合感知EMS调整参数"""
    # R1: SOC带缩减系数
    # D_BAT=1时SOC运行带缩减soc_band_narrow_k*100%
    soc_band_narrow_k: float = 0.15

    # R2: FC最小运行时间延长系数
    # D_BAT=1时FC最小运行时间延长fc_min_run_extend_k*100%
    # 基础75min, D_BAT=1时延长到75*(1+1.0)=150min (10步)
    fc_min_run_extend_k: float = 1.0

    # R3: EL爬坡限制系数
    # D_BAT=1时EL功率变化率限制为额定功率的el_ramp_limit_k
    el_ramp_limit_k: float = 0.30

    # D_BAT激活阈值: 低于此值不进行L4调整
    d_bat_threshold: float = 0.05


def dispatch_l4(load_kw: np.ndarray, pv_kw: np.ndarray,
                config: EMSConfig,
                D_bat: float = 0.0,
                bat_p_max_kw: Optional[float] = None,
                bat_capacity_kwh: Optional[float] = None,
                fc_eff_frac: Optional[float] = None,
                fc_p_max_kw: Optional[float] = None,
                el_eff_frac: Optional[float] = None,
                el_p_max_kw: Optional[float] = None,
                l4_config: Optional[L4Config] = None,
                strategy: str = 'battery_first') -> Tuple[np.ndarray, Dict[str, Any]]:
    """
    L4耦合感知EMS调度。

    在battery_first基础上增加三条D_BAT自适应调整规则:
        R1: SOC带缩减 -> 减少BAT DOD
        R2: FC最小运行时间延长 -> 减少FC启停
        R3: EL爬坡限制 -> 减少EL热应力

    Args:
        load_kw: (N_steps,) 负荷功率kW
        pv_kw: (N_steps,) PV输出功率kW
        config: EMS配置参数(EMSConfig)
        D_bat: BAT当前退化状态[0,1], L4调整的驱动信号
        bat_p_max_kw: BAT可用功率上限(退化感知)
        bat_capacity_kwh: BAT有效容量(退化Q缩减)
        fc_eff_frac: FC效率保持率(退化感知)
        fc_p_max_kw: FC最大可用功率(退化感知)
        el_eff_frac: EL效率保持率(退化感知)
        el_p_max_kw: EL最大可用功率(退化感知)
        l4_config: L4调整参数, None=默认值
        strategy: EMS策略, 仅支持'battery_first'

    Returns:
        commands: (N_steps, 3) [P_BAT, P_FC, P_EL] in kW
        state: dict, 含soc_trace/h2_trace/p_uns/p_cur及l4_adjustments
    """
    if strategy != 'battery_first':
        raise NotImplementedError(
            f"Strategy '{strategy}' is not implemented in dispatch_l4. "
            f"Only 'battery_first' is supported."
        )

    if l4_config is None:
        l4_config = L4Config()

    n = len(load_kw)
    dt_h = config.dt_min / 60.0

    LHV_H2 = 33.3
    ETA_FC = 0.50
    ETA_EL = 0.70

    # === L4调整量计算 (基于D_BAT) ===
    D_bat_clipped = float(np.clip(D_bat, 0.0, 1.0))
    l4_active = D_bat_clipped > l4_config.d_bat_threshold

    # R1: SOC带缩减
    soc_range_orig = config.soc_max - config.soc_min
    if l4_active:
        narrow = l4_config.soc_band_narrow_k * D_bat_clipped
        soc_range_eff = soc_range_orig * (1.0 - narrow)
        soc_center = (config.soc_max + config.soc_min) / 2.0
        soc_min_eff = soc_center - soc_range_eff / 2.0
        soc_max_eff = soc_center + soc_range_eff / 2.0
    else:
        soc_min_eff = config.soc_min
        soc_max_eff = config.soc_max

    # R2: FC最小运行时间延长
    if l4_active:
        extend_factor = 1.0 + l4_config.fc_min_run_extend_k * D_bat_clipped
        fc_min_run_min_eff = config.minimum_run_time_min * extend_factor
    else:
        fc_min_run_min_eff = config.minimum_run_time_min
    fc_min_run_steps = max(1, int(round(fc_min_run_min_eff / config.dt_min)))

    # R3: EL爬坡限制 (kW/step)
    el_rated_base = el_p_max_kw if el_p_max_kw is not None else config.pemel_kw
    if l4_active:
        el_max_ramp = el_rated_base * l4_config.el_ramp_limit_k * D_bat_clipped
    else:
        el_max_ramp = float('inf')

    l4_adjustments = {
        'l4_active': l4_active,
        'D_bat': D_bat_clipped,
        'R1_soc_min_eff': soc_min_eff,
        'R1_soc_max_eff': soc_max_eff,
        'R1_soc_range_narrow_pct': (1.0 - (soc_max_eff - soc_min_eff) / soc_range_orig) * 100 if l4_active else 0.0,
        'R2_fc_min_run_steps': fc_min_run_steps,
        'R2_fc_min_run_min': fc_min_run_min_eff,
        'R3_el_max_ramp_kW_per_step': el_max_ramp if l4_active else float('inf'),
    }

    # === 容量参数 (退化感知) ===
    bat_kwh_eff = bat_capacity_kwh if bat_capacity_kwh is not None else config.bat_kwh
    fc_rated = fc_p_max_kw if fc_p_max_kw is not None else config.pemfc_kw
    eta_fc_eff = ETA_FC * fc_eff_frac if fc_eff_frac is not None else ETA_FC
    el_rated = el_p_max_kw if el_p_max_kw is not None else config.pemel_kw
    eta_el_eff = ETA_EL * el_eff_frac if el_eff_frac is not None else ETA_EL

    h2_max = config.h2_kg
    h2_min = config.h2_kg * config.h2_min_fraction
    p_bat_max = bat_p_max_kw if bat_p_max_kw is not None else config.bat_kwh * 0.5

    installed = config.pv_kwp + bat_kwh_eff * 0.5 + fc_rated + el_rated
    p_aux = config.p_aux_fraction * installed

    fc_min_stable = fc_rated * config.min_stable_load_fc
    el_min_stable = el_rated * config.min_stable_load_el

    # 初始化轨迹
    p_bat = np.zeros(n)
    p_fc = np.zeros(n)
    p_el = np.zeros(n)
    p_uns = np.zeros(n)
    p_cur = np.zeros(n)
    soc_trace = np.zeros(n)
    h2_trace = np.zeros(n)

    soc = config.soc_initial
    h2 = config.h2_kg * config.h2_initial_fraction

    # R2: FC最小运行时间追踪
    fc_run_remaining = 0
    # R3: EL上一步功率
    el_prev_power = 0.0

    for t in range(n):
        # 氢气泄漏
        if h2 > h2_min:
            h2_leak = config.h2_leakage_per_day * h2 / 24.0 * dt_h
            h2 = max(h2 - h2_leak, h2_min)

        p_net = pv_kw[t] - load_kw[t] - p_aux

        if p_net >= 0:
            # === PV过剩: 充电 -> EL制氢 -> 弃光 ===
            # 1. 电池充电 (使用L4调整后的SOC上限)
            charge_cap = max(0.0, (soc_max_eff - soc) * bat_kwh_eff / dt_h)
            p_charge = min(p_net, p_bat_max, charge_cap)
            p_bat[t] = -p_charge
            soc += p_charge * dt_h / bat_kwh_eff
            soc = min(soc, soc_max_eff)
            remaining = p_net - p_charge

            # 2. EL制氢 (含R3爬坡限制)
            if remaining > el_min_stable and h2 < h2_max:
                h2_cap = max(0.0, (h2_max - h2) * LHV_H2 / (eta_el_eff * dt_h))
                p_el_raw = min(remaining, el_rated, h2_cap)
                if p_el_raw < el_min_stable:
                    p_el_raw = 0.0

                # R3: EL爬坡限制 (含冷启动例外 — 修复M1)
                # 冷启动(el_prev_power=0)时允许直接跳到el_min_stable,
                # 不受爬坡限制; 仅限制后续步的功率增量
                if el_max_ramp != float('inf') and p_el_raw > 0:
                    if el_prev_power > 0:
                        # 已运行: 限制功率增量
                        max_increase = el_prev_power + el_max_ramp
                        p_el_raw = min(p_el_raw, max_increase)
                        if p_el_raw < el_min_stable:
                            p_el_raw = 0.0
                    # 冷启动: 不受爬坡限制, 允许跳到最小稳定功率

                p_el[t] = p_el_raw
                h2 += p_el_raw * dt_h * eta_el_eff / LHV_H2
                h2 = min(h2, h2_max)
                remaining -= p_el_raw
                el_prev_power = p_el_raw
            else:
                el_prev_power = 0.0

            # 3. 弃光
            p_cur[t] = max(0.0, remaining)

            # R2: FC最小运行时间延续 (不因PV过剩而立即关停)
            if fc_run_remaining > 0 and h2 > h2_min:
                h2_avail = max(0.0, (h2 - h2_min) * eta_fc_eff * LHV_H2 / dt_h)
                p_fc_force = min(fc_min_stable, h2_avail)
                if p_fc_force >= fc_min_stable * 0.99:
                    p_fc[t] = p_fc_force
                    h2 -= p_fc_force * dt_h / (eta_fc_eff * LHV_H2)
                    h2 = max(h2, h2_min)
                    # FC多余发电回充给BAT (维持功率守恒)
                    charge_cap_extra = max(0.0, (soc_max_eff - soc) * bat_kwh_eff / dt_h)
                    p_charge_extra = min(p_fc_force, p_bat_max, charge_cap_extra)
                    p_bat[t] -= p_charge_extra
                    soc = min(soc + p_charge_extra * dt_h / bat_kwh_eff, soc_max_eff)
                    # 无法回充的部分转为弃光
                    p_cur[t] += max(0.0, p_fc_force - p_charge_extra)
                fc_run_remaining = max(0, fc_run_remaining - 1)
            else:
                fc_run_remaining = 0

        else:
            # === PV不足: 电池放电 -> FC发电 -> 未供电 ===
            deficit = -p_net

            # 1. 电池放电 (使用L4调整后的SOC下限)
            discharge_cap = max(0.0, (soc - soc_min_eff) * bat_kwh_eff / dt_h)
            p_discharge = min(deficit, p_bat_max, discharge_cap)
            p_bat[t] = p_discharge
            soc -= p_discharge * dt_h / bat_kwh_eff
            soc = max(soc, soc_min_eff)
            remaining = deficit - p_discharge

            # 2. FC发电 (含R2最小运行时间)
            if remaining > fc_min_stable and h2 > h2_min:
                h2_avail = max(0.0, (h2 - h2_min) * eta_fc_eff * LHV_H2 / dt_h)
                p_fc_raw = min(remaining, fc_rated, h2_avail)
                if p_fc_raw < fc_min_stable:
                    p_fc_raw = 0.0

                p_fc[t] = p_fc_raw
                h2 -= p_fc_raw * dt_h / (eta_fc_eff * LHV_H2)
                h2 = max(h2, h2_min)
                remaining -= p_fc_raw

                # R2: 激活FC最小运行计数器
                if p_fc_raw > 0:
                    fc_run_remaining = fc_min_run_steps
            else:
                # R2: FC最小运行时间强制保持
                if fc_run_remaining > 0 and h2 > h2_min:
                    h2_avail = max(0.0, (h2 - h2_min) * eta_fc_eff * LHV_H2 / dt_h)
                    p_fc_force = min(fc_min_stable, h2_avail)
                    if p_fc_force >= fc_min_stable * 0.99:
                        p_fc[t] = p_fc_force
                        h2 -= p_fc_force * dt_h / (eta_fc_eff * LHV_H2)
                        h2 = max(h2, h2_min)

                        # FC多余发电回充给BAT (维持功率守恒)
                        if remaining < p_fc_force:
                            excess = p_fc_force - remaining
                            charge_cap = max(0.0, (soc_max_eff - soc) * bat_kwh_eff / dt_h)
                            p_charge_back = min(excess, p_bat_max, charge_cap)
                            p_bat[t] = p_discharge - p_charge_back
                            soc = min(soc + p_charge_back * dt_h / bat_kwh_eff, soc_max_eff)
                        remaining = max(0.0, remaining - p_fc_force)

                fc_run_remaining = max(0, fc_run_remaining - 1)

            # 3. 未供电
            p_uns[t] = max(0.0, remaining)
            el_prev_power = 0.0

        soc_trace[t] = soc
        h2_trace[t] = h2

    commands = np.column_stack([p_bat, p_fc, p_el])

    # === 守恒残差 ===
    p_bat_dis = np.maximum(p_bat, 0)
    p_bat_ch = np.maximum(-p_bat, 0)
    power_lhs = np.sum(pv_kw) + np.sum(p_fc) + np.sum(p_bat_dis) + np.sum(p_uns)
    power_rhs = np.sum(load_kw) + np.sum(p_el) + np.sum(p_bat_ch) + np.sum(p_cur) + np.sum(p_aux * np.ones(n))
    power_residual = abs(power_lhs - power_rhs)

    m_start = config.h2_kg * config.h2_initial_fraction
    m_end = h2_trace[-1]
    m_prod = np.sum(p_el * dt_h * eta_el_eff / LHV_H2)
    m_cons = np.sum(p_fc * dt_h / (eta_fc_eff * LHV_H2))
    m_leak = config.h2_leakage_per_day * np.mean(h2_trace) / 24.0 * (n * dt_h)
    h2_residual = abs((m_end - m_start) - (m_prod - m_cons - m_leak))

    state = {
        'soc_trace': soc_trace,
        'h2_trace': h2_trace,
        'p_uns': p_uns,
        'p_cur': p_cur,
        'p_aux': p_aux,
        'power_residual': power_residual,
        'h2_residual': h2_residual,
        'p_bat_dis': p_bat_dis,
        'p_bat_ch': p_bat_ch,
        'total_load': float(np.sum(load_kw)),
        'total_pv': float(np.sum(pv_kw)),
        'l4_adjustments': l4_adjustments,
        'soc_min_eff': soc_min_eff,
        'soc_max_eff': soc_max_eff,
    }

    return commands, state


def get_l4_adjustment_summary(D_bat: float, config: EMSConfig,
                               l4_config: L4Config = None) -> Dict[str, Any]:
    """计算给定D_BAT下的L4调整量摘要 (不运行仿真, 仅计算参数)。"""
    if l4_config is None:
        l4_config = L4Config()

    D = float(np.clip(D_bat, 0.0, 1.0))
    l4_active = D > l4_config.d_bat_threshold

    soc_range_orig = config.soc_max - config.soc_min
    if l4_active:
        narrow = l4_config.soc_band_narrow_k * D
        soc_range_eff = soc_range_orig * (1.0 - narrow)
        soc_center = (config.soc_max + config.soc_min) / 2.0
        soc_min_eff = soc_center - soc_range_eff / 2.0
        soc_max_eff = soc_center + soc_range_eff / 2.0
    else:
        soc_min_eff = config.soc_min
        soc_max_eff = config.soc_max

    extend_factor = 1.0 + l4_config.fc_min_run_extend_k * D if l4_active else 1.0
    fc_min_run_eff = config.minimum_run_time_min * extend_factor
    fc_min_run_steps = max(1, int(round(fc_min_run_eff / config.dt_min)))

    el_ramp = config.pemel_kw * l4_config.el_ramp_limit_k * D if l4_active else float('inf')

    return {
        'D_bat': D,
        'l4_active': l4_active,
        'R1_soc_min': soc_min_eff,
        'R1_soc_max': soc_max_eff,
        'R1_soc_range': soc_max_eff - soc_min_eff,
        'R1_narrow_pct': (1.0 - (soc_max_eff - soc_min_eff) / soc_range_orig) * 100,
        'R2_fc_min_run_min': fc_min_run_eff,
        'R2_fc_min_run_steps': fc_min_run_steps,
        'R2_extend_pct': (extend_factor - 1.0) * 100,
        'R3_el_max_ramp_kW': el_ramp,
        'R3_el_ramp_pct_of_rated': (el_ramp / config.pemel_kw * 100) if l4_active else 100.0,
    }


if __name__ == "__main__":
    import json, sys
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(project_root / "06_src"))

    print("=" * 70)
    print("M3 ems_l4_lite - L4耦合感知EMS模块 自测")
    print("=" * 70)

    config_path = project_root / "05_config" / "mvp_c1_config.json"
    with open(config_path, 'r', encoding='utf-8') as f:
        c1 = json.load(f)

    cap = c1['capacity']; ems_p = c1['ems_parameters']
    bat_c = c1['battery_constraints']; h2_c = c1['hydrogen_constraints']
    msl = c1['minimum_stable_load']; topo = c1['topology']

    cfg = EMSConfig(
        pv_kwp=cap['PV_kWp'], bat_kwh=cap['BAT_kWh'],
        pemfc_kw=cap['PEMFC_kW'], pemel_kw=cap['PEMEL_kW'], h2_kg=cap['H2_kg'],
        soc_min=bat_c['soc_min'], soc_max=bat_c['soc_max'], soc_initial=bat_c['soc_initial'],
        h2_min_fraction=h2_c['m_min_fraction'], h2_max_fraction=h2_c['m_max_fraction'],
        h2_initial_fraction=h2_c['m_initial_fraction'],
        h2_leakage_per_day=h2_c['leakage_rate_per_day'],
        start_stop_threshold=ems_p['start_stop_threshold_fraction'],
        minimum_run_time_min=float(ems_p['minimum_run_time']),
        critical_load_fraction=ems_p['critical_load_fraction'],
        p_aux_fraction=topo['P_aux_fraction_of_installed_power'],
        min_stable_load_fc=msl['PEMFC'], min_stable_load_el=msl['PEMEL'], dt_min=15.0,
    )

    print(f"\n  PV={cfg.pv_kwp}kWp, BAT={cfg.bat_kwh}kWh, FC={cfg.pemfc_kw}kW, EL={cfg.pemel_kw}kW")

    # L4调整参数预览
    print("\n[1] L4调整参数预览 (不同D_BAT)")
    for D in [0.0, 0.05, 0.10, 0.25, 0.50, 0.75, 1.00]:
        s = get_l4_adjustment_summary(D, cfg)
        print(f"  D={D:.2f}: active={s['l4_active']}, "
              f"SOC=[{s['R1_soc_min']:.3f},{s['R1_soc_max']:.3f}], "
              f"FC_minrun={s['R2_fc_min_run_min']:.0f}min, "
              f"EL_ramp={s['R3_el_max_ramp_kW']:.1f}kW")

    # 加载W1数据
    import pandas as pd
    csv_path = str(project_root / "02_data_processed" / "tradestreet_pipeline" /
                   "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv")
    raw = pd.read_csv(csv_path, parse_dates=['timestamp_local_civil'])
    df = raw[raw['local_civil_year'] == 2017].copy()
    df = df.rename(columns={
        'p_pv_available_reconstructed_nonnegative_kW': 'P_pv_kW',
        'p_building_load_reconstructed_kW': 'P_load_kW'})
    df['P_pv_kW'] = df['P_pv_kW'].ffill().bfill()
    df['P_load_kW'] = df['P_load_kW'].ffill().bfill()
    pv_kw = df['P_pv_kW'].values[:672]
    load_kw = df['P_load_kW'].values[:672]

    # 单周对比
    print("\n[2] 单周对比: BOL(D=0) vs L4(D=0.5)")
    from ems.ems_bf_lite import rule_based_dispatch

    np.random.seed(42)
    cmds_bol, st_bol = rule_based_dispatch(load_kw, pv_kw, cfg)

    np.random.seed(42)
    cmds_l4, st_l4 = dispatch_l4(load_kw, pv_kw, cfg, D_bat=0.5)

    print(f"  {'Metric':<25} {'BOL':<15} {'L4(D=0.5)':<15}")
    print(f"  {'-'*25} {'-'*15} {'-'*15}")
    print(f"  {'Power residual':<25} {st_bol['power_residual']:<15.6f} {st_l4['power_residual']:<15.6f}")
    print(f"  {'H2 residual':<25} {st_bol['h2_residual']:<15.6f} {st_l4['h2_residual']:<15.6f}")
    print(f"  {'LPSP (%)':<25} {np.sum(st_bol['p_uns'])/np.sum(load_kw)*100:<15.3f} {np.sum(st_l4['p_uns'])/np.sum(load_kw)*100:<15.3f}")
    print(f"  {'Curtail (kWh)':<25} {np.sum(st_bol['p_cur'])*0.25:<15.1f} {np.sum(st_l4['p_cur'])*0.25:<15.1f}")
    print(f"  {'FC energy (kWh)':<25} {np.sum(cmds_bol[:,1])*0.25:<15.1f} {np.sum(cmds_l4[:,1])*0.25:<15.1f}")
    print(f"  {'EL energy (kWh)':<25} {np.sum(cmds_bol[:,2])*0.25:<15.1f} {np.sum(cmds_l4[:,2])*0.25:<15.1f}")
    print(f"  {'SOC range':<25} [{st_bol['soc_trace'].min():.3f},{st_bol['soc_trace'].max():.3f}]      [{st_l4['soc_trace'].min():.3f},{st_l4['soc_trace'].max():.3f}]")

    adj = st_l4['l4_adjustments']
    print(f"\n  L4调整: SOC=[{adj['R1_soc_min_eff']:.3f},{adj['R1_soc_max_eff']:.3f}], "
          f"FC_minrun={adj['R2_fc_min_run_steps']}步, "
          f"EL_ramp={adj['R3_el_max_ramp_kW_per_step']:.1f}kW/step")

    print("\n" + "=" * 70)
    print("M3 ems_l4_lite 自测完成")
    print("=" * 70)
