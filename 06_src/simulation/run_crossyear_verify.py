# -*- coding: utf-8 -*-
"""
跨年验证 (VS5 补充) — 2016/2017/2018 三年年度仿真对比
VS5设计要求跨年验证季节稳定性，当前仅2017年。
本脚本运行2016和2018年的年度仿真，与2017基准对比TE方向一致性。
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
YEARS = [2016, 2017, 2018]
YEAR_COLORS = {2016: '#1b9e77', 2017: '#3288BD', 2018: '#D73027'}

OUT_DIR = PROJECT_ROOT / "07_results" / "crossyear"
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_CSV = OUT_DIR / "crossyear_comparison.csv"
OUT_FIG = PROJECT_ROOT / "07_results" / "figures" / "crossyear_verification.png"

STEPS_PER_DAY = 96
STEPS_PER_WEEK = STEPS_PER_DAY * 7  # 672
WEEKS_PER_YEAR = 52

from ems.ems_bf_lite import EMSConfig, rule_based_dispatch
from simulation.closed_loop_lite import CoalitionState, compute_damage
from components.hp_bat_lite import _load_280ah_data, fit_hppc, interpolate_hp as interp_bat

# ================================================================
# 加载配置 (与run_annual.py完全一致)
# ================================================================
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

coalition_full = CoalitionState.from_code('1111')


# ================================================================
# 年度仿真函数 (从run_annual.py提取)
# ================================================================
def run_annual_for_year(year, pv_full, load_full):
    """对单年数据运行BOL+Feedback双模式年度仿真，返回结果字典。"""
    n_total = len(pv_full)
    n_weeks = min(WEEKS_PER_YEAR, n_total // STEPS_PER_WEEK)

    # --- BOL基线 ---
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
        })

    bol_total_bat = sum(wk['dD_bat'] for wk in weekly_bol)
    bol_total_fc = sum(wk['dD_fc'] for wk in weekly_bol)
    bol_total_el = sum(wk['dD_el'] for wk in weekly_bol)
    bol_total = bol_total_bat + bol_total_fc + bol_total_el
    bol_lpsp = np.mean([wk['lpsp'] for wk in weekly_bol])

    # --- 反馈闭环 ---
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

        D_state['bat'] = min(D_state['bat'] + dD_bat_sum, 1.0)
        D_state['fc'] = min(D_state['fc'] + dD_fc_sum, 1.0)
        D_state['el'] = min(D_state['el'] + dD_el_sum, 1.0)

        weekly_fb.append({
            'week': w + 1,
            'dD_bat': dD_bat_sum, 'dD_fc': dD_fc_sum, 'dD_el': dD_el_sum,
            'lpsp': float(np.sum(state['p_uns']) / max(np.sum(load_w), 1e-6)),
        })
        D_trajectory.append({
            'week': w + 1,
            'D_bat': D_state['bat'], 'D_fc': D_state['fc'], 'D_el': D_state['el'],
        })

    fb_total_bat = sum(wk['dD_bat'] for wk in weekly_fb)
    fb_total_fc = sum(wk['dD_fc'] for wk in weekly_fb)
    fb_total_el = sum(wk['dD_el'] for wk in weekly_fb)
    fb_total = fb_total_bat + fb_total_fc + fb_total_el
    fb_lpsp = np.mean([wk['lpsp'] for wk in weekly_fb])

    ae_bat = fb_total_bat - bol_total_bat
    ae_fc = fb_total_fc - bol_total_fc
    ae_el = fb_total_el - bol_total_el
    ae_total = fb_total - bol_total

    te_bat = ae_bat / abs(bol_total_bat) if abs(bol_total_bat) > 1e-10 else float('nan')
    te_fc = ae_fc / abs(bol_total_fc) if abs(bol_total_fc) > 1e-10 else float('nan')
    te_el = ae_el / abs(bol_total_el) if abs(bol_total_el) > 1e-10 else float('nan')
    te_total = ae_total / abs(bol_total) if abs(bol_total) > 1e-10 else float('nan')

    # 季度TE
    quarterly_te = []
    for q in range(4):
        wk_s = q * 13; wk_e = wk_s + 13
        bol_q = sum(weekly_bol[i]['dD_bat'] + weekly_bol[i]['dD_fc'] +
                    weekly_bol[i]['dD_el'] for i in range(wk_s, wk_e))
        fb_q = sum(weekly_fb[i]['dD_bat'] + weekly_fb[i]['dD_fc'] +
                   weekly_fb[i]['dD_el'] for i in range(wk_s, wk_e))
        ae_q = fb_q - bol_q
        te_q = ae_q / abs(bol_q) * 100 if abs(bol_q) > 1e-10 else 0
        quarterly_te.append(te_q)

    return {
        'year': year,
        'n_steps': n_total,
        'n_weeks': n_weeks,
        'bol_total': bol_total, 'bol_bat': bol_total_bat, 'bol_fc': bol_total_fc, 'bol_el': bol_total_el,
        'fb_total': fb_total, 'fb_bat': fb_total_bat, 'fb_fc': fb_total_fc, 'fb_el': fb_total_el,
        'ae_total': ae_total, 'ae_bat': ae_bat, 'ae_fc': ae_fc, 'ae_el': ae_el,
        'te_total': te_total, 'te_bat': te_bat, 'te_fc': te_fc, 'te_el': te_el,
        'bol_lpsp': bol_lpsp, 'fb_lpsp': fb_lpsp,
        'D_bat_eoy': D_state['bat'], 'D_fc_eoy': D_state['fc'], 'D_el_eoy': D_state['el'],
        'quarterly_te': quarterly_te,
        'weekly_bol': weekly_bol, 'weekly_fb': weekly_fb,
        'D_trajectory': D_trajectory,
        'pv_annual_mwh': np.sum(pv_full) * 0.25 / 1000,
        'load_annual_mwh': np.sum(load_full) * 0.25 / 1000,
    }


# ================================================================
# 主流程
# ================================================================
print("=" * 70)
print("跨年验证 (VS5补充) — 2016/2017/2018 三年年度仿真对比")
print("=" * 70)

print("\n[0] 加载三年数据...")
raw = pd.read_csv(csv_path, parse_dates=['timestamp_local_civil'])

results = {}
for year in YEARS:
    df_yr = raw[raw['local_civil_year'] == year].copy()
    df_yr = df_yr.rename(columns={
        'timestamp_local_civil': 'timestamp',
        'p_pv_available_reconstructed_nonnegative_kW': 'P_pv_kW',
        'p_building_load_reconstructed_kW': 'P_load_kW',
    })
    df_yr['P_pv_kW'] = df_yr['P_pv_kW'].ffill().bfill()
    df_yr['P_load_kW'] = df_yr['P_load_kW'].ffill().bfill()
    pv_full = df_yr['P_pv_kW'].values
    load_full = df_yr['P_load_kW'].values
    print(f"  {year}年: {len(pv_full)}步 ({len(pv_full)/96:.0f}天), "
          f"PV={np.sum(pv_full)*0.25/1000:.1f}MWh, Load={np.sum(load_full)*0.25/1000:.1f}MWh")

    print(f"\n  运行 {year} 年度仿真...")
    res = run_annual_for_year(year, pv_full, load_full)
    results[year] = res
    print(f"  {year}: BOL={res['bol_total']:.6f}, FB={res['fb_total']:.6f}, "
          f"TE={res['te_total']*100:+.2f}%, LPSP={res['bol_lpsp']*100:.3f}%")
    print(f"         设备TE: BAT={res['te_bat']*100:+.1f}%, FC={res['te_fc']*100:+.1f}%, EL={res['te_el']*100:+.1f}%")
    print(f"         年末D: BAT={res['D_bat_eoy']:.4f}, FC={res['D_fc_eoy']:.4f}, EL={res['D_el_eoy']:.4f}")
    print(f"         季度TE: Q1={res['quarterly_te'][0]:+.1f}%, Q2={res['quarterly_te'][1]:+.1f}%, "
          f"Q3={res['quarterly_te'][2]:+.1f}%, Q4={res['quarterly_te'][3]:+.1f}%")

# ================================================================
# 跨年一致性分析
# ================================================================
print("\n" + "=" * 70)
print("[1] 跨年一致性分析")
print("=" * 70)

# TE方向一致性
te_values = [results[y]['te_total'] for y in YEARS]
all_positive = all(te > 0 for te in te_values)
print(f"\n  TE方向一致性: {'PASS' if all_positive else 'FAIL'} — "
      f"TE = {[f'{te*100:+.2f}%' for te in te_values]}")

# TE变异系数
te_mean = np.mean(te_values)
te_std = np.std(te_values)
te_cv = te_std / abs(te_mean) if abs(te_mean) > 1e-10 else float('nan')
print(f"  TE统计: mean={te_mean*100:+.2f}%, std={te_std*100:.2f}%, CV={te_cv*100:.1f}%")

# 设备级TE方向一致性
for dev in ['bat', 'fc', 'el']:
    te_dev = [results[y][f'te_{dev}'] for y in YEARS]
    dev_positive = all(te > 0 for te in te_dev if not np.isnan(te))
    print(f"  {dev.upper()} TE方向: {'PASS' if dev_positive else 'FAIL'} — "
          f"{[f'{te*100:+.1f}%' for te in te_dev]}")

# LPSP跨年对比
print(f"\n  LPSP跨年:")
for y in YEARS:
    print(f"    {y}: BOL={results[y]['bol_lpsp']*100:.3f}%, FB={results[y]['fb_lpsp']*100:.3f}%")

# 季度TE对比
print(f"\n  季度TE跨年对比:")
quarters = ['Q1 (Win)', 'Q2 (Spr)', 'Q3 (Sum)', 'Q4 (Fall)']
for q_idx, q_name in enumerate(quarters):
    q_tes = [results[y]['quarterly_te'][q_idx] for y in YEARS]
    q_positive = all(te > 0 for te in q_tes)
    print(f"    {q_name}: {[f'{te:+.1f}%' for te in q_tes]} — {'PASS' if q_positive else 'FAIL'}")

# ================================================================
# 验证检查 (VS5补充标准)
# ================================================================
print("\n[2] VS5补充验证检查")
checks = []

# CY01: TE > 0 for all years (方向一致)
checks.append(('CY01', 'TE > 0 for all 3 years', all_positive,
               f"TE = {[f'{te*100:+.2f}%' for te in te_values]}"))

# CY02: TE CV < 50% (变异系数合理)
checks.append(('CY02', 'TE CV < 50%', te_cv < 0.50,
               f"CV = {te_cv*100:.1f}%"))

# CY03: BAT TE > 0 for all years
te_bat_all = [results[y]['te_bat'] for y in YEARS]
checks.append(('CY03', 'BAT TE > 0 all years', all(te > 0 for te in te_bat_all),
               f"BAT TE = {[f'{te*100:+.1f}%' for te in te_bat_all]}"))

# CY04: FC TE > 0 for all years
te_fc_all = [results[y]['te_fc'] for y in YEARS]
checks.append(('CY04', 'FC TE > 0 all years', all(te > 0 for te in te_fc_all),
               f"FC TE = {[f'{te*100:+.1f}%' for te in te_fc_all]}"))

# CY05: EL TE > 0 for all years
te_el_all = [results[y]['te_el'] for y in YEARS]
checks.append(('CY05', 'EL TE > 0 all years', all(te > 0 for te in te_el_all),
               f"EL TE = {[f'{te*100:+.1f}%' for te in te_el_all]}"))

# CY06: FB LPSP >= BOL LPSP for all years (退化不应降低可靠性)
lpsp_check = all(results[y]['fb_lpsp'] >= results[y]['bol_lpsp'] - 0.001 for y in YEARS)
bol_lpsp_str = [f'{results[y]["bol_lpsp"]*100:.2f}%' for y in YEARS]
fb_lpsp_str = [f'{results[y]["fb_lpsp"]*100:.2f}%' for y in YEARS]
checks.append(('CY06', 'FB LPSP >= BOL LPSP all years', lpsp_check,
               f'BOL={bol_lpsp_str}, FB={fb_lpsp_str}'))

# CY07: 2017结果与P1基准一致 (TE=6.59%)
r17 = results[2017]
checks.append(('CY07', '2017 TE matches P1 baseline (6.59%+-0.05)',
               abs(r17['te_total'] - 0.0659) < 0.0005,
               f"2017 TE = {r17['te_total']*100:.2f}%"))

# CY08: Q3(夏)TE > Q1(冬)TE for all years (夏季PV充足→更多循环→更大反馈效应)
q3_gt_q1 = all(results[y]['quarterly_te'][2] > results[y]['quarterly_te'][0] for y in YEARS)
q1_str = [f'{results[y]["quarterly_te"][0]:+.1f}%' for y in YEARS]
q3_str = [f'{results[y]["quarterly_te"][2]:+.1f}%' for y in YEARS]
checks.append(('CY08', 'Q3 TE > Q1 TE all years (seasonal pattern)', q3_gt_q1,
               f'Q1={q1_str}, Q3={q3_str}'))

# CY09: D_bat_EOY > 0.5 for all years (电池退化显著)
d_bat_check = all(results[y]['D_bat_eoy'] > 0.5 for y in YEARS)
d_bat_str = [f'{results[y]["D_bat_eoy"]:.3f}' for y in YEARS]
checks.append(('CY09', 'D_bat EOY > 0.5 all years', d_bat_check,
               f'D_bat = {d_bat_str}'))

# CY10: PV年度总量在合理范围 (各年差异<30%)
pv_mwh = [results[y]['pv_annual_mwh'] for y in YEARS]
pv_cv = np.std(pv_mwh) / np.mean(pv_mwh) if np.mean(pv_mwh) > 0 else float('nan')
checks.append(('CY10', 'PV annual variation CV < 30%', pv_cv < 0.30,
               f"PV MWh = {[f'{m:.1f}' for m in pv_mwh]}, CV={pv_cv*100:.1f}%"))

n_pass = sum(1 for _, _, passed, _ in checks if passed)
n_total = len(checks)
print(f"\n  {'ID':<6} {'Description':<45} {'Result':<8} Details")
print(f"  {'-'*6} {'-'*45} {'-'*8} {'-'*50}")
for cid, desc, passed, details in checks:
    status = 'PASS' if passed else 'FAIL'
    print(f"  {cid:<6} {desc:<45} {status:<8} {details}")

print(f"\n  总计: {n_pass}/{n_total} PASS")

# ================================================================
# 导出CSV
# ================================================================
print("\n[3] 导出CSV...")
with open(OUT_CSV, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['Year', 'PV_MWh', 'Load_MWh', 'n_steps',
                'BOL_total', 'BOL_bat', 'BOL_fc', 'BOL_el',
                'FB_total', 'FB_bat', 'FB_fc', 'FB_el',
                'AE_total', 'AE_bat', 'AE_fc', 'AE_el',
                'TE_total_pct', 'TE_bat_pct', 'TE_fc_pct', 'TE_el_pct',
                'BOL_LPSP_pct', 'FB_LPSP_pct',
                'D_bat_eoy', 'D_fc_eoy', 'D_el_eoy',
                'Q1_TE_pct', 'Q2_TE_pct', 'Q3_TE_pct', 'Q4_TE_pct'])
    for y in YEARS:
        r = results[y]
        w.writerow([y, f"{r['pv_annual_mwh']:.2f}", f"{r['load_annual_mwh']:.2f}", r['n_steps'],
                    f"{r['bol_total']:.8f}", f"{r['bol_bat']:.8f}", f"{r['bol_fc']:.8f}", f"{r['bol_el']:.8f}",
                    f"{r['fb_total']:.8f}", f"{r['fb_bat']:.8f}", f"{r['fb_fc']:.8f}", f"{r['fb_el']:.8f}",
                    f"{r['ae_total']:+.8f}", f"{r['ae_bat']:+.8f}", f"{r['ae_fc']:+.8f}", f"{r['ae_el']:+.8f}",
                    f"{r['te_total']*100:.2f}", f"{r['te_bat']*100:.2f}", f"{r['te_fc']*100:.2f}", f"{r['te_el']*100:.2f}",
                    f"{r['bol_lpsp']*100:.4f}", f"{r['fb_lpsp']*100:.4f}",
                    f"{r['D_bat_eoy']:.6f}", f"{r['D_fc_eoy']:.6f}", f"{r['D_el_eoy']:.6f}",
                    f"{r['quarterly_te'][0]:.2f}", f"{r['quarterly_te'][1]:.2f}",
                    f"{r['quarterly_te'][2]:.2f}", f"{r['quarterly_te'][3]:.2f}"])
    w.writerow([])
    w.writerow(['=== Verification Checks ==='])
    w.writerow(['Check_ID', 'Description', 'Result', 'Details'])
    for cid, desc, passed, details in checks:
        w.writerow([cid, desc, 'PASS' if passed else 'FAIL', details])
    w.writerow([])
    w.writerow(['=== Cross-Year TE Statistics ==='])
    w.writerow(['Metric', 'Value'])
    w.writerow(['TE_mean', f"{te_mean*100:.2f}%"])
    w.writerow(['TE_std', f"{te_std*100:.2f}%"])
    w.writerow(['TE_CV', f"{te_cv*100:.1f}%"])
    w.writerow(['All_positive', str(all_positive)])
    w.writerow(['Checks_pass', f"{n_pass}/{n_total}"])

print(f"  CSV: {OUT_CSV}")

# ================================================================
# 生成图表
# ================================================================
print("\n[4] 生成跨年对比图表...")
fig = plt.figure(figsize=(16, 14))
gs = GridSpec(3, 2, hspace=0.40, wspace=0.30, left=0.07, right=0.93, top=0.93, bottom=0.06)

# (a) 三年D轨迹对比
ax1 = fig.add_subplot(gs[0, 0])
for y in YEARS:
    traj = results[y]['D_trajectory']
    weeks = np.arange(1, len(traj) + 1)
    D_bat_arr = np.array([t['D_bat'] for t in traj])
    ax1.plot(weeks, D_bat_arr, color=YEAR_COLORS[y], label=f'{y} D_BAT', linewidth=1.5)
ax1.axhline(1.0, color='gray', ls='--', lw=0.8, label='EOL')
ax1.set_xlabel('Week'); ax1.set_ylabel('D_BAT')
ax1.set_title('(a) D_BAT Trajectory: 3-Year Comparison', fontsize=10, fontweight='bold')
ax1.legend(fontsize=8); ax1.grid(True, alpha=0.3)

# (b) 三年TE对比 (柱状图)
ax2 = fig.add_subplot(gs[0, 1])
te_data = [results[y]['te_total'] * 100 for y in YEARS]
te_bat_data = [results[y]['te_bat'] * 100 for y in YEARS]
te_fc_data = [results[y]['te_fc'] * 100 for y in YEARS]
te_el_data = [results[y]['te_el'] * 100 for y in YEARS]
x = np.arange(len(YEARS))
w_bar = 0.2
ax2.bar(x - 1.5*w_bar, te_bat_data, w_bar, label='TE_BAT', color=C_BAT, alpha=0.8)
ax2.bar(x - 0.5*w_bar, te_fc_data, w_bar, label='TE_FC', color=C_FC, alpha=0.8)
ax2.bar(x + 0.5*w_bar, te_el_data, w_bar, label='TE_EL', color=C_EL, alpha=0.8)
ax2.bar(x + 1.5*w_bar, te_data, w_bar, label='TE_total', color='k', alpha=0.8)
ax2.set_xticks(x); ax2.set_xticklabels([str(y) for y in YEARS])
ax2.set_ylabel('TE (%)'); ax2.axhline(0, color='gray', lw=0.5)
ax2.set_title('(b) Total Error by Year and Device', fontsize=10, fontweight='bold')
ax2.legend(fontsize=8); ax2.grid(True, alpha=0.3, axis='y')

# (c) 季度TE对比
ax3 = fig.add_subplot(gs[1, 0])
quarters_labels = ['Q1', 'Q2', 'Q3', 'Q4']
for y in YEARS:
    q_tes = results[y]['quarterly_te']
    ax3.plot(quarters_labels, q_tes, 'o-', color=YEAR_COLORS[y], label=str(y), linewidth=1.5, markersize=6)
ax3.axhline(0, color='gray', lw=0.5)
ax3.set_ylabel('Quarterly TE (%)')
ax3.set_title('(c) Quarterly TE: 3-Year Comparison', fontsize=10, fontweight='bold')
ax3.legend(fontsize=8); ax3.grid(True, alpha=0.3)

# (d) 三年LPSP对比
ax4 = fig.add_subplot(gs[1, 1])
bol_lpsp_data = [results[y]['bol_lpsp'] * 100 for y in YEARS]
fb_lpsp_data = [results[y]['fb_lpsp'] * 100 for y in YEARS]
ax4.bar(x - 0.2, bol_lpsp_data, 0.4, label='BOL LPSP', color='#3288BD', alpha=0.7)
ax4.bar(x + 0.2, fb_lpsp_data, 0.4, label='FB LPSP', color='#D73027', alpha=0.7)
ax4.set_xticks(x); ax4.set_xticklabels([str(y) for y in YEARS])
ax4.set_ylabel('LPSP (%)')
ax4.set_title('(d) Annual LPSP: 3-Year Comparison', fontsize=10, fontweight='bold')
ax4.legend(fontsize=8); ax4.grid(True, alpha=0.3, axis='y')

# (e) 周度TE对比 (三年叠加)
ax5 = fig.add_subplot(gs[2, 0])
for y in YEARS:
    weekly_bol = results[y]['weekly_bol']
    weekly_fb = results[y]['weekly_fb']
    n_wk = len(weekly_bol)
    cum_ae = np.cumsum([weekly_fb[i]['dD_bat'] + weekly_fb[i]['dD_fc'] + weekly_fb[i]['dD_el'] -
                        weekly_bol[i]['dD_bat'] - weekly_bol[i]['dD_fc'] - weekly_bol[i]['dD_el']
                        for i in range(n_wk)])
    weeks = np.arange(1, n_wk + 1)
    ax5.plot(weeks, cum_ae, color=YEAR_COLORS[y], label=f'{y} (total={cum_ae[-1]:+.2e})', linewidth=1.5)
ax5.axhline(0, color='gray', lw=0.5)
ax5.set_xlabel('Week'); ax5.set_ylabel('Cumulative AE')
ax5.set_title('(e) Cumulative AE: 3-Year Comparison', fontsize=10, fontweight='bold')
ax5.legend(fontsize=8); ax5.grid(True, alpha=0.3)

# (f) 验证检查汇总表
ax6 = fig.add_subplot(gs[2, 1])
ax6.axis('off')
table_data = [[cid, 'PASS' if p else 'FAIL', desc[:22]] for cid, desc, p, _ in checks]
col_labels = ['Check', 'Result', 'Description']
table = ax6.table(cellText=table_data, colLabels=col_labels, loc='center',
                  cellLoc='center', colColours=['#4477AA']*3,
                  colWidths=[0.15, 0.13, 0.72])
table.auto_set_font_size(False); table.set_fontsize(7.0)
auto_fit_table(ax6, table)
ax6.set_title(f'(f) Verification Checks ({n_pass}/{n_total} PASS)', fontsize=10, fontweight='bold', pad=10)

te_str_list = [f'{results[y]["te_total"]*100:+.2f}%' for y in YEARS]
fig.suptitle('Cross-Year Verification (VS5 Supplement): 2016/2017/2018\n'
             f'TE = {te_str_list} | '
             f'CV = {te_cv*100:.1f}% | Checks: {n_pass}/{n_total} PASS',
             fontsize=12, fontweight='bold', y=0.98)

fig.savefig(OUT_FIG, dpi=300, bbox_inches='tight')
plt.close(fig)
print(f"  图表: {OUT_FIG}")

# ================================================================
print("\n" + "=" * 70)
print("跨年验证完成")
print("=" * 70)
te_str_final = [f'{results[y]["te_total"]*100:+.2f}%' for y in YEARS]
print(f"  三年TE: {te_str_final}")
print(f"  TE mean={te_mean*100:+.2f}%, CV={te_cv*100:.1f}%")
print(f"  方向一致: {'YES' if all_positive else 'NO'}")
print(f"  验证检查: {n_pass}/{n_total} PASS")
print(f"  输出: {OUT_CSV}")
print(f"        {OUT_FIG}")
print("=" * 70)
