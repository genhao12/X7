# -*- coding: utf-8 -*-
"""大容量搜索: H2储罐+PV+BAT组合, LPSP<=1%"""
import sys, json
import numpy as np
import pandas as pd
from pathlib import Path
sys.path.insert(0, str(Path(r'D:\matlab\C') / '06_src'))
from ems.ems_bf_lite import EMSConfig, rule_based_dispatch

csv_path = r'D:\matlab\C\02_data_processed\tradestreet_pipeline\64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv'
raw = pd.read_csv(csv_path, parse_dates=['timestamp_local_civil'])
df = raw[raw['local_civil_year']==2017].copy()
df = df.rename(columns={'p_pv_available_reconstructed_nonnegative_kW':'P_pv_kW','p_building_load_reconstructed_kW':'P_load_kW'})
df['P_pv_kW'] = df['P_pv_kW'].ffill().bfill()
df['P_load_kW'] = df['P_load_kW'].ffill().bfill()
pv_full = df['P_pv_kW'].values; load_full = df['P_load_kW'].values

with open(r'D:\matlab\C\05_config\mvp_c1_config.json','r',encoding='utf-8') as f: c1=json.load(f)
ep=c1['ems_parameters']; bc=c1['battery_constraints']; hc=c1['hydrogen_constraints']; msl=c1['minimum_stable_load']; topo=c1['topology']
SPW=672
def test(pv,bat,fc,el,h2,h2_init=1.0):
    ems=EMSConfig(pv_kwp=pv,bat_kwh=bat,pemfc_kw=fc,pemel_kw=el,h2_kg=h2,
        soc_min=bc['soc_min'],soc_max=bc['soc_max'],soc_initial=bc['soc_initial'],
        h2_min_fraction=hc['m_min_fraction'],h2_max_fraction=hc['m_max_fraction'],
        h2_initial_fraction=h2_init,h2_leakage_per_day=hc['leakage_rate_per_day'],
        start_stop_threshold=ep['start_stop_threshold_fraction'],minimum_run_time_min=float(ep['minimum_run_time']),
        critical_load_fraction=ep['critical_load_fraction'],p_aux_fraction=topo['P_aux_fraction_of_installed_power'],
        min_stable_load_fc=msl['PEMFC'],min_stable_load_el=msl['PEMEL'],dt_min=15.0)
    lp=0; nw=52
    for w in range(nw):
        s=w*SPW; e=s+SPW
        np.random.seed(42)
        cmds,state=rule_based_dispatch(load_full[s:e],pv_full[s:e],ems)
        lp+=float(np.sum(state['p_uns'])/max(np.sum(load_full[s:e]),1e-6))
    return lp/nw*100

print(f'{"Config":<35} {"PV":>5} {"BAT":>6} {"FC":>5} {"EL":>5} {"H2":>6} {"LPSP%":>7}')
print('-'*75)
configs=[
    (500,2000,200,200,2000,'big-all-fullH2'),
    (500,2000,200,200,5000,'big-H2-5t'),
    (400,1500,150,150,3000,'mid-H2-3t'),
    (300,800,100,100,1000,'3x-H2-1t'),
    (200,500,80,80,500,'2x-H2-500'),
    (300,600,80,80,300,'3x-H2-300'),
    (400,800,100,100,500,'4x-H2-500'),
    (500,1000,120,120,800,'5x-H2-800'),
    (600,1200,150,150,1000,'6x-H2-1t'),
    (800,1500,200,200,1500,'8x-H2-1.5t'),
    (1000,2000,250,250,2000,'10x-H2-2t'),
    (1500,3000,300,300,3000,'15x-H2-3t'),
    (2000,4000,400,400,5000,'20x-H2-5t'),
]
for pv,bat,fc,el,h2,label in configs:
    lpsp=test(pv,bat,fc,el,h2)
    flag=' <--' if lpsp<=1.0 else ''
    print(f'{label:<35} {pv:>5} {bat:>6} {fc:>5.0f} {el:>5.0f} {h2:>6} {lpsp:>7.2f}{flag}')
