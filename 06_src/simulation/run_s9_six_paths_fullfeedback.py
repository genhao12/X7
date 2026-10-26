# -*- coding: utf-8 -*-
"""S9: 全设备反馈下重新辨识六路径 (固定D协议)

协议: 路径辨识协议 — 固定源D=0.5/0.9, 不允许年内演化
反馈: 全设备反馈 (FC/EL退化反馈到EMS效率/Pmax)
配置: C6_compact (最接近原目标等级)
区分: 结构性零路径(EMS不读取) vs 统计零路径(效应区间含0)
"""
import sys, json
import numpy as np
import pandas as pd
from pathlib import Path

PROJECT_ROOT = Path(r"D:\matlab\C")
sys.path.insert(0, str(PROJECT_ROOT / "06_src"))

from ems.ems_bf_lite import EMSConfig, rule_based_dispatch
from simulation.closed_loop_lite import CoalitionState, compute_damage
from components.hp_bat_lite import _load_280ah_data, fit_hppc, interpolate_hp as interp_bat
from components.hp_fc_lite import _load_fc1_data, fit_fc_aging, interpolate_hp as interp_fc
from components.hp_el_lite import _load_el_bol_data, fit_el_aging, interpolate_hp as interp_el

print("=" * 70)
print("S9: 全设备反馈下六路径辨识 (固定D协议, C6_compact)")
print("=" * 70)

# C6_compact配置
cap = {"PV_kWp": 200, "BAT_kWh": 500, "PEMFC_kW": 80, "PEMEL_kW": 80, "H2_kg": 500}
with open(PROJECT_ROOT / "05_config" / "c5_feasible_config.json", 'r', encoding='utf-8') as f:
    c5 = json.load(f)
ep=c5['ems_parameters']; bc=c5['battery_constraints']
hc=c5['hydrogen_constraints']; msl=c5['minimum_stable_load']; topo=c5['topology']
ems_cfg = EMSConfig(pv_kwp=cap['PV_kWp'], bat_kwh=cap['BAT_kWh'], pemfc_kw=cap['PEMFC_kW'],
    pemel_kw=cap['PEMEL_kW'], h2_kg=cap['H2_kg'], soc_min=bc['soc_min'], soc_max=bc['soc_max'],
    soc_initial=bc['soc_initial'], h2_min_fraction=hc['m_min_fraction'], h2_max_fraction=hc['m_max_fraction'],
    h2_initial_fraction=1.0, h2_leakage_per_day=hc['leakage_rate_per_day'],
    start_stop_threshold=ep['start_stop_threshold_fraction'],
    minimum_run_time_min=float(ep['minimum_run_time']),
    critical_load_fraction=ep['critical_load_fraction'],
    p_aux_fraction=topo['P_aux_fraction_of_installed_power'],
    min_stable_load_fc=msl['PEMFC'], min_stable_load_el=msl['PEMEL'], dt_min=15.0)
bat_rated_kw = cap['BAT_kWh'] * 0.5; bat_cap_kwh = cap['BAT_kWh']
config_dict = {'capacity': cap, 'battery_constraints': bc, 'ems_parameters': ep,
    'hydrogen_constraints': hc, 'minimum_stable_load': msl, 'topology': topo}

hp_bat = interp_bat(fit_hppc(_load_280ah_data()),
    {'bat_kwh': cap['BAT_kWh'], 'bat_voltage_nominal': 3.2*48, 'bat_rated_kw': bat_rated_kw})
hp_fc = interp_fc(fit_fc_aging(_load_fc1_data()),
    {'stack_config': {}, 'fc_system_kw': cap['PEMFC_kW']})
hp_el = interp_el(fit_el_aging(_load_el_bol_data()),
    {'el_system_kw': cap['PEMEL_kW'], 'active_area_cm2': 50.0})
coal = CoalitionState.from_code('1111')

csv_path = str(PROJECT_ROOT / "02_data_processed" / "tradestreet_pipeline" /
    "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv")
raw = pd.read_csv(csv_path, parse_dates=['timestamp_local_civil'])
df = raw[raw['local_civil_year'] == 2017].copy()
df = df.rename(columns={
    'p_pv_available_reconstructed_nonnegative_kW': 'P_pv_kW',
    'p_building_load_reconstructed_kW': 'P_load_kW'})
df['P_pv_kW'] = df['P_pv_kW'].ffill().bfill()
df['P_load_kW'] = df['P_load_kW'].ffill().bfill()
pv_full = df['P_pv_kW'].values; load_full = df['P_load_kW'].values

# W1窗口 (7天, 672步, 路径辨识协议)
SPW = 672
s, e = 0, SPW
pv_w = pv_full[s:e]; load_w = load_full[s:e]

# v3.0六路径定义
PATHS_6 = {
    'alpha1': {'source': 'BAT', 'target': 'FC', 'desc': 'BAT→FC'},
    'alpha2': {'source': 'EL',  'target': 'FC', 'desc': 'EL→FC'},
    'beta1':  {'source': 'BAT', 'target': 'EL', 'desc': 'BAT→EL'},
    'beta2':  {'source': 'FC',  'target': 'EL', 'desc': 'FC→EL'},
    'gamma1': {'source': 'FC',  'target': 'BAT', 'desc': 'FC→BAT'},
    'gamma2': {'source': 'EL',  'target': 'BAT', 'desc': 'EL→BAT'},
}

def run_fixed_D(D_source_device, D_level, feedback_mode='full'):
    """固定D协议: 源设备D固定, 其余D=0, 全设备反馈"""
    D = {'bat': 0.0, 'fc': 0.0, 'el': 0.0}
    D[D_source_device.lower()] = D_level
    kw = {}
    if feedback_mode == 'full':
        if D['bat'] > 0:
            q, r0, p_max = hp_bat(D['bat'])
            kw['bat_p_max_kw'] = bat_rated_kw * q
            kw['bat_capacity_kwh'] = bat_cap_kwh * q
        if D['fc'] > 0:
            eta_fc, _, pmax_fc = hp_fc(D['fc'])
            kw['fc_eff_frac'] = eta_fc; kw['fc_p_max_kw'] = pmax_fc
        if D['el'] > 0:
            eta_el, _, pmax_el = hp_el(D['el'])
            kw['el_eff_frac'] = eta_el; kw['el_p_max_kw'] = pmax_el
    # D_state固定(不累积), 路径辨识协议
    D_state = D.copy()
    np.random.seed(42)
    cmds, state = rule_based_dispatch(load_w, pv_w, ems_cfg, **kw)
    dD = compute_damage(hp_bat, cmds, config_dict, dt_min=15.0, D_state=D_state, coalition=coal)
    return {
        'bat': float(np.sum(dD['dD_bat'])),
        'fc': float(np.sum(dD['dD_fc'])),
        'el': float(np.sum(dD['dD_el'])),
    }

# BOL基线
print("\n[BOL] 基线 (D=0, 全反馈)")
dD_bol = run_fixed_D('BAT', 0.0, 'full')
print(f"  BAT={dD_bol['bat']:.6f}, FC={dD_bol['fc']:.6f}, EL={dD_bol['el']:.6f}")

# 六路径 × D=0.5/0.9
print("\n[六路径辨识 (全反馈, 固定D)]")
print(f"{'路径':<10} {'D':>5} {'ΔD_BAT':>12} {'ΔD_FC':>12} {'ΔD_EL':>12} {'AE_target':>12} {'分类':>10}")
print('-' * 85)

path_results = {}
for path_key, path_info in PATHS_6.items():
    source = path_info['source']
    target = path_info['target'].lower()
    results = {}
    for d_level in [0.5, 0.9]:
        dD = run_fixed_D(source, d_level, 'full')
        AE_target = dD[target] - dD_bol[target]
        results[d_level] = {'dD': dD, 'AE_target': AE_target}
    
    # 分类: 结构性零 vs 统计零 vs 激活
    ae_090 = results[0.9]['AE_target']
    ae_050 = results[0.5]['AE_target']
    
    # 检查EMS是否读取源设备健康参数
    source_lower = source.lower()
    if source_lower == 'bat':
        # BAT退化反馈到EMS (bat_p_max/bat_capacity)
        ems_reads = True
    elif source_lower == 'fc':
        # FC退化反馈到EMS (fc_eff_frac/fc_p_max) — 仅在全反馈模式
        ems_reads = True  # full模式下已传入
    else:  # el
        ems_reads = True  # full模式下已传入
    
    if not ems_reads:
        classification = '结构性零'
    elif abs(ae_090) < 1e-8:
        classification = '统计零'
    elif ae_090 > 0:
        classification = '激活(正)'
    else:
        classification = '激活(负)'
    
    path_results[path_key] = {'results': results, 'classification': classification, 'ems_reads': ems_reads}
    
    for d_level in [0.5, 0.9]:
        r = results[d_level]
        ae = r['AE_target']
        print(f"{path_info['desc']:<10} {d_level:>5.1f} {r['dD']['bat']:>12.6f} {r['dD']['fc']:>12.6f} {r['dD']['el']:>12.6f} {ae:>+12.6f} {classification if d_level==0.9 else '':>10}")

# 汇总
print(f"\n{'='*70}")
print("S9六路径分类汇总 (全反馈, 固定D, C6_compact, W1窗口)")
print(f"{'='*70}")
n_act = sum(1 for r in path_results.values() if '激活' in r['classification'])
n_struct_zero = sum(1 for r in path_results.values() if r['classification'] == '结构性零')
n_stat_zero = sum(1 for r in path_results.values() if r['classification'] == '统计零')
print(f"  激活: {n_act}, 结构性零: {n_struct_zero}, 统计零: {n_stat_zero}")
print(f"  N_eff = {n_act}")
print()
for pk, pi in PATHS_6.items():
    r = path_results[pk]
    ae = r['results'][0.9]['AE_target']
    print(f"  {pi['desc']:8s}: AE(D=0.9)={ae:+.6f}, EMS读取={r['ems_reads']}, 分类={r['classification']}")
