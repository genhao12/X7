# -*- coding: utf-8 -*-
"""
M6 mediation_lite 图表生成 (出版级, 按M1/M2标准)
4个子图: (a)Shapley值柱状图 (b)16联盟热力图 (c)边际贡献矩阵 (d)效率性验证
"""
import sys, json, numpy as np
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from pathlib import Path

project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root / "06_src"))

plt.rcParams.update({'axes.unicode_minus': False, 
    'font.family': 'sans-serif', 'font.sans-serif': ['Microsoft YaHei', 'SimHei', 'Arial', 'DejaVu Sans'],
    'font.size': 11, 'axes.titlesize': 12, 'axes.labelsize': 11,
    'xtick.labelsize': 10, 'ytick.labelsize': 10, 'legend.fontsize': 9,
    'axes.linewidth': 0.8, 'lines.linewidth': 1.5, 'grid.alpha': 0.25,
    'grid.linewidth': 0.5, 'axes.spines.top': False, 'axes.spines.right': False,
})

C_C = '#3288BD'; C_E = '#66C2A5'; C_P = '#ABDDA4'; C_I = '#FDAE61'; C_REF = '#999999'

config_path = project_root / "05_config" / "mvp_c1_config.json"
with open(config_path, 'r', encoding='utf-8') as f:
    config = json.load(f)

from components.data_lite import load_data
from ems.ems_bf_lite import EMSConfig, rule_based_dispatch
from simulation.closed_loop_lite import apply_coupling, CoalitionState
from identification.mediation_lite import coalition_loop, compute_shapley, validate_shapley, MEDIATOR_GROUPS

load_csv = str(project_root / "02_data_processed" / "tradestreet_pipeline" /
               "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv")
data = load_data(load_csv, str(config_path), year=2017, window_days=7)
df = data['data']; pv_kw = df['P_pv_kW'].values; load_kw = df['P_load_kW'].values

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

D_state = {'bat': 0.50, 'fc': 0.0, 'el': 0.0}
np.random.seed(42)
commands, _ = rule_based_dispatch(load_kw, pv_kw, cfg, bat_p_max_kw=60.75, bat_capacity_kwh=121.5)

states = CoalitionState.all_16_states()
results_16 = {}
for coalition in states:
    result = apply_coupling(test_hp_func, commands, coalition, config, D_state=D_state)
    results_16[coalition.code] = result

ae_values = {code: results_16[code]['total_damage'] for code in results_16}
coalition_values = coalition_loop(ae_values)
shapley_values = {g: compute_shapley(coalition_values, g) for g in MEDIATOR_GROUPS}
report = validate_shapley(shapley_values)

# === 绘图 ===
fig, axes = plt.subplots(2, 2, figsize=(13, 9))
fig.subplots_adjust(left=0.08, right=0.78, top=0.90, bottom=0.08, wspace=0.28, hspace=0.32)
fig.suptitle("M6 Shapley Mediation Decomposition | 16 Coalitions | BAT D=0.50", fontsize=12, fontweight='bold', x=0.42)

# --- (a) Shapley值柱状图 ---
ax = axes[0, 0]
groups = ['C\n(Capacity)', 'E\n(Efficiency)', 'P\n(Power)', 'I\n(Inventory)']
sv = [shapley_values[g] for g in MEDIATOR_GROUPS]
colors_sv = [C_C if v >= 0 else C_I for v in sv]
bars = ax.bar(groups, sv, color=colors_sv, edgecolor='black', linewidth=0.4, width=0.6)
ax.axhline(0, color='black', lw=0.8)
ax.set_ylabel('Shapley value')
ax.set_title(f'(a) Shapley Values | M1-M3 sign: {"PASS" if report["m1_m3_sign_check"] else "FAIL"}', fontsize=11)
ax.grid(True, axis='y')
for bar, val in zip(bars, sv):
    y = val + max(abs(v) for v in sv)*0.05 if val >= 0 else val - max(abs(v) for v in sv)*0.08
    ax.text(bar.get_x() + bar.get_width()/2, y, f'{val:+.5f}',
            ha='center', va='bottom' if val >= 0 else 'top', fontsize=9, fontweight='bold')
legend_a = [Patch(facecolor=C_C, edgecolor='black', label='phi >= 0 (penalty removed)'),
            Patch(facecolor=C_I, edgecolor='black', label='phi < 0 (M4 anomaly: protection removed)')]
ax.legend(handles=legend_a, loc='upper left', bbox_to_anchor=(1.02, 1.0), borderaxespad=0, fontsize=8, framealpha=0.9)

# --- (b) 16联盟v(S)热力图 ---
ax = axes[0, 1]
v_arr = np.array([coalition_values[format(i, '04b')] for i in range(16)])
v_grid = v_arr.reshape(4, 4)  # 4x4 grid: rows=CE, cols=PI
im = ax.imshow(v_grid, cmap='RdYlGn_r', aspect='auto')
ax.set_xticks(range(4)); ax.set_xticklabels(['00', '01', '10', '11'], fontsize=9)
ax.set_yticks(range(4)); ax.set_yticklabels(['00', '01', '10', '11'], fontsize=9)
ax.set_xlabel('P-I code')
ax.set_ylabel('C-E code')
ax.set_title('(b) v(S) Heatmap (16 Coalitions)', fontsize=11)
for i in range(4):
    for j in range(4):
        code = format(i, '02b') + format(j, '02b')
        val = coalition_values[code]
        ax.text(j, i, f'{val:.4f}', ha='center', va='center', fontsize=7.5,
                color='white' if val > v_arr.mean() else 'black')
cb = fig.colorbar(im, ax=ax, label='v(S) total damage', fraction=0.046, pad=0.04)

# --- (c) 边际贡献矩阵 (每个组OFF的AE) ---
ax = axes[1, 0]
# 单组OFF的AE: v(M\{i}) - v(M)
v_M = coalition_values['1111']
off_codes = {'C': '0111', 'E': '1011', 'P': '1101', 'I': '1110'}
ae_single = {g: coalition_values[off_codes[g]] - v_M for g in MEDIATOR_GROUPS}
ae_vals = [ae_single[g] for g in MEDIATOR_GROUPS]
colors_ae = [C_C if v <= 0 else C_I for v in ae_vals]
bars = ax.bar(groups, ae_vals, color=colors_ae, edgecolor='black', linewidth=0.4, width=0.6)
ax.axhline(0, color='black', lw=0.8)
ax.set_ylabel('AE = v(M\\{i}) - v(M)')
ax.set_title('(c) Single Group OFF: AE', fontsize=11)
ax.grid(True, axis='y')
for bar, val in zip(bars, ae_vals):
    y = val + max(abs(v) for v in ae_vals)*0.05 if val >= 0 else val - max(abs(v) for v in ae_vals)*0.08
    ax.text(bar.get_x() + bar.get_width()/2, y, f'{val:+.5f}',
            ha='center', va='bottom' if val >= 0 else 'top', fontsize=9, fontweight='bold')
ax.legend(handles=legend_a, loc='upper left', bbox_to_anchor=(1.02, 1.0), borderaxespad=0, fontsize=8, framealpha=0.9)

# --- (d) 效率性验证 ---
ax = axes[1, 1]
v_empty = coalition_values['0000']
v_M = coalition_values['1111']
total_effect = v_M - v_empty
shapley_sum = sum(shapley_values.values())
categories = ['v(M)', 'v(empty)', 'v(M)-v(empty)', 'sum(phi)']
values = [v_M, v_empty, total_effect, shapley_sum]
colors_d = [C_C, C_I, '#66C2A5', '#ABDDA4']
bars = ax.bar(categories, values, color=colors_d, edgecolor='black', linewidth=0.4, width=0.6)
ax.axhline(0, color='black', lw=0.8)
ax.set_ylabel('Total damage')
ax.set_title(f'(d) Efficiency Check | phi_sum = v(M)-v(empty): {"PASS" if abs(shapley_sum - total_effect) < 1e-6 else "FAIL"}', fontsize=10)
ax.grid(True, axis='y')
for bar, val in zip(bars, values):
    y = val + max(abs(v) for v in values)*0.02 if val >= 0 else val - max(abs(v) for v in values)*0.05
    ax.text(bar.get_x() + bar.get_width()/2, y, f'{val:.6f}',
            ha='center', va='bottom' if val >= 0 else 'top', fontsize=8)

fig.text(0.42, 0.01,
    f"Fig. M6 | Shapley decomposition: phi(C)+phi(E)+phi(P)+phi(I) = v(M)-v(empty). "
    f"M4 sign: {report['m4_sign_check']} (physical: {'PASS' if report['m4_is_physical'] else 'FAIL'}). "
    f"All pass: {report['all_pass']}",
    ha='center', fontsize=8, style='italic', color='gray')

out_path = project_root / "07_results" / "figures" / "M6_shapley_mediation.png"
fig.savefig(str(out_path), dpi=300, bbox_inches='tight')
plt.close(fig)
print(f"Figure saved: {out_path}")
print(f"File size: {out_path.stat().st_size / 1024:.0f} KB")
print(f"\n=== M6 Verification Summary ===")
for g in MEDIATOR_GROUPS:
    print(f"  phi({g}) = {shapley_values[g]:+.6f}")
print(f"Sum(phi) = {shapley_sum:.6f}, v(M)-v(empty) = {total_effect:.6f}")
print(f"All pass: {report['all_pass']}")
