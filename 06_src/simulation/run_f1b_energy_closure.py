# -*- coding: utf-8 -*-
"""F1b: 搜索能量闭合配置 (H2年末≈年初)

C6 PV=200kWp能量赤字90MWh(2017), 需增大PV使年度PV盈余≥0
"""
import sys, json
import numpy as np
import pandas as pd
from pathlib import Path

PROJECT_ROOT = Path(r"D:\matlab\C")
sys.path.insert(0, str(PROJECT_ROOT / "06_src"))

from ems.ems_bf_lite import EMSConfig, rule_based_dispatch

with open(PROJECT_ROOT / "05_config" / "c6_compact_config.json", 'r', encoding='utf-8') as f:
    c6 = json.load(f)
ep=c6['ems_parameters']; bc=c6['battery_constraints']
hc=c6['hydrogen_constraints']; msl=c6['minimum_stable_load']; topo=c6['topology']

csv_path = str(PROJECT_ROOT / "02_data_processed" / "tradestreet_pipeline" /
    "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv")
raw = pd.read_csv(csv_path, parse_dates=['timestamp_local_civil'])
SPW = 672

def test(pv, bat, fc, el, h2, year=2017):
    ems = EMSConfig(pv_kwp=pv, bat_kwh=bat, pemfc_kw=fc, pemel_kw=el, h2_kg=h2,
        soc_min=bc['soc_min'], soc_max=bc['soc_max'], soc_initial=bc['soc_initial'],
        h2_min_fraction=hc['m_min_fraction'], h2_max_fraction=hc['m_max_fraction'],
        h2_initial_fraction=1.0, h2_leakage_per_day=hc['leakage_rate_per_day'],
        start_stop_threshold=ep['start_stop_threshold_fraction'],
        minimum_run_time_min=float(ep['minimum_run_time']),
        critical_load_fraction=ep['critical_load_fraction'],
        p_aux_fraction=topo['P_aux_fraction_of_installed_power'],
        min_stable_load_fc=msl['PEMFC'], min_stable_load_el=msl['PEMEL'], dt_min=15.0)
    df = raw[raw['local_civil_year'] == year].copy()
    df = df.rename(columns={
        'p_pv_available_reconstructed_nonnegative_kW': 'P_pv_kW',
        'p_building_load_reconstructed_kW': 'P_load_kW'})
    df['P_pv_kW'] = df['P_pv_kW'].ffill().bfill()
    df['P_load_kW'] = df['P_load_kW'].ffill().bfill()
    pv_arr = df['P_pv_kW'].values * (pv / 200.0)  # 按PV容量缩放
    load = df['P_load_kW'].values
    n_weeks = min(52, len(pv_arr) // SPW)
    
    h2_init = h2
    h2_end = h2_init
    total_uns = 0; total_load = 0
    for w in range(n_weeks):
        s = w * SPW; e = s + SPW
        np.random.seed(42)
        cmds, state = rule_based_dispatch(load[s:e], pv_arr[s:e], ems)
        h2_end = state['h2_trace'][-1]
        total_uns += float(np.sum(state['p_uns']))
        total_load += float(np.sum(load[s:e]))
    lpsp = total_uns / max(total_load, 1e-6) * 100
    h2_change = (h2_end - h2_init) / h2_init * 100
    return lpsp, h2_change, h2_end

print(f"{'Config':<30} {'PV':>5} {'BAT':>5} {'FC':>5} {'EL':>5} {'H2':>5} {'LPSP%':>6} {'H2变化%':>8} {'稳态':>5}")
print('-' * 80)

# 搜索能量闭合配置
configs = [
    (200, 500, 80, 80, 500, 'C6-orig'),
    (250, 500, 80, 80, 500, 'C6+PV250'),
    (300, 500, 80, 80, 500, 'C6+PV300'),
    (350, 500, 80, 80, 500, 'C6+PV350'),
    (400, 500, 80, 80, 500, 'C6+PV400'),
    (300, 800, 100, 100, 1000, 'C5-feasible'),
    (250, 500, 80, 80, 1000, 'C6+H2-1t'),
    (300, 500, 80, 80, 1000, 'C6+PV300+H2-1t'),
    (350, 500, 80, 80, 1000, 'C6+PV350+H2-1t'),
    (400, 500, 80, 80, 1000, 'C6+PV400+H2-1t'),
    (300, 800, 100, 100, 500, 'C5-H2-500'),
    (350, 800, 100, 100, 500, 'C5+PV350'),
]

for pv, bat, fc, el, h2, label in configs:
    lpsp, h2_chg, h2_end = test(pv, bat, fc, el, h2, 2017)
    steady = 'PASS' if abs(h2_chg) < 5.0 else 'FAIL'
    print(f'{label:<30} {pv:>5} {bat:>5} {fc:>5.0f} {el:>5.0f} {h2:>5} {lpsp:>6.2f} {h2_chg:>+8.1f} {steady:>5}')

# 验证最佳配置在3年的表现
print(f"\n[3年验证: 最佳能量闭合配置]")
for pv, bat, fc, el, h2, label in [(300, 500, 80, 80, 1000, 'C6+PV300+H2-1t'), (400, 500, 80, 80, 500, 'C6+PV400')]:
    print(f"\n  {label}:")
    for year in [2016, 2017, 2018]:
        lpsp, h2_chg, h2_end = test(pv, bat, fc, el, h2, year)
        steady = 'PASS' if abs(h2_chg) < 5.0 else 'FAIL'
        print(f'    {year}: LPSP={lpsp:.2f}%, H2变化={h2_chg:+.1f}%, 稳态={steady}')
