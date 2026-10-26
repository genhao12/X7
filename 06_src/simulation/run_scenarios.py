# -*- coding: utf-8 -*-
"""
P3 Scenario Analysis - 情景参数敏感性分析
对3个关键参数做单变量扫描, 评估对年度损伤/AE/TE/LPSP/EOL的影响:

1. eta_degrade (FC/EL效率退化斜率): 0.05 / 0.10(基准) / 0.15 / 0.20
2. BAT_kWh (电池容量): 90 / 135(基准) / 180
3. PV_kWp (光伏容量): 90 / 120(基准) / 150

每组: 52周反馈闭环仿真 + BOL基线对比
输出:
  - 07_results/scenarios/scenario_sensitivity.csv
  - 07_results/figures/scenario_analysis.png
"""
import sys, json, csv
import numpy as np
import pandas as pd
from pathlib import Path
from dataclasses import replace

import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec

PROJECT_ROOT = Path(r"D:\matlab\C")
sys.path.insert(0, str(PROJECT_ROOT / "06_src"))

plt.rcParams.update({'axes.unicode_minus': False, 
    'font.family': 'sans-serif', 'font.sans-serif': ['Microsoft YaHei', 'SimHei', 'Arial', 'DejaVu Sans'],
    'font.size': 11, 'axes.titlesize': 10, 'axes.labelsize': 10,
    'xtick.labelsize': 9, 'ytick.labelsize': 9, 'legend.fontsize': 8,
    'axes.linewidth': 0.8, 'lines.linewidth': 1.5, 'grid.alpha': 0.25,
    'axes.spines.top': False, 'axes.spines.right': False,
})

C_BAT = '#3288BD'; C_FC = '#D73027'; C_EL = '#66C2A5'
C_BASE = '#999999'

OUT_FIG = PROJECT_ROOT / "07_results" / "figures" / "scenario_analysis.png"
OUT_CSV = PROJECT_ROOT / "07_results" / "scenarios" / "scenario_sensitivity.csv"

STEPS_PER_DAY = 96
STEPS_PER_WEEK = STEPS_PER_DAY * 7
WEEKS_PER_YEAR = 52

from ems.ems_bf_lite import EMSConfig, rule_based_dispatch
from simulation.closed_loop_lite import CoalitionState, compute_damage
from components.hp_bat_lite import _load_280ah_data, fit_hppc, interpolate_hp as interp_bat

config_path = PROJECT_ROOT / "05_config" / "mvp_c1_config.json"
with open(config_path, 'r', encoding='utf-8') as f:
    c1_base = json.load(f)

csv_path = str(PROJECT_ROOT / "02_data_processed" / "tradestreet_pipeline" /
               "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv")

hppc_data = _load_280ah_data()
bat_fit = fit_hppc(hppc_data)
coalition_full = CoalitionState.from_code('1111')

print("=" * 70)
print("P3 Scenario Analysis - 情景参数敏感性分析")
print("=" * 70)

# 加载2017全年数据
print("\n[0] 加载2017年数据...")
raw = pd.read_csv(csv_path, parse_dates=['timestamp_local_civil'])
df_2017 = raw[raw['local_civil_year'] == 2017].copy()
df_2017 = df_2017.rename(columns={
    'p_pv_available_reconstructed_nonnegative_kW': 'P_pv_kW',
    'p_building_load_reconstructed_kW': 'P_load_kW',
})
df_2017['P_pv_kW'] = df_2017['P_pv_kW'].ffill().bfill()
df_2017['P_load_kW'] = df_2017['P_load_kW'].ffill().bfill()
pv_full = df_2017['P_pv_kW'].values
load_full = df_2017['P_load_kW'].values
n_weeks = min(WEEKS_PER_YEAR, len(pv_full) // STEPS_PER_WEEK)
print(f"  {len(pv_full)}步, {n_weeks}周仿真")


def make_config(pv_kwp, bat_kwh, base_cfg):
    """根据参数构建EMSConfig."""
    cap = base_cfg['capacity']; ems_p = base_cfg['ems_parameters']
    bat_c = base_cfg['battery_constraints']; h2_c = base_cfg['hydrogen_constraints']
    msl = base_cfg['minimum_stable_load']; topo = base_cfg['topology']
    return EMSConfig(
        pv_kwp=pv_kwp, bat_kwh=bat_kwh,
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


def run_annual(pv_kwp, bat_kwh, eta_degrade, mode='feedback'):
    """运行52周年度仿真, 返回年度汇总."""
    cfg = make_config(pv_kwp, bat_kwh, c1_base)
    bat_rated_kw = bat_kwh * 0.5
    config_dict = {
        'capacity': {**c1_base['capacity'], 'PV_kWp': pv_kwp, 'BAT_kWh': bat_kwh},
        'battery_constraints': c1_base['battery_constraints'],
        'ems_parameters': c1_base['ems_parameters'],
        'hydrogen_constraints': c1_base['hydrogen_constraints'],
        'minimum_stable_load': c1_base['minimum_stable_load'],
        'topology': c1_base['topology'],
    }
    hp_bat_fn = interp_bat(bat_fit, {'bat_kwh': bat_kwh,
                                      'bat_voltage_nominal': 3.2*48,
                                      'bat_rated_kw': bat_rated_kw})

    if mode == 'bol':
        D_state = {'bat': 0.0, 'fc': 0.0, 'el': 0.0}
    else:
        D_state = {'bat': 0.0, 'fc': 0.0, 'el': 0.0}

    total_bat = 0.0; total_fc = 0.0; total_el = 0.0
    lpsp_list = []; curtail = 0.0; fc_e = 0.0; el_e = 0.0

    for w in range(n_weeks):
        s = w * STEPS_PER_WEEK; e = s + STEPS_PER_WEEK
        pv_w = pv_full[s:e]; load_w = load_full[s:e]

        if mode == 'feedback' and D_state['bat'] > 0:
            q_frac, _, _ = hp_bat_fn(D_state['bat'])
            bat_p_max = bat_rated_kw * q_frac
            bat_cap = bat_kwh * q_frac
        else:
            bat_p_max = None; bat_cap = None

        np.random.seed(42)
        cmds, state = rule_based_dispatch(load_w, pv_w, cfg,
                                           bat_p_max_kw=bat_p_max,
                                           bat_capacity_kwh=bat_cap)

        dD = compute_damage(hp_bat_fn, cmds, config_dict, dt_min=15.0,
                            D_state=D_state, coalition=coalition_full,
                            eta_degrade=eta_degrade)

        d_bat = float(np.sum(dD['dD_bat']))
        d_fc = float(np.sum(dD['dD_fc']))
        d_el = float(np.sum(dD['dD_el']))

        if mode == 'feedback':
            D_state['bat'] = min(D_state['bat'] + d_bat, 1.0)
            D_state['fc'] = min(D_state['fc'] + d_fc, 1.0)
            D_state['el'] = min(D_state['el'] + d_el, 1.0)

        total_bat += d_bat; total_fc += d_fc; total_el += d_el
        lpsp_list.append(float(np.sum(state['p_uns']) / max(np.sum(load_w), 1e-6)))
        curtail += float(np.sum(state['p_cur']) * 0.25)
        fc_e += float(np.sum(cmds[:, 1]) * 0.25)
        el_e += float(np.sum(cmds[:, 2]) * 0.25)

    return {
        'total_bat': total_bat, 'total_fc': total_fc, 'total_el': total_el,
        'total': total_bat + total_fc + total_el,
        'lpsp': float(np.mean(lpsp_list)),
        'curtailment': curtail, 'fc_energy': fc_e, 'el_energy': el_e,
        'D_bat_eoy': D_state['bat'] if mode == 'feedback' else 0.0,
        'D_fc_eoy': D_state['fc'] if mode == 'feedback' else 0.0,
        'D_el_eoy': D_state['el'] if mode == 'feedback' else 0.0,
    }


# === 情景定义 ===
SCENARIOS = []

# 参数1: eta_degrade
for eta in [0.05, 0.10, 0.15, 0.20]:
    SCENARIOS.append({
        'group': 'eta_degrade', 'param': 'eta_degrade', 'value': eta,
        'pv_kwp': 120, 'bat_kwh': 135, 'eta_degrade': eta,
        'label': f'eta={eta:.2f}',
    })

# 参数2: BAT_kWh
for bk in [90, 135, 180]:
    SCENARIOS.append({
        'group': 'BAT_kWh', 'param': 'BAT_kWh', 'value': bk,
        'pv_kwp': 120, 'bat_kwh': bk, 'eta_degrade': 0.10,
        'label': f'BAT={bk}kWh',
    })

# 参数3: PV_kWp
for pk in [90, 120, 150]:
    SCENARIOS.append({
        'group': 'PV_kWp', 'param': 'PV_kWp', 'value': pk,
        'pv_kwp': pk, 'bat_kwh': 135, 'eta_degrade': 0.10,
        'label': f'PV={pk}kWp',
    })

print(f"\n[1] 执行{len(SCENARIOS)}个情景 (每组BOL+Feedback双模式)...")
print(f"    总计 {len(SCENARIOS)*2} 次年度仿真")

results = []
for i, sc in enumerate(SCENARIOS):
    print(f"\n  [{i+1}/{len(SCENARIOS)}] {sc['label']} ({sc['group']})...")

    # BOL基线
    bol = run_annual(sc['pv_kwp'], sc['bat_kwh'], sc['eta_degrade'], mode='bol')
    # 反馈闭环
    fb = run_annual(sc['pv_kwp'], sc['bat_kwh'], sc['eta_degrade'], mode='feedback')

    ae_bat = fb['total_bat'] - bol['total_bat']
    ae_fc = fb['total_fc'] - bol['total_fc']
    ae_el = fb['total_el'] - bol['total_el']
    ae_total = fb['total'] - bol['total']
    te_total = ae_total / abs(bol['total']) * 100 if abs(bol['total']) > 1e-10 else 0

    eol_bat = 1.0 / fb['D_bat_eoy'] if fb['D_bat_eoy'] > 1e-6 else float('inf')
    eol_fc = 1.0 / fb['D_fc_eoy'] if fb['D_fc_eoy'] > 1e-6 else float('inf')
    eol_el = 1.0 / fb['D_el_eoy'] if fb['D_el_eoy'] > 1e-6 else float('inf')

    r = {
        **sc,
        'bol_bat': bol['total_bat'], 'bol_fc': bol['total_fc'], 'bol_el': bol['total_el'],
        'bol_total': bol['total'], 'bol_lpsp': bol['lpsp'],
        'fb_bat': fb['total_bat'], 'fb_fc': fb['total_fc'], 'fb_el': fb['total_el'],
        'fb_total': fb['total'], 'fb_lpsp': fb['lpsp'],
        'fb_curtail': fb['curtailment'], 'fb_fc_e': fb['fc_energy'], 'fb_el_e': fb['el_energy'],
        'ae_bat': ae_bat, 'ae_fc': ae_fc, 'ae_el': ae_el, 'ae_total': ae_total,
        'te_total': te_total,
        'D_bat_eoy': fb['D_bat_eoy'], 'D_fc_eoy': fb['D_fc_eoy'], 'D_el_eoy': fb['D_el_eoy'],
        'eol_bat': eol_bat, 'eol_fc': eol_fc, 'eol_el': eol_el,
    }
    results.append(r)

    print(f"    BOL: total={bol['total']:.4f}, LPSP={bol['lpsp']*100:.1f}%")
    print(f"    FB:  total={fb['total']:.4f}, LPSP={fb['lpsp']*100:.1f}%, "
          f"D_eoy BAT={fb['D_bat_eoy']:.4f} FC={fb['D_fc_eoy']:.4f} EL={fb['D_el_eoy']:.4f}")
    print(f"    AE={ae_total:+.4f}, TE={te_total:+.1f}%")

# ================================================================
# 汇总打印
# ================================================================
print("\n" + "=" * 70)
print("[2] 情景敏感性汇总")
print("=" * 70)

for group in ['eta_degrade', 'BAT_kWh', 'PV_kWp']:
    grp_results = [r for r in results if r['group'] == group]
    print(f"\n  --- {group} ---")
    print(f"  {'Label':>14s} | {'BOL_total':>10s} | {'FB_total':>10s} | {'AE':>10s} | "
          f"{'TE(%)':>7s} | {'LPSP(%)':>7s} | {'EOL_BAT':>7s} | {'EOL_FC':>7s} | {'EOL_EL':>7s}")
    for r in grp_results:
        print(f"  {r['label']:>14s} | {r['bol_total']:10.4f} | {r['fb_total']:10.4f} | "
              f"{r['ae_total']:+10.4f} | {r['te_total']:+7.1f} | {r['fb_lpsp']*100:7.1f} | "
              f"{r['eol_bat']:7.2f} | {r['eol_fc']:7.2f} | {r['eol_el']:7.2f}")

# ================================================================
# 导出CSV
# ================================================================
print("\n[3] 导出CSV...")

with open(OUT_CSV, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['Group', 'Param', 'Value', 'Label',
                'BOL_dD_bat', 'BOL_dD_fc', 'BOL_dD_el', 'BOL_dD_total', 'BOL_LPSP_pct',
                'FB_dD_bat', 'FB_dD_fc', 'FB_dD_el', 'FB_dD_total', 'FB_LPSP_pct',
                'FB_curtail_kWh', 'FB_FC_kWh', 'FB_EL_kWh',
                'AE_bat', 'AE_fc', 'AE_el', 'AE_total', 'TE_total_pct',
                'D_bat_eoy', 'D_fc_eoy', 'D_el_eoy',
                'EOL_bat_yr', 'EOL_fc_yr', 'EOL_el_yr'])
    for r in results:
        w.writerow([r['group'], r['param'], r['value'], r['label'],
                    f"{r['bol_bat']:.8f}", f"{r['bol_fc']:.8f}", f"{r['bol_el']:.8f}",
                    f"{r['bol_total']:.8f}", f"{r['bol_lpsp']*100:.4f}",
                    f"{r['fb_bat']:.8f}", f"{r['fb_fc']:.8f}", f"{r['fb_el']:.8f}",
                    f"{r['fb_total']:.8f}", f"{r['fb_lpsp']*100:.4f}",
                    f"{r['fb_curtail']:.2f}", f"{r['fb_fc_e']:.2f}", f"{r['fb_el_e']:.2f}",
                    f"{r['ae_bat']:+.8f}", f"{r['ae_fc']:+.8f}", f"{r['ae_el']:+.8f}",
                    f"{r['ae_total']:+.8f}", f"{r['te_total']:.2f}",
                    f"{r['D_bat_eoy']:.8f}", f"{r['D_fc_eoy']:.8f}", f"{r['D_el_eoy']:.8f}",
                    f"{r['eol_bat']:.2f}", f"{r['eol_fc']:.2f}", f"{r['eol_el']:.2f}"])
print(f"  CSV: {OUT_CSV}")

# ================================================================
# 生成图表
# ================================================================
print("\n[4] 生成情景分析图表...")

fig = plt.figure(figsize=(16, 14))
gs = GridSpec(3, 3, hspace=0.45, wspace=0.35, left=0.07, right=0.93, top=0.93, bottom=0.06)

groups = ['eta_degrade', 'BAT_kWh', 'PV_kWp']
group_titles = {
    'eta_degrade': r'$\eta_{degrade}$ (FC/EL efficiency degradation slope)',
    'BAT_kWp': 'BAT Capacity (kWh)',
    'PV_kWp': 'PV Capacity (kWp)',
}
group_units = {'eta_degrade': '', 'BAT_kWh': ' kWh', 'PV_kWp': ' kWp'}

for col, group in enumerate(groups):
    grp = [r for r in results if r['group'] == group]
    vals = [r['value'] for r in grp]
    labels = [r['label'] for r in grp]
    x = np.arange(len(vals))

    # Row 1: 年度总损伤 (BOL vs Feedback)
    ax1 = fig.add_subplot(gs[0, col])
    bol_tot = [r['bol_total'] for r in grp]
    fb_tot = [r['fb_total'] for r in grp]
    ax1.bar(x - 0.2, bol_tot, 0.35, label='BOL', color=C_BASE, alpha=0.7, edgecolor='k', linewidth=0.3)
    ax1.bar(x + 0.2, fb_tot, 0.35, label='Feedback', color=C_FC, alpha=0.7, edgecolor='k', linewidth=0.3)
    # 基准线
    base_idx = 1 if len(vals) == 3 else 1  # 中间为基准
    ax1.axhline(fb_tot[base_idx], color=C_FC, ls=':', lw=0.8, alpha=0.5)
    ax1.set_xticks(x); ax1.set_xticklabels(labels, fontsize=8, rotation=15)
    ax1.set_ylabel('Annual total dD')
    ax1.set_title(f'({chr(97+col)}) Total Damage vs {group}', fontsize=9, fontweight='bold')
    ax1.legend(fontsize=7); ax1.grid(True, alpha=0.3, axis='y')

    # Row 2: AE/TE
    ax2 = fig.add_subplot(gs[1, col])
    ae_b = [r['ae_bat'] for r in grp]
    ae_f = [r['ae_fc'] for r in grp]
    ae_e = [r['ae_el'] for r in grp]
    ax2.bar(x - 0.25, ae_b, 0.25, label='AE_BAT', color=C_BAT, alpha=0.8, edgecolor='k', linewidth=0.3)
    ax2.bar(x, ae_f, 0.25, label='AE_FC', color=C_FC, alpha=0.8, edgecolor='k', linewidth=0.3)
    ax2.bar(x + 0.25, ae_e, 0.25, label='AE_EL', color=C_EL, alpha=0.8, edgecolor='k', linewidth=0.3)
    ax2.axhline(0, color='k', lw=0.5)
    ax2.set_xticks(x); ax2.set_xticklabels(labels, fontsize=8, rotation=15)
    ax2.set_ylabel('AE (FB - BOL)')
    ax2.set_title(f'({chr(100+col)}) Device AE vs {group}', fontsize=9, fontweight='bold')
    ax2.legend(fontsize=7); ax2.grid(True, alpha=0.3, axis='y')

    # Row 3: EOL投影 + LPSP
    ax3 = fig.add_subplot(gs[2, col])
    eol_b = [r['eol_bat'] for r in grp]
    eol_f = [r['eol_fc'] for r in grp]
    eol_e = [r['eol_el'] for r in grp]
    ax3.plot(x, eol_b, 'o-', color=C_BAT, label='BAT EOL', linewidth=1.5, markersize=6)
    ax3.plot(x, eol_f, 's-', color=C_FC, label='FC EOL', linewidth=1.5, markersize=6)
    ax3.plot(x, eol_e, '^-', color=C_EL, label='EL EOL', linewidth=1.5, markersize=6)
    ax3.set_xticks(x); ax3.set_xticklabels(labels, fontsize=8, rotation=15)
    ax3.set_ylabel('EOL projection (years)')
    ax3.set_title(f'({chr(103+col)}) EOL vs {group}', fontsize=9, fontweight='bold')
    ax3.legend(fontsize=7); ax3.grid(True, alpha=0.3)
    # LPSP标注
    ax3r = ax3.twinx()
    lpsp_vals = [r['fb_lpsp']*100 for r in grp]
    ax3r.plot(x, lpsp_vals, 'D--', color='gray', label='LPSP(%)', linewidth=1, markersize=4, alpha=0.5)
    ax3r.set_ylabel('LPSP (%)', color='gray')
    ax3r.tick_params(axis='y', labelcolor='gray')
    ax3r.legend(fontsize=7, loc='upper right')

fig.suptitle('P3 Scenario Analysis: Parameter Sensitivity (2017, 52 weeks)\n'
             '3 parameters x 3-4 levels | BOL vs Feedback | AE/TE/EOL/LPSP',
             fontsize=12, fontweight='bold', y=0.98)

fig.savefig(OUT_FIG, dpi=300, bbox_inches='tight')
plt.close(fig)
print(f"  图表已保存: {OUT_FIG}")

# ================================================================
print("\n" + "=" * 70)
print("P3 Scenario Analysis 完成")
print("=" * 70)
print(f"  情景数: {len(SCENARIOS)} (3组参数 x 3-4水平)")
print(f"  仿真次数: {len(SCENARIOS)*2} (BOL+Feedback)")
print(f"  输出: {OUT_CSV}")
print(f"        {OUT_FIG}")
print("=" * 70)
