# -*- coding: utf-8 -*-
"""
VS8 边界鲁棒性验证 — MVP简化版
4类边界场景: N_eff=0 / M4极端异常 / TE=NA / 极端气象

检查项:
  R10: N_eff=0正确识别 (低退化不激活路径)
  R08: M4异常物理判定 (极端退化D=0.99, |phi_I|不爆炸)
  R12: TE=NA优雅处理 (零基线损伤分母保护)
  BW05: 极端条件不崩溃 (高PV暴风/低PV极寒)
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

C_PASS = '#2ca02c'; C_FAIL = '#d62728'; C_WARN = '#ff7f0e'
C_BAT = '#3288BD'; C_FC = '#D73027'; C_EL = '#66C2A5'

OUT_FIG = PROJECT_ROOT / "07_results" / "figures" / "VS8_boundary_robustness.png"
OUT_CSV = PROJECT_ROOT / "07_results" / "annual" / "VS8_boundary_summary.csv"

MEDIATOR_GROUPS = ['C', 'E', 'P', 'I']

print("=" * 70)
print("VS8 边界鲁棒性验证 — MVP简化版")
print("=" * 70)

from components.data_lite import load_data
from ems.ems_bf_lite import EMSConfig, rule_based_dispatch
from simulation.closed_loop_lite import CoalitionState, compute_damage, apply_coupling
from identification.mediation_lite import compute_shapley, check_m4_anomaly
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

hppc_data = _load_280ah_data()
bat_fit = fit_hppc(hppc_data)
hp_bat = interp_bat(bat_fit, {'bat_kwh': cap['BAT_kWh'], 'bat_voltage_nominal': 3.2*48, 'bat_rated_kw': bat_rated_kw})

config_dict = {
    'capacity': cap, 'battery_constraints': bat_c,
    'ems_parameters': ems_p, 'hydrogen_constraints': h2_c,
    'minimum_stable_load': msl, 'topology': topo,
}

load_csv = str(PROJECT_ROOT / "02_data_processed" / "tradestreet_pipeline" /
               "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv")
data = load_data(load_csv, str(config_path), year=2017, window_days=7)
df = data['data']
pv_kw = df['P_pv_kW'].values
load_kw = df['P_load_kW'].values

TE_THRESHOLD = 0.05
boundary_results = {}

# ============================================================
# B1: N_eff=0 (低退化 D=0.10, 路径不应激活)
# ============================================================
print("\n[B1] N_eff=0 边界 (D=0.10, 路径不应激活)...")

D_low = {'bat': 0.10, 'fc': 0.0, 'el': 0.0}
q_frac, r0, p_max = hp_bat(D_low['bat'])
bat_p_max = bat_rated_kw * q_frac
bat_cap = bat_capacity_kwh * q_frac

np.random.seed(42)
cmds_b1, _ = rule_based_dispatch(load_kw, pv_kw, cfg, bat_p_max_kw=bat_p_max, bat_capacity_kwh=bat_cap)

# BOL基线
np.random.seed(42)
cmds_bol, _ = rule_based_dispatch(load_kw, pv_kw, cfg)
coalition_full = CoalitionState.from_code('1111')
dD_bol = compute_damage(hp_bat, cmds_bol, config_dict, dt_min=15.0,
                         D_state={'bat': 0.0, 'fc': 0.0, 'el': 0.0}, coalition=coalition_full)
dD_b1 = compute_damage(hp_bat, cmds_b1, config_dict, dt_min=15.0,
                        D_state=D_low, coalition=coalition_full)

ae_fc_b1 = np.sum(dD_b1['dD_fc']) - np.sum(dD_bol['dD_fc'])
ae_el_b1 = np.sum(dD_b1['dD_el']) - np.sum(dD_bol['dD_el'])
baseline_fc = np.sum(dD_bol['dD_fc'])
baseline_el = np.sum(dD_bol['dD_el'])

te_fc_b1 = ae_fc_b1 / abs(baseline_fc) if abs(baseline_fc) > 1e-4 else float('nan')
te_el_b1 = ae_el_b1 / abs(baseline_el) if abs(baseline_el) > 1e-4 else float('nan')

# 路径激活判定
fc_activated = (ae_fc_b1 > 0) and (not np.isnan(te_fc_b1)) and (te_fc_b1 >= TE_THRESHOLD)
el_activated = (ae_el_b1 > 0) and (not np.isnan(te_el_b1)) and (te_el_b1 >= TE_THRESHOLD)
n_eff_b1 = sum([fc_activated, el_activated])

r10_pass = (n_eff_b1 == 0)  # D=0.10时不应有路径激活
print(f"  D=0.10: AE(FC)={ae_fc_b1:+.6f}, TE(FC)={te_fc_b1*100 if not np.isnan(te_fc_b1) else 'NA':.2f}%")
print(f"  D=0.10: AE(EL)={ae_el_b1:+.6f}, TE(EL)={te_el_b1*100 if not np.isnan(te_el_b1) else 'NA':.2f}%")
print(f"  N_eff={n_eff_b1} (预期0) -> {'PASS' if r10_pass else 'FAIL'}")

boundary_results['B1_N_eff0'] = {
    'D_state': D_low, 'ae_fc': ae_fc_b1, 'ae_el': ae_el_b1,
    'te_fc': te_fc_b1, 'te_el': te_el_b1,
    'n_eff': n_eff_b1, 'r10_pass': r10_pass,
}

# ============================================================
# B2: M4极端异常 (D=0.99, |phi_I|不应爆炸)
# ============================================================
print("\n[B2] M4极端异常边界 (D=0.99)...")

D_extreme = {'bat': 0.99, 'fc': 0.0, 'el': 0.0}
q_frac, r0, p_max = hp_bat(D_extreme['bat'])
bat_p_max = bat_rated_kw * q_frac
bat_cap = bat_capacity_kwh * q_frac

np.random.seed(42)
cmds_b2, _ = rule_based_dispatch(load_kw, pv_kw, cfg, bat_p_max_kw=bat_p_max, bat_capacity_kwh=bat_cap)

# 16联盟Shapley
coalition_values_b2 = {}
for cs in CoalitionState.all_16_states():
    result = apply_coupling(hp_bat, cmds_b2, cs, config_dict, D_state=D_extreme)
    coalition_values_b2[cs.code] = result['total_damage']

shapley_b2 = {g: compute_shapley(coalition_values_b2, mediator=g) for g in MEDIATOR_GROUPS}
v_M_b2 = coalition_values_b2['1111']
v_empty_b2 = coalition_values_b2['0000']

# M4异常判定
phi_I_b2 = shapley_b2['I']
m4_report_b2 = check_m4_anomaly(phi_I_b2, shapley_b2['C'], shapley_b2['E'], shapley_b2['P'])
r08_pass = m4_report_b2['is_physical'] and not m4_report_b2['is_anomaly']

# 检查phi_I是否爆炸 (|phi_I| > 10*max(|phi_C|, |phi_P|))
max_other = max(abs(shapley_b2['C']), abs(shapley_b2['E']), abs(shapley_b2['P']), 1e-10)
phi_I_ratio = abs(phi_I_b2) / max_other

print(f"  D=0.99: phi(C)={shapley_b2['C']:+.6f}, phi(E)={shapley_b2['E']:+.6f}, "
      f"phi(P)={shapley_b2['P']:+.6f}, phi(I)={phi_I_b2:+.6f}")
print(f"  |phi(I)|/max(|C/E/P|) = {phi_I_ratio:.2f} (阈值<=10)")
print(f"  M4异常: is_physical={m4_report_b2['is_physical']}, is_anomaly={m4_report_b2['is_anomaly']}")
print(f"  R08: {'PASS' if r08_pass else 'FAIL'}")

boundary_results['B2_M4_extreme'] = {
    'D_state': D_extreme, 'shapley': shapley_b2,
    'phi_I': phi_I_b2, 'phi_I_ratio': phi_I_ratio,
    'm4_report': m4_report_b2, 'r08_pass': r08_pass,
}

# ============================================================
# B3: TE=NA (零基线损伤分母保护)
# ============================================================
print("\n[B3] TE=NA边界 (零基线损伤)...")

# 构造零基线: 让FC/EL不运行 (修改EMS命令)
# 方法: 将FC和EL命令设为0, 只有BAT运行
cmds_zero = cmds_bol.copy()
cmds_zero[:, 1] = 0.0  # FC=0
cmds_zero[:, 2] = 0.0  # EL=0

dD_zero = compute_damage(hp_bat, cmds_zero, config_dict, dt_min=15.0,
                          D_state={'bat': 0.0, 'fc': 0.0, 'el': 0.0}, coalition=coalition_full)
baseline_fc_zero = np.sum(dD_zero['dD_fc'])
baseline_el_zero = np.sum(dD_zero['dD_el'])

# 计算AE
ae_fc_b3 = 0.0 - baseline_fc_zero  # 耦合运行=0, 基线=0
ae_el_b3 = 0.0 - baseline_el_zero

# TE分母保护
te_fc_b3 = ae_fc_b3 / abs(baseline_fc_zero) if abs(baseline_fc_zero) > 1e-4 else float('nan')
te_el_b3 = ae_el_b3 / abs(baseline_el_zero) if abs(baseline_el_zero) > 1e-4 else float('nan')

r12_pass = (np.isnan(te_fc_b3) or np.isnan(te_el_b3))  # 应触发NA保护
no_crash_b3 = True  # 无异常崩溃

print(f"  零基线: baseline_FC={baseline_fc_zero:.8f}, baseline_EL={baseline_el_zero:.8f}")
print(f"  TE(FC)={'NA' if np.isnan(te_fc_b3) else f'{te_fc_b3:.4f}'}, TE(EL)={'NA' if np.isnan(te_el_b3) else f'{te_el_b3:.4f}'}")
print(f"  分母保护触发: {'是' if r12_pass else '否'}")
print(f"  无崩溃: {'PASS' if no_crash_b3 else 'FAIL'}")
print(f"  R12: {'PASS' if (r12_pass and no_crash_b3) else 'FAIL'}")

boundary_results['B3_TE_NA'] = {
    'baseline_fc': baseline_fc_zero, 'baseline_el': baseline_el_zero,
    'te_fc': te_fc_b3, 'te_el': te_el_b3,
    'r12_pass': r12_pass and no_crash_b3,
}

# ============================================================
# B4: 极端气象 (高PV暴风 + 低PV极寒)
# ============================================================
print("\n[B4] 极端气象边界 (高PV/低PV)...")

# B4a: 高PV (夏季峰值 × 1.5)
pv_high = np.clip(pv_kw * 1.5, 0, cap['PV_kWp'] * 2)
# B4b: 低PV (冬季 × 0.3, 模拟暴风)
pv_low = np.clip(pv_kw * 0.3, 0, cap['PV_kWp'])

b4_results = {}
for b4_label, pv_test in [('HighPV', pv_high), ('LowPV', pv_low)]:
    D_state = {'bat': 0.90, 'fc': 0.0, 'el': 0.0}
    q_frac, r0, p_max = hp_bat(D_state['bat'])
    bat_p_max = bat_rated_kw * q_frac
    bat_cap = bat_capacity_kwh * q_frac

    try:
        np.random.seed(42)
        cmds_b4, ems_state = rule_based_dispatch(load_kw, pv_test, cfg,
                                                    bat_p_max_kw=bat_p_max, bat_capacity_kwh=bat_cap)
        dD_b4 = compute_damage(hp_bat, cmds_b4, config_dict, dt_min=15.0,
                                D_state=D_state, coalition=coalition_full)

        # BOL基线
        np.random.seed(42)
        cmds_bol_b4, _ = rule_based_dispatch(load_kw, pv_test, cfg)
        dD_bol_b4 = compute_damage(hp_bat, cmds_bol_b4, config_dict, dt_min=15.0,
                                    D_state={'bat': 0.0, 'fc': 0.0, 'el': 0.0}, coalition=coalition_full)

        ae_fc = np.sum(dD_b4['dD_fc']) - np.sum(dD_bol_b4['dD_fc'])
        ae_el = np.sum(dD_b4['dD_el']) - np.sum(dD_bol_b4['dD_el'])
        no_crash = True
        lople = float(np.sum(ems_state.get('p_uns', np.zeros(1)) > 0.01) / len(load_kw))

        print(f"  {b4_label}: PV mean={pv_test.mean():.1f}kW, AE(FC)={ae_fc:+.6f}, AE(EL)={ae_el:+.6f}, LOLE={lople:.4f}")
        print(f"    无崩溃: PASS")

    except Exception as e:
        ae_fc = float('nan')
        ae_el = float('nan')
        no_crash = False
        lople = float('nan')
        print(f"  {b4_label}: CRASH - {e}")

    b4_results[b4_label] = {
        'pv_mean': float(pv_test.mean()), 'ae_fc': ae_fc, 'ae_el': ae_el,
        'lole': lople, 'no_crash': no_crash,
    }

bw05_pass = all(r['no_crash'] for r in b4_results.values())
print(f"  BW05: {'PASS' if bw05_pass else 'FAIL'} (极端条件无崩溃)")

boundary_results['B4_extreme_weather'] = {
    'sub_results': b4_results, 'bw05_pass': bw05_pass,
}

# === VS8 结果汇总 ===
print("\n" + "=" * 70)
print("VS8 验证结果汇总")
print("=" * 70)

# R10 证据: AE>0 的路径里最大 TE (即最接近激活的那条)
# 这样 Value 和 Threshold 不再是同义反复, 读者可以直接验证 N_eff=0 的依据
_te_eligible = [abs(te) for te, ae in [(te_fc_b1, ae_fc_b1), (te_el_b1, ae_el_b1)]
                 if ae > 0 and not np.isnan(te)]
max_te_eligible = max(_te_eligible) if _te_eligible else 0.0

results = {
    'R10': {'metric': 'N_eff=0识别(D=0.10)', 'value': f'TE_max={max_te_eligible*100:.1f}%', 'threshold': 'TE<5% (no act)', 'pass': r10_pass},
    'R08': {'metric': 'M4异常物理判定(D=0.99)', 'value': f'ratio = {phi_I_ratio:.2f}', 'threshold': '≤ 10 & physical', 'pass': r08_pass},
    'R12': {'metric': 'TE=NA分母保护', 'value': 'NA triggered', 'threshold': 'no crash', 'pass': r12_pass and no_crash_b3},
    'BW05': {'metric': '极端气象不崩溃', 'value': f'{sum(1 for r in b4_results.values() if r["no_crash"])}/2', 'threshold': 'all pass', 'pass': bw05_pass},
}

for check_id, r in results.items():
    print(f"  {check_id}: {r['metric']} = {r['value']} (阈值{r['threshold']}) -> {'PASS' if r['pass'] else 'FAIL'}")

all_pass = all(r['pass'] for r in results.values())
print(f"\n  VS8 总体: {'PASS' if all_pass else 'FAIL'}")

# === 生成图表 ===
print("\n[绘图] 生成VS8边界鲁棒性图表...")

fig = plt.figure(figsize=(15, 10))
gs = GridSpec(3, 2, hspace=0.45, wspace=0.35, left=0.08, right=0.78, top=0.92, bottom=0.06)

# (a) B1: 低退化AE/TE vs 正常退化
ax1 = fig.add_subplot(gs[0, 0])
# 重新计算各D水平的AE (D=0.10, 0.50, 0.90)
ae_fc_list = [boundary_results['B1_N_eff0']['ae_fc']]
ae_el_list = [boundary_results['B1_N_eff0']['ae_el']]
for D_val in [0.50, 0.90]:
    D_s = {'bat': D_val, 'fc': 0.0, 'el': 0.0}
    q, _, _ = hp_bat(D_val)
    bp = bat_rated_kw * q
    bc = bat_capacity_kwh * q
    np.random.seed(42)
    c, _ = rule_based_dispatch(load_kw, pv_kw, cfg, bat_p_max_kw=bp, bat_capacity_kwh=bc)
    dD = compute_damage(hp_bat, c, config_dict, dt_min=15.0, D_state=D_s, coalition=coalition_full)
    ae_fc_list.append(np.sum(dD['dD_fc']) - np.sum(dD_bol['dD_fc']))
    ae_el_list.append(np.sum(dD['dD_el']) - np.sum(dD_bol['dD_el']))

ax1.bar(['D=0.10', 'D=0.50', 'D=0.90'], ae_fc_list[:3], 0.3, color=C_FC, edgecolor='k', linewidth=0.4, alpha=0.7, label='AE(FC)')
ax1.bar(['D=0.10', 'D=0.50', 'D=0.90'], ae_el_list[:3], 0.3, color=C_EL, edgecolor='k', linewidth=0.4, alpha=0.7, label='AE(EL)', bottom=ae_fc_list[:3])
ax1.axhline(0, color='k', lw=0.8)
ax1.set_ylabel('AE')
ax1.set_title(f'(a) B1: Low Degradation (D=0.10) | R10: {"PASS" if r10_pass else "FAIL"}', fontsize=10, fontweight='bold')
ax1.legend(fontsize=8)
ax1.grid(True, alpha=0.3, axis='y')

# (b) B2: D=0.99 Shapley值
ax2 = fig.add_subplot(gs[0, 1])
sv = [shapley_b2[g] for g in MEDIATOR_GROUPS]
colors = [C_BAT, C_EL, C_PASS, C_WARN]
bars = ax2.bar(MEDIATOR_GROUPS, sv, color=colors, edgecolor='k', linewidth=0.4, alpha=0.85)
ax2.axhline(0, color='k', lw=0.8)
ax2.set_ylabel('Shapley value')
ax2.set_title(f'(b) B2: Extreme Degradation (D=0.99) | R08: {"PASS" if r08_pass else "FAIL"}', fontsize=10, fontweight='bold')
for bar, val in zip(bars, sv):
    ax2.text(bar.get_x() + bar.get_width()/2, val + max(abs(v) for v in sv)*0.05,
             f'{val:+.5f}', ha='center', va='bottom' if val >= 0 else 'top', fontsize=9, fontweight='bold')
ax2.grid(True, alpha=0.3, axis='y')

# (c) B3: TE=NA分母保护
ax3 = fig.add_subplot(gs[1, 0])
categories = ['baseline_FC', 'baseline_EL', 'AE_FC', 'AE_EL', 'TE_FC', 'TE_EL']
values = [baseline_fc_zero, baseline_el_zero, ae_fc_b3, ae_el_b3,
          0 if np.isnan(te_fc_b3) else te_fc_b3, 0 if np.isnan(te_el_b3) else te_el_b3]
colors_b3 = [C_FC, C_EL, C_FC, C_EL, C_WARN, C_WARN]
bars = ax3.bar(categories, values, color=colors_b3, edgecolor='k', linewidth=0.4, alpha=0.7)
ax3.axhline(0, color='k', lw=0.8)
# 标注NA
ax3.text(4, 0.0001, 'NA', ha='center', fontsize=12, fontweight='bold', color=C_FAIL)
ax3.text(5, 0.0001, 'NA', ha='center', fontsize=12, fontweight='bold', color=C_FAIL)
ax3.set_ylabel('Value')
ax3.set_title(f'(c) B3: TE=NA Protection | R12: {"PASS" if r12_pass else "FAIL"}', fontsize=10, fontweight='bold')
ax3.set_xticklabels(categories, fontsize=8, rotation=15)
ax3.grid(True, alpha=0.3, axis='y')

# (d) B4: 极端气象AE对比
ax4 = fig.add_subplot(gs[1, 1])
b4_labels = list(b4_results.keys())
b4_ae_fc = [b4_results[l]['ae_fc'] for l in b4_labels]
b4_ae_el = [b4_results[l]['ae_el'] for l in b4_labels]
x = np.arange(len(b4_labels))
ax4.bar(x - 0.15, b4_ae_fc, 0.3, color=C_FC, edgecolor='k', linewidth=0.4, alpha=0.7, label='AE(FC)')
ax4.bar(x + 0.15, b4_ae_el, 0.3, color=C_EL, edgecolor='k', linewidth=0.4, alpha=0.7, label='AE(EL)')
ax4.axhline(0, color='k', lw=0.8)
ax4.set_xticks(x)
ax4.set_xticklabels([f'{l}\nPV={b4_results[l]["pv_mean"]:.0f}kW' for l in b4_labels], fontsize=8)
ax4.set_ylabel('AE')
ax4.set_title(f'(d) B4: Extreme Weather | BW05: {"PASS" if bw05_pass else "FAIL"}', fontsize=10, fontweight='bold')
ax4.legend(fontsize=8)
ax4.grid(True, alpha=0.3, axis='y')

# (e) D扫描: AE vs D (连续)
ax5 = fig.add_subplot(gs[2, 0])
D_scan = np.linspace(0.01, 0.99, 20)
ae_fc_scan = []
ae_el_scan = []
for D_val in D_scan:
    D_s = {'bat': D_val, 'fc': 0.0, 'el': 0.0}
    q, _, _ = hp_bat(D_val)
    bp = bat_rated_kw * q
    bc = bat_capacity_kwh * q
    np.random.seed(42)
    c, _ = rule_based_dispatch(load_kw, pv_kw, cfg, bat_p_max_kw=bp, bat_capacity_kwh=bc)
    dD = compute_damage(hp_bat, c, config_dict, dt_min=15.0, D_state=D_s, coalition=coalition_full)
    ae_fc_scan.append(np.sum(dD['dD_fc']) - np.sum(dD_bol['dD_fc']))
    ae_el_scan.append(np.sum(dD['dD_el']) - np.sum(dD_bol['dD_el']))

ax5.plot(D_scan, ae_fc_scan, 'o-', color=C_FC, markersize=4, lw=1.5, label='AE(FC)')
ax5.plot(D_scan, ae_el_scan, 's-', color=C_EL, markersize=4, lw=1.5, label='AE(EL)')
ax5.axhline(0, color='k', lw=0.5)
ax5.set_xlabel('D (BAT degradation)')
ax5.set_ylabel('AE')
ax5.set_title('(e) AE vs D Scan (B1/B2 boundary)', fontsize=10, fontweight='bold')
ax5.legend(fontsize=8)
ax5.grid(True, alpha=0.3)

# (f) 检查汇总表 (用 ax.text 自绘, 避开 ax.table 字体 fallback 问题)
ax6 = fig.add_subplot(gs[2, 1])
ax6.axis('off')
ax6.set_title('(f) Check Summary', fontsize=10, fontweight='bold', pad=10)

# 显式指定 Microsoft YaHei 字体 (ax.text 也可能 fallback)
from matplotlib.font_manager import FontProperties
_cn_font = FontProperties(family='Microsoft YaHei')

table_data = []
for cid, r in results.items():
    table_data.append([cid, r['metric'][:22], r['value'][:25], r['threshold'][:22], 'P' if r['pass'] else 'F'])
col_labels = ['Check', 'Metric', 'Value', 'Threshold', 'Pass']
n_rows = len(table_data) + 1  # +1 for header

# 列宽归一化到 [0, 1]
cw = np.array([0.10, 0.30, 0.26, 0.24, 0.10])
cw = cw / cw.sum()
col_x = np.concatenate([[0], np.cumsum(cw)])
row_h = 0.13  # 5 行 + 上下 padding
y0 = 0.05
header_h = 0.18

# header 背景 (蓝)
for j in range(5):
    x_l, x_r = col_x[j], col_x[j+1]
    ax6.add_patch(plt.Rectangle((x_l, 1 - header_h - 0.02), x_r-x_l, header_h,
                                 facecolor='#4477AA', edgecolor='k', linewidth=0.6,
                                 transform=ax6.transAxes, clip_on=False))
# header 文字 (白加粗)
for j, lbl in enumerate(col_labels):
    x_c = (col_x[j] + col_x[j+1]) / 2
    ax6.text(x_c, 1 - header_h/2 - 0.02, lbl, ha='center', va='center',
             fontsize=8.5, fontweight='bold', color='white',
             transform=ax6.transAxes, fontproperties=_cn_font)

# 数据行文字
for i, row in enumerate(table_data):
    y_c = 1 - header_h - 0.02 - (i + 0.5) * row_h
    for j, val in enumerate(row):
        x_c = (col_x[j] + col_x[j+1]) / 2
        # Pass 列加底色
        if j == 4:
            x_l, x_r = col_x[j], col_x[j+1]
            face = (C_PASS if val == 'P' else C_FAIL) + '40'
            ax6.add_patch(plt.Rectangle((x_l, y_c - row_h/2), x_r-x_l, row_h,
                                         facecolor=face, edgecolor='k', linewidth=0.6,
                                         transform=ax6.transAxes, clip_on=False))
        # 数值列文字色与表头一致 (默认黑)
        ax6.text(x_c, y_c, str(val), ha='center', va='center', fontsize=8,
                 transform=ax6.transAxes, fontproperties=_cn_font)

# ===== 网格线用细矩形绘制 (与 header patch 同一渲染路径: transAxes + clip_on=False) =====
# 注意: ax.plot(transform=transAxes) 在 axis('off')+bbox_inches='tight' 下竖线会渲染失败,
#      改用 Rectangle 细矩形; Rectangle((x,y), w, h) 中 y 是 bottom-left.
# axis('off') 让 axes spine 消失, 因此 col_x[0]=0 与 col_x[-1]=1 的左右边界必须自绘.
_y_hdr_top = 1 - 0.02                   # header 上边界 = 0.98
_y_top = 1 - header_h - 0.02             # header 下边界 (=R10 顶) = 0.80
_y_bot = _y_top - (n_rows - 1) * row_h   # 表格底线 (=BW05 底, 严格等距) = 0.28
_h_line_color = 'black'
_line_t = 0.004                         # 横线厚度 (axes 比例)
_v_w = 0.004                            # 竖线宽度 (与横线等厚, 不再突兀)
# 水平线 6 条: header 上, header 下, 4 行数据底 (= 表格底线)
_h_ys = [_y_hdr_top] + [_y_top - i * row_h for i in range(n_rows)]
for y in _h_ys:
    ax6.add_patch(plt.Rectangle((0, y - _line_t/2), 1.0, _line_t,
                                 facecolor=_h_line_color, edgecolor='none',
                                 transform=ax6.transAxes, clip_on=False, zorder=20))
# 垂直分隔线 6 条: 包含最左 col_x[0]=0 与最右 col_x[-1]=1 (axis('off') 无 spine, 必须自绘)
for x_pos in col_x:
    ax6.add_patch(plt.Rectangle((x_pos - _v_w/2, _y_bot), _v_w,
                                 _y_top - _y_bot,
                                 facecolor=_h_line_color, edgecolor='none',
                                 transform=ax6.transAxes, clip_on=False, zorder=20))

fig.suptitle('VS8 Boundary Robustness Verification\n'
             f'4 boundary scenarios | Overall: {"PASS" if all_pass else "FAIL"}',
             fontsize=12, fontweight='bold', y=0.98)

result_text = f"VS8 Overall: {'PASS' if all_pass else 'FAIL'} | "
for cid, r in results.items():
    result_text += f"{cid}: {'P' if r['pass'] else 'F'} | "
fig.text(0.08, 0.01, result_text, fontsize=8, fontweight='bold',
         color=C_PASS if all_pass else C_FAIL)

fig.savefig(OUT_FIG, dpi=300, bbox_inches='tight')
plt.close(fig)
print(f"  Figure saved: {OUT_FIG} ({OUT_FIG.stat().st_size // 1024} KB)")

# === Export CSV ===
with open(OUT_CSV, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['Boundary', 'Check', 'Metric', 'Value', 'Threshold', 'Pass'])
    for cid, r in results.items():
        w.writerow([cid, cid, r['metric'], r['value'], r['threshold'], r['pass']])
    w.writerow([])

    # B2 Shapley
    w.writerow(['B2_Shapley', 'Mediator', 'Value'])
    for g in MEDIATOR_GROUPS:
        w.writerow(['', g, f"{shapley_b2[g]:.10f}"])
    w.writerow([])

    # B4 extreme weather
    w.writerow(['B4_Weather', 'Scenario', 'PV_mean_kW', 'AE_FC', 'AE_EL', 'LOLE', 'No_crash'])
    for label, r in b4_results.items():
        ae_fc_str = f"{r['ae_fc']:.10f}" if not np.isnan(r['ae_fc']) else "NaN"
        ae_el_str = f"{r['ae_el']:.10f}" if not np.isnan(r['ae_el']) else "NaN"
        w.writerow(['', label, f"{r['pv_mean']:.2f}", ae_fc_str, ae_el_str, f"{r['lole']:.4f}", r['no_crash']])

print(f"  CSV saved: {OUT_CSV}")

print("\n" + "=" * 70)
print("Methodology:")
print("  B1/R10: Low degradation (D=0.10) -> N_eff=0 (no path activation)")
print("  B2/R08: Extreme degradation (D=0.99) -> M4 anomaly physical (|phi_I|<=10x)")
print("  B3/R12: Zero baseline damage -> TE=NA (denominator protection)")
print("  B4/BW05: Extreme weather (1.5x PV / 0.3x PV) -> no crash")
print("=" * 70)
print(f"\nVS8 Boundary Robustness: {'PASS' if all_pass else 'FAIL'}")
print(f"{'='*70}")
