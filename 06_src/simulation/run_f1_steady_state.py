# -*- coding: utf-8 -*-
"""F1: 周期稳态验证 (C6_compact, H2=500kg)

修正:
  1. 使用C6配置(H2=500kg), 非C5(H2=1000kg)
  2. 周期稳态: H2年末≈年初(或差值可由PV盈余解释)
  3. 排除外部能量补贴: 初始H2满载不构成补贴的前提是年末≥年初
"""
import sys, json
import numpy as np
import pandas as pd
from pathlib import Path

PROJECT_ROOT = Path(r"D:\matlab\C")
sys.path.insert(0, str(PROJECT_ROOT / "06_src"))

from ems.ems_bf_lite import EMSConfig, rule_based_dispatch

print("=" * 70)
print("F1: 周期稳态验证 (C6_compact, H2=500kg)")
print("=" * 70)

with open(PROJECT_ROOT / "05_config" / "c6_compact_config.json", 'r', encoding='utf-8') as f:
    c6 = json.load(f)
cap = c6['capacity']; ep = c6['ems_parameters']; bc = c6['battery_constraints']
hc = c6['hydrogen_constraints']; msl = c6['minimum_stable_load']; topo = c6['topology']
ems_cfg = EMSConfig(pv_kwp=cap['PV_kWp'], bat_kwh=cap['BAT_kWh'], pemfc_kw=cap['PEMFC_kW'],
    pemel_kw=cap['PEMEL_kW'], h2_kg=cap['H2_kg'], soc_min=bc['soc_min'], soc_max=bc['soc_max'],
    soc_initial=bc['soc_initial'], h2_min_fraction=hc['m_min_fraction'], h2_max_fraction=hc['m_max_fraction'],
    h2_initial_fraction=hc['m_initial_fraction'], h2_leakage_per_day=hc['leakage_rate_per_day'],
    start_stop_threshold=ep['start_stop_threshold_fraction'],
    minimum_run_time_min=float(ep['minimum_run_time']),
    critical_load_fraction=ep['critical_load_fraction'],
    p_aux_fraction=topo['P_aux_fraction_of_installed_power'],
    min_stable_load_fc=msl['PEMFC'], min_stable_load_el=msl['PEMEL'], dt_min=15.0)
bat_rated_kw = cap['BAT_kWh'] * 0.5; bat_cap_kwh = cap['BAT_kWh']

print(f"配置: PV={cap['PV_kWp']}kWp, BAT={cap['BAT_kWh']}kWh, FC={cap['PEMFC_kW']}kW, EL={cap['PEMEL_kW']}kW, H2={cap['H2_kg']}kg")
print(f"H2初始: {hc['m_initial_fraction']*100:.0f}% = {cap['H2_kg']*hc['m_initial_fraction']:.0f}kg")
print(f"H2范围: [{cap['H2_kg']*hc['m_min_fraction']:.0f}, {cap['H2_kg']*hc['m_max_fraction']:.0f}]kg")

csv_path = str(PROJECT_ROOT / "02_data_processed" / "tradestreet_pipeline" /
    "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv")
raw = pd.read_csv(csv_path, parse_dates=['timestamp_local_civil'])
SPW = 672

# 测试3年
for year in [2016, 2017, 2018]:
    df = raw[raw['local_civil_year'] == year].copy()
    df = df.rename(columns={
        'p_pv_available_reconstructed_nonnegative_kW': 'P_pv_kW',
        'p_building_load_reconstructed_kW': 'P_load_kW'})
    df['P_pv_kW'] = df['P_pv_kW'].ffill().bfill()
    df['P_load_kW'] = df['P_load_kW'].ffill().bfill()
    pv = df['P_pv_kW'].values; load = df['P_load_kW'].values
    n_weeks = min(52, len(pv) // SPW)
    
    soc_all = []; h2_all = []
    total_uns = 0; total_load = 0; total_pv = 0; total_cur = 0
    total_fc = 0; total_el = 0
    
    for w in range(n_weeks):
        s = w * SPW; e = s + SPW
        np.random.seed(42)
        cmds, state = rule_based_dispatch(load[s:e], pv[s:e], ems_cfg)
        soc_all.extend(state['soc_trace'])
        h2_all.extend(state['h2_trace'])
        total_uns += float(np.sum(state['p_uns']))
        total_load += float(np.sum(load[s:e]))
        total_pv += float(np.sum(pv[s:e]))
        total_cur += float(np.sum(state['p_cur']))
        total_fc += float(np.sum(cmds[:, 1]))
        total_el += float(np.sum(cmds[:, 2]))
    
    soc_arr = np.array(soc_all); h2_arr = np.array(h2_all)
    dt_h = 0.25
    h2_init = cap['H2_kg'] * hc['m_initial_fraction']
    h2_end = h2_arr[-1]
    h2_change = h2_end - h2_init
    lpsp = total_uns / max(total_load, 1e-6)
    
    # 周期稳态判定
    # H2年末与年初差值: 若|差值|<5%年初, 视为周期稳态
    h2_change_pct = h2_change / h2_init * 100
    steady_state = abs(h2_change_pct) < 5.0
    # 若H2消耗>5%, 系统依赖初始库存补贴
    subsidy = h2_change < -5.0  # 消耗>5%为补贴
    
    print(f"\n[{year}] C6_compact (H2=500kg)")
    print(f"  H2: 年初={h2_init:.0f}kg, 年末={h2_end:.1f}kg, 变化={h2_change:+.1f}kg ({h2_change_pct:+.1f}%)")
    print(f"  H2范围: [{np.min(h2_arr):.1f}, {np.max(h2_arr):.1f}]kg (限值: [{cap['H2_kg']*hc['m_min_fraction']:.0f}, {cap['H2_kg']*hc['m_max_fraction']:.0f}]kg)")
    print(f"  SOC: 年初={soc_arr[0]:.3f}, 年末={soc_arr[-1]:.3f}, 范围=[{np.min(soc_arr):.3f}, {np.max(soc_arr):.3f}]")
    print(f"  LPSP={lpsp*100:.2f}%, 弃光率={total_cur/max(total_pv,1e-6)*100:.1f}%")
    print(f"  FC发电={total_fc*dt_h/1000:.1f}MWh, EL耗电={total_el*dt_h/1000:.1f}MWh")
    print(f"  PV={total_pv*dt_h/1000:.1f}MWh, 负荷={total_load*dt_h/1000:.1f}MWh, 未供={total_uns*dt_h/1000:.1f}MWh")
    print(f"  周期稳态: {'PASS' if steady_state else 'FAIL'} (H2变化{h2_change_pct:+.1f}%)")
    print(f"  外部补贴: {'是(H2消耗>5%)' if subsidy else '否'}")
    
    # H2能量平衡分析
    h2_energy_consumed = -h2_change * 33.3  # kWh (LHV)
    pv_surplus = total_pv - total_load - total_cur  # 可用于制氢的PV盈余
    print(f"  H2能量变化: {h2_energy_consumed:+.0f}kWh ({'消耗' if h2_energy_consumed > 0 else '增加'})")
    print(f"  PV盈余(扣除弃光): {pv_surplus*dt_h/1000:.1f}MWh")

print(f"\n{'='*70}")
print("F1结论")
print(f"{'='*70}")
print("若H2年末<年初(消耗>5%), 系统依赖初始库存补贴, LPSP不能证明年度能量闭合。")
print("修正要求: 调整配置使H2年末≈年初, 或调整初始H2为50%使系统自平衡。")
