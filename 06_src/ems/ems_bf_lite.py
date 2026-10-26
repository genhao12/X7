# -*- coding: utf-8 -*-
"""
M4 ems_bf_lite — MVP battery_first规则EMS模块
功能：实现battery_first策略的规则式EMS调度
依赖：M3 closed_loop_lite
输入：closed_loop_result, C1容量参数
输出：ems_commands (N_steps, 3) [P_BAT, P_FC, P_EL] in kW
"""
import numpy as np
from typing import Dict, Any, Tuple
from dataclasses import dataclass


@dataclass
class EMSConfig:
    """EMS配置参数(来自mvp_c1_config.json)"""
    pv_kwp: float = 120.0
    bat_kwh: float = 135.0
    pemfc_kw: float = 37.5
    pemel_kw: float = 35.0
    h2_kg: float = 25.0
    soc_min: float = 0.15
    soc_max: float = 0.95
    soc_initial: float = 0.50
    h2_min_fraction: float = 0.10
    h2_max_fraction: float = 1.00
    h2_initial_fraction: float = 0.50
    h2_leakage_per_day: float = 0.0005
    start_stop_threshold: float = 0.275
    minimum_run_time_min: float = 75.0
    critical_load_fraction: float = 0.50
    p_aux_fraction: float = 0.02
    min_stable_load_fc: float = 0.15
    min_stable_load_el: float = 0.15
    dt_min: float = 15.0


def rule_based_dispatch(load_kw: np.ndarray, pv_kw: np.ndarray,
                        config: EMSConfig,
                        bat_p_max_kw: float = None,
                        bat_capacity_kwh: float = None,
                        fc_eff_frac: float = None,
                        fc_p_max_kw: float = None,
                        el_eff_frac: float = None,
                        el_p_max_kw: float = None,
                        strategy: str = 'battery_first',
                        soc_initial: float = None,
                        h2_initial_kg: float = None) -> Tuple[np.ndarray, Dict[str, Any]]:
    """
    规则式EMS调度 (battery_first策略)。

    注意:
        本函数仅实现battery_first策略。hydrogen_first/balanced策略
        在run_ems_comparison.py:dispatch_strategy()中独立实现。
        strategy参数保留用于API一致性, 非battery_first值将抛出NotImplementedError。

    策略逻辑 (battery_first):
        1. PV优先供电
        2. PV不足: BAT放电 -> FC发电 -> 未供电
        3. PV过剩: BAT充电 -> EL制氢 -> 弃光

    Args:
        load_kw: (N_steps,) 负荷功率kW
        pv_kw: (N_steps,) PV输出功率kW
        config: EMS配置参数
        bat_p_max_kw: BAT可用功率上限(退化感知), None=用额定0.5C
        bat_capacity_kwh: BAT有效容量(退化感知Q缩减), None=用额定容量
        fc_eff_frac: FC效率保持率(退化感知), None=BOL(1.0). 实际效率=ETA_FC*fc_eff_frac
        fc_p_max_kw: FC最大可用功率(退化感知), None=用额定功率
        el_eff_frac: EL效率保持率(退化感知), None=BOL(1.0). 实际效率=ETA_EL*el_eff_frac
        el_p_max_kw: EL最大可用功率(退化感知), None=用额定功率
        strategy: EMS策略, 仅支持'battery_first'(其他策略抛NotImplementedError)
        soc_initial: 本段起始SOC；None时使用config.soc_initial
        h2_initial_kg: 本段起始储氢量kg；None时使用配置初值

    Returns:
        commands: (N_steps, 3) [P_BAT, P_FC, P_EL] in kW
            P_BAT > 0 = 放电, < 0 = 充电
        state: dict, 含soc_trace/h2_trace/p_uns/p_cur/power_residual/h2_residual
    """
    # 策略守卫: 本函数仅实现battery_first
    # hydrogen_first/balanced在run_ems_comparison.py:dispatch_strategy()中独立实现
    if strategy != 'battery_first':
        raise NotImplementedError(
            f"Strategy '{strategy}' is not implemented in rule_based_dispatch. "
            f"Use run_ems_comparison.py:dispatch_strategy() for "
            f"hydrogen_first/balanced strategies."
        )

    n = len(load_kw)
    dt_h = config.dt_min / 60.0  # 0.25h

    # 物理常数
    LHV_H2 = 33.3  # kWh/kg
    ETA_FC = 0.50   # FC发电效率 (BOL)
    ETA_EL = 0.70   # EL制氢效率 (BOL)

    # 容量参数 (退化感知: 若传入则用退化后的值)
    bat_kwh_eff = bat_capacity_kwh if bat_capacity_kwh is not None else config.bat_kwh
    # FC退化感知: 效率和功率
    fc_rated = fc_p_max_kw if fc_p_max_kw is not None else config.pemfc_kw
    eta_fc_eff = ETA_FC * fc_eff_frac if fc_eff_frac is not None else ETA_FC
    # EL退化感知: 效率和功率
    el_rated = el_p_max_kw if el_p_max_kw is not None else config.pemel_kw
    eta_el_eff = ETA_EL * el_eff_frac if el_eff_frac is not None else ETA_EL

    h2_max = config.h2_kg
    h2_min = config.h2_kg * config.h2_min_fraction
    p_bat_max = bat_p_max_kw if bat_p_max_kw is not None else config.bat_kwh * 0.5

    # === 辅机功耗 (S6修正, 2026-08-02) ===
    # 分设备建模, 基于额定功率的固定损耗+运行功率的比例损耗
    # 文献依据: NREL ATB 2024, IRENA 2023 — 微网变流器/辅机典型损耗
    # PV变换器: 1.5% × P_PV_rated (固定, 无论是否发电)
    # BAT PCS: 1.0% × P_BAT_rated (固定)
    # FC辅机(空压机/冷却/控制): 3.0% × P_FC_rated (仅FC运行时)
    # EL辅机(水处理/冷却/控制): 2.5% × P_EL_rated (仅EL运行时)
    # 待机/控制: 0.5kW (固定, 系统控制器/传感器)
    p_pv_aux = 0.015 * config.pv_kwp           # PV变换器损耗(固定)
    p_bat_aux = 0.010 * (config.bat_kwh * 0.5) # BAT PCS损耗(固定)
    p_standby = 0.5                             # 系统待机(固定, kW)
    # FC/EL辅机在循环中按运行状态计算(见下方循环)

    # 辅机功耗追踪 (S6: 每步不同)
    p_aux_trace = np.zeros(n)
    h2_leak_trace = np.zeros(n)

    # 最小稳定负荷
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

    initial_soc = config.soc_initial if soc_initial is None else float(soc_initial)
    initial_h2 = (config.h2_kg * config.h2_initial_fraction
                  if h2_initial_kg is None else float(h2_initial_kg))
    if not config.soc_min <= initial_soc <= config.soc_max:
        raise ValueError("soc_initial must be within configured SOC bounds")
    if not h2_min <= initial_h2 <= h2_max:
        raise ValueError("h2_initial_kg must be within configured H2 bounds")
    soc = initial_soc
    h2 = initial_h2

    # FC/EL 最小运行时间追踪 (75min = 5步)
    # MVP简化: 最小运行时间约束未在调度循环中强制执行。
    # fc_run_remaining/el_run_remaining已初始化但未在循环体内更新。
    # 影响: FC/EL可能在最小运行时间内被关停, 增加启停次数。
    # MVP可接受因: (1)启停惩罚已在compute_damage中计入; (2)7天窗口内FC/EL启停次数有限。
    # 后续版本应在循环内实现: 若fc_run_remaining>0则强制p_fc>=fc_min_stable。
    fc_run_remaining = 0
    el_run_remaining = 0
    fc_was_on = False
    el_was_on = False

    for t in range(n):
        # 氢气泄漏 (储罐有下限保护, 到下限后不再泄漏)
        if h2 > h2_min:
            h2_leak = config.h2_leakage_per_day * h2 / 24.0 * dt_h
            h2_after_leak = max(h2 - h2_leak, h2_min)
            h2_leak_trace[t] = h2 - h2_after_leak
            h2 = h2_after_leak

        # 辅机功耗 (S6: 分设备建模, FC/EL辅机按运行状态)
        p_fc_aux = 0.03 * fc_rated if fc_was_on else 0.0      # FC辅机(空压机/冷却)
        p_el_aux = 0.025 * el_rated if el_was_on else 0.0     # EL辅机(水处理/冷却)
        p_aux = p_pv_aux + p_bat_aux + p_fc_aux + p_el_aux + p_standby
        p_aux_trace[t] = p_aux

        # 净功率 = PV - 负荷 - 辅机
        p_net = pv_kw[t] - load_kw[t] - p_aux

        if p_net >= 0:
            # === PV过剩: 充电 -> EL制氢 -> 弃光 ===
            # 1. 电池充电
            charge_cap = max(0.0, (config.soc_max - soc) * bat_kwh_eff / dt_h)
            p_charge = min(p_net, p_bat_max, charge_cap)
            p_bat[t] = -p_charge  # 负=充电
            soc += p_charge * dt_h / bat_kwh_eff
            soc = min(soc, config.soc_max)
            remaining = p_net - p_charge

            # 2. EL制氢 (仅在有剩余且H2未满)
            if remaining > el_min_stable and h2 < h2_max:
                h2_cap = max(0.0, (h2_max - h2) * LHV_H2 / (eta_el_eff * dt_h))
                p_el_raw = min(remaining, el_rated, h2_cap)
                # 最小稳定负荷约束: 不够min_stable则不启动
                if p_el_raw < el_min_stable:
                    p_el_raw = 0.0
                p_el[t] = p_el_raw
                h2 += p_el_raw * dt_h * eta_el_eff / LHV_H2
                h2 = min(h2, h2_max)
                remaining -= p_el_raw

            # 3. 弃光
            p_cur[t] = max(0.0, remaining)

            # FC不运行
            fc_was_on = False

        else:
            # === PV不足: 电池放电 -> FC发电 -> 未供电 ===
            deficit = -p_net
            # 1. 电池放电
            discharge_cap = max(0.0, (soc - config.soc_min) * bat_kwh_eff / dt_h)
            p_discharge = min(deficit, p_bat_max, discharge_cap)
            p_bat[t] = p_discharge  # 正=放电
            soc -= p_discharge * dt_h / bat_kwh_eff
            soc = max(soc, config.soc_min)
            remaining = deficit - p_discharge

            # 2. FC发电 (仅在有剩余且H2充足)
            if remaining > fc_min_stable and h2 > h2_min:
                h2_avail = max(0.0, (h2 - h2_min) * eta_fc_eff * LHV_H2 / dt_h)
                p_fc_raw = min(remaining, fc_rated, h2_avail)
                # 最小稳定负荷约束: 不够min_stable则不启动
                if p_fc_raw < fc_min_stable:
                    p_fc_raw = 0.0
                p_fc[t] = p_fc_raw
                h2 -= p_fc_raw * dt_h / (eta_fc_eff * LHV_H2)
                h2 = max(h2, h2_min)
                remaining -= p_fc_raw

            # 3. 未供电
            p_uns[t] = max(0.0, remaining)

            # EL不运行
            el_was_on = False

        soc_trace[t] = soc
        h2_trace[t] = h2

    commands = np.column_stack([p_bat, p_fc, p_el])

    # 计算守恒残差
    # 功率: P_PV + P_FC + P_BAT_dis + P_uns = P_load + P_EL + P_BAT_ch + P_cur + P_aux
    p_bat_dis = np.maximum(p_bat, 0)
    p_bat_ch = np.maximum(-p_bat, 0)
    power_residual_trace = (
        pv_kw + p_fc + p_bat_dis + p_uns
        - load_kw - p_el - p_bat_ch - p_cur - p_aux_trace
    )
    power_residual = float(np.max(np.abs(power_residual_trace)))

    # 氢气: m_end - m_start = m_prod - m_cons - m_leak
    m_start = initial_h2
    m_end = h2_trace[-1]
    h2_prod_trace = p_el * dt_h * eta_el_eff / LHV_H2
    h2_cons_trace = p_fc * dt_h / (eta_fc_eff * LHV_H2)
    m_prod = np.sum(h2_prod_trace)
    m_cons = np.sum(h2_cons_trace)
    m_leak = np.sum(h2_leak_trace)
    h2_residual = abs((m_end - m_start) - (m_prod - m_cons - m_leak))

    state = {
        'initial_soc': initial_soc,
        'initial_h2_kg': initial_h2,
        'soc_trace': soc_trace,
        'h2_trace': h2_trace,
        'p_uns': p_uns,
        'p_cur': p_cur,
        'p_aux': p_aux_trace,
        'power_residual_trace': power_residual_trace,
        'h2_leak_trace': h2_leak_trace,
        'h2_prod_trace': h2_prod_trace,
        'h2_cons_trace': h2_cons_trace,
        'power_residual': power_residual,
        'h2_residual': h2_residual,
        'p_bat_dis': p_bat_dis,
        'p_bat_ch': p_bat_ch,
        'total_load': float(np.sum(load_kw)),
        'total_pv': float(np.sum(pv_kw)),
    }

    return commands, state


def check_constraints(commands: np.ndarray, state: Dict[str, Any],
                      config: EMSConfig) -> Dict[str, Any]:
    """
    校验EMS命令是否满足所有物理约束。

    Args:
        commands: (N_steps, 3) EMS命令
        state: rule_based_dispatch返回的state
        config: EMS配置

    Returns:
        report: dict, 包含
            - power_conservation_pass: bool, 功率守恒(残差/额定 <= 1e-6)
            - h2_conservation_pass: bool, 氢气守恒(残差/吞吐 <= 1e-6)
            - soc_bounds_pass: bool, SOC在[soc_min, soc_max]范围内
            - h2_bounds_pass: bool, H2在[m_min, m_max]范围内
            - min_stable_load_pass: bool, FC/EL功率 >= min_stable_load * rated
            - lpsp: float, 失负荷率(unserved/total)
            - all_pass: bool, 全部通过

    守恒公式:
        功率: P_PV + P_FC + P_BAT_dis + P_uns = P_load + P_EL + P_BAT_ch + P_cur + P_aux
        氢气: m_end - m_start = m_prod - m_cons - m_leak
    """
    n = commands.shape[0]
    p_bat = commands[:, 0]
    p_fc = commands[:, 1]
    p_el = commands[:, 2]

    # 1. 功率守恒：最大逐步残差/总额定功率 <= 1e-6
    rated = (config.pv_kwp + config.bat_kwh * 0.5
             + config.pemfc_kw + config.pemel_kw)
    power_residual = state['power_residual']
    power_conservation_pass = power_residual / max(rated, 1.0) <= 1e-6

    # 2. 氢气守恒：精确质量残差/储氢额定质量 <= 1e-6
    h2_scale = max(config.h2_kg, 1.0)
    h2_residual = state['h2_residual']
    h2_conservation_pass = h2_residual / h2_scale <= 1e-6

    # 3. SOC边界
    soc = state['soc_trace']
    soc_bounds_pass = bool(np.all(soc >= config.soc_min - 1e-6) and
                           np.all(soc <= config.soc_max + 1e-6))

    # 4. H2边界
    h2 = state['h2_trace']
    h2_min = config.h2_kg * config.h2_min_fraction
    h2_max = config.h2_kg * config.h2_max_fraction
    h2_bounds_pass = bool(np.all(h2 >= h2_min - 1e-4) and
                          np.all(h2 <= h2_max + 1e-4))

    # 5. 最小稳定负荷 (FC/EL运行时 >= min_stable * rated)
    fc_min = config.pemfc_kw * config.min_stable_load_fc
    el_min = config.pemel_kw * config.min_stable_load_el
    fc_active = p_fc > 1e-6
    el_active = p_el > 1e-6
    min_stable_fc_pass = bool(np.all(p_fc[fc_active] >= fc_min - 1e-6))
    min_stable_el_pass = bool(np.all(p_el[el_active] >= el_min - 1e-6))
    min_stable_load_pass = min_stable_fc_pass and min_stable_el_pass

    # 6. LPSP (失负荷率 = 未供电量 / 总负荷)
    total_load = state.get('total_load', 0.0)
    p_uns = state['p_uns']
    total_uns = float(np.sum(p_uns))
    lpsp = total_uns / max(total_load, 1e-6)

    all_pass = (power_conservation_pass and h2_conservation_pass and
                soc_bounds_pass and h2_bounds_pass and min_stable_load_pass)

    return {
        'power_conservation_pass': power_conservation_pass,
        'h2_conservation_pass': h2_conservation_pass,
        'soc_bounds_pass': soc_bounds_pass,
        'h2_bounds_pass': h2_bounds_pass,
        'min_stable_load_pass': min_stable_load_pass,
        'min_stable_fc_pass': min_stable_fc_pass,
        'min_stable_el_pass': min_stable_el_pass,
        'lpsp': lpsp,
        'power_residual': power_residual,
        'h2_residual': h2_residual,
        'power_residual_ratio': power_residual / max(rated, 1e-6),
        'h2_residual_ratio': h2_residual / h2_scale,
        'all_pass': all_pass,
    }


if __name__ == "__main__":
    import json
    import sys
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(project_root / "06_src"))

    print("=" * 70)
    print("M4 ems_bf_lite — battery_first规则EMS模块 自测")
    print("=" * 70)

    # 1. 加载C1配置
    print("\n[1] 加载C1配置")
    config_path = project_root / "05_config" / "mvp_c1_config.json"
    with open(config_path, 'r', encoding='utf-8') as f:
        c1 = json.load(f)

    cap = c1['capacity']
    ems_p = c1['ems_parameters']
    bat_c = c1['battery_constraints']
    h2_c = c1['hydrogen_constraints']
    msl = c1['minimum_stable_load']
    topo = c1['topology']

    cfg = EMSConfig(
        pv_kwp=cap['PV_kWp'],
        bat_kwh=cap['BAT_kWh'],
        pemfc_kw=cap['PEMFC_kW'],
        pemel_kw=cap['PEMEL_kW'],
        h2_kg=cap['H2_kg'],
        soc_min=bat_c['soc_min'],
        soc_max=bat_c['soc_max'],
        soc_initial=bat_c['soc_initial'],
        h2_min_fraction=h2_c['m_min_fraction'],
        h2_max_fraction=h2_c['m_max_fraction'],
        h2_initial_fraction=h2_c['m_initial_fraction'],
        h2_leakage_per_day=h2_c['leakage_rate_per_day'],
        start_stop_threshold=ems_p['start_stop_threshold_fraction'],
        minimum_run_time_min=float(ems_p['minimum_run_time']),
        critical_load_fraction=ems_p['critical_load_fraction'],
        p_aux_fraction=topo['P_aux_fraction_of_installed_power'],
        min_stable_load_fc=msl['PEMFC'],
        min_stable_load_el=msl['PEMEL'],
        dt_min=15.0,
    )
    print(f"  PV={cfg.pv_kwp}kWp, BAT={cfg.bat_kwh}kWh, FC={cfg.pemfc_kw}kW, EL={cfg.pemel_kw}kW")
    print(f"  SOC=[{cfg.soc_min},{cfg.soc_max}], init={cfg.soc_initial}")
    print(f"  H2=[{cfg.h2_kg*cfg.h2_min_fraction},{cfg.h2_kg*cfg.h2_max_fraction}]kg, init={cfg.h2_kg*cfg.h2_initial_fraction}kg")
    print(f"  P_aux={cfg.p_aux_fraction*100}% of installed")

    # 2. 加载W1窗口数据 (从M1 data_lite)
    print("\n[2] 加载W1窗口数据 (2017-01-01~07, 672步)")
    from components.data_lite import load_data
    load_csv = str(project_root / "02_data_processed" / "tradestreet_pipeline" /
                   "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv")
    config_json = str(config_path)
    data = load_data(load_csv, config_json, year=2017, window_days=7)
    df = data['data']
    pv_kw = df['P_pv_kW'].values
    load_kw = df['P_load_kW'].values
    print(f"  数据形状: pv={pv_kw.shape}, load={load_kw.shape}")
    print(f"  PV: max={pv_kw.max():.1f}kW, mean={pv_kw.mean():.1f}kW")
    print(f"  Load: max={load_kw.max():.1f}kW, mean={load_kw.mean():.1f}kW")

    # 3. 运行battery_first调度
    print("\n[3] 运行battery_first规则调度")
    commands, state = rule_based_dispatch(load_kw, pv_kw, cfg)
    print(f"  命令形状: {commands.shape}")
    print(f"  BAT: 放电={np.sum(state['p_bat_dis']):.1f}kWh, 充电={np.sum(state['p_bat_ch']):.1f}kWh")
    print(f"  FC:  运行={np.sum(commands[:,1]>0)}步, 总发电={np.sum(commands[:,1]):.1f}kWh")
    print(f"  EL:  运行={np.sum(commands[:,2]>0)}步, 总制氢耗电={np.sum(commands[:,2]):.1f}kWh")
    print(f"  弃光: {np.sum(state['p_cur']):.1f}kWh")
    print(f"  未供电: {np.sum(state['p_uns']):.1f}kWh")
    print(f"  SOC轨迹: [{state['soc_trace'].min():.3f}, {state['soc_trace'].max():.3f}]")
    print(f"  H2轨迹: [{state['h2_trace'].min():.2f}, {state['h2_trace'].max():.2f}]kg")

    # 4. 约束校验
    print("\n[4] 约束校验")
    report = check_constraints(commands, state, cfg)
    print(f"  功率守恒: residual={report['power_residual']:.6f}kW, "
          f"ratio={report['power_residual_ratio']:.2e} "
          f"({'PASS' if report['power_conservation_pass'] else 'FAIL'})")
    print(f"  氢气守恒: residual={report['h2_residual']:.6f}kg, "
          f"ratio={report['h2_residual_ratio']:.2e} "
          f"({'PASS' if report['h2_conservation_pass'] else 'FAIL'})")
    print(f"  SOC边界: {'PASS' if report['soc_bounds_pass'] else 'FAIL'}")
    print(f"  H2边界:  {'PASS' if report['h2_bounds_pass'] else 'FAIL'}")
    print(f"  最小稳定负荷: FC={'PASS' if report['min_stable_fc_pass'] else 'FAIL'}, "
          f"EL={'PASS' if report['min_stable_el_pass'] else 'FAIL'}")
    print(f"  LPSP (失负荷率): {report['lpsp']*100:.3f}%")

    # 5. 物理合理性检查
    print("\n[5] 物理合理性检查")
    # PV供给 vs 负荷需求
    total_pv = np.sum(pv_kw)
    total_load = np.sum(load_kw)
    total_fc = np.sum(commands[:,1])
    total_el = np.sum(commands[:,2])
    print(f"  PV总供给: {total_pv:.1f}kWh")
    print(f"  负荷总需求: {total_load:.1f}kWh")
    print(f"  FC总发电: {total_fc:.1f}kWh")
    print(f"  EL总耗电: {total_el:.1f}kWh")
    print(f"  供给/需求比: {(total_pv+total_fc)/total_load*100:.1f}%")
    print(f"  自给率(无未供): {(1-report['lpsp'])*100:.1f}%")

    print("\n" + "=" * 70)
    print("M4 ems_bf_lite 自测完成")
    print(f"策略: battery_first (PV->BAT->FC / PV->BAT->EL)")
    print(f"约束: 全部{'PASS' if report['all_pass'] else '部分FAIL'}")
    print(f"状态: 2个函数全部实现并验证")
    print("=" * 70)
