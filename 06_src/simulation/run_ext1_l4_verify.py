# -*- coding: utf-8 -*-
"""扩展1: L4 EMS验证 (C6_compact, 3年, EOL守卫)

协议: 寿命评价协议(动态D, EOL守卫)
对比: M2(固定EMS) vs M3(L4耦合感知EMS)
配置: C6_compact
"""
import sys, json
import numpy as np
import pandas as pd
from pathlib import Path

PROJECT_ROOT = Path(r"D:\matlab\C")
sys.path.insert(0, str(PROJECT_ROOT / "06_src"))

from ems.ems_bf_lite import EMSConfig, rule_based_dispatch
from ems.ems_l4_lite import dispatch_l4, L4Config
from simulation.closed_loop_lite import CoalitionState, compute_damage
from components.hp_bat_lite import _load_280ah_data, fit_hppc, interpolate_hp as interp_bat

print("=" * 70)
print("扩展1: L4 EMS验证 (C6_compact, 3年, EOL守卫)")
print("=" * 70)

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
coal = CoalitionState.from_code('1111')
cfg_l4 = L4Config(soc_band_narrow_k=0.0, fc_min_run_extend_k=1.0, el_ramp_limit_k=10.0, d_bat_threshold=0.0)

csv_path = str(PROJECT_ROOT / "02_data_processed" / "tradestreet_pipeline" /
    "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv")
raw = pd.read_csv(csv_path, parse_dates=['timestamp_local_civil'])
SPW = 672

def run_year(year, use_l4):
    df = raw[raw['local_civil_year'] == year].copy()
    df = df.rename(columns={
        'p_pv_available_reconstructed_nonnegative_kW': 'P_pv_kW',
        'p_building_load_reconstructed_kW': 'P_load_kW'})
    df['P_pv_kW'] = df['P_pv_kW'].ffill().bfill()
    df['P_load_kW'] = df['P_load_kW'].ffill().bfill()
    pv = df['P_pv_kW'].values; load = df['P_load_kW'].values
    n_weeks = min(52, len(pv) // SPW)
    # BOL基线(固定D=0)
    D_bol = {'bat': 0.0, 'fc': 0.0, 'el': 0.0}
    bol_b = bol_f = bol_e = 0; bol_lp = 0
    for w in range(n_weeks):
        s = w * SPW; e = s + SPW
        np.random.seed(42)
        if use_l4:
            cmds, state = dispatch_l4(load[s:e], pv[s:e], ems_cfg, D_bat=0.0, l4_config=cfg_l4)
        else:
            cmds, state = rule_based_dispatch(load[s:e], pv[s:e], ems_cfg)
        dD = compute_damage(hp_bat, cmds, config_dict, dt_min=15.0, D_state=D_bol, coalition=coal)
        bol_b += float(np.sum(dD['dD_bat']))
        bol_f += float(np.sum(dD['dD_fc']))
        bol_e += float(np.sum(dD['dD_el']))
        bol_lp += float(np.sum(state['p_uns']) / max(np.sum(load[s:e]), 1e-6))
    # BAT-only反馈(动态D, EOL守卫)
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
        if use_l4:
            cmds, state = dispatch_l4(load[s:e], pv[s:e], ems_cfg, D_bat=D['bat'], l4_config=cfg_l4, **kw)
        else:
            cmds, state = rule_based_dispatch(load[s:e], pv[s:e], ems_cfg, **kw)
        dD = compute_damage(hp_bat, cmds, config_dict, dt_min=15.0, D_state=D, coalition=coal)
        dD_b = float(np.sum(dD['dD_bat'])); dD_f = float(np.sum(dD['dD_fc'])); dD_e = float(np.sum(dD['dD_el']))
        D['bat'] = min(D['bat'] + dD_b, 1.0); D['fc'] = min(D['fc'] + dD_f, 1.0); D['el'] = min(D['el'] + dD_e, 1.0)
        fb_b += dD_b; fb_f += dD_f; fb_e += dD_e
        fb_lp += float(np.sum(state['p_uns']) / max(np.sum(load[s:e]), 1e-6))
    te_bat = (fb_b - bol_b) / abs(bol_b) * 100 if abs(bol_b) > 1e-10 else 0
    te_el = (fb_e - bol_e) / abs(bol_e) * 100 if abs(bol_e) > 1e-10 else 0
    return {'te_bat': te_bat, 'te_el': te_el, 'lpsp': fb_lp/n_weeks*100,
            'D_bat': D['bat'], 'D_el': D['el']}

print(f"\n{'Year':>6} {'Mode':>6} {'TE_BAT%':>8} {'TE_EL%':>8} {'LPSP%':>7} {'D_bat':>7} {'D_el':>7}")
print('-' * 55)
results = {}
for year in [2016, 2017, 2018]:
    r_m2 = run_year(year, use_l4=False)
    r_m3 = run_year(year, use_l4=True)
    results[year] = {'m2': r_m2, 'm3': r_m3}
    print(f"{year:>6} {'M2':>6} {r_m2['te_bat']:>+8.2f} {r_m2['te_el']:>+8.2f} {r_m2['lpsp']:>7.2f} {r_m2['D_bat']:>7.3f} {r_m2['D_el']:>7.3f}")
    print(f"{'':>6} {'M3':>6} {r_m3['te_bat']:>+8.2f} {r_m3['te_el']:>+8.2f} {r_m3['lpsp']:>7.2f} {r_m3['D_bat']:>7.3f} {r_m3['D_el']:>7.3f}")

# 统计
print(f"\n{'='*70}")
print("L4 vs M2 跨年对比 (C6_compact)")
print(f"{'='*70}")
for year in [2016, 2017, 2018]:
    m2 = results[year]['m2']; m3 = results[year]['m3']
    d_te_bat = m3['te_bat'] - m2['te_bat']
    d_te_el = m3['te_el'] - m2['te_el']
    print(f"  {year}: ΔTE_BAT={d_te_bat:+.2f}pp, ΔTE_EL={d_te_el:+.2f}pp, M3<M2_BAT={m3['te_bat']<m2['te_bat']}, M3<M2_EL={m3['te_el']<m2['te_el']}")

tes_bat_m2 = [results[y]['m2']['te_bat'] for y in [2016,2017,2018]]
tes_bat_m3 = [results[y]['m3']['te_bat'] for y in [2016,2017,2018]]
tes_el_m2 = [results[y]['m2']['te_el'] for y in [2016,2017,2018]]
tes_el_m3 = [results[y]['m3']['te_el'] for y in [2016,2017,2018]]
cv_m2 = np.std(tes_bat_m2)/abs(np.mean(tes_bat_m2))*100 if abs(np.mean(tes_bat_m2))>1e-10 else 0
cv_m3 = np.std(tes_bat_m3)/abs(np.mean(tes_bat_m3))*100 if abs(np.mean(tes_bat_m3))>1e-10 else 0
print(f"\n  M2 TE_BAT: {[f'{t:+.2f}%' for t in tes_bat_m2]} CV={cv_m2:.1f}%")
print(f"  M3 TE_BAT: {[f'{t:+.2f}%' for t in tes_bat_m3]} CV={cv_m3:.1f}%")
print(f"  M3<M2 全年: {all(results[y]['m3']['te_bat']<results[y]['m2']['te_bat'] for y in [2016,2017,2018])}")
