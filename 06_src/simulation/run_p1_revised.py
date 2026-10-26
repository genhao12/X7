# -*- coding: utf-8 -*-
"""修正后P1年度仿真: C5_feasible配置 + EOL守卫 + 全设备反馈

协议: 寿命评价协议（动态D, 年内健康演化, EOL守卫触发）
区别: 路径辨识协议（固定D=0/0.5/0.9, 不允许年内演化）见run_VS3_path_identification.py
"""
import sys, json, csv, time
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
print("修正后P1年度仿真: C5_feasible + EOL守卫")
print("=" * 70)

# C5配置
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

print(f"\n配置: PV={cap['PV_kWp']}kWp, BAT={cap['BAT_kWh']}kWh, FC={cap['PEMFC_kW']}kW, EL={cap['PEMEL_kW']}kW, H2={cap['H2_kg']}kg")
print(f"H2初始: {hc['m_initial_fraction']*100:.0f}%")

print("\n构建健康-性能映射...")
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
SPW = 672; n_weeks = min(52, len(pv_full) // SPW)
assert len(pv_full) >= 35040, f"数据不完整: {len(pv_full)}步"
print(f"2017年: {len(pv_full)}步, {n_weeks}周")

# ===== BOL基线 =====
# 修正: BOL基线也累积退化(D_state更新), 但退化不反馈到EMS调度
# 这样EOL守卫才能正确触发(BOL基线FC达EOL后也停止累积)
print("\n[BOL] 基线仿真(累积退化, 不反馈EMS)...")
D_bol = {'bat': 0.0, 'fc': 0.0, 'el': 0.0}
bol_b = bol_f = bol_e = 0; bol_lp = 0
for w in range(n_weeks):
    s = w * SPW; e = s + SPW
    np.random.seed(42)
    cmds, state = rule_based_dispatch(load_full[s:e], pv_full[s:e], ems_cfg)
    # BOL: D_state累积退化(用于EOL守卫), 但EMS用BOL参数(D不反馈)
    dD = compute_damage(hp_bat, cmds, config_dict, dt_min=15.0, D_state=D_bol, coalition=coal)
    dD_b = float(np.sum(dD['dD_bat'])); dD_f = float(np.sum(dD['dD_fc'])); dD_e = float(np.sum(dD['dD_el']))
    D_bol['bat'] = min(D_bol['bat'] + dD_b, 1.0)
    D_bol['fc'] = min(D_bol['fc'] + dD_f, 1.0)
    D_bol['el'] = min(D_bol['el'] + dD_e, 1.0)
    bol_b += dD_b; bol_f += dD_f; bol_e += dD_e
    bol_lp += float(np.sum(state['p_uns']) / max(np.sum(load_full[s:e]), 1e-6))
bol_total = bol_b + bol_f + bol_e
print(f"  BOL: BAT={bol_b:.6f}, FC={bol_f:.6f}, EL={bol_e:.6f}, Total={bol_total:.6f}")
print(f"  BOL LPSP={bol_lp/n_weeks*100:.2f}%")
print(f"  BOL D年末: BAT={D_bol['bat']:.4f}, FC={D_bol['fc']:.4f}, EL={D_bol['el']:.4f}")

# ===== BAT-only反馈 =====
print("\n[BAT-only反馈] 仿真...")
D = {'bat': 0.0, 'fc': 0.0, 'el': 0.0}
bo_b = bo_f = bo_e = 0; bo_lp = 0
for w in range(n_weeks):
    s = w * SPW; e = s + SPW
    D_bat = D['bat']
    kw = {}
    if D_bat > 0:
        q, r0, p_max = hp_bat(D_bat)
        kw['bat_p_max_kw'] = bat_rated_kw * q
        kw['bat_capacity_kwh'] = bat_cap_kwh * q
    np.random.seed(42)
    cmds, state = rule_based_dispatch(load_full[s:e], pv_full[s:e], ems_cfg, **kw)
    dD = compute_damage(hp_bat, cmds, config_dict, dt_min=15.0, D_state=D, coalition=coal)
    dD_b = float(np.sum(dD['dD_bat'])); dD_f = float(np.sum(dD['dD_fc'])); dD_e = float(np.sum(dD['dD_el']))
    D['bat'] = min(D['bat'] + dD_b, 1.0); D['fc'] = min(D['fc'] + dD_f, 1.0); D['el'] = min(D['el'] + dD_e, 1.0)
    bo_b += dD_b; bo_f += dD_f; bo_e += dD_e
    bo_lp += float(np.sum(state['p_uns']) / max(np.sum(load_full[s:e]), 1e-6))
bo_total = bo_b + bo_f + bo_e
te_bo = (bo_total - bol_total) / abs(bol_total) * 100 if abs(bol_total) > 1e-10 else 0
print(f"  BAT-only: BAT={bo_b:.6f}, FC={bo_f:.6f}, EL={bo_e:.6f}, Total={bo_total:.6f}")
print(f"  TE={te_bo:+.2f}%, LPSP={bo_lp/n_weeks*100:.2f}%")
print(f"  D年末: BAT={D['bat']:.4f}, FC={D['fc']:.4f}, EL={D['el']:.4f}")

# ===== 全设备反馈 =====
print("\n[全设备反馈] 仿真...")
D2 = {'bat': 0.0, 'fc': 0.0, 'el': 0.0}
ff_b = ff_f = ff_e = 0; ff_lp = 0
for w in range(n_weeks):
    s = w * SPW; e = s + SPW
    kw = {}
    if D2['bat'] > 0:
        q, r0, p_max = hp_bat(D2['bat'])
        kw['bat_p_max_kw'] = bat_rated_kw * q
        kw['bat_capacity_kwh'] = bat_cap_kwh * q
    if D2['fc'] > 0:
        eta_fc, _, pmax_fc = hp_fc(D2['fc'])
        kw['fc_eff_frac'] = eta_fc; kw['fc_p_max_kw'] = pmax_fc
    if D2['el'] > 0:
        eta_el, _, pmax_el = hp_el(D2['el'])
        kw['el_eff_frac'] = eta_el; kw['el_p_max_kw'] = pmax_el
    np.random.seed(42)
    cmds, state = rule_based_dispatch(load_full[s:e], pv_full[s:e], ems_cfg, **kw)
    dD = compute_damage(hp_bat, cmds, config_dict, dt_min=15.0, D_state=D2, coalition=coal)
    dD_b = float(np.sum(dD['dD_bat'])); dD_f = float(np.sum(dD['dD_fc'])); dD_e = float(np.sum(dD['dD_el']))
    D2['bat'] = min(D2['bat'] + dD_b, 1.0); D2['fc'] = min(D2['fc'] + dD_f, 1.0); D2['el'] = min(D2['el'] + dD_e, 1.0)
    ff_b += dD_b; ff_f += dD_f; ff_e += dD_e
    ff_lp += float(np.sum(state['p_uns']) / max(np.sum(load_full[s:e]), 1e-6))
ff_total = ff_b + ff_f + ff_e
te_ff = (ff_total - bol_total) / abs(bol_total) * 100 if abs(bol_total) > 1e-10 else 0
print(f"  Full: BAT={ff_b:.6f}, FC={ff_f:.6f}, EL={ff_e:.6f}, Total={ff_total:.6f}")
print(f"  TE={te_ff:+.2f}%, LPSP={ff_lp/n_weeks*100:.2f}%")
print(f"  D年末: BAT={D2['bat']:.4f}, FC={D2['fc']:.4f}, EL={D2['el']:.4f}")

# ===== 设备级TE向量 =====
te_bat = (bo_b - bol_b) / abs(bol_b) * 100 if abs(bol_b) > 1e-10 else 0
te_fc = (bo_f - bol_f) / abs(bol_f) * 100 if abs(bol_f) > 1e-10 else 0
te_el = (bo_e - bol_e) / abs(bol_e) * 100 if abs(bol_e) > 1e-10 else 0
te_bat_ff = (ff_b - bol_b) / abs(bol_b) * 100 if abs(bol_b) > 1e-10 else 0
te_fc_ff = (ff_f - bol_f) / abs(bol_f) * 100 if abs(bol_f) > 1e-10 else 0
te_el_ff = (ff_e - bol_e) / abs(bol_e) * 100 if abs(bol_e) > 1e-10 else 0

print("\n" + "=" * 70)
print("修正后P1结果汇总")
print("=" * 70)
print(f"\n{'指标':<20} {'BOL':<14} {'BAT-only':<14} {'Full':<14} {'TE_BO(%)':<10} {'TE_FF(%)':<10}")
print(f"{'-'*20} {'-'*14} {'-'*14} {'-'*14} {'-'*10} {'-'*10}")
print(f"{'BAT损伤':<20} {bol_b:<14.6f} {bo_b:<14.6f} {ff_b:<14.6f} {te_bat:<+10.2f} {te_bat_ff:<+10.2f}")
print(f"{'FC损伤':<20} {bol_f:<14.6f} {bo_f:<14.6f} {ff_f:<14.6f} {te_fc:<+10.2f} {te_fc_ff:<+10.2f}")
print(f"{'EL损伤':<20} {bol_e:<14.6f} {bo_e:<14.6f} {ff_e:<14.6f} {te_el:<+10.2f} {te_el_ff:<+10.2f}")
print(f"{'设备TE向量':<20} {'—':<14} {'—':<14} {'—':<14} [{te_bat:+.2f}, {te_fc:+.2f}, {te_el:+.2f}]")
print(f"{'LPSP(%)':<20} {bol_lp/n_weeks*100:<14.2f} {bo_lp/n_weeks*100:<14.2f} {ff_lp/n_weeks*100:<14.2f} {'—':<10} {'—':<10}")
print(f"\n关键检查:")
print(f"  LPSP≤1%: BOL={bol_lp/n_weeks*100:.2f}% {'PASS' if bol_lp/n_weeks*100<=1.0 else 'FAIL'}")
print(f"  EOL守卫生效: FC年末D={D['fc']:.4f}({'<1.0无幻影' if D['fc']<1.0 else '=1.0已封顶'}), EL年末D={D['el']:.4f}({'<1.0无幻影' if D['el']<1.0 else '=1.0已封顶'})")
print(f"  设备级TE向量(替代总损伤求和): TE=[{te_bat:+.2f}%, {te_fc:+.2f}%, {te_el:+.2f}%]")

# 导出CSV
out_csv = PROJECT_ROOT / "07_results" / "annual" / "p1_revised_c5_eolguard.csv"
with open(out_csv, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['Mode', 'BAT', 'FC', 'EL', 'Total', 'TE_BAT_pct', 'TE_FC_pct', 'TE_EL_pct', 'LPSP_pct', 'D_bat_eoy', 'D_fc_eoy', 'D_el_eoy'])
    w.writerow(['BOL', f'{bol_b:.8f}', f'{bol_f:.8f}', f'{bol_e:.8f}', f'{bol_total:.8f}', '—', '—', '—', f'{bol_lp/n_weeks*100:.4f}', '0.0', '0.0', '0.0'])
    w.writerow(['BAT-only', f'{bo_b:.8f}', f'{bo_f:.8f}', f'{bo_e:.8f}', f'{bo_total:.8f}', f'{te_bat:.4f}', f'{te_fc:.4f}', f'{te_el:.4f}', f'{bo_lp/n_weeks*100:.4f}', f"{D['bat']:.6f}", f"{D['fc']:.6f}", f"{D['el']:.6f}"])
    w.writerow(['Full', f'{ff_b:.8f}', f'{ff_f:.8f}', f'{ff_e:.8f}', f'{ff_total:.8f}', f'{te_bat_ff:.4f}', f'{te_fc_ff:.4f}', f'{te_el_ff:.4f}', f'{ff_lp/n_weeks*100:.4f}', f"{D2['bat']:.6f}", f"{D2['fc']:.6f}", f"{D2['el']:.6f}"])
print(f"\nCSV: {out_csv}")
print("=" * 70)
