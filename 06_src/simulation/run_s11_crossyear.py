# -*- coding: utf-8 -*-
"""S11: 跨年稳定性验证 (2016/2017/2018, C6_compact)

验证3个冻结配置在3年的LPSP和TE方向一致性
协议: 寿命评价协议(动态D, EOL守卫)
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

print("=" * 70)
print("S11: 跨年稳定性验证 (3配置 × 3年)")
print("=" * 70)

with open(PROJECT_ROOT / "05_config" / "c5_feasible_config.json", 'r', encoding='utf-8') as f:
    c5 = json.load(f)
ep=c5['ems_parameters']; bc=c5['battery_constraints']
hc=c5['hydrogen_constraints']; msl=c5['minimum_stable_load']; topo=c5['topology']

configs = {
    'C6_compact': {"PV_kWp": 200, "BAT_kWh": 500, "PEMFC_kW": 80, "PEMEL_kW": 80, "H2_kg": 500},
    'C7_balanced': {"PV_kWp": 250, "BAT_kWh": 500, "PEMFC_kW": 80, "PEMEL_kW": 80, "H2_kg": 500},
    'C5_feasible': {"PV_kWp": 300, "BAT_kWh": 800, "PEMFC_kW": 100, "PEMEL_kW": 100, "H2_kg": 1000},
}

csv_path = str(PROJECT_ROOT / "02_data_processed" / "tradestreet_pipeline" /
    "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv")
raw = pd.read_csv(csv_path, parse_dates=['timestamp_local_civil'])

hp_bat = interp_bat(fit_hppc(_load_280ah_data()),
    {'bat_kwh': 500, 'bat_voltage_nominal': 3.2*48, 'bat_rated_kw': 250})
coal = CoalitionState.from_code('1111')
SPW = 672

def run_year(cap, year):
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
    
    df = raw[raw['local_civil_year'] == year].copy()
    df = df.rename(columns={
        'p_pv_available_reconstructed_nonnegative_kW': 'P_pv_kW',
        'p_building_load_reconstructed_kW': 'P_load_kW'})
    df['P_pv_kW'] = df['P_pv_kW'].ffill().bfill()
    df['P_load_kW'] = df['P_load_kW'].ffill().bfill()
    pv = df['P_pv_kW'].values; load = df['P_load_kW'].values
    n_weeks = min(52, len(pv) // SPW)
    
    # BOL基线 (固定D=0, 不累积)
    D_bol = {'bat': 0.0, 'fc': 0.0, 'el': 0.0}
    bol_b = bol_f = bol_e = 0; bol_lp = 0
    for w in range(n_weeks):
        s = w * SPW; e = s + SPW
        np.random.seed(42)
        cmds, state = rule_based_dispatch(load[s:e], pv[s:e], ems_cfg)
        dD = compute_damage(hp_bat, cmds, config_dict, dt_min=15.0, D_state=D_bol, coalition=coal)
        bol_b += float(np.sum(dD['dD_bat']))
        bol_f += float(np.sum(dD['dD_fc']))
        bol_e += float(np.sum(dD['dD_el']))
        bol_lp += float(np.sum(state['p_uns']) / max(np.sum(load[s:e]), 1e-6))
    bol_total = bol_b + bol_f + bol_e
    
    # BAT-only反馈 (动态D, EOL守卫)
    D = {'bat': 0.0, 'fc': 0.0, 'el': 0.0}
    fb_b = fb_f = fb_e = 0; fb_lp = 0
    for w in range(n_weeks):
        s = w * SPW; e = s + SPW
        kw = {}
        if D['bat'] > 0:
            q, r0, p_max = hp_bat(D['bat'])
            kw['bat_p_max_kw'] = bat_rated_kw * q
            kw['bat_capacity_kwh'] = bat_cap_kwh * q
        np.random.seed(42)
        cmds, state = rule_based_dispatch(load[s:e], pv[s:e], ems_cfg, **kw)
        dD = compute_damage(hp_bat, cmds, config_dict, dt_min=15.0, D_state=D, coalition=coal)
        dD_b = float(np.sum(dD['dD_bat']))
        dD_f = float(np.sum(dD['dD_fc']))
        dD_e = float(np.sum(dD['dD_el']))
        D['bat'] = min(D['bat'] + dD_b, 1.0)
        D['fc'] = min(D['fc'] + dD_f, 1.0)
        D['el'] = min(D['el'] + dD_e, 1.0)
        fb_b += dD_b; fb_f += dD_f; fb_e += dD_e
        fb_lp += float(np.sum(state['p_uns']) / max(np.sum(load[s:e]), 1e-6))
    fb_total = fb_b + fb_f + fb_e
    
    te_bat = (fb_b - bol_b) / abs(bol_b) * 100 if abs(bol_b) > 1e-10 else 0
    te_fc = (fb_f - bol_f) / abs(bol_f) * 100 if abs(bol_f) > 1e-10 else 0
    te_el = (fb_e - bol_e) / abs(bol_e) * 100 if abs(bol_e) > 1e-10 else 0
    
    return {
        'lpsp': bol_lp / n_weeks * 100,
        'te_bat': te_bat, 'te_fc': te_fc, 'te_el': te_el,
        'D_bat_eoy': D['bat'], 'D_fc_eoy': D['fc'], 'D_el_eoy': D['el'],
        'bol_total': bol_total, 'fb_total': fb_total,
    }

print(f"\n{'Config':<15} {'Year':>6} {'LPSP%':>7} {'TE_BAT%':>8} {'TE_FC%':>8} {'TE_EL%':>8} {'D_bat':>7} {'D_fc':>7} {'D_el':>7}")
print('-' * 80)

results = {}
for cfg_name, cap in configs.items():
    results[cfg_name] = {}
    for year in [2016, 2017, 2018]:
        r = run_year(cap, year)
        results[cfg_name][year] = r
        print(f"{cfg_name:<15} {year:>6} {r['lpsp']:>7.2f} {r['te_bat']:>+8.2f} {r['te_fc']:>+8.2f} {r['te_el']:>+8.2f} {r['D_bat_eoy']:>7.3f} {r['D_fc_eoy']:>7.3f} {r['D_el_eoy']:>7.3f}")

# 跨年统计
print(f"\n{'='*70}")
print("跨年TE统计")
print(f"{'='*70}")
for cfg_name in configs:
    tes_bat = [results[cfg_name][y]['te_bat'] for y in [2016, 2017, 2018]]
    tes_el = [results[cfg_name][y]['te_el'] for y in [2016, 2017, 2018]]
    cv_bat = np.std(tes_bat) / abs(np.mean(tes_bat)) * 100 if abs(np.mean(tes_bat)) > 1e-10 else 0
    cv_el = np.std(tes_el) / abs(np.mean(tes_el)) * 100 if abs(np.mean(tes_el)) > 1e-10 else 0
    all_pos = all(t > 0 for t in tes_bat + tes_el)
    print(f"  {cfg_name}: TE_BAT={[f'{t:+.2f}%' for t in tes_bat]} CV={cv_bat:.1f}%, TE_EL={[f'{t:+.2f}%' for t in tes_el]} CV={cv_el:.1f}%, 全TE>0={all_pos}")
