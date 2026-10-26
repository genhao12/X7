#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
M3+M4 Integrated Publication Figure
====================================
Generates a 6-panel composite figure combining L4 EMS (M3) and
Economic Evaluation (M4) key results for methodology paper.

Panels:
  (a) M2 vs M3 annual damage comparison (bar chart)
  (b) L4 parameter sensitivity (R3 fine sweep)
  (c) M4 cost structure decomposition (pie/bar)
  (d) Discrete vs Continuous NPC (bar)
  (e) CAPEX sensitivity (twin axis line)
  (f) Discount rate sensitivity (twin axis line)

Output: 07_results/figures/paper_figure4_l4_economic.png
"""
import sys, csv, json
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "06_src"))

RESULTS = PROJECT_ROOT / "07_results"
L4_DIR = RESULTS / "l4_comparison"
ECON_DIR = RESULTS / "economic"
FIG_DIR = RESULTS / "figures"

# Publication style
plt.rcParams.update({
    'font.family': 'sans-serif',
    'font.sans-serif': ['Microsoft YaHei', 'SimHei', 'Arial', 'DejaVu Sans'],
    'font.size': 8,
    'axes.titlesize': 9,
    'axes.labelsize': 8,
    'xtick.labelsize': 7,
    'ytick.labelsize': 7,
    'legend.fontsize': 7,
    'figure.dpi': 300,
    'savefig.dpi': 300,
    'savefig.bbox': 'standard',
    'savefig.pad_inches': 0.05,
    'axes.linewidth': 0.6,
    'axes.grid': True,
    'grid.alpha': 0.3,
    'grid.linewidth': 0.4,
    'lines.linewidth': 1.2,
    'lines.markersize': 4,
    'axes.unicode_minus': False,
})

# Colors
C_M2 = '#4472C4'   # Blue for M2
C_M3 = '#ED7D31'   # Orange for M3
C_BAT = '#70AD47'  # Green for BAT
C_FC = '#FFC000'   # Gold for FC
C_EL = '#E6B89C'   # Tan for EL
C_CAPEX = '#4472C4'
C_OPEX = '#70AD47'
C_REPL = '#ED7D31'
C_NPC = '#4472C4'
C_LCOE = '#ED7D31'

# ============================================================
# Load data
# ============================================================

# P1 annual damage
p1_path = RESULTS / "annual" / "annual_damage_summary.csv"
with open(p1_path, 'r') as f:
    reader = csv.DictReader(f)
    p1_rows = [r for r in reader if r.get('Week', '').isdigit()]

m2_bat = sum(float(r['FB_dD_bat']) for r in p1_rows)
m2_fc = sum(float(r['FB_dD_fc']) for r in p1_rows)
m2_el = sum(float(r['FB_dD_el']) for r in p1_rows)
m2_total = m2_bat + m2_fc + m2_el
bol_bat = sum(float(r['BOL_dD_bat']) for r in p1_rows)
bol_fc = sum(float(r['BOL_dD_fc']) for r in p1_rows)
bol_el = sum(float(r['BOL_dD_el']) for r in p1_rows)
bol_total = bol_bat + bol_fc + bol_el

# M3 from L4 final validation
m3_bat = 0.91637570
m3_fc = 1.38597855
m3_el = 1.80443033
m3_total = m3_bat + m3_fc + m3_el

# M4 from economic_comparison.csv
econ_path = ECON_DIR / "economic_comparison.csv"
with open(econ_path, 'r', encoding='utf-8') as f:
    econ_lines = f.read().strip().split('\n')

# Parse M2 discrete NPC/LCOE and cost structure
m2_npc = m3_npc = m2_lcoe = m3_lcoe = None
m2_capex_pv = m2_opex_pv = m2_repl_pv = None
m2_cont_npc = m3_cont_npc = None
capex_sens_npcs = []
capex_sens_lcoes = []
disc_sens_npcs = []
disc_sens_lcoes = []

in_discrete = in_continuous = in_capex = in_disc = False
for line in econ_lines:
    if line.startswith('=== 离散法'):
        in_discrete, in_continuous, in_capex, in_disc = True, False, False, False
    elif line.startswith('=== 连续法'):
        in_discrete, in_continuous, in_capex, in_disc = False, True, False, False
    elif line.startswith('=== CAPEX敏感'):
        in_discrete, in_continuous, in_capex, in_disc = False, False, True, False
    elif line.startswith('=== 贴现率敏感'):
        in_discrete, in_continuous, in_capex, in_disc = False, False, False, True
    elif line.startswith('==='):
        in_discrete = in_continuous = in_capex = in_disc = False

    parts = line.split(',')
    if in_discrete and len(parts) >= 7:
        if parts[0] == 'M2':
            m2_npc = float(parts[1]); m2_lcoe = float(parts[3])
            m2_capex_pv = float(parts[4]); m2_opex_pv = float(parts[5]); m2_repl_pv = float(parts[6])
        elif parts[0] == 'M3':
            m3_npc = float(parts[1]); m3_lcoe = float(parts[3])
    elif in_continuous and len(parts) >= 7:
        if parts[0] == 'M2':
            m2_cont_npc = float(parts[1])
        elif parts[0] == 'M3':
            m3_cont_npc = float(parts[1])
    elif in_capex and len(parts) >= 5:
        try:
            val = float(parts[1])
            capex_sens_npcs.append(val)
            capex_sens_lcoes.append(float(parts[3]))
        except (ValueError, TypeError):
            pass
    elif in_disc and len(parts) >= 5:
        try:
            val = float(parts[1])
            disc_sens_npcs.append(val)
            disc_sens_lcoes.append(float(parts[3]))
        except (ValueError, TypeError):
            pass

# L4 fine sweep data (CSV 含 === 分段标题, 需定位 el_k 表头行)
fine_sweep_path = L4_DIR / "l4_fine_sweep.csv"
fine_el_k = []
fine_te = []
if fine_sweep_path.exists():
    fs_lines = fine_sweep_path.read_text(encoding='utf-8').splitlines()
    header_idx = None
    for i, ln in enumerate(fs_lines):
        if ln.strip().startswith('el_k'):
            header_idx = i
            break
    if header_idx is not None:
        cols = [c.strip() for c in fs_lines[header_idx].split(',')]
        try:
            ki = cols.index('el_k'); ti = cols.index('te_pct')
            for ln in fs_lines[header_idx + 1:]:
                parts = [p.strip() for p in ln.split(',')]
                if len(parts) <= max(ki, ti):
                    continue
                try:
                    fine_el_k.append(float(parts[ki]))
                    fine_te.append(float(parts[ti]))
                except ValueError:
                    pass
        except ValueError:
            pass
if not fine_el_k:
    print("  [WARN] fine_el_k empty: l4_fine_sweep.csv el_k 表头未找到或无数据行")

# ============================================================
# Create 6-panel figure
# ============================================================
fig, axes = plt.subplots(2, 3, figsize=(10.5, 7.0))
fig.subplots_adjust(hspace=0.35, wspace=0.40)

# --- (a) M2 vs M3 annual damage comparison ---
ax = axes[0, 0]
devices = ['BAT', 'PEMFC', 'PEMEL', 'Total']
m2_vals = [m2_bat, m2_fc, m2_el, m2_total]
m3_vals = [m3_bat, m3_fc, m3_el, m3_total]
x = np.arange(len(devices))
w = 0.3
bars1 = ax.bar(x - w/2, m2_vals, w, label='M2 (Baseline)', color=C_M2, edgecolor='black', linewidth=0.3)
bars2 = ax.bar(x + w/2, m3_vals, w, label='M3 (L4 EMS)', color=C_M3, edgecolor='black', linewidth=0.3)
ax.set_xticks(x)
ax.set_xticklabels(devices, fontsize=7)
ax.set_ylabel('Annual Damage (D/yr)')
ax.set_title('(a) M2 vs M3 Annual Damage', fontweight='bold')
ax.legend(framealpha=0.9, loc='upper left')
ax.set_ylim(0, max(m2_vals) * 1.2)
for bars in [bars1, bars2]:
    for bar in bars:
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.05,
                f'{bar.get_height():.2f}', ha='center', va='bottom', fontsize=6)

# --- (b) L4 R3 parameter sensitivity ---
ax = axes[0, 1]
if fine_el_k and fine_te:
    ax.semilogx(fine_el_k, fine_te, 'o-', color=C_M3, linewidth=1.5, markersize=5)
    ax.axhline(y=6.59, color=C_M2, linestyle='--', linewidth=1, alpha=0.7, label='M2 TE=+6.59%')
    ax.axhline(y=0, color='gray', linestyle=':', linewidth=0.8, alpha=0.5)
    ax.fill_between(fine_el_k, 0, fine_te, alpha=0.1, color=C_M3)
    ax.set_xlabel('$R_3$ Parameter ($k_{el}$)')
    ax.set_ylabel('Total Effect TE (%)')
    ax.set_title('(b) L4 $R_3$ Sensitivity (EL Ramp)', fontweight='bold')
    ax.legend(framealpha=0.9, loc='upper right')
    # Annotate optimal
    opt_idx = np.argmin(np.abs(np.array(fine_te)))
    ax.annotate(f'Optimal\n$k_{{el}}$={fine_el_k[opt_idx]:.0f}\nTE={fine_te[opt_idx]:.1f}%',
                xy=(fine_el_k[opt_idx], fine_te[opt_idx]),
                xytext=(0.15, 0.65), textcoords='axes fraction',
                fontsize=6, arrowprops=dict(arrowstyle='->', color='black', lw=0.5),
                bbox=dict(boxstyle='round,pad=0.2', facecolor='white', edgecolor='gray', alpha=0.9))

# --- (c) M4 cost structure decomposition ---
ax = axes[0, 2]
cats = ['CAPEX', 'OPEX', 'BAT Repl.', 'FC Repl.', 'EL Repl.', 'PE Repl.']
# Compute individual replacement PVs
r_rate = 0.06
# From M4: BAT=18×293625, FC=27×435000, EL=36×406000, PE=1×354525
bat_repl_pv = sum(293625 / (1.06)**yr for yr in
                  [2,3,4,5,6,7,8,9,10,11,13,14,15,16,17,18,19,20])
fc_repl_pv = sum(435000 / (1.06)**yr for yr in
                 [1,2,3,3,4,5,6,6,7,8,8,9,10,11,11,12,13,13,14,15,16,16,17,18,19,19,20])
el_repl_pv = sum(406000 / (1.06)**yr for yr in
                 [1,2,2,3,3,4,4,5,5,6,7,7,8,8,9,9,10,10,11,12,12,13,13,14,14,15,15,16,17,17,18,18,19,19,20,20])
pe_repl_pv = 354525 / (1.06)**15
vals = [m2_capex_pv/1e6, m2_opex_pv/1e6, bat_repl_pv/1e6, fc_repl_pv/1e6, el_repl_pv/1e6, pe_repl_pv/1e6]
colors = [C_CAPEX, C_OPEX, C_BAT, C_FC, C_EL, '#A5A5A5']
bars = ax.barh(cats, vals, color=colors, edgecolor='black', linewidth=0.3)
ax.set_xlabel('Present Value (M CNY)')
ax.set_title('(c) M4 Cost Structure (M2, 20yr)', fontweight='bold')
ax.invert_yaxis()
for bar, val in zip(bars, vals):
    ax.text(val + 0.2, bar.get_y() + bar.get_height()/2,
            f'{val:.2f}', va='center', fontsize=6)
total_pv = sum(vals)
ax.text(0.95, 0.05, f'NPC={total_pv:.1f}M', transform=ax.transAxes,
        fontsize=7, ha='right', va='bottom', fontweight='bold',
        bbox=dict(facecolor='white', edgecolor='gray', alpha=0.8))

# --- (d) Discrete vs Continuous NPC ---
ax = axes[1, 0]
methods = ['Discrete\nM2', 'Discrete\nM3', 'Continuous\nM2', 'Continuous\nM3']
npcs = [m2_npc/1e6, m3_npc/1e6, m2_cont_npc/1e6, m3_cont_npc/1e6]
colors_d = [C_M2, C_M3, C_M2, C_M3]
hatches = ['', '', '//', '//']
bars = ax.bar(methods, npcs, color=colors_d, edgecolor='black', linewidth=0.3, width=0.6)
for bar, hatch in zip(bars, hatches):
    bar.set_hatch(hatch)
ax.set_ylabel('NPC (M CNY)')
ax.set_title('(d) Discrete vs Continuous NPC', fontweight='bold')
ax.set_ylim(0, max(npcs) * 1.15)
for bar, npc in zip(bars, npcs):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.3,
            f'{npc:.2f}', ha='center', va='bottom', fontsize=6)
# Annotate savings
if m2_cont_npc and m3_cont_npc:
    savings = m2_cont_npc - m3_cont_npc
    ax.annotate(f'M3 saves\n{savings/1e3:.1f}K CNY',
                xy=(3, m3_cont_npc/1e6),
                xytext=(0.7, 0.3), textcoords='axes fraction',
                fontsize=6, arrowprops=dict(arrowstyle='->', color='black', lw=0.5),
                bbox=dict(boxstyle='round,pad=0.2', facecolor='lightyellow', edgecolor='gray', alpha=0.9))

# --- (e) CAPEX sensitivity ---
ax = axes[1, 1]
capex_vars = [-30, -15, 0, 15, 30]
ax2 = ax.twinx()
l1, = ax.plot(capex_vars, [n/1e6 for n in capex_sens_npcs], 'o-', color=C_NPC, lw=1.5, ms=5, label='NPC')
l2, = ax2.plot(capex_vars, capex_sens_lcoes, 's--', color=C_LCOE, lw=1.5, ms=5, label='LCOE')
ax.axvline(x=0, color='gray', ls=':', lw=0.8, alpha=0.5)
ax.set_xlabel('CAPEX Variation (%)')
ax.set_ylabel('NPC (M CNY)', color=C_NPC)
ax2.set_ylabel('LCOE (CNY/kWh)', color=C_LCOE)
ax.set_title('(e) CAPEX Sensitivity', fontweight='bold')
ax.legend([l1, l2], ['NPC', 'LCOE'], loc='upper left', framealpha=0.9)

# --- (f) Discount rate sensitivity ---
ax = axes[1, 2]
disc_vars = [4, 5, 6, 8, 10]
ax2 = ax.twinx()
l1, = ax.plot(disc_vars, [n/1e6 for n in disc_sens_npcs], 'o-', color=C_NPC, lw=1.5, ms=5, label='NPC')
l2, = ax2.plot(disc_vars, disc_sens_lcoes, 's--', color=C_LCOE, lw=1.5, ms=5, label='LCOE')
ax.axvline(x=6, color='gray', ls=':', lw=0.8, alpha=0.5)
ax.set_xlabel('Discount Rate (%)')
ax.set_ylabel('NPC (M CNY)', color=C_NPC)
ax2.set_ylabel('LCOE (CNY/kWh)', color=C_LCOE)
ax.set_title('(f) Discount Rate Sensitivity', fontweight='bold')
ax.legend([l1, l2], ['NPC', 'LCOE'], loc='upper right', framealpha=0.9)

# ============================================================
# Save
# ============================================================
fig.subplots_adjust(left=0.07, right=0.93, top=0.94, bottom=0.07, hspace=0.35, wspace=0.40)
fig.suptitle('Figure 4: L4 Coupling-Aware EMS (M3) & 20-Year Economic Evaluation (M4)',
             fontsize=11, fontweight='bold', y=0.98)

fig_path = FIG_DIR / "paper_figure4_l4_economic.png"
plt.savefig(fig_path)
plt.close()
print(f"Saved: {fig_path}")
print(f"Size: {fig_path.stat().st_size / 1024:.0f} KB")
