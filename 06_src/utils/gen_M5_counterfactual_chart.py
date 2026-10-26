# -*- coding: utf-8 -*-
"""
M5 counterfactual_lite 图表生成 (出版级, 按M1/M2标准)
4个子图, 突出重点, 标签外部右上方, 不遮挡数据
(a) 三运行设备损伤对比
(b) AE值柱状图 (路径×退化水平)
(c) TE值柱状图
(d) 损伤增量时序对比
"""
import sys
import json
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from pathlib import Path

project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root / "06_src"))

# === 样式 ===
plt.rcParams.update({'axes.unicode_minus': False, 
    'font.family': 'sans-serif', 'font.sans-serif': ['Microsoft YaHei', 'SimHei', 'Arial', 'DejaVu Sans'],
    'font.size': 11, 'axes.titlesize': 12, 'axes.labelsize': 11,
    'xtick.labelsize': 10, 'ytick.labelsize': 10, 'legend.fontsize': 9,
    'axes.linewidth': 0.8, 'lines.linewidth': 1.5, 'lines.markersize': 5,
    'grid.alpha': 0.25, 'grid.linewidth': 0.5,
    'axes.spines.top': False, 'axes.spines.right': False,
})

C_BAT = '#3288BD'; C_FC = '#FDAE61'; C_EL = '#74ADD1'
C_DATA = '#3288BD'; C_FIT = '#D73027'; C_REF = '#999999'
C_ALPHA = '#FC8D62'; C_BETA = '#66C2A5'

# === 1. 加载数据 ===
config_path = project_root / "05_config" / "mvp_c1_config.json"
with open(config_path, 'r', encoding='utf-8') as f:
    config = json.load(f)

from components.data_lite import load_data
from ems.ems_bf_lite import EMSConfig, rule_based_dispatch
from simulation.closed_loop_lite import compute_damage, CoalitionState
from simulation.counterfactual_lite import pair_runs, validate_ae_values, PATHS

load_csv = str(project_root / "02_data_processed" / "tradestreet_pipeline" /
               "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv")
data = load_data(load_csv, str(config_path), year=2017, window_days=7)
df = data['data']
pv_kw = df['P_pv_kW'].values
load_kw = df['P_load_kW'].values
n = len(pv_kw)
t_hours = np.arange(n) * 0.25

cap = config['capacity']; ems_p = config['ems_parameters']
bat_c = config['battery_constraints']; h2_c = config['hydrogen_constraints']
msl = config['minimum_stable_load']; topo = config['topology']
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

def test_hp_func(D):
    D = float(np.clip(D, 0.0, 1.0))
    return (1.0 - 0.2*D, 0.000427*(1+0.1673*D), 67.5*(1.0-0.2*D))

run_configs = {
    'BOL':      {'D_state': {'bat': 0.0,  'fc': 0.0, 'el': 0.0}, 'bat_p_max': 67.50, 'bat_cap': 135.0},
    'BAT_D050': {'D_state': {'bat': 0.50, 'fc': 0.0, 'el': 0.0}, 'bat_p_max': 60.75, 'bat_cap': 121.5},
    'BAT_D090': {'D_state': {'bat': 0.90, 'fc': 0.0, 'el': 0.0}, 'bat_p_max': 55.35, 'bat_cap': 110.7},
}

coalition_full = CoalitionState.from_code('1111')
run_results = {}
run_commands = {}
for run_label, rc in run_configs.items():
    np.random.seed(42)
    commands, _ = rule_based_dispatch(load_kw, pv_kw, cfg,
                                       bat_p_max_kw=rc['bat_p_max'], bat_capacity_kwh=rc['bat_cap'])
    dD = compute_damage(test_hp_func, commands, config,
                        D_state=rc['D_state'], coalition=coalition_full)
    run_results[run_label] = dD
    run_commands[run_label] = commands

ae_list = pair_runs(run_results)
report = validate_ae_values(ae_list)

# === 2. 绘图 ===
fig, axes = plt.subplots(2, 2, figsize=(13, 9))
fig.subplots_adjust(left=0.08, right=0.78, top=0.90, bottom=0.08, wspace=0.28, hspace=0.32)
fig.suptitle("M5 Counterfactual Pairing | 3 Runs x 2 Paths = 4 AE Values", fontsize=12, fontweight='bold', x=0.42)

# --- (a) 三运行设备损伤对比 ---
ax = axes[0, 0]
run_labels = ['BOL', 'BAT D=0.50', 'BAT D=0.90']
bat_vals = [np.sum(run_results[r]['dD_bat']) for r in run_configs.keys()]
fc_vals = [np.sum(run_results[r]['dD_fc']) for r in run_configs.keys()]
el_vals = [np.sum(run_results[r]['dD_el']) for r in run_configs.keys()]
x = np.arange(len(run_labels)); w = 0.25
ax.bar(x - w, bat_vals, w, label='BAT', color=C_BAT, edgecolor='black', linewidth=0.4)
ax.bar(x, fc_vals, w, label='FC', color=C_FC, edgecolor='black', linewidth=0.4)
ax.bar(x + w, el_vals, w, label='EL', color=C_EL, edgecolor='black', linewidth=0.4)
ax.set_xticks(x); ax.set_xticklabels(run_labels)
ax.set_ylabel('Total damage (sum dD)')
ax.set_title('(a) Device Damage: 3 Runs', fontsize=11)
ax.grid(True, axis='y')
ax.legend(loc='upper left', bbox_to_anchor=(1.02, 1.0), borderaxespad=0, fontsize=8, framealpha=0.9)

# --- (b) AE值柱状图 ---
ax = axes[0, 1]
ae_labels = ['alpha1\nD=0.50', 'alpha1\nD=0.90', 'beta1\nD=0.50', 'beta1\nD=0.90']
ae_vals = [ae.AE for ae in ae_list]
ae_colors = [C_ALPHA if 'alpha1' in ae.path else C_BETA for ae in ae_list]
bars = ax.bar(range(4), ae_vals, color=ae_colors, edgecolor='black', linewidth=0.4, width=0.6)
ax.axhline(0, color='black', lw=0.8)
ax.set_xticks(range(4)); ax.set_xticklabels(ae_labels, fontsize=9)
ax.set_ylabel('AE = dD_coupled - dD_baseline')
ax.set_title('(b) Absolute Effect (AE)', fontsize=11)
ax.grid(True, axis='y')
for bar, val in zip(bars, ae_vals):
    ax.text(bar.get_x() + bar.get_width()/2, val + max(ae_vals)*0.02,
            f'{val:+.4f}', ha='center', va='bottom', fontsize=9, fontweight='bold')
legend_b = [Patch(facecolor=C_ALPHA, edgecolor='black', label='alpha1 (BAT->FC)'),
            Patch(facecolor=C_BETA, edgecolor='black', label='beta1 (BAT->EL)')]
ax.legend(handles=legend_b, loc='upper left', bbox_to_anchor=(1.02, 1.0), borderaxespad=0, fontsize=8, framealpha=0.9)

# --- (c) TE值柱状图 ---
ax = axes[1, 0]
te_vals = [ae.TE * 100 if np.isfinite(ae.TE) else 0 for ae in ae_list]
te_colors = [C_ALPHA if 'alpha1' in ae.path else C_BETA for ae in ae_list]
bars = ax.bar(range(4), te_vals, color=te_colors, edgecolor='black', linewidth=0.4, width=0.6)
ax.axhline(5, color=C_REF, ls='--', lw=1.0)  # TE>=5% 阈值
ax.text(3.5, 5.5, 'TE=5% threshold', ha='right', fontsize=8, color=C_REF)
ax.set_xticks(range(4)); ax.set_xticklabels(ae_labels, fontsize=9)
ax.set_ylabel('TE (%)')
ax.set_title('(c) Total Effect (TE)', fontsize=11)
ax.grid(True, axis='y')
for bar, val in zip(bars, te_vals):
    ax.text(bar.get_x() + bar.get_width()/2, val + max(te_vals)*0.02,
            f'{val:.1f}%', ha='center', va='bottom', fontsize=9, fontweight='bold')
ax.legend(handles=legend_b, loc='upper left', bbox_to_anchor=(1.02, 1.0), borderaxespad=0, fontsize=8, framealpha=0.9)

# --- (d) FC损伤增量时序 (alpha1路径) ---
ax = axes[1, 1]
for run_label, color, ls in [('BOL', C_REF, '-'), ('BAT_D050', C_ALPHA, '--'), ('BAT_D090', C_FIT, '-')]:
    dD_fc = run_results[run_label]['dD_fc']
    cum_dD = np.cumsum(dD_fc)
    ax.plot(t_hours, cum_dD, color=color, ls=ls, lw=1.3,
            label=f'{run_label} (sum={cum_dD[-1]:.4f})')
ax.set_xlabel('Time (hours)')
ax.set_ylabel('Cumulative FC damage')
ax.set_title('(d) FC Damage Trajectory (alpha1 path)', fontsize=11)
ax.grid(True, axis='y')
ax.legend(loc='upper left', bbox_to_anchor=(1.02, 1.0), borderaxespad=0, fontsize=8, framealpha=0.9)

fig.text(0.42, 0.01,
    f"Fig. M5 | AE: BAT degradation -> EMS re-dispatch -> FC/EL stress change -> damage delta. "
    f"Validation: {'PASS' if report['all_pass'] else 'FAIL'} | "
    f"M1-M3 sign: {'PASS' if report['m1_m3_positive'] else 'FAIL'}",
    ha='center', fontsize=8, style='italic', color='gray')

out_path = project_root / "07_results" / "figures" / "M5_counterfactual_AE.png"
fig.savefig(str(out_path), dpi=300, bbox_inches='tight')
plt.close(fig)
print(f"Figure saved: {out_path}")
print(f"File size: {out_path.stat().st_size / 1024:.0f} KB")
print(f"\n=== M5 Verification Summary ===")
for ae in ae_list:
    te_str = f"{ae.TE*100:.2f}%" if np.isfinite(ae.TE) else "NA"
    print(f"  {ae.path} {ae.run_label}: AE={ae.AE:+.6f}, TE={te_str}")
print(f"All finite: {report['all_finite']}, M1-M3 positive: {report['m1_m3_positive']}, All pass: {report['all_pass']}")
