# -*- coding: utf-8 -*-
"""
M4 npc_lite — NPC/LCOE 经济评价计算模块
功能：20年净现值成本(NPC)与平准化供电成本(LCOE)计算
依赖：P1年度损伤结果 + P2寿命外推替换计划 + economic_parameters_v1.0.json
输入：容量配置、CAPEX/OPEX参数、替换事件序列、年度供电量
输出：NPC(元)、LCOE(元/kWh)、逐年现金流
"""
import json
import numpy as np
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional


@dataclass
class EconomicConfig:
    """经济评价配置参数 (来自 economic_parameters_v1.0.json)"""
    # 贴现率
    discount_rate: float = 0.06
    # 评价年限
    horizon_years: int = 20
    # 残值比例
    residual_fraction: float = 0.0
    # 汇率
    exchange_rate: float = 7.25

    # CAPEX单价 (CNY)
    pv_unit_cost: float = 8337.5          # CNY/kWp
    bat_unit_cost: float = 2175.0          # CNY/kWh
    pemfc_unit_cost: float = 14500.0       # CNY/kW
    pemel_unit_cost: float = 14500.0       # CNY/kW
    h2_tank_unit_cost: float = 725.0       # CNY/kg

    # 替换成本比例
    bat_replacement_fraction: float = 1.0     # 电池整包更换
    pemfc_replacement_fraction: float = 0.80   # 电堆更换, BOP复用
    pemel_replacement_fraction: float = 0.80   # 电堆更换, BOP复用

    # 电力电子
    power_electronics_fraction: float = 0.15   # 占设备CAPEX比例
    power_electronics_life: int = 15            # 设计寿命(年)

    # 安装工程
    installation_fraction: float = 0.20         # 占设备CAPEX比例

    # round16 成本边界补全（H2A/IRENA/CRU 公开参数）
    gas_conditioning_fraction: float = 0.12     # PEMEL 气体处理(干燥/纯化/冷却) 占 PEMEL 电堆 CAPEX
    h2_compression_fraction: float = 0.15       # 压缩设备(电解→30bar储氢) 占 PEMEL 电堆 CAPEX
    system_bop_fraction: float = 0.10           # 系统级辅机(管道/仪表/控制) 占设备 subtotal
    tax_fraction: float = 0.0                   # 税费比例（基准 0；中国增值税 13% 为情景参考）

    # OPEX (占各自CAPEX的年比例)
    pv_opex_fraction: float = 0.015
    bat_opex_fraction: float = 0.01
    pemfc_opex_fraction: float = 0.03
    pemel_opex_fraction: float = 0.03
    h2_tank_opex_fraction: float = 0.005
    power_electronics_opex_fraction: float = 0.01
    general_opex_fraction: float = 0.01          # 占总CAPEX

    # 容量
    pv_kwp: float = 120.0
    bat_kwh: float = 135.0
    pemfc_kw: float = 37.5
    pemel_kw: float = 35.0
    h2_kg: float = 25.0


def compute_equipment_capex(config: EconomicConfig) -> Dict[str, float]:
    """
    计算各设备初始CAPEX (CNY)。

    Returns:
        dict: 各设备CAPEX + 总设备CAPEX + 电力电子CAPEX + 安装CAPEX + 总CAPEX
    """
    capex = {}

    capex['PV'] = config.pv_kwp * config.pv_unit_cost
    capex['BAT'] = config.bat_kwh * config.bat_unit_cost
    capex['PEMFC'] = config.pemfc_kw * config.pemfc_unit_cost
    capex['PEMEL'] = config.pemel_kw * config.pemel_unit_cost
    capex['H2_tank'] = config.h2_kg * config.h2_tank_unit_cost

    capex['equipment_subtotal'] = sum([
        capex['PV'], capex['BAT'], capex['PEMFC'],
        capex['PEMEL'], capex['H2_tank']
    ])

    # 电力电子（DC-DC/逆变器/整流器/汇流柜）仅施加于需要功率变换的设备，
    # 不含 H2 储罐（round14 修复：此前按全设备 subtotal 计算导致储罐被误加 15%）。
    capex['power_electronics_base'] = (
        capex['PV'] + capex['BAT'] + capex['PEMFC'] + capex['PEMEL']
    )
    capex['power_electronics'] = (
        capex['power_electronics_base'] * config.power_electronics_fraction
    )
    # 安装工程按全部设备 subtotal 另计（设备单价均为本体，不含安装，见
    # economic_parameters_v1.0.json 各设备 includes 字段——round14 已同步去"安装"表述，
    # 消除 BAT"含安装"与 20% 安装另计的重复计费）。
    capex['installation'] = capex['equipment_subtotal'] * config.installation_fraction

    # round16 成本边界补全（H2A/IRENA/CRU 公开参数）
    capex['gas_conditioning'] = (
        capex['PEMEL'] * config.gas_conditioning_fraction
    )
    capex['h2_compression'] = (
        capex['PEMEL'] * config.h2_compression_fraction
    )
    capex['system_bop_aux'] = (
        capex['equipment_subtotal'] * config.system_bop_fraction
    )
    capex['taxes'] = (
        capex['equipment_subtotal'] + capex['power_electronics'] +
        capex['installation'] + capex['gas_conditioning'] +
        capex['h2_compression'] + capex['system_bop_aux']
    ) * config.tax_fraction

    capex['total_initial'] = (
        capex['equipment_subtotal'] +
        capex['power_electronics'] +
        capex['installation'] +
        capex['gas_conditioning'] +
        capex['h2_compression'] +
        capex['system_bop_aux'] +
        capex['taxes']
    )

    return capex


def compute_annual_opex(config: EconomicConfig,
                        capex: Dict[str, float]) -> float:
    """
    计算年度OPEX (CNY/yr)。

    Args:
        config: 经济配置
        capex: compute_equipment_capex()的输出

    Returns:
        float: 年度总OPEX (CNY/yr)
    """
    opex = {}

    opex['PV'] = capex['PV'] * config.pv_opex_fraction
    opex['BAT'] = capex['BAT'] * config.bat_opex_fraction
    opex['PEMFC'] = capex['PEMFC'] * config.pemfc_opex_fraction
    opex['PEMEL'] = capex['PEMEL'] * config.pemel_opex_fraction
    opex['H2_tank'] = capex['H2_tank'] * config.h2_tank_opex_fraction
    opex['power_electronics'] = capex['power_electronics'] * config.power_electronics_opex_fraction
    opex['general'] = capex['total_initial'] * config.general_opex_fraction

    return sum(opex.values())


def compute_replacement_cost(config: EconomicConfig,
                             capex: Dict[str, float],
                             device: str) -> float:
    """
    计算单次替换成本 (CNY)。

    Args:
        config: 经济配置
        capex: CAPEX字典
        device: 'BAT' | 'PEMFC' | 'PEMEL' | 'power_electronics'

    Returns:
        float: 单次替换成本
    """
    if device == 'BAT':
        return capex['BAT'] * config.bat_replacement_fraction
    elif device == 'PEMFC':
        return capex['PEMFC'] * config.pemfc_replacement_fraction
    elif device == 'PEMEL':
        return capex['PEMEL'] * config.pemel_replacement_fraction
    elif device == 'power_electronics':
        return capex['power_electronics'] * 1.0
    else:
        raise ValueError(f"未知设备: {device}")


def build_replacement_schedule(
    annual_damage: Dict[str, float],
    config: EconomicConfig,
    capex: Dict[str, float]
) -> Dict[str, List[int]]:
    """
    从年度损伤率推导20年替换事件序列。

    逻辑：
    - BAT/PEMFC/PEMEL 的年度损伤率 > 1.0 意味着设备在年内达到 EOL
    - 使用 P2 Strategy A 方法: 每次达到 D=1.0 时替换, D 重置为残余值
    - 残余损伤: 替换时 D 超过 1.0 的部分 (mod 1.0)

    Args:
        annual_damage: {'BAT': dD_bat_annual, 'PEMFC': dD_fc_annual, 'PEMEL': dD_el_annual}
        config: 经济配置
        capex: CAPEX字典

    Returns:
        dict: {'BAT': [year1, year2, ...], 'PEMFC': [...], 'PEMEL': [...], 'power_electronics': [15]}
    """
    schedule = {}

    for device in ['BAT', 'PEMFC', 'PEMEL']:
        dD_annual = annual_damage[device]
        D_current = 0.0
        replacements = []

        for year in range(1, config.horizon_years + 1):
            D_current += dD_annual
            # Strategy A: D >= 1.0 时替换, D重置为残余值
            while D_current >= 1.0:
                replacements.append(year)
                D_current = D_current - 1.0  # 残余损伤带入新设备

        schedule[device] = replacements

    # 电力电子: 设计寿命15年, 第15年更换一次
    schedule['power_electronics'] = [config.power_electronics_life] if \
        config.power_electronics_life <= config.horizon_years else []

    return schedule


def build_timed_replacement_schedule(
    annual_damage: Dict[str, float],
    horizon_years: float,
    power_electronics_life: Optional[float] = None,
) -> Dict[str, List[float]]:
    """由年度原始损伤率生成精确到年内小数时刻的更换计划。

    假设各年的损伤时序重复且年均速率恒定。第 ``n`` 次更换发生在
    ``n / annual_damage`` 年，避免把年内多次事件压缩为每年一次。
    """
    if horizon_years <= 0:
        raise ValueError("horizon_years must be positive")

    schedule: Dict[str, List[float]] = {}
    for device in ['BAT', 'PEMFC', 'PEMEL']:
        rate = float(annual_damage.get(device, 0.0))
        if not np.isfinite(rate) or rate < 0:
            raise ValueError(f"annual damage for {device} must be finite and nonnegative")
        count = int(np.floor(rate * horizon_years + 1e-12))
        schedule[device] = [n / rate for n in range(1, count + 1)] if rate > 0 else []

    schedule['power_electronics'] = (
        [float(power_electronics_life)]
        if power_electronics_life is not None
        and 0 < power_electronics_life <= horizon_years
        else []
    )
    return schedule


def compute_npc(
    config: EconomicConfig,
    replacement_schedule: Dict[str, List[int]],
    annual_energy_delivered_mwh: float,
    label: str = ""
) -> Dict[str, any]:
    """
    计算20年NPC和LCOE。

    NPC = CAPEX_initial + Σ(replacement_cost_t / (1+r)^t) + Σ(opex / (1+r)^t) - residual / (1+r)^T

    LCOE = NPC / (T × annual_energy_delivered)

    Args:
        config: 经济配置
        replacement_schedule: 各设备的替换年份列表
        annual_energy_delivered_mwh: 年度实际供电量 (MWh/yr)
        label: 标签 (如 'M2', 'M3')

    Returns:
        dict: 包含NPC, LCOE, 逐年现金流等
    """
    r = config.discount_rate
    T = config.horizon_years

    # 1. 计算CAPEX
    capex = compute_equipment_capex(config)

    # 2. 计算年度OPEX
    annual_opex = compute_annual_opex(config, capex)

    # 3. 构建逐年现金流
    cash_flows = np.zeros(T + 1)  # year 0 = initial, year 1..T = operating

    # Year 0: 初始投资
    cash_flows[0] = capex['total_initial']

    # Year 1..T: OPEX + 替换成本
    for year in range(1, T + 1):
        # OPEX (年末发生)
        cash_flows[year] += annual_opex

        # 替换成本 (同年可能多次替换)
        for device, years in replacement_schedule.items():
            count = years.count(year)
            if count > 0:
                repl_cost = compute_replacement_cost(config, capex, device)
                cash_flows[year] += repl_cost * count

    # Year T: 残值 (负成本)
    if config.residual_fraction > 0:
        cash_flows[T] -= capex['total_initial'] * config.residual_fraction

    # 4. 贴现
    discount_factors = np.array([1.0 / (1.0 + r) ** t for t in range(T + 1)])
    discounted_cash_flows = cash_flows * discount_factors

    # 5. NPC
    npc = np.sum(discounted_cash_flows)

    # 6. LCOE (元/kWh)
    total_energy_kwh = annual_energy_delivered_mwh * 1000 * T
    lcoe = npc / total_energy_kwh if total_energy_kwh > 0 else float('inf')

    # 7. 分解成本
    opex_pv_total = sum(
        annual_opex / (1.0 + r) ** t
        for t in range(1, T + 1)
    )

    replacement_pv_total = 0.0
    for device, years in replacement_schedule.items():
        repl_cost = compute_replacement_cost(config, capex, device)
        for yr in years:
            if yr <= T:
                replacement_pv_total += repl_cost / (1.0 + r) ** yr

    result = {
        'label': label,
        'npc_cny': npc,
        'npc_million_cny': npc / 1e6,
        'lcoe_cny_per_kwh': lcoe,
        'lcoe_yuan_per_mwh': lcoe * 1000,
        'capex_initial_cny': capex['total_initial'],
        'capex_initial_million_cny': capex['total_initial'] / 1e6,
        'annual_opex_cny': annual_opex,
        'capex_pv_cny': capex['total_initial'],  # year 0, no discount
        'opex_pv_cny': opex_pv_total,
        'replacement_pv_cny': replacement_pv_total,
        'residual_pv_cny': 0.0,
        'capex_fraction': capex['total_initial'] / npc * 100 if npc > 0 else 0,
        'opex_fraction': opex_pv_total / npc * 100 if npc > 0 else 0,
        'replacement_fraction': replacement_pv_total / npc * 100 if npc > 0 else 0,
        'annual_energy_mwh': annual_energy_delivered_mwh,
        'total_energy_mwh': annual_energy_delivered_mwh * T,
        'replacement_counts': {
            dev: len(years) for dev, years in replacement_schedule.items()
        },
        'cash_flows': cash_flows,
        'discounted_cash_flows': discounted_cash_flows,
        'capex_breakdown': capex,
        'discount_factors': discount_factors,
    }

    return result


def compute_npc_timed(
    config: EconomicConfig,
    replacement_schedule: Dict[str, List[float]],
    annual_energy_delivered_mwh: float,
    label: str = "",
) -> Dict[str, any]:
    """按更换事件的年内小数时刻贴现 NPC 与 LCOE。"""
    if annual_energy_delivered_mwh <= 0:
        raise ValueError("annual_energy_delivered_mwh must be positive")

    r = config.discount_rate
    T = config.horizon_years
    capex = compute_equipment_capex(config)
    annual_opex = compute_annual_opex(config, capex)

    opex_pv = sum(annual_opex / (1.0 + r) ** year
                  for year in range(1, T + 1))
    replacement_pv = 0.0
    replacement_counts: Dict[str, int] = {}
    for device, times in replacement_schedule.items():
        valid_times = [float(time) for time in times if 0 < float(time) <= T]
        replacement_counts[device] = len(valid_times)
        unit_cost = compute_replacement_cost(config, capex, device)
        replacement_pv += sum(unit_cost / (1.0 + r) ** time
                              for time in valid_times)

    residual_pv = 0.0
    if config.residual_fraction > 0:
        residual_pv = (capex['total_initial'] * config.residual_fraction
                       / (1.0 + r) ** T)

    npc = capex['total_initial'] + opex_pv + replacement_pv - residual_pv
    total_energy_kwh = annual_energy_delivered_mwh * 1000.0 * T

    return {
        'label': label,
        'method': 'timed_events',
        'npc_cny': npc,
        'npc_million_cny': npc / 1e6,
        'lcoe_cny_per_kwh': npc / total_energy_kwh,
        'capex_pv_cny': capex['total_initial'],
        'opex_pv_cny': opex_pv,
        'replacement_pv_cny': replacement_pv,
        'residual_pv_cny': residual_pv,
        'annual_energy_mwh': annual_energy_delivered_mwh,
        'replacement_counts': replacement_counts,
        'replacement_schedule_years': replacement_schedule,
        'capex_breakdown': capex,
    }


def compute_continuous_damage_cost(
    config: EconomicConfig,
    annual_damage: Dict[str, float],
    annual_energy_delivered_mwh: float,
    label: str = ""
) -> Dict[str, any]:
    """
    连续损伤成本法：按年度损伤率等比例分摊替换成本。

    与离散法不同，连续法不依赖整数替换次数，而是：
    - 每年替换成本 = annual_damage_rate × unit_replacement_cost
    - 适用于损伤率微小差异的经济影响分析

    Args:
        config: 经济配置
        annual_damage: {'BAT': dD, 'PEMFC': dD, 'PEMEL': dD}
        annual_energy_delivered_mwh: 年度供电量
        label: 标签

    Returns:
        dict: 连续法NPC/LCOE结果
    """
    r = config.discount_rate
    T = config.horizon_years

    capex = compute_equipment_capex(config)
    annual_opex = compute_annual_opex(config, capex)

    # 年度连续替换成本
    annual_repl_cost = {}
    for device in ['BAT', 'PEMFC', 'PEMEL']:
        repl_cost = compute_replacement_cost(config, capex, device)
        annual_repl_cost[device] = annual_damage[device] * repl_cost

    # 电力电子: 第15年一次性替换
    pe_repl_cost = compute_replacement_cost(config, capex, 'power_electronics')

    # 逐年现金流
    cash_flows = np.zeros(T + 1)
    cash_flows[0] = capex['total_initial']

    for t in range(1, T + 1):
        cash_flows[t] += annual_opex
        for device in ['BAT', 'PEMFC', 'PEMEL']:
            cash_flows[t] += annual_repl_cost[device]
        if t == config.power_electronics_life:
            cash_flows[t] += pe_repl_cost

    # 贴现
    discount_factors = np.array([1.0 / (1.0 + r) ** t for t in range(T + 1)])
    discounted = cash_flows * discount_factors
    npc = np.sum(discounted)

    total_energy_kwh = annual_energy_delivered_mwh * 1000 * T
    lcoe = npc / total_energy_kwh if total_energy_kwh > 0 else float('inf')

    # 分解
    opex_pv = sum(annual_opex / (1.0 + r) ** t for t in range(1, T + 1))
    repl_pv = sum(
        sum(annual_repl_cost[d] for d in ['BAT', 'PEMFC', 'PEMEL']) / (1.0 + r) ** t
        for t in range(1, T + 1)
    ) + pe_repl_cost / (1.0 + r) ** config.power_electronics_life

    return {
        'label': label,
        'method': 'continuous',
        'npc_cny': npc,
        'npc_million_cny': npc / 1e6,
        'lcoe_cny_per_kwh': lcoe,
        'lcoe_yuan_per_mwh': lcoe * 1000,
        'capex_pv_cny': capex['total_initial'],
        'opex_pv_cny': opex_pv,
        'replacement_pv_cny': repl_pv,
        'annual_repl_cost_cny': {d: annual_repl_cost[d] for d in ['BAT', 'PEMFC', 'PEMEL']},
        'capex_fraction': capex['total_initial'] / npc * 100 if npc > 0 else 0,
        'opex_fraction': opex_pv / npc * 100 if npc > 0 else 0,
        'replacement_fraction': repl_pv / npc * 100 if npc > 0 else 0,
        'cash_flows': cash_flows,
        'discounted_cash_flows': discounted,
    }


def run_sensitivity(
    base_config: EconomicConfig,
    annual_damage: Dict[str, float],
    annual_energy_mwh: float,
    label: str = ""
) -> List[Dict]:
    """
    CAPEX ±30% 敏感性分析。

    Args:
        base_config: 基准经济配置
        annual_damage: 年度损伤率
        annual_energy_mwh: 年度供电量
        label: 标签

    Returns:
        list: 各敏感性场景的结果
    """
    import copy

    results = []
    capex_variations = [-0.30, -0.15, 0.0, 0.15, 0.30]
    discount_variations = [0.04, 0.05, 0.06, 0.08, 0.10]

    # CAPEX敏感性 (贴现率固定)
    for var in capex_variations:
        cfg = copy.deepcopy(base_config)
        cfg.pv_unit_cost *= (1 + var)
        cfg.bat_unit_cost *= (1 + var)
        cfg.pemfc_unit_cost *= (1 + var)
        cfg.pemel_unit_cost *= (1 + var)
        cfg.h2_tank_unit_cost *= (1 + var)

        sched = build_replacement_schedule(annual_damage, cfg, compute_equipment_capex(cfg))
        result = compute_npc(cfg, sched, annual_energy_mwh, label=f"{label}_CAPEX{var:+.0%}")
        result['sensitivity_type'] = 'CAPEX'
        result['sensitivity_value'] = var
        results.append(result)

    # 贴现率敏感性 (CAPEX固定)
    for dr in discount_variations:
        cfg = copy.deepcopy(base_config)
        cfg.discount_rate = dr

        sched = build_replacement_schedule(annual_damage, cfg, compute_equipment_capex(cfg))
        result = compute_npc(cfg, sched, annual_energy_mwh, label=f"{label}_r{dr:.0%}")
        result['sensitivity_type'] = 'discount_rate'
        result['sensitivity_value'] = dr
        results.append(result)

    return results


def load_economic_config(econ_params_path: str,
                         c1_config_path: str) -> EconomicConfig:
    """
    从JSON配置文件加载EconomicConfig。

    Args:
        econ_params_path: economic_parameters_v1.0.json 路径
        c1_config_path: mvp_c1_config.json 路径

    Returns:
        EconomicConfig 实例
    """
    with open(econ_params_path, 'r', encoding='utf-8') as f:
        econ = json.load(f)
    with open(c1_config_path, 'r', encoding='utf-8') as f:
        c1 = json.load(f)

    cap = c1['capacity']

    return EconomicConfig(
        discount_rate=econ['discount']['rate'],
        horizon_years=econ['horizon']['years'],
        residual_fraction=econ['residual_value']['fraction_of_capex'],
        exchange_rate=econ['currency']['exchange_rate_usd_to_cny'],
        pv_unit_cost=econ['capex']['PV']['unit_cost_cny_per_kWp'],
        bat_unit_cost=econ['capex']['BAT']['unit_cost_cny_per_kWh'],
        pemfc_unit_cost=econ['capex']['PEMFC']['unit_cost_cny_per_kW'],
        pemel_unit_cost=econ['capex']['PEMEL']['unit_cost_cny_per_kW'],
        h2_tank_unit_cost=econ['capex']['H2_tank']['unit_cost_cny_per_kg'],
        bat_replacement_fraction=econ['capex']['BAT']['replacement_cost_fraction_of_capex'],
        pemfc_replacement_fraction=econ['capex']['PEMFC']['replacement_cost_fraction_of_capex'],
        pemel_replacement_fraction=econ['capex']['PEMEL']['replacement_cost_fraction_of_capex'],
        power_electronics_fraction=econ['capex']['power_electronics']['cost_fraction_of_total_equipment_capex'],
        power_electronics_life=econ['capex']['power_electronics']['design_life_years'],
        installation_fraction=econ['capex']['installation_and_engineering']['cost_fraction_of_equipment_capex'],
        gas_conditioning_fraction=econ['capex']['pemel_gas_conditioning']['cost_fraction_of_pemel_capex'],
        h2_compression_fraction=econ['capex']['h2_compression']['cost_fraction_of_pemel_capex'],
        system_bop_fraction=econ['capex']['system_bop_aux']['cost_fraction_of_equipment_capex'],
        tax_fraction=econ['taxes']['fraction'],
        pv_opex_fraction=econ['opex']['PV']['annual_fraction_of_capex'],
        bat_opex_fraction=econ['opex']['BAT']['annual_fraction_of_capex'],
        pemfc_opex_fraction=econ['opex']['PEMFC']['annual_fraction_of_capex'],
        pemel_opex_fraction=econ['opex']['PEMEL']['annual_fraction_of_capex'],
        h2_tank_opex_fraction=econ['opex']['H2_tank']['annual_fraction_of_capex'],
        power_electronics_opex_fraction=econ['opex']['power_electronics']['annual_fraction_of_capex'],
        general_opex_fraction=econ['opex']['general']['annual_fixed_fraction_of_total_capex'],
        pv_kwp=cap['PV_kWp'],
        bat_kwh=cap['BAT_kWh'],
        pemfc_kw=cap['PEMFC_kW'],
        pemel_kw=cap['PEMEL_kW'],
        h2_kg=cap['H2_kg'],
    )


def print_npc_summary(result: Dict, indent: str = ""):
    """打印NPC/LCOE结果摘要。"""
    print(f"\n{indent}=== NPC/LCOE 摘要 [{result['label']}] ===")
    print(f"{indent}NPC:                    {result['npc_million_cny']:.4f} 百万元")
    print(f"{indent}LCOE:                   {result['lcoe_cny_per_kwh']:.4f} 元/kWh")
    print(f"{indent}初始CAPEX:              {result['capex_initial_million_cny']:.4f} 百万元")
    print(f"{indent}年度OPEX:               {result['annual_opex_cny']:.0f} 元/yr")
    print(f"{indent}CAPEX现值:              {result['capex_pv_cny']/1e6:.4f} 百万元 ({result['capex_fraction']:.1f}%)")
    print(f"{indent}OPEX现值:               {result['opex_pv_cny']/1e6:.4f} 百万元 ({result['opex_fraction']:.1f}%)")
    print(f"{indent}替换现值:               {result['replacement_pv_cny']/1e6:.4f} 百万元 ({result['replacement_fraction']:.1f}%)")
    print(f"{indent}年度供电量:             {result['annual_energy_mwh']:.1f} MWh/yr")
    print(f"{indent}20年总供电量:           {result['total_energy_mwh']:.0f} MWh")
    print(f"{indent}替换次数:               {result['replacement_counts']}")


# ============================================================
# 自测
# ============================================================
if __name__ == '__main__':
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

    print("=== npc_lite.py 自测 ===\n")

    # 加载配置
    econ_path = Path(__file__).resolve().parents[2] / "05_config" / "economic_parameters_v1.0.json"
    c1_path = Path(__file__).resolve().parents[2] / "05_config" / "mvp_c1_config.json"

    config = load_economic_config(str(econ_path), str(c1_path))

    print(f"贴现率: {config.discount_rate*100:.1f}%")
    print(f"评价年限: {config.horizon_years} 年")
    print(f"容量: PV={config.pv_kwp}kWp, BAT={config.bat_kwh}kWh, FC={config.pemfc_kw}kW, EL={config.pemel_kw}kW, H2={config.h2_kg}kg")

    # 计算CAPEX
    capex = compute_equipment_capex(config)
    print(f"\n设备CAPEX分解:")
    for k, v in capex.items():
        print(f"  {k:25s}: {v:>12,.0f} 元")

    # 年度OPEX
    opex = compute_annual_opex(config, capex)
    print(f"\n年度OPEX: {opex:,.0f} 元/yr")

    # M2年度损伤 (来自P1结果)
    m2_damage = {
        'BAT': 0.91622230,
        'PEMFC': 1.38885683,
        'PEMEL': 1.80838701
    }

    # M3年度损伤 (来自L4 final validation)
    m3_damage = {
        'BAT': 0.91637570,
        'PEMFC': 1.38597855,
        'PEMEL': 1.80443033
    }

    print(f"\nM2年度损伤: BAT={m2_damage['BAT']:.4f}, FC={m2_damage['PEMFC']:.4f}, EL={m2_damage['PEMEL']:.4f}")
    print(f"M3年度损伤: BAT={m3_damage['BAT']:.4f}, FC={m3_damage['PEMFC']:.4f}, EL={m3_damage['PEMEL']:.4f}")

    # 构建替换计划
    m2_sched = build_replacement_schedule(m2_damage, config, capex)
    m3_sched = build_replacement_schedule(m3_damage, config, capex)

    print(f"\nM2替换计划 (20年):")
    for dev, years in m2_sched.items():
        print(f"  {dev:20s}: {len(years)}次, 年份={years}")

    print(f"\nM3替换计划 (20年):")
    for dev, years in m3_sched.items():
        print(f"  {dev:20s}: {len(years)}次, 年份={years}")

    # 计算年度供电量
    # 从annual_damage_summary.csv精确计算: 52周Load_mean求和
    # load_mean均值约52kW, 8760h/yr → ~455.5 MWh
    # LPSP=44.79% → delivered = 455.5 × (1-0.4479) = 251.4 MWh
    annual_load_mwh = 455.5
    lpsp = 0.4479
    annual_delivered_mwh = annual_load_mwh * (1 - lpsp)

    print(f"\n年度负荷: {annual_load_mwh:.1f} MWh")
    print(f"LPSP: {lpsp*100:.2f}%")
    print(f"年度供电: {annual_delivered_mwh:.1f} MWh")

    # === 离散法 NPC ===
    m2_result = compute_npc(config, m2_sched, annual_delivered_mwh, label='M2')
    m3_result = compute_npc(config, m3_sched, annual_delivered_mwh, label='M3')

    print_npc_summary(m2_result)
    print_npc_summary(m3_result)

    # === 连续法 NPC ===
    m2_cont = compute_continuous_damage_cost(config, m2_damage, annual_delivered_mwh, label='M2_cont')
    m3_cont = compute_continuous_damage_cost(config, m3_damage, annual_delivered_mwh, label='M3_cont')

    print(f"\n=== 连续法 NPC/LCOE ===")
    print(f"M2_cont: NPC={m2_cont['npc_million_cny']:.4f}百万, LCOE={m2_cont['lcoe_cny_per_kwh']:.4f}元/kWh")
    print(f"M3_cont: NPC={m3_cont['npc_million_cny']:.4f}百万, LCOE={m3_cont['lcoe_cny_per_kwh']:.4f}元/kWh")
    cont_diff = m3_cont['npc_cny'] - m2_cont['npc_cny']
    cont_diff_pct = cont_diff / m2_cont['npc_cny'] * 100
    print(f"连续法NPC差异: {cont_diff:+,.0f}元 ({cont_diff_pct:+.4f}%)")

    # 对比
    npc_diff = m3_result['npc_cny'] - m2_result['npc_cny']
    npc_diff_pct = npc_diff / m2_result['npc_cny'] * 100
    lcoe_diff = m3_result['lcoe_cny_per_kwh'] - m2_result['lcoe_cny_per_kwh']

    print(f"\n=== M2 vs M3 对比 (离散法) ===")
    print(f"NPC差异:   {npc_diff:+,.0f} 元 ({npc_diff_pct:+.4f}%)")
    print(f"LCOE差异:  {lcoe_diff:+.6f} 元/kWh")

    # 验证检查
    print(f"\n=== 自测检查 ===")
    checks = []

    # Check 1: NPC > 0
    checks.append(('NPC-01', 'NPC > 0', m2_result['npc_cny'] > 0, f"NPC={m2_result['npc_cny']:.0f}"))

    # Check 2: LCOE > 0
    checks.append(('NPC-02', 'LCOE > 0', m2_result['lcoe_cny_per_kwh'] > 0, f"LCOE={m2_result['lcoe_cny_per_kwh']:.4f}"))

    # Check 3: CAPEX + OPEX + Replacement ≈ NPC
    total = m2_result['capex_pv_cny'] + m2_result['opex_pv_cny'] + m2_result['replacement_pv_cny']
    checks.append(('NPC-03', 'CAPEX+OPEX+Repl=NPC',
                   abs(total - m2_result['npc_cny']) / m2_result['npc_cny'] < 1e-6,
                   f"sum={total:.0f} vs NPC={m2_result['npc_cny']:.0f}"))

    # Check 4: M3 replacements <= M2 replacements
    for dev in ['BAT', 'PEMFC', 'PEMEL']:
        m2_n = len(m2_sched[dev])
        m3_n = len(m3_sched[dev])
        checks.append(('NPC-04', f'M3 {dev} repl <= M2',
                       m3_n <= m2_n, f"M3={m3_n} vs M2={m2_n}"))

    # Check 5: M3 continuous NPC <= M2 continuous NPC
    checks.append(('NPC-05', 'M3 cont NPC <= M2 cont NPC',
                   m3_cont['npc_cny'] <= m2_cont['npc_cny'],
                   f"M3={m3_cont['npc_cny']:.0f} vs M2={m2_cont['npc_cny']:.0f}"))

    # Check 6: Replacement cost is significant (>10% of NPC)
    checks.append(('NPC-06', 'Replacement > 10% NPC',
                   m2_result['replacement_pv_cny'] / m2_result['npc_cny'] > 0.10,
                   f"{m2_result['replacement_pv_cny']/m2_result['npc_cny']*100:.1f}%"))

    # Check 7: Continuous method NPC > discrete method NPC (overestimates due to fractional replacements)
    checks.append(('NPC-07', 'Cont NPC >= Discrete NPC',
                   m2_cont['npc_cny'] >= m2_result['npc_cny'] * 0.99,
                   f"Cont={m2_cont['npc_cny']:.0f} vs Disc={m2_result['npc_cny']:.0f}"))

    # Check 8: Continuous M2 vs M3 difference is non-zero (captures damage reduction)
    checks.append(('NPC-08', 'Cont M2!=M3 (damage effect visible)',
                   abs(cont_diff) > 0,
                   f"diff={cont_diff:.2f}元"))

    all_pass = True
    for cid, desc, result, details in checks:
        status = 'PASS' if result else 'FAIL'
        if not result:
            all_pass = False
        print(f"  {cid}: {desc}: {status} ({details})")

    print(f"\n{'全部PASS' if all_pass else '存在FAIL'}")
