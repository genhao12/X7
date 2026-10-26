# -*- coding: utf-8 -*-
"""
VS3 路径识别验证 (M2核心层) — MVP简化版
协议: 路径辨识协议（固定D=0/0.5/0.9, 不允许年内健康演化, 验证窗口截断在EOL前）
区别: 寿命评价协议（动态D, 年内演化, EOL更换）见run_p1_revised.py
7健康状态 × 16联盟 = 112次运行, 识别6候选路径激活/条件性/零路径

六候选路径 (v3.0冻结定义):
  alpha1: BAT→FC  (BAT退化→FC损伤增量, 母线纹波归因)
  alpha2: EL→FC   (EL退化→FC损伤增量, 母线纹波归因)
  beta1:  BAT→EL  (BAT退化→EL损伤增量, 供氢波动归因)
  beta2:  FC→EL   (FC退化→EL损伤增量, 供氢波动归因)
  gamma1: FC→BAT  (FC退化→BAT损伤增量, 母线纹波归因)
  gamma2: EL→BAT  (EL退化→BAT损伤增量, 母线纹波归因)

7健康运行:
  BOL:     D={bat:0, fc:0, el:0}
  BAT_D050: D={bat:0.5, fc:0, el:0}
  BAT_D090: D={bat:0.9, fc:0, el:0}
  FC_D050:  D={bat:0, fc:0.5, el:0}
  FC_D090:  D={bat:0, fc:0.9, el:0}
  EL_D050:  D={bat:0, fc:0, el:0.5}
  EL_D090:  D={bat:0, fc:0, el:0.9}

检查项:
  I01: 六路径覆盖 (6/6)
  I06: 七健康运行覆盖 (7/7)
  C03: NC1数值泄漏残差 (≤10^-4)
  P01-P06: 路径激活判定 (AE>0且TE≥5%)
"""
import sys, json, csv
import numpy as np
from pathlib import Path
from itertools import product

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

C_ACT = '#2ca02c'; C_COND = '#ff7f0e'; C_ZERO = '#d62728'
C_BAT = '#3288BD'; C_FC = '#D73027'; C_EL = '#66C2A5'

OUT_FIG = PROJECT_ROOT / "07_results" / "figures" / "VS3_path_identification.png"
OUT_CSV = PROJECT_ROOT / "07_results" / "annual" / "VS3_path_summary.csv"

print("=" * 70)
print("VS3 路径识别验证 (M2核心层) — MVP简化版")
print("=" * 70)

# === 加载配置和数据 ===
from components.data_lite import load_data
from ems.ems_bf_lite import EMSConfig, rule_based_dispatch
from simulation.closed_loop_lite import CoalitionState, compute_damage, apply_coupling
from components.hp_bat_lite import _load_280ah_data, fit_hppc, interpolate_hp as interp_bat

config_path = PROJECT_ROOT / "05_config" / "mvp_c1_config.json"
with open(config_path, 'r', encoding='utf-8') as f:
    c1 = json.load(f)

load_csv = str(PROJECT_ROOT / "02_data_processed" / "tradestreet_pipeline" /
               "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv")
data = load_data(load_csv, str(config_path), year=2017, window_days=7)
df = data['data']
pv_kw = df['P_pv_kW'].values
load_kw = df['P_load_kW'].values
n = len(pv_kw)

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

# 构建hp_bat函数
hppc_data = _load_280ah_data()
bat_fit = fit_hppc(hppc_data)
hp_bat = interp_bat(bat_fit, {'bat_kwh': cap['BAT_kWh'], 'bat_voltage_nominal': 3.2*48, 'bat_rated_kw': bat_rated_kw})

config_dict = {
    'capacity': cap, 'battery_constraints': bat_c,
    'ems_parameters': ems_p, 'hydrogen_constraints': h2_c,
    'minimum_stable_load': msl, 'topology': topo,
}

# === 7健康运行定义 ===
RUNS_7 = {
    'BOL':      {'bat': 0.0, 'fc': 0.0, 'el': 0.0},
    'BAT_D050': {'bat': 0.50, 'fc': 0.0, 'el': 0.0},
    'BAT_D090': {'bat': 0.90, 'fc': 0.0, 'el': 0.0},
    'FC_D050':  {'bat': 0.0, 'fc': 0.50, 'el': 0.0},
    'FC_D090':  {'bat': 0.0, 'fc': 0.90, 'el': 0.0},
    'EL_D050':  {'bat': 0.0, 'fc': 0.0, 'el': 0.50},
    'EL_D090':  {'bat': 0.0, 'fc': 0.0, 'el': 0.90},
}

# === 6候选路径定义 ===
PATHS_6 = {
    'alpha1': {'source': 'BAT', 'target': 'FC', 'desc': 'BAT→FC'},
    'alpha2': {'source': 'EL',  'target': 'FC', 'desc': 'EL→FC'},
    'beta1':  {'source': 'BAT', 'target': 'EL', 'desc': 'BAT→EL'},
    'beta2':  {'source': 'FC',  'target': 'EL', 'desc': 'FC→EL'},
    'gamma1': {'source': 'FC',  'target': 'BAT', 'desc': 'FC→BAT'},
    'gamma2': {'source': 'EL',  'target': 'BAT', 'desc': 'EL→BAT'},
}

# === 步骤1: 对每个健康运行, 执行EMS调度+全ON损伤计算 ===
print("\n[1] 执行7健康运行 × EMS调度...")

run_commands = {}  # {run_label: ems_commands}
run_damages_full = {}  # {run_label: {dD_bat, dD_fc, dD_el}} (全ON, coalition=1111)

for run_label, D_state in RUNS_7.items():
    # BAT退化感知EMS参数
    q_frac, r0, p_max = hp_bat(D_state['bat'])
    bat_p_max = bat_rated_kw * q_frac if D_state['bat'] > 0 else None
    bat_cap = bat_capacity_kwh * q_frac if D_state['bat'] > 0 else None

    np.random.seed(42)
    cmds, state = rule_based_dispatch(
        load_kw, pv_kw, cfg,
        bat_p_max_kw=bat_p_max, bat_capacity_kwh=bat_cap
    )
    run_commands[run_label] = cmds

    # 全ON损伤计算 (coalition=1111, 所有归因激活)
    coalition_full = CoalitionState.from_code('1111')
    dD_dict = compute_damage(hp_bat, cmds, config_dict, dt_min=15.0,
                              D_state=D_state, coalition=coalition_full)
    run_damages_full[run_label] = dD_dict

    total = float(np.sum(dD_dict['dD_bat']) + np.sum(dD_dict['dD_fc']) + np.sum(dD_dict['dD_el']))
    print(f"  {run_label:10s}: D={D_state} → dD_bat={np.sum(dD_dict['dD_bat']):.6f}, "
          f"dD_fc={np.sum(dD_dict['dD_fc']):.6f}, dD_el={np.sum(dD_dict['dD_el']):.6f}, total={total:.6f}")

# === 步骤2: 计算每条路径的AE ===
print("\n[2] 计算6候选路径AE值...")

path_results = {}  # {path_key: {run_label: {AE, TE, dD_coupled, dD_baseline}}}

for path_key, path_info in PATHS_6.items():
    source = path_info['source'].lower()  # 'bat', 'fc', 'el'
    target = path_info['target'].lower()  # 'bat', 'fc', 'el'
    dD_key = f'dD_{target}'

    # 基线 = BOL运行的目标设备损伤
    dD_baseline = np.sum(run_damages_full['BOL'][dD_key])

    # 退化运行 = source设备退化的运行
    for d_level in ['D050', 'D090']:
        run_label = f"{path_info['source']}_{d_level}"
        dD_coupled = np.sum(run_damages_full[run_label][dD_key])

        AE = dD_coupled - dD_baseline
        # TE分母保护
        if abs(dD_baseline) < 1e-4:
            TE = float('nan')
            te_str = 'NA'
        else:
            TE = AE / abs(dD_baseline)
            te_str = f"{TE*100:.2f}%"

        if path_key not in path_results:
            path_results[path_key] = {}
        path_results[path_key][run_label] = {
            'AE': AE, 'TE': TE, 'te_str': te_str,
            'dD_coupled': dD_coupled, 'dD_baseline': dD_baseline,
        }

        print(f"  {path_info['desc']:8s} {run_label:10s}: AE={AE:+.6f}, TE={te_str}")

# === 步骤3: 路径激活判定 ===
print("\n[3] 路径激活判定 (AE>0且TE>=5%为激活)...")

TE_THRESHOLD = 0.05  # 5%
path_status = {}  # {path_key: 'activated'/'conditional'/'zero'}

for path_key, path_info in PATHS_6.items():
    results_d050 = path_results[path_key].get(f"{path_info['source']}_D050", {})
    results_d090 = path_results[path_key].get(f"{path_info['source']}_D090", {})

    ae_050 = results_d050.get('AE', 0)
    ae_090 = results_d090.get('AE', 0)
    te_050 = results_d050.get('TE', float('nan'))
    te_090 = results_d090.get('TE', float('nan'))

    # 激活条件: D=0.90时AE>0且TE>=5%
    activated = (ae_090 > 0) and (not np.isnan(te_090)) and (te_090 >= TE_THRESHOLD)
    # 条件性: D=0.50时AE>0但TE<5%, 或D=0.90时AE>0但TE<5%
    conditional = (ae_050 > 0 or ae_090 > 0) and not activated
    # 零路径: AE≈0 (|AE|<1e-6)
    zero_path = abs(ae_090) < 1e-6

    if activated:
        status = 'activated'
    elif zero_path:
        status = 'zero'
    else:
        status = 'conditional'

    path_status[path_key] = status
    print(f"  {path_info['desc']:8s}: AE(D090)={ae_090:+.6f}, TE(D090)={results_d090.get('te_str','NA'):>8s} → {status.upper()}")

n_activated = sum(1 for s in path_status.values() if s == 'activated')
n_conditional = sum(1 for s in path_status.values() if s == 'conditional')
n_zero = sum(1 for s in path_status.values() if s == 'zero')
N_eff = n_activated + n_conditional  # 有效路径数

print(f"\n  路径统计: 激活={n_activated}, 条件性={n_conditional}, 零路径={n_zero}")
print(f"  N_eff (有效路径数) = {N_eff}")

# === 步骤4: NC1控制试验 (健康映射旁路残差) ===
print("\n[4] NC1控制试验: g_i(D)=g_i(0)条件下的数值泄漏...")

# NC1: 用BOL命令 + C/E/P OFF (coalition=0001, I_ON), 检查不同D_state下损伤是否一致
# 关键: 用BOL EMS命令(非退化命令), 确保EMS调度不引入退化依赖
coalition_nc1 = CoalitionState.from_code('0001')  # C/E/P OFF, I ON
bol_cmds = run_commands['BOL']
nc1_max_residual = 0
nc1_details = {}

for run_label, D_state in RUNS_7.items():
    # NC1用BOL命令 + C/E/P OFF, 仅改变D_state
    dD_dict = compute_damage(hp_bat, bol_cmds, config_dict, dt_min=15.0,
                              D_state=D_state, coalition=coalition_nc1)
    total = float(np.sum(dD_dict['dD_bat']) + np.sum(dD_dict['dD_fc']) + np.sum(dD_dict['dD_el']))
    nc1_details[run_label] = total

# NC1残差 = 不同D_state下C/E/P OFF损伤的差异 (应≈0, 因退化映射被旁路)
bol_off = nc1_details['BOL']
for run_label in ['BAT_D090', 'FC_D090', 'EL_D090']:
    residual = abs(nc1_details[run_label] - bol_off)
    nc1_max_residual = max(nc1_max_residual, residual)
    print(f"  NC1 {run_label}: total_OFF={nc1_details[run_label]:.8f}, residual_vs_BOL={residual:.2e}")

# NC1还应检查I_OFF的影响 (M4异常)
# I_OFF时BAT循环×1.5, 需单独验证
coalition_i_off = CoalitionState.from_code('1110')  # I_OFF only
for run_label in ['BAT_D090']:
    cmds = run_commands[run_label]
    dD_i_off = apply_coupling(hp_bat, cmds, coalition_i_off, config_dict, D_state=RUNS_7[run_label])
    dD_full = apply_coupling(hp_bat, cmds, CoalitionState.from_code('1111'), config_dict, D_state=RUNS_7[run_label])
    i_effect = float(np.sum(dD_i_off['dD_bat']) - np.sum(dD_full['dD_bat']))
    print(f"  NC1 I_OFF({run_label}): dD_bat(I_OFF)-dD_bat(full)={i_effect:+.6f} (M4方向: {'增加(物理合理)' if i_effect>0 else '减少'})")

c03_pass = nc1_max_residual <= 1e-4
print(f"  NC1最大残差: {nc1_max_residual:.2e} (阈值≤1e-4) → {'PASS' if c03_pass else 'FAIL'}")

# === 步骤5: 16联盟值表 (用于VS4 Shapley分解) ===
print("\n[5] 16联盟值表 (BAT_D090运行)...")

coalition_values = {}
coalition_states = CoalitionState.all_16_states()
D_test = RUNS_7['BAT_D090']
cmds_test = run_commands['BAT_D090']

for cs in coalition_states:
    result = apply_coupling(hp_bat, cmds_test, cs, config_dict, D_state=D_test)
    v = result['total_damage']
    coalition_values[cs.code] = v

print(f"  16联盟值已计算, 范围: [{min(coalition_values.values()):.6f}, {max(coalition_values.values()):.6f}]")
print(f"  v(1111)={coalition_values['1111']:.6f}, v(0000)={coalition_values['0000']:.6f}")

# === VS3 结果汇总 ===
print("\n" + "=" * 70)
print("VS3 验证结果汇总")
print("=" * 70)

i01_pass = len(PATHS_6) == 6  # 六路径覆盖
i06_pass = len(RUNS_7) == 7   # 七健康运行覆盖

results = {
    'I01': {'metric': '六路径覆盖', 'value': f"{len(PATHS_6)}/6", 'pass': i01_pass},
    'I06': {'metric': '七健康运行覆盖', 'value': f"{len(RUNS_7)}/7", 'pass': i06_pass},
    'C03': {'metric': 'NC1数值泄漏残差', 'value': f"{nc1_max_residual:.2e}", 'threshold': '1e-4', 'pass': c03_pass},
    'N_eff': {'metric': '有效路径数', 'value': N_eff, 'pass': N_eff >= 1},
}

for check_id, r in results.items():
    print(f"  {check_id}: {r['metric']} = {r['value']} → {'PASS' if r.get('pass') else 'FAIL'}")

print(f"\n  路径识别结果:")
for path_key, path_info in PATHS_6.items():
    status = path_status[path_key]
    ae_090 = path_results[path_key].get(f"{path_info['source']}_D090", {}).get('AE', 0)
    te_090 = path_results[path_key].get(f"{path_info['source']}_D090", {}).get('te_str', 'NA')
    print(f"    {path_info['desc']:8s}: AE={ae_090:+.6f}, TE={te_090:>8s} → {status}")

all_pass = all(r.get('pass', False) for r in results.values())
print(f"\n  VS3 总体: {'PASS' if all_pass else 'FAIL'} (N_eff={N_eff})")

# === 生成图表 ===
print("\n[绘图] 生成VS3路径识别图表...")

fig = plt.figure(figsize=(14, 10))
gs = GridSpec(2, 2, hspace=0.35, wspace=0.35, left=0.08, right=0.78, top=0.92, bottom=0.08)

# (a) 6路径AE柱状图 (D=0.90)
ax1 = fig.add_subplot(gs[0, 0])
path_names = [PATHS_6[k]['desc'] for k in PATHS_6]
ae_090_vals = [path_results[k].get(f"{PATHS_6[k]['source']}_D090", {}).get('AE', 0) for k in PATHS_6]
colors = [C_ACT if path_status[k]=='activated' else C_COND if path_status[k]=='conditional' else C_ZERO for k in PATHS_6]
bars = ax1.bar(range(6), ae_090_vals, color=colors, edgecolor='k', linewidth=0.5)
ax1.set_xticks(range(6)); ax1.set_xticklabels(path_names, rotation=30, ha='right')
ax1.set_ylabel('AE (D=0.90)')
ax1.set_title('(a) 6-path AE at D=0.90', fontsize=10, fontweight='bold')
ax1.axhline(y=0, color='k', lw=0.5)
ax1.grid(True, alpha=0.3, axis='y')
# 标注状态
for i, (k, s) in enumerate(path_status.items()):
    ax1.text(i, ae_090_vals[i] + (max(ae_090_vals)*0.05 if ae_090_vals[i]>=0 else -max(ae_090_vals)*0.05),
             s[:4].upper(), ha='center', fontsize=7, fontweight='bold')

# (b) 路径激活矩阵 (7运行 × 6路径 AE热力图)
ax2 = fig.add_subplot(gs[0, 1])
run_labels = list(RUNS_7.keys())
ae_matrix = np.zeros((7, 6))
for j, (pk, pi) in enumerate(PATHS_6.items()):
    for i, rl in enumerate(run_labels):
        if rl == 'BOL':
            ae_matrix[i, j] = 0
        else:
            ae_matrix[i, j] = path_results[pk].get(rl, {}).get('AE', 0)

im = ax2.imshow(ae_matrix, cmap='RdBu_r', aspect='auto', vmin=-max(abs(ae_matrix.max()), abs(ae_matrix.min())),
                vmax=max(abs(ae_matrix.max()), abs(ae_matrix.min())))
ax2.set_xticks(range(6)); ax2.set_xticklabels(path_names, rotation=30, ha='right')
ax2.set_yticks(range(7)); ax2.set_yticklabels(run_labels, fontsize=8)
ax2.set_title('(b) AE matrix (7 runs x 6 paths)', fontsize=10, fontweight='bold')
plt.colorbar(im, ax=ax2, label='AE', shrink=0.8)

# (c) 16联盟值表
ax3 = fig.add_subplot(gs[1, 0])
codes = sorted(coalition_values.keys())
vals = [coalition_values[c] for c in codes]
ax3.bar(range(16), vals, color=C_BAT, alpha=0.7, edgecolor='k', linewidth=0.3)
ax3.set_xticks(range(16)); ax3.set_xticklabels(codes, rotation=90, fontsize=6)
ax3.set_ylabel('Total damage v(S)')
ax3.set_title('(c) 16-coalition values (BAT_D090)', fontsize=10, fontweight='bold')
ax3.axhline(y=coalition_values['1111'], color=C_ACT, ls='--', lw=1, label=f'v(M)={coalition_values["1111"]:.4f}')
ax3.axhline(y=coalition_values['0000'], color=C_ZERO, ls='--', lw=1, label=f'v(0)={coalition_values["0000"]:.4f}')
ax3.legend(loc='upper left', fontsize=7)
ax3.grid(True, alpha=0.3, axis='y')

# (d) Path AE & status — 横向条形图(AE强度) + inset 分类饼图(比例)
# 修正: 原饼图 red=zero 误导为"失败"; zero path 实为数值零(非错误)
ax4 = fig.add_subplot(gs[1, 1])
path_keys = list(PATHS_6.keys())
path_labels = [PATHS_6[k]['desc'] for k in path_keys]
ae_vals = [path_results[k].get(f"{PATHS_6[k]['source']}_D090", {}).get('AE', 0) for k in path_keys]
status_list = [path_status[k] for k in path_keys]

# 状态配色中性化: activated=蓝, conditional=橙, zero=灰 (避免红=危险误导)
ST_COLOR = {'activated': '#1f77b4', 'conditional': '#ff7f0e', 'zero': '#B0B0B0'}
bar_colors = [ST_COLOR[s] for s in status_list]

y_pos = np.arange(len(path_keys))[::-1]  # 顶部=第一条路径
ax4.barh(y_pos, ae_vals, color=bar_colors, edgecolor='k', linewidth=0.4, height=0.62)
ax4.set_yticks(y_pos)
ax4.set_yticklabels(path_labels, fontsize=8)
ax4.set_xlabel('AE (D=0.90)')
max_ae = max((abs(a) for a in ae_vals), default=1e-6) or 1e-6
ax4.set_xlim(0, max_ae * 2.0)
ax4.grid(True, alpha=0.3, axis='x')
ax4.axvline(0, color='k', lw=0.5)

# 条末标注: AE 数值 + 状态 (zero 类标 "ZERO (numeric null)" 说明非失败)
for y, ae, st in zip(y_pos, ae_vals, status_list):
    if st == 'zero':
        label = 'ZERO (numeric null)'
        xpos = max_ae * 0.02
    else:
        label = f"{ae:.2e} {st[:4].upper()}"
        xpos = ae + max_ae * 0.02
    ax4.text(xpos, y, label, va='center', fontsize=6.5,
             fontweight='bold', color=ST_COLOR[st])

ax4.set_title(f'(d) Path AE & status (N_eff={N_eff})', fontsize=10, fontweight='bold')

# inset: 分类计数饼图 (仅画非零类, 隐藏 0% 标签, 避免悬空 "0%")
axins = ax4.inset_axes([0.58, 0.04, 0.40, 0.46])
pie_counts = {'activated': n_activated, 'conditional': n_conditional, 'zero': n_zero}
pie_labels = [k for k, v in pie_counts.items() if v > 0]
pie_vals = [pie_counts[k] for k in pie_labels]
pie_colors = [ST_COLOR[k] for k in pie_labels]
def _autopct(pct):
    return f"{pct:.0f}%" if pct >= 1 else ""
axins.pie(pie_vals, labels=pie_labels, colors=pie_colors,
          autopct=_autopct, startangle=90, textprops={'fontsize': 7})
axins.set_title(f"N_eff={N_eff}\n(zero=numeric null)", fontsize=7, fontweight='bold')

fig.suptitle('VS3 Path Identification Verification (M2 Core Layer)\n'
             f'6 paths x 7 health runs | N_eff={N_eff} | NC1 residual={nc1_max_residual:.1e}',
             fontsize=12, fontweight='bold', y=0.98)

result_text = f"VS3 Overall: {'PASS' if all_pass else 'FAIL'} | "
for cid, r in results.items():
    result_text += f"{cid}: {'PASS' if r.get('pass') else 'FAIL'} | "
fig.text(0.08, 0.02, result_text, fontsize=10, fontweight='bold',
         color=C_ACT if all_pass else C_ZERO)

fig.savefig(OUT_FIG, dpi=300, bbox_inches='tight')
plt.close(fig)
print(f"  图表已保存: {OUT_FIG} ({OUT_FIG.stat().st_size // 1024} KB)")

# === 导出CSV ===
with open(OUT_CSV, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['Path', 'Source', 'Target', 'Run', 'AE', 'TE', 'Status'])
    for pk, pi in PATHS_6.items():
        for rl in [f"{pi['source']}_D050", f"{pi['source']}_D090"]:
            r = path_results[pk].get(rl, {})
            w.writerow([pi['desc'], pi['source'], pi['target'], rl,
                       f"{r.get('AE', 0):.8f}", r.get('te_str', 'NA'),
                       path_status[pk]])
    w.writerow([])
    w.writerow(['Check', 'Metric', 'Value', 'Threshold', 'Pass'])
    for cid, r in results.items():
        w.writerow([cid, r['metric'], r['value'], r.get('threshold', ''), r.get('pass', '')])
    w.writerow([])
    w.writerow(['Coalition', 'v(S)'])
    for c in codes:
        w.writerow([c, f"{coalition_values[c]:.8f}"])

print(f"  CSV已保存: {OUT_CSV}")

print(f"\n{'='*70}")
print(f"VS3 路径识别验证: {'PASS' if all_pass else 'FAIL'} (N_eff={N_eff})")
print(f"{'='*70}")
