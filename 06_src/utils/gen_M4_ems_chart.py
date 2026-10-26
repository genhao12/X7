# -*- coding: utf-8 -*-
"""
M4 ems_bf_lite 图表生成 (出版级, 按M1/M2标准)
4个子图(非6个), 突出重点, 标签外部右上方, 不遮挡数据
(a) PV vs Load + 净负荷 — 核心图
(b) EMS调度时序 (BAT/FC/EL) — 核心图
(c) SOC + H2 轨迹 (双Y轴)
(d) 能量平衡汇总
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

from ems.ems_bf_lite import EMSConfig, rule_based_dispatch, check_constraints
from components.data_lite import load_data

# === 样式 (按M1/M2标准) ===
plt.rcParams.update({'axes.unicode_minus': False, 
    'font.family': 'sans-serif',
    'font.sans-serif': ['Microsoft YaHei', 'SimHei', 'Arial', 'DejaVu Sans'],
    'font.size': 11,
    'axes.titlesize': 12,
    'axes.labelsize': 11,
    'xtick.labelsize': 10,
    'ytick.labelsize': 10,
    'legend.fontsize': 9,
    'axes.linewidth': 0.8,
    'lines.linewidth': 1.5,
    'lines.markersize': 5,
    'grid.alpha': 0.25,
    'grid.linewidth': 0.5,
    'axes.spines.top': False,
    'axes.spines.right': False,
})

C_PV = '#FC8D62'
C_LOAD = '#66C2A5'
C_SURPLUS = '#ABDDA4'
C_DEFICIT = '#D73027'
C_BAT = '#3288BD'
C_FC = '#FDAE61'
C_EL = '#74ADD1'
C_REF = '#999999'
C_UNS = '#D73027'
C_CUR = '#FDAE61'

# === 1. 加载数据和配置 ===
config_path = project_root / "05_config" / "mvp_c1_config.json"
with open(config_path, 'r', encoding='utf-8') as f:
    c1 = json.load(f)

cap = c1['capacity']
ems_p = c1['ems_parameters']
bat_c = c1['battery_constraints']
h2_c = c1['hydrogen_constraints']
msl = c1['minimum_stable_load']
topo = c1['topology']

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

load_csv = str(project_root / "02_data_processed" / "tradestreet_pipeline" /
               "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv")
data = load_data(load_csv, str(config_path), year=2017, window_days=7)
df = data['data']
pv_kw = df['P_pv_kW'].values
load_kw = df['P_load_kW'].values
n = len(pv_kw)
t_hours = np.arange(n) * 0.25

# === 2. 运行EMS ===
commands, state = rule_based_dispatch(load_kw, pv_kw, cfg)
report = check_constraints(commands, state, cfg)

p_bat = commands[:, 0]
p_fc = commands[:, 1]
p_el = commands[:, 2]
p_uns = state['p_uns']
p_cur = state['p_cur']
soc = state['soc_trace']
h2 = state['h2_trace']

# === 3. 绘图: 4子图, 2x2 ===
fig, axes = plt.subplots(2, 2, figsize=(14, 9))
fig.subplots_adjust(left=0.08, right=0.78, top=0.90, bottom=0.08, wspace=0.28, hspace=0.32)
fig.suptitle(
    f"M4 battery_first EMS | W1 (2017-01-01~07, {n} steps) | "
    f"LPSP={report['lpsp']*100:.1f}%",
    fontsize=12, fontweight='bold', x=0.42
)

# --- (a) PV vs Load ---
ax = axes[0, 0]
ax.fill_between(t_hours, 0, pv_kw, alpha=0.65, color=C_PV)
ax.plot(t_hours, load_kw, color=C_LOAD, lw=1.3)
ax.fill_between(t_hours, load_kw, pv_kw, where=pv_kw >= load_kw,
                alpha=0.35, color=C_SURPLUS)
ax.fill_between(t_hours, load_kw, pv_kw, where=pv_kw < load_kw,
                alpha=0.35, color=C_DEFICIT)
ax.set_xlabel('Time (hours)')
ax.set_ylabel('Power (kW)')
ax.set_title('(a) PV vs Load', fontsize=11)
ax.grid(True, axis='y')
legend_elements_a = [
    Patch(facecolor=C_PV, alpha=0.65, label='PV output'),
    Patch(facecolor=C_LOAD, alpha=0.65, label='Building load'),
    Patch(facecolor=C_SURPLUS, alpha=0.35, label='PV surplus'),
    Patch(facecolor=C_DEFICIT, alpha=0.35, label='PV deficit'),
]
ax.legend(handles=legend_elements_a, loc='upper left', bbox_to_anchor=(1.02, 1.0),
          borderaxespad=0, fontsize=8, framealpha=0.9)

# --- (b) EMS调度时序 ---
ax = axes[0, 1]
ax.fill_between(t_hours, 0, np.maximum(p_bat, 0), alpha=0.6, color=C_BAT)
ax.fill_between(t_hours, 0, np.minimum(p_bat, 0), alpha=0.6, color='#313695')
ax.plot(t_hours, p_fc, color=C_FC, lw=1.0)
ax.plot(t_hours, p_el, color=C_EL, lw=1.0)
ax.axhline(0, color='black', lw=0.5)
ax.set_xlabel('Time (hours)')
ax.set_ylabel('Power (kW)')
ax.set_title('(b) EMS Dispatch Commands', fontsize=11)
ax.grid(True, axis='y')
legend_elements_b = [
    Patch(facecolor=C_BAT, alpha=0.6, label='BAT discharge'),
    Patch(facecolor='#313695', alpha=0.6, label='BAT charge'),
    Patch(facecolor=C_FC, alpha=0.6, label='FC power'),
    Patch(facecolor=C_EL, alpha=0.6, label='EL power'),
]
ax.legend(handles=legend_elements_b, loc='upper left', bbox_to_anchor=(1.02, 1.0),
          borderaxespad=0, fontsize=8, framealpha=0.9)

# --- (c) SOC + H2 双Y轴 ---
ax = axes[1, 0]
ax.plot(t_hours, soc, color=C_BAT, lw=1.3, label='SOC')
ax.axhline(cfg.soc_max, color=C_REF, ls='--', lw=0.8)
ax.axhline(cfg.soc_min, color=C_REF, ls='--', lw=0.8)
ax.fill_between(t_hours, cfg.soc_min, cfg.soc_max, alpha=0.08, color='green')
ax.set_xlabel('Time (hours)')
ax.set_ylabel('SOC', color=C_BAT)
ax.set_ylim(0, 1)
ax.tick_params(axis='y', labelcolor=C_BAT)
ax.grid(True, axis='y')

ax2 = ax.twinx()
ax2.plot(t_hours, h2, color=C_FC, lw=1.3, label='H2 mass')
ax2.axhline(cfg.h2_kg * cfg.h2_max_fraction, color=C_REF, ls=':', lw=0.8)
ax2.axhline(cfg.h2_kg * cfg.h2_min_fraction, color=C_REF, ls=':', lw=0.8)
ax2.set_ylabel('H2 mass (kg)', color=C_FC)
ax2.tick_params(axis='y', labelcolor=C_FC)
ax2.spines['right'].set_visible(True)

ax.set_title(f'(c) SOC & H2 Trace | bounds: {"PASS" if report["soc_bounds_pass"] and report["h2_bounds_pass"] else "FAIL"}', fontsize=11)
# 合并图例外部右上方
lines1, labels1 = ax.get_legend_handles_labels()
lines2, labels2 = ax2.get_legend_handles_labels()
legend_elements_c = [
    Patch(facecolor=C_BAT, alpha=0.6, label='SOC'),
    Patch(facecolor=C_FC, alpha=0.6, label='H2 mass'),
    Patch(facecolor='green', alpha=0.2, label='Allowed range'),
]
ax.legend(handles=legend_elements_c, loc='upper left', bbox_to_anchor=(1.02, 1.0),
          borderaxespad=0, fontsize=8, framealpha=0.9)

# --- (d) 能量平衡 ---
ax = axes[1, 1]
categories = ['PV\nsupply', 'FC\ngen', 'BAT\ndis', 'Load\ndemand', 'EL\ncons', 'BAT\nch', 'Curtail', 'Unserved']
values = [np.sum(pv_kw), np.sum(p_fc), np.sum(state['p_bat_dis']),
          np.sum(load_kw), np.sum(p_el), np.sum(state['p_bat_ch']),
          np.sum(p_cur), np.sum(p_uns)]
colors_bar = [C_PV, C_FC, C_BAT, C_LOAD, C_EL, '#313695', '#ABDDA4', C_UNS]
bars = ax.bar(categories, values, color=colors_bar, edgecolor='black', linewidth=0.4, width=0.65)
ax.set_ylabel('Energy (kWh)')
ax.set_title(f'(d) Energy Balance | residual={report["power_residual"]:.1e}kW', fontsize=11)
ax.grid(True, axis='y')
for bar, val in zip(bars, values):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + max(values)*0.01,
            f'{val:.0f}', ha='center', va='bottom', fontsize=7.5)

fig.text(0.42, 0.01,
    f"Fig. M4 | battery_first: PV->BAT(charge)->EL | PV->BAT(discharge)->FC->Unserved | "
    f"Power={'PASS' if report['power_conservation_pass'] else 'FAIL'}, "
    f"H2={'PASS' if report['h2_conservation_pass'] else 'FAIL'}, "
    f"MinStable={'PASS' if report['min_stable_load_pass'] else 'FAIL'}",
    ha='center', fontsize=8, style='italic', color='gray')

out_path = project_root / "07_results" / "figures" / "M4_ems_bf_dispatch.png"
fig.savefig(str(out_path), dpi=300, bbox_inches='tight')
plt.close(fig)
print(f"Figure saved: {out_path}")
print(f"File size: {out_path.stat().st_size / 1024:.0f} KB")
