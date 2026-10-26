# -*- coding: utf-8 -*-
"""S7: 多配置可行域搜索 — 含目标等级临界配置

搜索策略:
  1. 原目标等级(FC 30-45kW)附近的临界配置
  2. 中间过渡配置
  3. C5可行性上界
目标: 找到3-5个代表配置集, 覆盖从临界到可行的范围
"""
import sys, json
import numpy as np
import pandas as pd
from pathlib import Path

PROJECT_ROOT = Path(r"D:\matlab\C")
sys.path.insert(0, str(PROJECT_ROOT / "06_src"))

from ems.ems_bf_lite import EMSConfig, rule_based_dispatch

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

with open(PROJECT_ROOT / "05_config" / "c5_feasible_config.json", 'r', encoding='utf-8') as f:
    c5 = json.load(f)
ep=c5['ems_parameters']; bc=c5['battery_constraints']
hc=c5['hydrogen_constraints']; msl=c5['minimum_stable_load']; topo=c5['topology']
SPW=672
def test(pv,bat,fc,el,h2,h2_init=1.0):
    ems=EMSConfig(pv_kwp=pv,bat_kwh=bat,pemfc_kw=fc,pemel_kw=el,h2_kg=h2,
        soc_min=bc['soc_min'],soc_max=bc['soc_max'],soc_initial=bc['soc_initial'],
        h2_min_fraction=hc['m_min_fraction'],h2_max_fraction=hc['m_max_fraction'],
        h2_initial_fraction=h2_init,h2_leakage_per_day=hc['leakage_rate_per_day'],
        start_stop_threshold=ep['start_stop_threshold_fraction'],minimum_run_time_min=float(ep['minimum_run_time']),
        critical_load_fraction=ep['critical_load_fraction'],p_aux_fraction=topo['P_aux_fraction_of_installed_power'],
        min_stable_load_fc=msl['PEMFC'],min_stable_load_el=msl['PEMEL'],dt_min=15.0)
    lp=0; nw=52; cur_total=0; fc_run=0; el_run=0
    h2_init_val = h2 * h2_init
    h2_end = h2_init_val
    for w in range(nw):
        s=w*SPW; e=s+SPW
        np.random.seed(42)
        cmds,state=rule_based_dispatch(load_full[s:e],pv_full[s:e],ems)
        lp+=float(np.sum(state['p_uns'])/max(np.sum(load_full[s:e]),1e-6))
        cur_total+=float(np.sum(state['p_cur']))
        fc_run+=np.sum(cmds[:,1]>0)
        el_run+=np.sum(cmds[:,2]>0)
        h2_end=state['h2_trace'][-1]
    lpsp=lp/nw*100
    curtail=cur_total/np.sum(pv_full)*100
    h2_ratio=h2_end/h2_init_val
    return lpsp, curtail, fc_run, el_run, h2_ratio

print(f'{"Label":<25} {"PV":>5} {"BAT":>6} {"FC":>5} {"EL":>5} {"H2":>6} {"LPSP%":>7} {"Curt%":>6} {"FC_run":>7} {"EL_run":>7} {"H2_end/init":>11}')
print('-'*105)

# 系统搜索: 从原目标等级到C5
configs = [
    # 原目标等级 (FC 30-45kW)
    (120, 135, 37.5, 35, 25, 0.5, 'C1-orig'),
    (120, 135, 37.5, 35, 100, 1.0, 'C1+H2'),
    (150, 200, 45, 45, 100, 1.0, 'C1.25+H2'),
    (150, 300, 45, 45, 200, 1.0, 'C1.25+bat+H2'),
    # 过渡配置
    (180, 300, 50, 50, 200, 1.0, '1.5x'),
    (200, 400, 60, 60, 300, 1.0, '2x'),
    (200, 500, 80, 80, 500, 1.0, '2x-storage'),
    (250, 500, 80, 80, 500, 1.0, '2.5x'),
    # 可行配置
    (300, 800, 100, 100, 1000, 1.0, 'C5-feasible'),
    # 目标等级附近的大H2配置
    (120, 135, 37.5, 35, 500, 1.0, 'C1+H2-500'),
    (120, 135, 37.5, 35, 1000, 1.0, 'C1+H2-1000'),
    (120, 200, 37.5, 35, 500, 1.0, 'C1+bat+H2'),
    (150, 200, 45, 45, 500, 1.0, 'C1.25+H2-500'),
    (150, 300, 45, 45, 500, 1.0, 'C1.25+bat+H2-500'),
    (150, 300, 45, 45, 1000, 1.0, 'C1.25+bat+H2-1t'),
    (180, 400, 50, 50, 500, 1.0, '1.5x+H2-500'),
    (180, 400, 50, 50, 1000, 1.0, '1.5x+H2-1t'),
    (200, 400, 60, 60, 1000, 1.0, '2x+H2-1t'),
    (250, 500, 80, 80, 1000, 1.0, '2.5x+H2-1t'),
]

feasible = []
for pv,bat,fc,el,h2,init,label in configs:
    lpsp,curtail,fc_run,el_run,h2_ratio = test(pv,bat,fc,el,h2,init)
    flag = ' <--' if lpsp <= 1.0 else ''
    print(f'{label:<25} {pv:>5} {bat:>6} {fc:>5.0f} {el:>5.0f} {h2:>6} {lpsp:>7.2f} {curtail:>6.1f} {fc_run:>7} {el_run:>7} {h2_ratio:>11.3f}{flag}')
    if lpsp <= 1.0:
        feasible.append((pv,bat,fc,el,h2,label,lpsp,curtail))

print(f'\n可行配置数: {len(feasible)}')
if feasible:
    print('\n可行配置汇总:')
    for pv,bat,fc,el,h2,label,lpsp,curtail in feasible:
        print(f'  {label}: PV={pv} BAT={bat} FC={fc} EL={el} H2={h2} LPSP={lpsp:.2f}% Curtail={curtail:.1f}%')
