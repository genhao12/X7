# -*- coding: utf-8 -*-
"""
P1 Annual Simulation - 全年度退化反馈闭环仿真
将7天窗口MVP扩展到全年52周, 逐周运行EMS+损伤计算, 累积年度退化D。
"""
import sys, json, csv
import numpy as np
import pandas as pd
from pathlib import Path

import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec

PROJECT_ROOT = Path(r"D:\matlab\C")
sys.path.insert(0, str(PROJECT_ROOT / "06_src"))
from utils.fit_table import auto_fit_table

plt.rcParams.update({'axes.unicode_minus': False, 
    'font.family': 'sans-serif', 'font.sans-serif': ['Microsoft YaHei', 'SimHei', 'Arial', 'DejaVu Sans'],
    'font.size': 11, 'axes.titlesize': 10, 'axes.labelsize': 10,
    'xtick.labelsize': 9, 'ytick.labelsize': 9, 'legend.fontsize': 8,
    'axes.linewidth': 0.8, 'lines.linewidth': 1.5, 'grid.alpha': 0.25,
    'axes.spines.top': False, 'axes.spines.right': False,
})

C_BAT = '#3288BD'; C_FC = '#D73027'; C_EL = '#66C2A5'
C_FEEDBACK = '#D73027'; C_BOL = '#3288BD'

OUT_FIG = PROJECT_ROOT / "07_results" / "figures" / "annual_simulation.png"
OUT_CSV_SUMMARY = PROJECT_ROOT / "07_results" / "annual" / "annual_damage_summary.csv"
OUT_CSV_TRAJ = PROJECT_ROOT / "07_results" / "annual" / "annual_D_trajectory.csv"

STEPS_PER_DAY = 96
STEPS_PER_WEEK = STEPS_PER_DAY * 7  # 672
WEEKS_PER_YEAR = 52

from ems.ems_bf_lite import EMSConfig, rule_based_dispatch
from simulation.closed_loop_lite import CoalitionState, compute_damage
from components.hp_bat_lite import _load_280ah_data, fit_hppc, interpolate_hp as interp_bat

config_path = PROJECT_ROOT / "05_config" / "mvp_c1_config.json"
with open(config_path, 'r', encoding='utf-8') as f:
    c1 = json.load(f)

cap = c1['capacity']; ems_p = c1['ems_parameters']
bat_c = c1['battery_constraints']; h2_c = c1['hydrogen_constraints']
msl = c1['minimum_stable_load']; topo = c1['topology']

cfg = EMSConfig(
    pv_kwp=cap['PV_kWp'], bat_kwh=cap['BAT_kWh'],
    pemfc_kw=cap['PEMFC_kW'], pemel_kw=cap['PEMEL_kW'], h2_kg=cap['H2_kg'],
    soc_min=bat_c['soc_min'], soc_max=bat_c['soc_max'], soc_initial=bat_c['soc_initial'],
    h2_min_fraction=h2_c['m_min_fraction'], h2_max_fraction=h2_c['m_max_fraction'],
    h2_initial_fraction=h2_c['m_initial_fraction'],
    h2_leakage_per_day=h2_c['leakage_rate_per_day'],
    start_stop_threshold=ems_p['start_stop_threshold_fraction'],
    minimum_run_time_min=float(ems_p['minimum_run_time']),
    critical_load_fraction=ems_p['critical_load_fraction'],
    p_aux_fraction=topo['P_aux_fraction_of_installed_power'],
    min_stable_load_fc=msl['PEMFC'], min_stable_load_el=msl['PEMEL'], dt_min=15.0,
)
bat_rated_kw = cap['BAT_kWh'] * 0.5
bat_capacity_kwh = cap['BAT_kWh']

config_dict = {
    'capacity': cap, 'battery_constraints': bat_c,
    'ems_parameters': ems_p, 'hydrogen_constraints': h2_c,
    'minimum_stable_load': msl, 'topology': topo,
}

hppc_data = _load_280ah_data()
bat_fit = fit_hppc(hppc_data)
hp_bat = interp_bat(bat_fit, {'bat_kwh': cap['BAT_kWh'],
                               'bat_voltage_nominal': 3.2*48,
                               'bat_rated_kw': bat_rated_kw})

csv_path = str(PROJECT_ROOT / "02_data_processed" / "tradestreet_pipeline" /
               "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv")

print("=" * 70)
print("P1 Annual Simulation - 全年度退化反馈闭环仿真")
print("=" * 70)

print("\n[0] 加载全年2017数据...")
raw = pd.read_csv(csv_path, parse_dates=['timestamp_local_civil'])
df_2017 = raw[raw['local_civil_year'] == 2017].copy()
df_2017 = df_2017.rename(columns={
    'timestamp_local_civil': 'timestamp',
    'p_pv_available_reconstructed_nonnegative_kW': 'P_pv_kW',
    'p_building_load_reconstructed_kW': 'P_load_kW',
})

# H2修复: NaN计数与警告 (修复前静默处理, 无日志)
nan_pv = int(df_2017['P_pv_kW'].isna().sum())
nan_load = int(df_2017['P_load_kW'].isna().sum())
if nan_pv > 0 or nan_load > 0:
    print(f"  [WARNING] NaN detected: PV={nan_pv} steps, Load={nan_load} steps — applying ffill/bfill")
    if nan_pv > 100 or nan_load > 100:
        print(f"  [WARNING] Large NaN count (>100) may cause unrealistic data interpolation!")
df_2017['P_pv_kW'] = df_2017['P_pv_kW'].ffill().bfill()
df_2017['P_load_kW'] = df_2017['P_load_kW'].ffill().bfill()

pv_full = df_2017['P_pv_kW'].values
load_full = df_2017['P_load_kW'].values
n_total = len(pv_full)
print(f"  2017年数据: {n_total}步 ({n_total/96:.0f}天)")

# H2修复: 数据完整性验证 (35040步 = 365天 × 96步/天)
EXPECTED_STEPS = 365 * STEPS_PER_DAY  # 35040
assert n_total >= EXPECTED_STEPS, \
    f"数据完整性检查失败: 期望>= {EXPECTED_STEPS}步, 实际{n_total}步 — 数据不完整将导致仿真结果不可靠"
print(f"  数据完整性检查: PASS ({n_total} >= {EXPECTED_STEPS})")
print(f"  PV: max={pv_full.max():.1f}kW, mean={pv_full.mean():.1f}kW, 年总量={np.sum(pv_full)*0.25/1000:.1f}MWh")
print(f"  Load: max={load_full.max():.1f}kW, mean={load_full.mean():.1f}kW, 年总量={np.sum(load_full)*0.25/1000:.1f}MWh")

n_weeks = min(WEEKS_PER_YEAR, n_total // STEPS_PER_WEEK)
print(f"  仿真周数: {n_weeks}周 x {STEPS_PER_WEEK}步/周 = {n_weeks*STEPS_PER_WEEK}步")

coalition_full = CoalitionState.from_code('1111')

# ================================================================
# 模式1: 无反馈基线 (BOL-fixed, D=0全年)
# ================================================================
print("\n[1] 无反馈基线 (BOL-fixed, D=0全年)...")
D_bol = {'bat': 0.0, 'fc': 0.0, 'el': 0.0}
weekly_bol = []

for w in range(n_weeks):
    s = w * STEPS_PER_WEEK
    e = s + STEPS_PER_WEEK
    pv_w = pv_full[s:e]
    load_w = load_full[s:e]

    np.random.seed(42)
    cmds, state = rule_based_dispatch(load_w, pv_w, cfg)

    dD = compute_damage(hp_bat, cmds, config_dict, dt_min=15.0,
                        D_state=D_bol, coalition=coalition_full)

    weekly_bol.append({
        'week': w + 1,
        'dD_bat': float(np.sum(dD['dD_bat'])),
        'dD_fc': float(np.sum(dD['dD_fc'])),
        'dD_el': float(np.sum(dD['dD_el'])),
        'lpsp': float(np.sum(state['p_uns']) / max(np.sum(load_w), 1e-6)),
        'curtailment': float(np.sum(state['p_cur']) * 0.25),
        'fc_energy': float(np.sum(cmds[:, 1]) * 0.25),
        'el_energy': float(np.sum(cmds[:, 2]) * 0.25),
    })

bol_total_bat = sum(wk['dD_bat'] for wk in weekly_bol)
bol_total_fc = sum(wk['dD_fc'] for wk in weekly_bol)
bol_total_el = sum(wk['dD_el'] for wk in weekly_bol)
bol_total = bol_total_bat + bol_total_fc + bol_total_el
bol_lpsp = np.mean([wk['lpsp'] for wk in weekly_bol])
bol_curt = sum(wk['curtailment'] for wk in weekly_bol)
bol_fc_energy = sum(wk['fc_energy'] for wk in weekly_bol)
bol_el_energy = sum(wk['el_energy'] for wk in weekly_bol)

print(f"  BOL年度总损伤: BAT={bol_total_bat:.6f}, FC={bol_total_fc:.6f}, EL={bol_total_el:.6f}")
print(f"  BOL年度总损伤合计: {bol_total:.6f}")
print(f"  BOL年度LPSP: {bol_lpsp*100:.3f}%, 弃光: {bol_curt/1000:.1f}MWh")
print(f"  BOL年度FC发电: {bol_fc_energy/1000:.1f}MWh, EL制氢耗电: {bol_el_energy/1000:.1f}MWh")

# ================================================================
# 模式2: 有反馈闭环 (degradation-feedback, D逐周累积)
# ================================================================
print("\n[2] 有反馈闭环 (degradation-feedback, D逐周累积)...")
D_state = {'bat': 0.0, 'fc': 0.0, 'el': 0.0}
weekly_fb = []
D_trajectory = []

for w in range(n_weeks):
    s = w * STEPS_PER_WEEK
    e = s + STEPS_PER_WEEK
    pv_w = pv_full[s:e]
    load_w = load_full[s:e]

    D_bat_curr = D_state['bat']
    q_frac, r0, p_max = hp_bat(D_bat_curr)

    bat_p_max_kw = bat_rated_kw * q_frac if D_bat_curr > 0 else None
    bat_cap_kwh = bat_capacity_kwh * q_frac if D_bat_curr > 0 else None

    np.random.seed(42)
    cmds, state = rule_based_dispatch(load_w, pv_w, cfg,
                                       bat_p_max_kw=bat_p_max_kw,
                                       bat_capacity_kwh=bat_cap_kwh)

    dD = compute_damage(hp_bat, cmds, config_dict, dt_min=15.0,
                        D_state=D_state, coalition=coalition_full)

    dD_bat_sum = float(np.sum(dD['dD_bat']))
    dD_fc_sum = float(np.sum(dD['dD_fc']))
    dD_el_sum = float(np.sum(dD['dD_el']))

    D_bat_before = D_state['bat']
    D_fc_before = D_state['fc']
    D_el_before = D_state['el']

    D_state['bat'] = min(D_state['bat'] + dD_bat_sum, 1.0)
    D_state['fc'] = min(D_state['fc'] + dD_fc_sum, 1.0)
    D_state['el'] = min(D_state['el'] + dD_el_sum, 1.0)

    weekly_fb.append({
        'week': w + 1,
        'D_bat_start': D_bat_before,
        'D_fc_start': D_fc_before,
        'D_el_start': D_el_before,
        'D_bat_end': D_state['bat'],
        'D_fc_end': D_state['fc'],
        'D_el_end': D_state['el'],
        'dD_bat': dD_bat_sum,
        'dD_fc': dD_fc_sum,
        'dD_el': dD_el_sum,
        'lpsp': float(np.sum(state['p_uns']) / max(np.sum(load_w), 1e-6)),
        'curtailment': float(np.sum(state['p_cur']) * 0.25),
        'fc_energy': float(np.sum(cmds[:, 1]) * 0.25),
        'el_energy': float(np.sum(cmds[:, 2]) * 0.25),
        'pv_max': float(pv_w.max()),
        'load_mean': float(load_w.mean()),
    })

    D_trajectory.append({
        'week': w + 1,
        'D_bat': D_state['bat'],
        'D_fc': D_state['fc'],
        'D_el': D_state['el'],
        'dD_bat': dD_bat_sum,
        'dD_fc': dD_fc_sum,
        'dD_el': dD_el_sum,
    })

    if (w + 1) % 13 == 0:
        print(f"  Week {w+1:2d}: D_bat={D_state['bat']:.6f}, D_fc={D_state['fc']:.6f}, "
              f"D_el={D_state['el']:.6f}, LPSP={weekly_fb[-1]['lpsp']*100:.3f}%")

fb_total_bat = sum(wk['dD_bat'] for wk in weekly_fb)
fb_total_fc = sum(wk['dD_fc'] for wk in weekly_fb)
fb_total_el = sum(wk['dD_el'] for wk in weekly_fb)
fb_total = fb_total_bat + fb_total_fc + fb_total_el
fb_lpsp = np.mean([wk['lpsp'] for wk in weekly_fb])
fb_curt = sum(wk['curtailment'] for wk in weekly_fb)
fb_fc_energy = sum(wk['fc_energy'] for wk in weekly_fb)
fb_el_energy = sum(wk['el_energy'] for wk in weekly_fb)

print(f"\n  反馈年度总损伤: BAT={fb_total_bat:.6f}, FC={fb_total_fc:.6f}, EL={fb_total_el:.6f}")
print(f"  反馈年度总损伤合计: {fb_total:.6f}")
print(f"  反馈年度末D: BAT={D_state['bat']:.6f}, FC={D_state['fc']:.6f}, EL={D_state['el']:.6f}")
print(f"  反馈年度LPSP: {fb_lpsp*100:.3f}%, 弃光: {fb_curt/1000:.1f}MWh")
print(f"  反馈年度FC发电: {fb_fc_energy/1000:.1f}MWh, EL制氢耗电: {fb_el_energy/1000:.1f}MWh")

# ================================================================
# 年度AE/TE计算
# ================================================================
print("\n[3] 年度AE/TE (反馈 vs BOL基线)...")
ae_bat = fb_total_bat - bol_total_bat
ae_fc = fb_total_fc - bol_total_fc
ae_el = fb_total_el - bol_total_el
ae_total = fb_total - bol_total

te_bat = ae_bat / abs(bol_total_bat) if abs(bol_total_bat) > 1e-10 else float('nan')
te_fc = ae_fc / abs(bol_total_fc) if abs(bol_total_fc) > 1e-10 else float('nan')
te_el = ae_el / abs(bol_total_el) if abs(bol_total_el) > 1e-10 else float('nan')
te_total = ae_total / abs(bol_total) if abs(bol_total) > 1e-10 else float('nan')

print(f"  AE(BAT) = {ae_bat:+.6f}  TE = {te_bat*100:+.2f}%")
print(f"  AE(FC)  = {ae_fc:+.6f}  TE = {te_fc*100:+.2f}%")
print(f"  AE(EL)  = {ae_el:+.6f}  TE = {te_el*100:+.2f}%")
print(f"  AE(total) = {ae_total:+.6f}  TE = {te_total*100:+.2f}%")

# ================================================================
# EOL投影 (线性外推)
# ================================================================
print("\n[4] EOL投影 (线性外推)...")
D_bat_eoy = D_state['bat']
D_fc_eoy = D_state['fc']
D_el_eoy = D_state['el']

eol_years_bat = 1.0 / D_bat_eoy if D_bat_eoy > 1e-6 else float('inf')
eol_years_fc = 1.0 / D_fc_eoy if D_fc_eoy > 1e-6 else float('inf')
eol_years_el = 1.0 / D_el_eoy if D_el_eoy > 1e-6 else float('inf')

eol_years_bat_bol = 1.0 / bol_total_bat if bol_total_bat > 1e-6 else float('inf')
eol_years_fc_bol = 1.0 / bol_total_fc if bol_total_fc > 1e-6 else float('inf')
eol_years_el_bol = 1.0 / bol_total_el if bol_total_el > 1e-6 else float('inf')

print(f"  反馈EOL投影: BAT={eol_years_bat:.1f}y, FC={eol_years_fc:.1f}y, EL={eol_years_el:.1f}y")
print(f"  BOL基线EOL:   BAT={eol_years_bat_bol:.1f}y, FC={eol_years_fc_bol:.1f}y, EL={eol_years_el_bol:.1f}y")
print(f"  寿命缩减比(反馈/BOL): BAT={eol_years_bat/eol_years_bat_bol:.2f}x, "
      f"FC={eol_years_fc/eol_years_fc_bol:.2f}x, EL={eol_years_el/eol_years_el_bol:.2f}x")

# ================================================================
# 季度对比
# ================================================================
print("\n[5] 季度对比...")
quarters = ['Q1 (Win)', 'Q2 (Spr)', 'Q3 (Sum)', 'Q4 (Fall)']
for q in range(4):
    wk_s = q * 13
    wk_e = wk_s + 13
    bol_q = sum(weekly_bol[i]['dD_bat'] + weekly_bol[i]['dD_fc'] +
                weekly_bol[i]['dD_el'] for i in range(wk_s, wk_e))
    fb_q = sum(weekly_fb[i]['dD_bat'] + weekly_fb[i]['dD_fc'] +
               weekly_fb[i]['dD_el'] for i in range(wk_s, wk_e))
    ae_q = fb_q - bol_q
    te_q = ae_q / abs(bol_q) * 100 if abs(bol_q) > 1e-10 else 0
    print(f"  {quarters[q]}: BOL={bol_q:.6f}, FB={fb_q:.6f}, AE={ae_q:+.6f}, TE={te_q:+.1f}%")

# ================================================================
# 导出CSV
# ================================================================
print("\n[6] 导出CSV...")

with open(OUT_CSV_SUMMARY, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['Week', 'PV_max_kW', 'Load_mean_kW',
                'BOL_dD_bat', 'BOL_dD_fc', 'BOL_dD_el', 'BOL_dD_total',
                'BOL_LPSP_pct', 'BOL_curtail_kWh', 'BOL_FC_kWh', 'BOL_EL_kWh',
                'FB_D_bat_start', 'FB_D_fc_start', 'FB_D_el_start',
                'FB_D_bat_end', 'FB_D_fc_end', 'FB_D_el_end',
                'FB_dD_bat', 'FB_dD_fc', 'FB_dD_el', 'FB_dD_total',
                'FB_LPSP_pct', 'FB_curtail_kWh', 'FB_FC_kWh', 'FB_EL_kWh',
                'AE_bat', 'AE_fc', 'AE_el', 'AE_total'])
    for i in range(n_weeks):
        bol_w = weekly_bol[i]
        fb_w = weekly_fb[i]
        ae_bat_w = fb_w['dD_bat'] - bol_w['dD_bat']
        ae_fc_w = fb_w['dD_fc'] - bol_w['dD_fc']
        ae_el_w = fb_w['dD_el'] - bol_w['dD_el']
        ae_tot_w = ae_bat_w + ae_fc_w + ae_el_w
        w.writerow([i + 1, f"{fb_w['pv_max']:.2f}", f"{fb_w['load_mean']:.2f}",
                    f"{bol_w['dD_bat']:.8f}", f"{bol_w['dD_fc']:.8f}", f"{bol_w['dD_el']:.8f}",
                    f"{bol_w['dD_bat']+bol_w['dD_fc']+bol_w['dD_el']:.8f}",
                    f"{bol_w['lpsp']*100:.4f}", f"{bol_w['curtailment']:.2f}",
                    f"{bol_w['fc_energy']:.2f}", f"{bol_w['el_energy']:.2f}",
                    f"{fb_w['D_bat_start']:.8f}", f"{fb_w['D_fc_start']:.8f}", f"{fb_w['D_el_start']:.8f}",
                    f"{fb_w['D_bat_end']:.8f}", f"{fb_w['D_fc_end']:.8f}", f"{fb_w['D_el_end']:.8f}",
                    f"{fb_w['dD_bat']:.8f}", f"{fb_w['dD_fc']:.8f}", f"{fb_w['dD_el']:.8f}",
                    f"{fb_w['dD_bat']+fb_w['dD_fc']+fb_w['dD_el']:.8f}",
                    f"{fb_w['lpsp']*100:.4f}", f"{fb_w['curtailment']:.2f}",
                    f"{fb_w['fc_energy']:.2f}", f"{fb_w['el_energy']:.2f}",
                    f"{ae_bat_w:+.8f}", f"{ae_fc_w:+.8f}", f"{ae_el_w:+.8f}", f"{ae_tot_w:+.8f}"])
    w.writerow([])
    w.writerow(['=== Annual Summary ==='])
    w.writerow(['Metric', 'BOL_baseline', 'Feedback', 'AE', 'TE(%)'])
    w.writerow(['Damage_BAT', f"{bol_total_bat:.8f}", f"{fb_total_bat:.8f}",
                f"{ae_bat:+.8f}", f"{te_bat*100:.2f}" if not np.isnan(te_bat) else "NA"])
    w.writerow(['Damage_FC', f"{bol_total_fc:.8f}", f"{fb_total_fc:.8f}",
                f"{ae_fc:+.8f}", f"{te_fc*100:.2f}" if not np.isnan(te_fc) else "NA"])
    w.writerow(['Damage_EL', f"{bol_total_el:.8f}", f"{fb_total_el:.8f}",
                f"{ae_el:+.8f}", f"{te_el*100:.2f}" if not np.isnan(te_el) else "NA"])
    w.writerow(['Damage_total', f"{bol_total:.8f}", f"{fb_total:.8f}",
                f"{ae_total:+.8f}", f"{te_total*100:.2f}" if not np.isnan(te_total) else "NA"])
    w.writerow([])
    w.writerow(['LPSP(%)', f"{bol_lpsp*100:.4f}", f"{fb_lpsp*100:.4f}", '', ''])
    w.writerow(['Curtailment(MWh)', f"{bol_curt/1000:.2f}", f"{fb_curt/1000:.2f}", '', ''])
    w.writerow(['FC_energy(MWh)', f"{bol_fc_energy/1000:.2f}", f"{fb_fc_energy/1000:.2f}", '', ''])
    w.writerow(['EL_energy(MWh)', f"{bol_el_energy/1000:.2f}", f"{fb_el_energy/1000:.2f}", '', ''])
    w.writerow([])
    w.writerow(['EOL_projection(years)', 'BOL', 'Feedback', 'Lifetime_ratio', ''])
    w.writerow(['BAT', f"{eol_years_bat_bol:.1f}", f"{eol_years_bat:.1f}",
                f"{eol_years_bat/eol_years_bat_bol:.3f}" if eol_years_bat_bol < float('inf') else "NA", ''])
    w.writerow(['FC', f"{eol_years_fc_bol:.1f}", f"{eol_years_fc:.1f}",
                f"{eol_years_fc/eol_years_fc_bol:.3f}" if eol_years_fc_bol < float('inf') else "NA", ''])
    w.writerow(['EL', f"{eol_years_el_bol:.1f}", f"{eol_years_el:.1f}",
                f"{eol_years_el/eol_years_el_bol:.3f}" if eol_years_el_bol < float('inf') else "NA", ''])

print(f"  Summary: {OUT_CSV_SUMMARY}")

with open(OUT_CSV_TRAJ, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['Week', 'D_bat', 'D_fc', 'D_el', 'dD_bat', 'dD_fc', 'dD_el', 'dD_total'])
    for traj in D_trajectory:
        w.writerow([traj['week'], f"{traj['D_bat']:.8f}", f"{traj['D_fc']:.8f}",
                    f"{traj['D_el']:.8f}", f"{traj['dD_bat']:.8f}",
                    f"{traj['dD_fc']:.8f}", f"{traj['dD_el']:.8f}",
                    f"{traj['dD_bat']+traj['dD_fc']+traj['dD_el']:.8f}"])
print(f"  Trajectory: {OUT_CSV_TRAJ}")

# ================================================================
# 生成图表
# ================================================================
print("\n[7] 生成年度仿真图表...")

fig = plt.figure(figsize=(16, 12))
gs = GridSpec(3, 2, hspace=0.40, wspace=0.30, left=0.07, right=0.93, top=0.93, bottom=0.06)

weeks = np.arange(1, n_weeks + 1)
D_bat_arr = np.array([t['D_bat'] for t in D_trajectory])
D_fc_arr = np.array([t['D_fc'] for t in D_trajectory])
D_el_arr = np.array([t['D_el'] for t in D_trajectory])

# (a) D轨迹
ax1 = fig.add_subplot(gs[0, 0])
ax1.plot(weeks, D_bat_arr, color=C_BAT, label='D_BAT', linewidth=2)
ax1.plot(weeks, D_fc_arr, color=C_FC, label='D_FC', linewidth=2)
ax1.plot(weeks, D_el_arr, color=C_EL, label='D_EL', linewidth=2)
ax1.axhline(1.0, color='gray', ls='--', lw=0.8, label='EOL (D=1.0)')
ax1.set_xlabel('Week')
ax1.set_ylabel('Degradation D')
ax1.set_title('(a) Annual D Trajectory (Feedback Mode)', fontsize=10, fontweight='bold')
ax1.legend(fontsize=8)
ax1.grid(True, alpha=0.3)
ax1.set_xlim(0, n_weeks + 1)

# (b) 周度总损伤对比
ax2 = fig.add_subplot(gs[0, 1])
bol_wt = np.array([wk['dD_bat']+wk['dD_fc']+wk['dD_el'] for wk in weekly_bol])
fb_wt = np.array([wk['dD_bat']+wk['dD_fc']+wk['dD_el'] for wk in weekly_fb])
ax2.bar(weeks - 0.2, bol_wt, 0.4, label='BOL (no feedback)', color=C_BOL, alpha=0.7, edgecolor='k', linewidth=0.3)
ax2.bar(weeks + 0.2, fb_wt, 0.4, label='Feedback', color=C_FEEDBACK, alpha=0.7, edgecolor='k', linewidth=0.3)
ax2.set_xlabel('Week')
ax2.set_ylabel('Weekly total dD')
ax2.set_title('(b) Weekly Damage: BOL vs Feedback', fontsize=10, fontweight='bold')
ax2.legend(fontsize=8)
ax2.grid(True, alpha=0.3, axis='y')

# (c) 各设备周度损伤 (反馈模式)
ax3 = fig.add_subplot(gs[1, 0])
fb_bat = np.array([wk['dD_bat'] for wk in weekly_fb])
fb_fc = np.array([wk['dD_fc'] for wk in weekly_fb])
fb_el = np.array([wk['dD_el'] for wk in weekly_fb])
ax3.bar(weeks, fb_bat, 0.6, label='BAT', color=C_BAT, alpha=0.8, edgecolor='k', linewidth=0.3)
ax3.bar(weeks, fb_fc, 0.6, bottom=fb_bat, label='FC', color=C_FC, alpha=0.8, edgecolor='k', linewidth=0.3)
ax3.bar(weeks, fb_el, 0.6, bottom=fb_bat+fb_fc, label='EL', color=C_EL, alpha=0.8, edgecolor='k', linewidth=0.3)
ax3.set_xlabel('Week')
ax3.set_ylabel('Weekly dD by device')
ax3.set_title('(c) Device-Level Damage (Feedback)', fontsize=10, fontweight='bold')
ax3.legend(fontsize=8)
ax3.grid(True, alpha=0.3, axis='y')

# (d) 累积AE对比
ax4 = fig.add_subplot(gs[1, 1])
cum_ae_bat = np.cumsum([weekly_fb[i]['dD_bat'] - weekly_bol[i]['dD_bat'] for i in range(n_weeks)])
cum_ae_fc = np.cumsum([weekly_fb[i]['dD_fc'] - weekly_bol[i]['dD_fc'] for i in range(n_weeks)])
cum_ae_el = np.cumsum([weekly_fb[i]['dD_el'] - weekly_bol[i]['dD_el'] for i in range(n_weeks)])
cum_ae_total = cum_ae_bat + cum_ae_fc + cum_ae_el
ax4.plot(weeks, cum_ae_bat, color=C_BAT, label=f'Cum AE BAT ({ae_bat:+.2e})', linewidth=1.5)
ax4.plot(weeks, cum_ae_fc, color=C_FC, label=f'Cum AE FC ({ae_fc:+.2e})', linewidth=1.5)
ax4.plot(weeks, cum_ae_el, color=C_EL, label=f'Cum AE EL ({ae_el:+.2e})', linewidth=1.5)
ax4.plot(weeks, cum_ae_total, color='k', label=f'Cum AE Total ({ae_total:+.2e})', linewidth=2, ls='--')
ax4.axhline(0, color='gray', lw=0.5)
ax4.set_xlabel('Week')
ax4.set_ylabel('Cumulative AE')
ax4.set_title('(d) Cumulative AE (Feedback - BOL)', fontsize=10, fontweight='bold')
ax4.legend(fontsize=7)
ax4.grid(True, alpha=0.3)

# (e) LPSP对比
ax5 = fig.add_subplot(gs[2, 0])
bol_lpsp_arr = np.array([wk['lpsp']*100 for wk in weekly_bol])
fb_lpsp_arr = np.array([wk['lpsp']*100 for wk in weekly_fb])
ax5.plot(weeks, bol_lpsp_arr, color=C_BOL, label=f'BOL (mean={bol_lpsp*100:.3f}%)', linewidth=1.5)
ax5.plot(weeks, fb_lpsp_arr, color=C_FEEDBACK, label=f'Feedback (mean={fb_lpsp*100:.3f}%)', linewidth=1.5)
ax5.set_xlabel('Week')
ax5.set_ylabel('LPSP (%)')
ax5.set_title('(e) Weekly LPSP Comparison', fontsize=10, fontweight='bold')
ax5.legend(fontsize=8)
ax5.grid(True, alpha=0.3)

# (f) 年度汇总表
ax6 = fig.add_subplot(gs[2, 1])
ax6.axis('off')
table_data = [
    ['BAT', f'{bol_total_bat:.4e}', f'{fb_total_bat:.4e}', f'{ae_bat:+.4e}',
     f'{te_bat*100:+.1f}%' if not np.isnan(te_bat) else 'NA', f'{eol_years_bat:.1f}y'],
    ['FC', f'{bol_total_fc:.4e}', f'{fb_total_fc:.4e}', f'{ae_fc:+.4e}',
     f'{te_fc*100:+.1f}%' if not np.isnan(te_fc) else 'NA', f'{eol_years_fc:.1f}y'],
    ['EL', f'{bol_total_el:.4e}', f'{fb_total_el:.4e}', f'{ae_el:+.4e}',
     f'{te_el*100:+.1f}%' if not np.isnan(te_el) else 'NA', f'{eol_years_el:.1f}y'],
    ['Total', f'{bol_total:.4e}', f'{fb_total:.4e}', f'{ae_total:+.4e}',
     f'{te_total*100:+.1f}%' if not np.isnan(te_total) else 'NA', '-'],
]
col_labels = ['Device', 'BOL dD', 'FB dD', 'AE', 'TE', 'EOL(yr)']
table = ax6.table(cellText=table_data, colLabels=col_labels, loc='center',
                  cellLoc='center', colColours=['#4477AA']*6)
table.auto_set_font_size(False)
table.set_fontsize(7.0)
auto_fit_table(ax6, table)
ax6.set_title('(f) Annual Summary Table', fontsize=10, fontweight='bold', pad=10)

fig.suptitle('P1 Annual Simulation: Degradation Feedback Loop (2017, 52 weeks)\n'
             f'AE_total={ae_total:+.4e} | TE_total={te_total*100:+.1f}% | '
             f'D_BAT(EOY)={D_bat_eoy:.4f} ({eol_years_bat:.1f}y to EOL)',
             fontsize=12, fontweight='bold', y=0.98)

fig.savefig(OUT_FIG, dpi=300, bbox_inches='tight')
plt.close(fig)
print(f"  图表已保存: {OUT_FIG}")

# ================================================================
print("\n" + "=" * 70)
print("P1 Annual Simulation 完成")
print("=" * 70)
print(f"  BOL基线年度总损伤: {bol_total:.6f}")
print(f"  反馈闭环年度总损伤: {fb_total:.6f}")
print(f"  年度AE = {ae_total:+.6f}, TE = {te_total*100:+.2f}%")
print(f"  年末D: BAT={D_state['bat']:.6f}, FC={D_state['fc']:.6f}, EL={D_state['el']:.6f}")
print(f"  EOL投影: BAT={eol_years_bat:.1f}y, FC={eol_years_fc:.1f}y, EL={eol_years_el:.1f}y")
print(f"  输出: {OUT_CSV_SUMMARY}")
print(f"        {OUT_CSV_TRAJ}")
print(f"        {OUT_FIG}")
print("=" * 70)
