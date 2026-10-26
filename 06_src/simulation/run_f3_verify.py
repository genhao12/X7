# -*- coding: utf-8 -*-
"""F3验证: k_cal修正后P1结果"""
import sys, json
import numpy as np
import pandas as pd
from pathlib import Path
sys.path.insert(0, str(Path(r'D:\matlab\C') / '06_src'))
from ems.ems_bf_lite import EMSConfig, rule_based_dispatch
from simulation.closed_loop_lite import CoalitionState, compute_damage
from components.hp_bat_lite import _load_280ah_data, fit_hppc, interpolate_hp as interp_bat

cap={'PV_kWp':300,'BAT_kWh':800,'PEMFC_kW':100,'PEMEL_kW':100,'H2_kg':500}
with open(r'D:\matlab\C\05_config\c6_compact_config.json','r',encoding='utf-8') as f: c6=json.load(f)
ep=c6['ems_parameters']; bc=c6['battery_constraints']; hc=c6['hydrogen_constraints']; msl=c6['minimum_stable_load']; topo=c6['topology']
ems=EMSConfig(pv_kwp=cap['PV_kWp'],bat_kwh=cap['BAT_kWh'],pemfc_kw=cap['PEMFC_kW'],pemel_kw=cap['PEMEL_kW'],h2_kg=cap['H2_kg'],
    soc_min=bc['soc_min'],soc_max=bc['soc_max'],soc_initial=bc['soc_initial'],h2_min_fraction=hc['m_min_fraction'],h2_max_fraction=hc['m_max_fraction'],
    h2_initial_fraction=1.0,h2_leakage_per_day=hc['leakage_rate_per_day'],start_stop_threshold=ep['start_stop_threshold_fraction'],
    minimum_run_time_min=float(ep['minimum_run_time']),critical_load_fraction=ep['critical_load_fraction'],
    p_aux_fraction=topo['P_aux_fraction_of_installed_power'],min_stable_load_fc=msl['PEMFC'],min_stable_load_el=msl['PEMEL'],dt_min=15.0)
bat_rated=cap['BAT_kWh']*0.5; bat_cap=cap['BAT_kWh']
config_dict={'capacity':cap,'battery_constraints':bc,'ems_parameters':ep,'hydrogen_constraints':hc,'minimum_stable_load':msl,'topology':topo}
hp_bat=interp_bat(fit_hppc(_load_280ah_data()),{'bat_kwh':cap['BAT_kWh'],'bat_voltage_nominal':3.2*48,'bat_rated_kw':bat_rated})
coal=CoalitionState.from_code('1111')

csv_path=r'D:\matlab\C\02_data_processed\tradestreet_pipeline\64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv'
raw=pd.read_csv(csv_path,parse_dates=['timestamp_local_civil'])
df=raw[raw['local_civil_year']==2017].copy()
df=df.rename(columns={'p_pv_available_reconstructed_nonnegative_kW':'P_pv_kW','p_building_load_reconstructed_kW':'P_load_kW'})
df['P_pv_kW']=df['P_pv_kW'].ffill().bfill(); df['P_load_kW']=df['P_load_kW'].ffill().bfill()
pv=df['P_pv_kW'].values*(300/200.0); load=df['P_load_kW'].values
SPW=672; nw=52

# BOL
D_bol={'bat':0.0,'fc':0.0,'el':0.0}; bol_b=bol_f=bol_e=0
for w in range(nw):
    s=w*SPW; e=s+SPW
    np.random.seed(42)
    cmds,state=rule_based_dispatch(load[s:e],pv[s:e],ems)
    dD=compute_damage(hp_bat,cmds,config_dict,dt_min=15.0,D_state=D_bol,coalition=coal)
    d_b=float(np.sum(dD['dD_bat'])); d_f=float(np.sum(dD['dD_fc'])); d_e=float(np.sum(dD['dD_el']))
    bol_b+=d_b; bol_f+=d_f; bol_e+=d_e
    D_bol['bat']=min(D_bol['bat']+d_b,1.0); D_bol['fc']=min(D_bol['fc']+d_f,1.0); D_bol['el']=min(D_bol['el']+d_e,1.0)

# BAT-only反馈
D={'bat':0.0,'fc':0.0,'el':0.0}; fb_b=fb_f=fb_e=0
for w in range(nw):
    s=w*SPW; e=s+SPW
    kw={}
    if D['bat']>0:
        q,r0,p_max=hp_bat(D['bat']); kw['bat_p_max_kw']=bat_rated*q; kw['bat_capacity_kwh']=bat_cap*q
    np.random.seed(42)
    cmds,state=rule_based_dispatch(load[s:e],pv[s:e],ems,**kw)
    dD=compute_damage(hp_bat,cmds,config_dict,dt_min=15.0,D_state=D,coalition=coal)
    d_b=float(np.sum(dD['dD_bat'])); d_f=float(np.sum(dD['dD_fc'])); d_e=float(np.sum(dD['dD_el']))
    D['bat']=min(D['bat']+d_b,1.0); D['fc']=min(D['fc']+d_f,1.0); D['el']=min(D['el']+d_e,1.0)
    fb_b+=d_b; fb_f+=d_f; fb_e+=d_e

te_bat=(fb_b-bol_b)/abs(bol_b)*100 if abs(bol_b)>1e-10 else 0
te_el=(fb_e-bol_e)/abs(bol_e)*100 if abs(bol_e)>1e-10 else 0
print('F3修正后P1 (C5-H2-500, k_cal=2e-6):')
print(f'  BOL: BAT={bol_b:.6f}, FC={bol_f:.6f}, EL={bol_e:.6f}')
print(f'  FB:  BAT={fb_b:.6f}, FC={fb_f:.6f}, EL={fb_e:.6f}')
print(f'  TE_BAT={te_bat:+.2f}%, TE_EL={te_el:+.2f}%')
print(f'  D年末: BAT={D["bat"]:.4f}, FC={D["fc"]:.4f}, EL={D["el"]:.4f}')
print(f'  对比修正前(k_cal=2e-5): BAT D=0.769 -> 修正后: {D["bat"]:.4f}')
