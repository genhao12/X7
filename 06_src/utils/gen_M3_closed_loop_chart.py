# -*- coding: utf-8 -*-
"""
M3 closed_loop_lite 图表生成 (出版级, 按M1/M2标准)
4个子图(非6个), 突出重点, 标签外部右上方, 不遮挡数据
(a) 16联盟状态总损伤柱状图 — 核心图
(b) 4归因组AE方向 — 核心图
(c) 同向/异向超可加性对比
(d) 设备损伤分解 (全ON vs I_OFF)
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

from simulation.closed_loop_lite import (
    CoalitionState, compute_damage, apply_coupling, check_superadditivity
)

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

C_DATA = '#3288BD'
C_FIT = '#D73027'
C_REF = '#999999'
C_PASS = '#1A9850'
C_FAIL = '#D73027'
C_M4 = '#FDAE61'

# === 1. 构建数据 ===
def test_hp_func(D):
    D = float(np.clip(D, 0.0, 1.0))
    q_frac = 1.0 - 0.2 * D
    r0_ohm = 0.000427 * (1 + 0.1673 * D)
    p_max_kw = 67.5 * q_frac
    return (q_frac, r0_ohm, p_max_kw)

config_path = project_root / "05_config" / "mvp_c1_config.json"
with open(config_path, 'r', encoding='utf-8') as f:
    config = json.load(f)

np.random.seed(42)
n_steps = 672
p_bat = 20 * np.sin(2 * np.pi * np.arange(n_steps) / 96) + np.random.normal(0, 5, n_steps)
fc_on = (np.arange(n_steps) % 192) < 96
p_fc = np.where(fc_on, 37.5 * (0.5 + 0.3 * np.random.rand(n_steps)), 0.0)
el_on = (np.arange(n_steps) % 192) >= 96
p_el = np.where(el_on, 35.0 * (0.5 + 0.3 * np.random.rand(n_steps)), 0.0)
ems_commands = np.column_stack([p_bat, p_fc, p_el])
D_state = {'bat': 0.5, 'fc': 0.3, 'el': 0.3}

states = CoalitionState.all_16_states()
results_16 = {}
for coalition in states:
    result = apply_coupling(test_hp_func, ems_commands, coalition, config, D_state=D_state)
    results_16[coalition.code] = result

codes = [s.code for s in states]
totals = [results_16[c]['total_damage'] for c in codes]
v_M = results_16['1111']['total_damage']
ae_C = results_16['0111']['total_damage'] - v_M
ae_E = results_16['1011']['total_damage'] - v_M
ae_P = results_16['1101']['total_damage'] - v_M
ae_I = results_16['1110']['total_damage'] - v_M
report = check_superadditivity(results_16)

# === 2. 绘图: 4子图, 2x2, 大图幅 ===
fig, axes = plt.subplots(2, 2, figsize=(13, 9))
fig.subplots_adjust(left=0.08, right=0.78, top=0.90, bottom=0.08, wspace=0.30, hspace=0.35)
fig.suptitle(
    f"M3 Closed-Loop Degradation Coupling | 16 Coalition States | D=(bat0.5, fc0.3, el0.3)",
    fontsize=12, fontweight='bold', x=0.42
)

# --- (a) 16联盟状态总损伤 ---
ax = axes[0, 0]
colors_bar = []
for c in codes:
    n_off = 4 - c.count('1')
    if n_off == 0:
        colors_bar.append(C_PASS)
    elif n_off == 1:
        colors_bar.append('#FDAE61')
    elif n_off == 2:
        colors_bar.append('#F46D43')
    else:
        colors_bar.append(C_FAIL)
bars = ax.bar(range(16), totals, color=colors_bar, edgecolor='black', linewidth=0.4, width=0.7)
ax.axhline(v_M, color=C_REF, ls='--', lw=1.0)
ax.text(15.5, v_M, f'v(M)={v_M:.4f}', ha='right', va='bottom', fontsize=8, color=C_REF)
ax.set_xticks(range(16))
ax.set_xticklabels(codes, fontsize=7, rotation=45, ha='right')
ax.set_xlabel('Coalition code (C-E-P-I)')
ax.set_ylabel('Total damage (sum dD)')
ax.set_title('(a) 16 Coalition States Total Damage', fontsize=11)
ax.grid(True, axis='y')
# 图例外部右上方
legend_elements_a = [
    Patch(facecolor=C_PASS, edgecolor='black', label='M=1111 (all ON)'),
    Patch(facecolor='#FDAE61', edgecolor='black', label='1 group OFF'),
    Patch(facecolor='#F46D43', edgecolor='black', label='2 groups OFF'),
    Patch(facecolor=C_FAIL, edgecolor='black', label='3+ groups OFF'),
]
ax.legend(handles=legend_elements_a, loc='upper left', bbox_to_anchor=(1.02, 1.0),
          borderaxespad=0, fontsize=8, framealpha=0.9)

# --- (b) 4归因组AE ---
ax = axes[0, 1]
groups = ['C\n(Capacity)', 'E\n(Efficiency)', 'P\n(Power)', 'I\n(Inventory)']
ae_vals = [ae_C, ae_E, ae_P, ae_I]
ae_colors = [C_PASS if v <= 0 else C_M4 for v in ae_vals]
bars = ax.bar(groups, ae_vals, color=ae_colors, edgecolor='black', linewidth=0.4, width=0.6)
ax.axhline(0, color='black', lw=0.8)
ax.set_ylabel('AE = v(M\\{i}) - v(M)')
ax.set_title(f'(b) Mediator Group AE | M4 anomaly sign: {report["m4_ae_sign"]}', fontsize=11)
ax.grid(True, axis='y')
for bar, val in zip(bars, ae_vals):
    y = val + 0.0003 if val >= 0 else val - 0.0005
    ax.text(bar.get_x() + bar.get_width()/2, y, f'{val:+.4f}',
            ha='center', va='bottom' if val >= 0 else 'top', fontsize=9, fontweight='bold')
legend_elements_b = [
    Patch(facecolor=C_PASS, edgecolor='black', label='AE<=0 (penalty removed)'),
    Patch(facecolor=C_M4, edgecolor='black', label='AE>0 (M4 anomaly: protection removed)'),
]
ax.legend(handles=legend_elements_b, loc='upper left', bbox_to_anchor=(1.02, 1.0),
          borderaxespad=0, fontsize=8, framealpha=0.9)

# --- (c) 同向/异向超可加性 ---
ax = axes[1, 0]
pairs_labels = ['C+E', 'C+P', 'E+P', 'C+I', 'E+I', 'P+I']
pair_codes = ['0011', '0101', '1001', '0110', '1010', '1100']
single_max = [
    max(results_16['0111']['total_damage'], results_16['1011']['total_damage']),
    max(results_16['0111']['total_damage'], results_16['1101']['total_damage']),
    max(results_16['1011']['total_damage'], results_16['1101']['total_damage']),
    max(results_16['0111']['total_damage'], results_16['1110']['total_damage']),
    max(results_16['1011']['total_damage'], results_16['1110']['total_damage']),
    max(results_16['1101']['total_damage'], results_16['1110']['total_damage']),
]
pair_v = [results_16[c]['total_damage'] for c in pair_codes]
x = np.arange(len(pairs_labels))
w = 0.35
bars1 = ax.bar(x - w/2, single_max, w, label='max(v(M\\i), v(M\\j))',
               color='#ABDDA4', edgecolor='black', linewidth=0.4)
bars2 = ax.bar(x + w/2, pair_v, w, label='v(M\\{i,j})',
               color=C_DATA, edgecolor='black', linewidth=0.4)
ax.set_xticks(x)
ax.set_xticklabels(pairs_labels, fontsize=9)
ax.set_ylabel('Total damage')
ax.set_title(f'(c) Superadditivity Check | Same-dir={"PASS" if report["same_direction_pass"] else "FAIL"}, '
             f'Cross-dir={"PASS" if report["cross_direction_pass"] else "FAIL"}', fontsize=10)
ax.grid(True, axis='y')
# 分隔线
ax.axvline(2.5, color=C_REF, ls=':', lw=0.8)
ax.text(1.0, ax.get_ylim()[1]*0.95, 'Same-direction', ha='center', fontsize=8, color=C_REF, style='italic')
ax.text(4.0, ax.get_ylim()[1]*0.95, 'Cross-direction (M4)', ha='center', fontsize=8, color=C_REF, style='italic')
ax.legend(loc='upper left', bbox_to_anchor=(1.02, 1.0),
          borderaxespad=0, fontsize=8, framealpha=0.9)

# --- (d) 设备损伤分解 ---
ax = axes[1, 1]
devices = ['BAT', 'FC', 'EL']
dmg_full = [np.sum(results_16['1111']['dD_bat']),
            np.sum(results_16['1111']['dD_fc']),
            np.sum(results_16['1111']['dD_el'])]
dmg_i_off = [np.sum(results_16['1110']['dD_bat']),
             np.sum(results_16['1110']['dD_fc']),
             np.sum(results_16['1110']['dD_el'])]
x = np.arange(len(devices))
w = 0.35
bars1 = ax.bar(x - w/2, dmg_full, w, label='Full (M=1111)',
               color='#66C2A5', edgecolor='black', linewidth=0.4)
bars2 = ax.bar(x + w/2, dmg_i_off, w, label='I_OFF (M\\{I})',
               color='#FC8D62', edgecolor='black', linewidth=0.4)
ax.set_xticks(x)
ax.set_xticklabels(devices, fontsize=11)
ax.set_ylabel('Device damage (sum dD)')
ax.set_title('(d) Device Damage: Full vs I_OFF', fontsize=11)
ax.grid(True, axis='y')
for bar, val in zip(bars1, dmg_full):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.001,
            f'{val:.4f}', ha='center', va='bottom', fontsize=8)
for bar, val in zip(bars2, dmg_i_off):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.001,
            f'{val:.4f}', ha='center', va='bottom', fontsize=8)
ax.legend(loc='upper left', bbox_to_anchor=(1.02, 1.0),
          borderaxespad=0, fontsize=8, framealpha=0.9)

# Caption
fig.text(0.42, 0.01,
    f"Fig. M3 | C/E/P OFF removes degradation penalty (AE<=0); "
    f"I OFF removes EMS protection (AE>=0, M4 anomaly). "
    f"Same-dir: {'PASS' if report['same_direction_pass'] else 'FAIL'}, "
    f"Cross-dir: {'PASS' if report['cross_direction_pass'] else 'FAIL'}",
    ha='center', fontsize=8, style='italic', color='gray')

out_path = project_root / "07_results" / "figures" / "M3_closed_loop_coalition.png"
fig.savefig(str(out_path), dpi=300, bbox_inches='tight')
plt.close(fig)
print(f"Figure saved: {out_path}")
print(f"File size: {out_path.stat().st_size / 1024:.0f} KB")
