# -*- coding: utf-8 -*-
"""
VS6 决策偏差验证 (M1独立 vs M2耦合) — MVP简化版
量化忽略耦合对5个决策维度的影响

M1 (独立模型): EMS使用BOL参数(无退化感知), 损伤计算传入D_state
  → FC/EL损伤不随BAT退化变化 (D_fc=D_el=0, EMS命令不变)
M2 (耦合模型): EMS使用退化感知参数(bat_p_max/bat_capacity缩减)
  → FC/EL损伤随BAT退化变化 (EMS调度变化)

5决策维度:
  D1 ΔD_j: 各设备总损伤 (BAT/FC/EL)
  D2 LOLE: 未供电负荷比例 (Loss of Load Expectation)
  D3 容量充裕度: SOC>min的时间比例
  D4 EOL时点: 外推至EOL的年数 (1/(dD×52))
  D5 路径贡献: AE占总损伤比例 (耦合效应占比)

检查项:
  A02: 偏差显著性 (|Bias|>=10% for >=1 dimension)
  A03: 偏差方向一致性 (跨窗口方向一致)
  N01: M1/M2唯一差异验证 (BOL时M1=M2)
  A04: LOLE偏差方向 (M2应有不同LOLE)
  A05: EOL时点偏差 (FC/EL更换时间偏差)
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
C_M1 = '#3288BD'; C_M2 = '#D73027'
C_PASS = '#2ca02c'; C_FAIL = '#d62728'; C_WARN = '#ff7f0e'

OUT_FIG = PROJECT_ROOT / "07_results" / "figures" / "VS6_decision_bias.png"
OUT_CSV = PROJECT_ROOT / "07_results" / "annual" / "VS6_decision_bias_summary.csv"

# === 窗口定义 ===
WINDOWS = [
    {'label': '2017-W1',  'year': 2017, 'start_day': 1,   'season': 'Winter'},
    {'label': '2017-W27', 'year': 2017, 'start_day': 175, 'season': 'Summer'},
]

# === 健康状态 ===
RUNS_3 = {
    'BOL':      {'bat': 0.0, 'fc': 0.0, 'el': 0.0},
    'BAT_D050': {'bat': 0.50, 'fc': 0.0, 'el': 0.0},
    'BAT_D090': {'bat': 0.90, 'fc': 0.0, 'el': 0.0},
}

# === 2条路径 ===
PATHS_2 = {
    'alpha1': {'source': 'BAT', 'target': 'FC', 'desc': 'BAT->FC'},
    'alpha2': {'source': 'BAT', 'target': 'EL', 'desc': 'BAT->EL'},
}

BIAS_THRESHOLD = 0.10  # 10%

print("=" * 70)
print("VS6 决策偏差验证 (M1独立 vs M2耦合) — MVP简化版")
print("=" * 70)

# === 加载配置 ===
from components.data_lite import load_data
from ems.ems_bf_lite import EMSConfig, rule_based_dispatch
from simulation.closed_loop_lite import CoalitionState, compute_damage, apply_coupling
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

csv_path = str(PROJECT_ROOT / "02_data_processed" / "tradestreet_pipeline" /
               "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv")

def load_window(year, start_day, window_days=7):
    raw = pd.read_csv(csv_path, parse_dates=['timestamp_local_civil'])
    df_year = raw[raw['local_civil_year'] == year].copy()
    start_idx = (start_day - 1) * 96
    end_idx = start_idx + window_days * 96
    df_window = df_year.iloc[start_idx:end_idx].copy()
    df_window = df_window.rename(columns={
        'timestamp_local_civil': 'timestamp',
        'p_pv_available_reconstructed_nonnegative_kW': 'P_pv_kW',
        'p_building_load_reconstructed_kW': 'P_load_kW',
    })
    df_window['P_pv_kW'] = df_window['P_pv_kW'].ffill().bfill()
    df_window['P_load_kW'] = df_window['P_load_kW'].ffill().bfill()
    return df_window

# === 步骤1: 对每个窗口, 运行M1/M2 × 3健康状态 ===
print(f"\n[1] 执行{len(WINDOWS)}窗口 x 2模型 x 3健康状态 = {len(WINDOWS)*2*3}次运行...")

window_results = {}

for win_info in WINDOWS:
    win_label = win_info['label']
    print(f"\n  --- {win_label} ({win_info['season']}) ---")

    df_win = load_window(win_info['year'], win_info['start_day'])
    pv_kw = df_win['P_pv_kW'].values
    load_kw = df_win['P_load_kW'].values

    model_results = {}  # {(model, run_label): {damage, lople, capacity, eol}}

    for model in ['M1', 'M2']:
        for run_label, D_state in RUNS_3.items():
            # M1: EMS用BOL参数 (无退化感知)
            # M2: EMS用退化感知参数
            if model == 'M2' and D_state['bat'] > 0:
                q_frac, r0, p_max = hp_bat(D_state['bat'])
                bat_p_max = bat_rated_kw * q_frac
                bat_cap = bat_capacity_kwh * q_frac
            else:
                bat_p_max = None  # M1 or BOL: 用额定参数
                bat_cap = None

            np.random.seed(42)
            cmds, ems_state = rule_based_dispatch(
                load_kw, pv_kw, cfg,
                bat_p_max_kw=bat_p_max, bat_capacity_kwh=bat_cap
            )

            # 全ON损伤计算
            coalition_full = CoalitionState.from_code('1111')
            dD_dict = compute_damage(hp_bat, cmds, config_dict, dt_min=15.0,
                                      D_state=D_state, coalition=coalition_full)

            # 5决策维度
            dD_bat = float(np.sum(dD_dict['dD_bat']))
            dD_fc = float(np.sum(dD_dict['dD_fc']))
            dD_el = float(np.sum(dD_dict['dD_el']))
            total_damage = dD_bat + dD_fc + dD_el

            # LOLE: 未供电比例
            p_uns = ems_state.get('p_uns', np.zeros(len(load_kw)))
            lople = float(np.sum(p_uns > 0.01) / len(p_uns))  # >0.01kW阈值

            # 容量充裕度: SOC > soc_min的比例
            soc_trace = ems_state.get('soc_trace', np.ones(len(load_kw)))
            capacity_adequacy = float(np.mean(soc_trace > bat_c['soc_min']))

            # EOL时点: 外推年数 (7天窗口→52周/年)
            steps_per_year = 52  # 7天→1年
            eol_bat = 1.0 / max(dD_bat * steps_per_year, 1e-10)
            eol_fc = 1.0 / max(dD_fc * steps_per_year, 1e-10)
            eol_el = 1.0 / max(dD_el * steps_per_year, 1e-10)

            model_results[(model, run_label)] = {
                'dD_bat': dD_bat, 'dD_fc': dD_fc, 'dD_el': dD_el,
                'total_damage': total_damage,
                'lole': lople,
                'capacity_adequacy': capacity_adequacy,
                'eol_bat': eol_bat, 'eol_fc': eol_fc, 'eol_el': eol_el,
                'cmds': cmds,
                'ems_state': ems_state,
            }

            print(f"    {model}/{run_label:10s}: dD_bat={dD_bat:.6f}, dD_fc={dD_fc:.6f}, dD_el={dD_el:.6f}, "
                  f"LOLE={lople:.4f}, cap_adeq={capacity_adequacy:.4f}")

    window_results[win_label] = model_results

# === 步骤2: 计算偏差 (M2 vs M1) ===
print(f"\n[2] 计算5决策维度偏差 (Bias = (M2-M1)/M1):")

bias_results = {}  # {win_label: {run_label: {dim: bias_pct}}}

for win_label, model_results in window_results.items():
    bias_results[win_label] = {}
    for run_label in ['BAT_D050', 'BAT_D090']:
        m1 = model_results[('M1', run_label)]
        m2 = model_results[('M2', run_label)]

        biases = {}
        # D1: ΔD_j (各设备损伤偏差)
        for dev in ['bat', 'fc', 'el']:
            m1_val = m1[f'dD_{dev}']
            m2_val = m2[f'dD_{dev}']
            if abs(m1_val) > 1e-10:
                biases[f'D1_dD_{dev}'] = (m2_val - m1_val) / m1_val * 100
            else:
                biases[f'D1_dD_{dev}'] = float('nan')

        # D2: LOLE偏差 (绝对差, 因LOLE可能=0)
        biases['D2_LOLE'] = (m2['lole'] - m1['lole']) * 100  # 百分点

        # D3: 容量充裕度偏差
        biases['D3_cap_adeq'] = (m2['capacity_adequacy'] - m1['capacity_adequacy']) * 100

        # D4: EOL时点偏差
        for dev in ['bat', 'fc', 'el']:
            m1_eol = m1[f'eol_{dev}']
            m2_eol = m2[f'eol_{dev}']
            if abs(m1_eol) > 1e-10:
                biases[f'D4_EOL_{dev}'] = (m2_eol - m1_eol) / m1_eol * 100
            else:
                biases[f'D4_EOL_{dev}'] = float('nan')

        # D5: 路径贡献 (AE占总损伤比例)
        # AE_FC = dD_fc(M2) - dD_fc(M1) = coupling effect on FC
        ae_fc = m2['dD_fc'] - m1['dD_fc']
        ae_el = m2['dD_el'] - m1['dD_el']
        total_ae = ae_fc + ae_el
        biases['D5_path_contrib'] = total_ae / max(m2['total_damage'], 1e-10) * 100

        bias_results[win_label][run_label] = biases

        print(f"  {win_label}/{run_label}:")
        for dim, val in biases.items():
            if not np.isnan(val):
                print(f"    {dim}: {val:+.2f}%")
            else:
                print(f"    {dim}: NA")

# === 步骤3: 检查N01 M1/M2唯一差异 ===
print(f"\n[3] N01 M1/M2唯一差异验证 (BOL时M1=M2):")
n01_pass = True
for win_label, model_results in window_results.items():
    m1_bol = model_results[('M1', 'BOL')]
    m2_bol = model_results[('M2', 'BOL')]
    # BOL时D=0, M1和M2应完全相同
    diffs = []
    for key in ['dD_bat', 'dD_fc', 'dD_el', 'lole', 'capacity_adequacy']:
        diff = abs(m1_bol[key] - m2_bol[key])
        diffs.append(diff)
        if diff > 1e-10:
            n01_pass = False
    max_diff = max(diffs)
    print(f"  {win_label}: max|M1-M2| @ BOL = {max_diff:.2e} -> {'PASS' if max_diff <= 1e-10 else 'FAIL'}")

print(f"  N01总体: {'PASS' if n01_pass else 'FAIL'}")

# === 步骤4: 检查A02 偏差显著性 ===
print(f"\n[4] A02 偏差显著性 (|Bias|>=10% for >=1 dimension):")
a02_pass = False
a02_significant_count = 0
a02_total_dims = 0

for win_label in [w['label'] for w in WINDOWS]:
    for run_label in ['BAT_D050', 'BAT_D090']:
        biases = bias_results[win_label][run_label]
        for dim, val in biases.items():
            if not np.isnan(val) and dim not in ['D2_LOLE', 'D3_cap_adeq', 'D5_path_contrib']:
                a02_total_dims += 1
                if abs(val) >= BIAS_THRESHOLD * 100:
                    a02_significant_count += 1
                    a02_pass = True
                    print(f"  {win_label}/{run_label}/{dim}: |Bias|={abs(val):.2f}% >= 10% -> SIGNIFICANT")

# 对于LOLE和容量充裕度, 用绝对差(百分点)判断
for win_label in [w['label'] for w in WINDOWS]:
    for run_label in ['BAT_D050', 'BAT_D090']:
        biases = bias_results[win_label][run_label]
        if abs(biases['D2_LOLE']) >= 1.0:  # 1个百分点
            a02_pass = True
            print(f"  {win_label}/{run_label}/D2_LOLE: {biases['D2_LOLE']:+.2f}pp -> SIGNIFICANT")

print(f"  A02: {'PASS' if a02_pass else 'FAIL'} (至少1个维度|Bias|>=10%)")

# === 步骤5: 检查A03 偏差方向一致性 ===
print(f"\n[5] A03 偏差方向一致性 (跨窗口):")
a03_pass = True
a03_details = []

# 对每个维度, 检查2个窗口的偏差符号是否一致
dims_to_check = ['D1_dD_bat', 'D1_dD_fc', 'D1_dD_el', 'D4_EOL_bat', 'D4_EOL_fc', 'D4_EOL_el']
for dim in dims_to_check:
    for run_label in ['BAT_D090']:
        vals = []
        for win_label in [w['label'] for w in WINDOWS]:
            v = bias_results[win_label][run_label].get(dim, float('nan'))
            vals.append(v)
        if all(not np.isnan(v) for v in vals):
            signs = [1 if v > 0 else (-1 if v < 0 else 0) for v in vals]
            all_same = len(set(signs)) == 1
            if not all_same:
                a03_pass = False
            a03_details.append((dim, run_label, vals, signs, all_same))
            print(f"  {dim}/{run_label}: {[f'{v:+.2f}%' for v in vals]}, signs={signs} -> {'PASS' if all_same else 'FAIL'}")

print(f"  A03总体: {'PASS' if a03_pass else 'FAIL'}")

# === 步骤6: 检查A04 LOLE偏差 ===
print(f"\n[6] A04 LOLE偏差方向:")
a04_pass = False
for win_label in [w['label'] for w in WINDOWS]:
    for run_label in ['BAT_D090']:
        m1_lole = window_results[win_label][('M1', run_label)]['lole']
        m2_lole = window_results[win_label][('M2', run_label)]['lole']
        diff = m2_lole - m1_lole
        if abs(diff) > 0.001:  # 0.1%差异
            a04_pass = True
        print(f"  {win_label}/{run_label}: M1_LOLE={m1_lole:.4f}, M2_LOLE={m2_lole:.4f}, diff={diff:+.4f}")

print(f"  A04: {'PASS' if a04_pass else 'FAIL'} (M1/M2 LOLE有差异)")

# === 步骤7: 检查A05 EOL时点偏差 ===
print(f"\n[7] A05 EOL时点偏差 (FC/EL):")
a05_pass = False
for win_label in [w['label'] for w in WINDOWS]:
    for run_label in ['BAT_D090']:
        for dev in ['fc', 'el']:
            m1_eol = window_results[win_label][('M1', run_label)][f'eol_{dev}']
            m2_eol = window_results[win_label][('M2', run_label)][f'eol_{dev}']
            if abs(m1_eol) > 1e-10:
                bias = (m2_eol - m1_eol) / m1_eol * 100
                if abs(bias) >= 10:
                    a05_pass = True
                print(f"  {win_label}/{run_label}/EOL_{dev}: M1={m1_eol:.2f}yr, M2={m2_eol:.2f}yr, bias={bias:+.2f}%")

print(f"  A05: {'PASS' if a05_pass else 'FAIL'} (EOL时点偏差>=10%)")

# === VS6 结果汇总 ===
print("\n" + "=" * 70)
print("VS6 验证结果汇总")
print("=" * 70)

results = {
    'N01': {'metric': 'M1/M2唯一差异(BOL)', 'value': 'M1=M2@BOL', 'threshold': 'diff<1e-10', 'pass': n01_pass},
    'A02': {'metric': '偏差显著性', 'value': f'{a02_significant_count} dims >=10%', 'threshold': '>=1 dim', 'pass': a02_pass},
    'A03': {'metric': '偏差方向一致性', 'value': f'{sum(1 for d in a03_details if d[4])}/{len(a03_details)}', 'threshold': 'all same sign', 'pass': a03_pass},
    'A04': {'metric': 'LOLE偏差', 'value': 'M1!=M2', 'threshold': 'diff>0.1%', 'pass': a04_pass},
    'A05': {'metric': 'EOL时点偏差', 'value': '>=10%', 'threshold': '>=10%', 'pass': a05_pass},
}

for check_id, r in results.items():
    print(f"  {check_id}: {r['metric']} = {r['value']} (阈值{r['threshold']}) -> {'PASS' if r['pass'] else 'FAIL'}")

all_pass = all(r['pass'] for r in results.values())
print(f"\n  VS6 总体: {'PASS' if all_pass else 'FAIL'}")

# === 生成图表 ===
print("\n[绘图] 生成VS6决策偏差图表...")

fig = plt.figure(figsize=(15, 10))
gs = GridSpec(3, 2, hspace=0.45, wspace=0.35, left=0.08, right=0.78, top=0.92, bottom=0.06)

# (a) 设备损伤对比 (M1 vs M2, BAT_D090, 2窗口)
ax1 = fig.add_subplot(gs[0, 0])
bar_width = 0.15
devices = ['BAT', 'FC', 'EL']
x = np.arange(len(devices))
for i, win_label in enumerate([w['label'] for w in WINDOWS]):
    m1_damages = [window_results[win_label][('M1', 'BAT_D090')][f'dD_{d.lower()}'] for d in devices]
    m2_damages = [window_results[win_label][('M2', 'BAT_D090')][f'dD_{d.lower()}'] for d in devices]
    offset = (i - 0.5) * 2 * bar_width
    bars1 = ax1.bar(x + offset - bar_width/2, m1_damages, bar_width,
                    label=f'{win_label} M1', color=C_M1, edgecolor='k', linewidth=0.3, alpha=0.6+0.3*i)
    bars2 = ax1.bar(x + offset + bar_width/2, m2_damages, bar_width,
                    label=f'{win_label} M2', color=C_M2, edgecolor='k', linewidth=0.3, alpha=0.6+0.3*i)
ax1.set_xticks(x)
ax1.set_xticklabels(devices)
ax1.set_ylabel('Damage dD')
ax1.set_title('(a) Device Damage: M1 vs M2 (BAT_D090)', fontsize=10, fontweight='bold')
ax1.legend(fontsize=7, ncol=2)
ax1.grid(True, alpha=0.3, axis='y')

# (b) 偏差柱状图 (各维度, BAT_D090, 2窗口)
ax2 = fig.add_subplot(gs[0, 1])
bias_dims = ['D1_dD_bat', 'D1_dD_fc', 'D1_dD_el', 'D4_EOL_bat', 'D4_EOL_fc', 'D4_EOL_el']
bias_labels = ['dD_BAT', 'dD_FC', 'dD_EL', 'EOL_BAT', 'EOL_FC', 'EOL_EL']
x = np.arange(len(bias_dims))
for i, win_label in enumerate([w['label'] for w in WINDOWS]):
    vals = [bias_results[win_label]['BAT_D090'].get(d, 0) for d in bias_dims]
    offset = (i - 0.5) * bar_width
    colors_bar = [C_PASS if v >= 0 else C_FAIL for v in vals]
    bars = ax2.bar(x + offset, vals, bar_width, label=win_label,
                   color=[C_M1 if i == 0 else C_M2], edgecolor='k', linewidth=0.3, alpha=0.85)
ax2.axhline(10, color=C_FAIL, ls='--', lw=1, label='+10% threshold')
ax2.axhline(-10, color=C_FAIL, ls='--', lw=1)
ax2.axhline(0, color='k', lw=0.8)
ax2.set_xticks(x)
ax2.set_xticklabels(bias_labels, fontsize=8, rotation=15)
ax2.set_ylabel('Bias (%)')
ax2.set_title(f'(b) Decision Bias (BAT_D090) | A02: {"PASS" if a02_pass else "FAIL"}', fontsize=10, fontweight='bold')
ax2.legend(fontsize=7)
ax2.grid(True, alpha=0.3, axis='y')

# (c) LOLE对比 (M1 vs M2, 3健康状态, 2窗口)
ax3 = fig.add_subplot(gs[1, 0])
run_labels = list(RUNS_3.keys())
x = np.arange(len(run_labels))
for i, win_label in enumerate([w['label'] for w in WINDOWS]):
    m1_loles = [window_results[win_label][('M1', rl)]['lole'] * 100 for rl in run_labels]
    m2_loles = [window_results[win_label][('M2', rl)]['lole'] * 100 for rl in run_labels]
    offset = (i - 0.5) * 2 * bar_width
    bars1 = ax3.bar(x + offset - bar_width/2, m1_loles, bar_width,
                    label=f'{win_label} M1', color=C_M1, edgecolor='k', linewidth=0.3, alpha=0.6+0.3*i)
    bars2 = ax3.bar(x + offset + bar_width/2, m2_loles, bar_width,
                    label=f'{win_label} M2', color=C_M2, edgecolor='k', linewidth=0.3, alpha=0.6+0.3*i)
ax3.set_xticks(x)
ax3.set_xticklabels(run_labels, fontsize=8)
ax3.set_ylabel('LOLE (%)')
ax3.set_title(f'(c) LOLE: M1 vs M2 | A04: {"PASS" if a04_pass else "FAIL"}', fontsize=10, fontweight='bold')
ax3.legend(fontsize=7, ncol=2)
ax3.grid(True, alpha=0.3, axis='y')

# (d) EOL时点对比 (FC/EL, BAT_D090, 2窗口)
ax4 = fig.add_subplot(gs[1, 1])
eol_devs = ['fc', 'el']
eol_labels = ['FC', 'EL']
x = np.arange(len(eol_devs))
for i, win_label in enumerate([w['label'] for w in WINDOWS]):
    m1_eols = [window_results[win_label][('M1', 'BAT_D090')][f'eol_{d}'] for d in eol_devs]
    m2_eols = [window_results[win_label][('M2', 'BAT_D090')][f'eol_{d}'] for d in eol_devs]
    offset = (i - 0.5) * 2 * bar_width
    bars1 = ax4.bar(x + offset - bar_width/2, m1_eols, bar_width,
                    label=f'{win_label} M1', color=C_M1, edgecolor='k', linewidth=0.3, alpha=0.6+0.3*i)
    bars2 = ax4.bar(x + offset + bar_width/2, m2_eols, bar_width,
                    label=f'{win_label} M2', color=C_M2, edgecolor='k', linewidth=0.3, alpha=0.6+0.3*i)
ax4.set_xticks(x)
ax4.set_xticklabels(eol_labels)
ax4.set_ylabel('EOL timing (years)')
ax4.set_title(f'(d) EOL Timing: M1 vs M2 | A05: {"PASS" if a05_pass else "FAIL"}', fontsize=10, fontweight='bold')
ax4.legend(fontsize=7, ncol=2)
ax4.grid(True, alpha=0.3, axis='y')

# (e) 路径贡献 (D5)
ax5 = fig.add_subplot(gs[2, 0])
for i, win_label in enumerate([w['label'] for w in WINDOWS]):
    contribs = [bias_results[win_label][rl]['D5_path_contrib'] for rl in ['BAT_D050', 'BAT_D090']]
    x = np.arange(2)
    offset = (i - 0.5) * bar_width
    bars = ax5.bar(x + offset, contribs, bar_width, label=win_label,
                   color=C_M1 if i == 0 else C_M2, edgecolor='k', linewidth=0.3, alpha=0.85)
ax5.set_xticks(np.arange(2))
ax5.set_xticklabels(['BAT_D050', 'BAT_D090'])
ax5.set_ylabel('Path contribution (%)')
ax5.set_title('(e) Coupling Path Contribution', fontsize=10, fontweight='bold')
ax5.legend(fontsize=8)
ax5.grid(True, alpha=0.3, axis='y')

# (f) 检查汇总表
ax6 = fig.add_subplot(gs[2, 1])
ax6.axis('off')
table_data = []
for cid, r in results.items():
    table_data.append([cid, r['metric'][:20], r['value'][:15], r['threshold'][:15], 'P' if r['pass'] else 'F'])

col_labels = ['Check', 'Metric', 'Value', 'Threshold', 'Pass']
table = ax6.table(cellText=table_data, colLabels=col_labels, loc='center',
                  cellLoc='center', colColours=['#4477AA']*5,
                  colWidths=[0.12, 0.36, 0.24, 0.18, 0.10])
table.auto_set_font_size(False)
table.set_fontsize(7.0)
auto_fit_table(ax6, table)
for i, row in enumerate(table_data):
    cell = table[i + 1, 4]
    cell.set_facecolor(C_PASS + '40' if row[4] == 'P' else C_FAIL + '40')
ax6.set_title('(f) Check Summary', fontsize=10, fontweight='bold', pad=10)

fig.suptitle('VS6 Decision Bias Verification (M1 Independent vs M2 Coupled)\n'
             f'5 dimensions x 2 windows | Overall: {"PASS" if all_pass else "FAIL"}',
             fontsize=12, fontweight='bold', y=0.98)

result_text = f"VS6 Overall: {'PASS' if all_pass else 'FAIL'} | "
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
    # M1/M2 raw values
    w.writerow(['Window', 'Model', 'Run', 'dD_bat', 'dD_fc', 'dD_el', 'total', 'LOLE_pct', 'cap_adeq', 'EOL_bat_yr', 'EOL_fc_yr', 'EOL_el_yr'])
    for win_label in [w2['label'] for w2 in WINDOWS]:
        for model in ['M1', 'M2']:
            for run_label in RUNS_3:
                r = window_results[win_label][(model, run_label)]
                w.writerow([win_label, model, run_label,
                           f"{r['dD_bat']:.10f}", f"{r['dD_fc']:.10f}", f"{r['dD_el']:.10f}",
                           f"{r['total_damage']:.10f}", f"{r['lole']*100:.4f}", f"{r['capacity_adequacy']:.4f}",
                           f"{r['eol_bat']:.4f}", f"{r['eol_fc']:.4f}", f"{r['eol_el']:.4f}"])
    w.writerow([])

    # Bias table
    w.writerow(['Window', 'Run', 'D1_dD_bat_pct', 'D1_dD_fc_pct', 'D1_dD_el_pct',
                'D2_LOLE_pp', 'D3_cap_adeq_pp', 'D4_EOL_bat_pct', 'D4_EOL_fc_pct', 'D4_EOL_el_pct',
                'D5_path_contrib_pct'])
    for win_label in [w2['label'] for w2 in WINDOWS]:
        for run_label in ['BAT_D050', 'BAT_D090']:
            b = bias_results[win_label][run_label]
            w.writerow([win_label, run_label,
                       f"{b['D1_dD_bat']:.4f}", f"{b['D1_dD_fc']:.4f}", f"{b['D1_dD_el']:.4f}",
                       f"{b['D2_LOLE']:.4f}", f"{b['D3_cap_adeq']:.4f}",
                       f"{b['D4_EOL_bat']:.4f}", f"{b['D4_EOL_fc']:.4f}", f"{b['D4_EOL_el']:.4f}",
                       f"{b['D5_path_contrib']:.4f}"])
    w.writerow([])

    # Check summary
    w.writerow(['Check', 'Metric', 'Value', 'Threshold', 'Pass'])
    for cid, r in results.items():
        w.writerow([cid, r['metric'], r['value'], r['threshold'], r['pass']])

print(f"  CSV saved: {OUT_CSV}")

# === Methodology notes ===
print("\n" + "=" * 70)
print("Methodology:")
print("  M1 (independent): EMS uses BOL params, damage computed with D_state")
print("    -> FC/EL damage unchanged by BAT degradation (no coupling)")
print("  M2 (coupled): EMS uses degradation-aware params (bat_p_max/capacity scaled)")
print("    -> FC/EL damage changes due to EMS dispatch adjustment")
print("  Bias = (M2 - M1) / M1 * 100%")
print("  N01: BOL run M1=M2 (only difference is degradation-aware EMS)")
print("  A02: At least 1 dimension has |Bias| >= 10% (coupling matters)")
print("  A03: Bias direction consistent across windows")
print("  A04: LOLE differs between M1 and M2 (reliability impact)")
print("  A05: EOL timing bias >= 10% (maintenance planning impact)")
print("=" * 70)
print(f"\nVS6 Decision Bias Verification: {'PASS' if all_pass else 'FAIL'}")
print(f"{'='*70}")