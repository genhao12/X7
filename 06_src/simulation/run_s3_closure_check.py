# -*- coding: utf-8 -*-
"""S3: 初始/年末SOC/H2库存闭合验证

验证项:
  - SOC年末闭合: SOC(T)与SOC(0)的关系（可接受季节性变化，但不应完全耗尽）
  - H2年末闭合: m_H2(T) >= m_H2(0) - 允许消耗（否则系统依赖初始储氢补贴）
  - 关键负荷满足率 >= 99%
  - 功率守恒残差
  - 年度氢质量残差
  - 弃光率
  - H2库存最低值
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
print("S3: 初始/年末SOC/H2库存闭合验证")
print("=" * 70)

with open(PROJECT_ROOT / "05_config" / "c5_feasible_config.json", 'r', encoding='utf-8') as f:
    c5 = json.load(f)
cap = c5['capacity']; ep = c5['ems_parameters']; bc = c5['battery_constraints']
hc = c5['hydrogen_constraints']; msl = c5['minimum_stable_load']; topo = c5['topology']
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
config_dict = {'capacity': cap, 'battery_constraints': bc, 'ems_parameters': ep,
    'hydrogen_constraints': hc, 'minimum_stable_load': msl, 'topology': topo}

hp_bat = interp_bat(fit_hppc(_load_280ah_data()),
    {'bat_kwh': cap['BAT_kWh'], 'bat_voltage_nominal': 3.2*48, 'bat_rated_kw': bat_rated_kw})
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
SPW = 672; n_weeks = min(52, len(pv_full) // SPW)

# BOL模式运行（固定D=0, 路径辨识协议）
D_bol = {'bat': 0.0, 'fc': 0.0, 'el': 0.0}
soc_trace_all = []; h2_trace_all = []
total_uns = 0; total_load = 0; total_cur = 0; total_pv = 0
total_fc = 0; total_el = 0; total_bat_dis = 0; total_bat_ch = 0
fc_starts = 0; el_starts = 0; fc_run_steps = 0; el_run_steps = 0
power_res_total = 0; h2_res_total = 0

for w in range(n_weeks):
    s = w * SPW; e = s + SPW
    np.random.seed(42)
    cmds, state = rule_based_dispatch(load_full[s:e], pv_full[s:e], ems_cfg)
    dD = compute_damage(hp_bat, cmds, config_dict, dt_min=15.0, D_state=D_bol, coalition=coal)
    soc_trace_all.extend(state['soc_trace'])
    h2_trace_all.extend(state['h2_trace'])
    total_uns += float(np.sum(state['p_uns']))
    total_load += float(np.sum(load_full[s:e]))
    total_pv += float(np.sum(pv_full[s:e]))
    total_cur += float(np.sum(state['p_cur']))
    total_fc += float(np.sum(cmds[:, 1]))
    total_el += float(np.sum(cmds[:, 2]))
    total_bat_dis += float(np.sum(np.maximum(cmds[:, 0], 0)))
    total_bat_ch += float(np.sum(np.maximum(-cmds[:, 0], 0)))
    power_res_total += state['power_residual']
    h2_res_total += state['h2_residual']
    # 启停计数
    fc_a = cmds[:, 1] >= ems_cfg.pemfc_kw * ems_cfg.start_stop_threshold
    el_a = cmds[:, 2] >= ems_cfg.pemel_kw * ems_cfg.start_stop_threshold
    fc_starts += np.sum(np.diff(np.concatenate([[0], fc_a.astype(int)])) > 0)
    el_starts += np.sum(np.diff(np.concatenate([[0], el_a.astype(int)])) > 0)
    fc_run_steps += np.sum(fc_a)
    el_run_steps += np.sum(el_a)

soc_arr = np.array(soc_trace_all)
h2_arr = np.array(h2_trace_all)
dt_h = 0.25

print(f"\n初始条件:")
print(f"  SOC(0) = {soc_arr[0]:.4f} (配置: {bc['soc_initial']})")
print(f"  m_H2(0) = {h2_arr[0]:.2f} kg (配置: {cap['H2_kg']*hc['m_initial_fraction']:.0f} kg, {hc['m_initial_fraction']*100:.0f}%)")

print(f"\n年末状态:")
print(f"  SOC(T) = {soc_arr[-1]:.4f}")
print(f"  m_H2(T) = {h2_arr[-1]:.2f} kg")

print(f"\n闭合检查:")
soc_change = soc_arr[-1] - soc_arr[0]
h2_change = h2_arr[-1] - h2_arr[0]
h2_init = h2_arr[0]
print(f"  SOC变化: {soc_change:+.4f} ({'可接受(季节性)' if abs(soc_change) < 0.3 else '过大'})")
print(f"  H2变化: {h2_change:+.2f} kg ({h2_change/h2_init*100:+.1f}%)")
print(f"  H2年末/年初比: {h2_arr[-1]/h2_arr[0]:.4f}")
if h2_arr[-1] < h2_arr[0] * 0.5:
    print(f"  ⚠️ H2年末 < 年初50%, 系统依赖初始储氢补贴!")
elif h2_arr[-1] < h2_arr[0]:
    print(f"  ⚠️ H2年末 < 年初, 系统消耗了初始储氢")
else:
    print(f"  ✓ H2年末 ≥ 年初, 年度能量闭合")

print(f"\nH2库存统计:")
print(f"  最低值: {np.min(h2_arr):.2f} kg (下限: {cap['H2_kg']*hc['m_min_fraction']:.0f} kg)")
print(f"  最高值: {np.max(h2_arr):.2f} kg (上限: {cap['H2_kg']*hc['m_max_fraction']:.0f} kg)")
print(f"  均值: {np.mean(h2_arr):.2f} kg")

print(f"\nSOC统计:")
print(f"  最低值: {np.min(soc_arr):.4f} (下限: {bc['soc_min']})")
print(f"  最高值: {np.max(soc_arr):.4f} (上限: {bc['soc_max']})")
print(f"  均值: {np.mean(soc_arr):.4f}")

print(f"\n可靠性指标:")
lpsp = total_uns / max(total_load, 1e-6)
critical_load_supplied = 1 - lpsp
print(f"  LPSP = {lpsp*100:.2f}%")
print(f"  关键负荷满足率 = {critical_load_supplied*100:.2f}% ({'PASS' if critical_load_supplied >= 0.99 else 'FAIL'} ≥99%)")

print(f"\n能量平衡:")
print(f"  PV总发电: {total_pv*dt_h/1000:.1f} MWh")
print(f"  FC总发电: {total_fc*dt_h/1000:.1f} MWh")
print(f"  BAT放电: {total_bat_dis*dt_h/1000:.1f} MWh")
print(f"  BAT充电: {total_bat_ch*dt_h/1000:.1f} MWh")
print(f"  EL耗电: {total_el*dt_h/1000:.1f} MWh")
print(f"  负荷需求: {total_load*dt_h/1000:.1f} MWh")
print(f"  未供电: {total_uns*dt_h/1000:.1f} MWh")
print(f"  弃光: {total_cur*dt_h/1000:.1f} MWh")
print(f"  弃光率: {total_cur/max(total_pv,1e-6)*100:.1f}%")

print(f"\n设备利用率:")
print(f"  FC运行: {fc_run_steps}步 ({fc_run_steps/len(soc_arr)*100:.1f}%), 启停{fc_starts}次")
print(f"  EL运行: {el_run_steps}步 ({el_run_steps/len(soc_arr)*100:.1f}%), 启停{el_starts}次")
print(f"  FC满负荷小时: {total_fc/(ems_cfg.pemfc_kw):.0f}h (年最大8760h)")
print(f"  EL满负荷小时: {total_el/(ems_cfg.pemel_kw):.0f}h")
print(f"  BAT额定功率: {bat_rated_kw:.0f} kW (P/E比: {bat_rated_kw/bat_cap_kwh:.2f})")

print(f"\n守恒残差:")
print(f"  功率残差(总): {power_res_total:.6f} kW")
print(f"  H2残差(总): {h2_res_total:.6f} kg")

print(f"\nBAT额定功率/P/E比:")
print(f"  BAT能量: {bat_cap_kwh} kWh")
print(f"  BAT功率: {bat_rated_kw} kW (0.5C)")
print(f"  P/E比: {bat_rated_kw/bat_cap_kwh:.3f} (1/h)")

# 汇总
checks = []
checks.append(('S3-01', 'LPSP≤1%', lpsp <= 0.01, f'{lpsp*100:.2f}%'))
checks.append(('S3-02', '关键负荷≥99%', critical_load_supplied >= 0.99, f'{critical_load_supplied*100:.2f}%'))
checks.append(('S3-03', 'SOC在边界内', np.min(soc_arr) >= bc['soc_min']-1e-6 and np.max(soc_arr) <= bc['soc_max']+1e-6, f'[{np.min(soc_arr):.3f},{np.max(soc_arr):.3f}]'))
checks.append(('S3-04', 'H2在边界内', np.min(h2_arr) >= cap['H2_kg']*hc['m_min_fraction']-0.1 and np.max(h2_arr) <= cap['H2_kg']*hc['m_max_fraction']+0.1, f'[{np.min(h2_arr):.1f},{np.max(h2_arr):.1f}]kg'))
checks.append(('S3-05', 'H2年末≥年初50%', h2_arr[-1] >= h2_arr[0]*0.5, f'{h2_arr[-1]:.1f} vs {h2_arr[0]*0.5:.1f}kg'))
checks.append(('S3-06', '功率守恒', power_res_total < 1e-3, f'{power_res_total:.6f}'))
checks.append(('S3-07', 'BAT P/E比合理(0.25-2)', 0.25 <= bat_rated_kw/bat_cap_kwh <= 2.0, f'{bat_rated_kw/bat_cap_kwh:.3f}'))

print(f"\n{'='*70}")
print(f"S3闭合验证汇总 ({sum(1 for _,_,p,_ in checks if p)}/{len(checks)} PASS):")
print(f"{'='*70}")
for cid, desc, passed, details in checks:
    print(f"  {cid}: {'PASS' if passed else 'FAIL'} - {desc} - {details}")
