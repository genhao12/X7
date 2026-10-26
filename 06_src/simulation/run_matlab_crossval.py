# -*- coding: utf-8 -*-
"""
P4 MATLAB Cross-Validation - BLAST-Lite LFP|Gr 独立验证Python编排脚本
1. 运行1周EMS获取SoC轨迹 → 导出CSV
2. 调用MATLAB运行BLAST-Lite LFP|Gr老化模型
3. 加载BLAST结果, 与Python hp_bat_lite Q(D)/R0(D)对比
4. 验证方向+量级一致性 (偏差<20%)

输出:
  - 07_results/annual/soc_profile_for_blast.csv (SoC轨迹输入)
  - 07_results/annual/blast_lfp_results.csv (BLAST-Lite输出)
  - 07_results/figures/matlab_crossval.png (对比图)
"""
import sys, json, subprocess
import numpy as np
import pandas as pd
from pathlib import Path

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

C_PYTHON = '#3288BD'; C_MATLAB = '#D73027'
C_BAT = '#3288BD'; C_FC = '#D73027'; C_EL = '#66C2A5'

OUT_FIG = PROJECT_ROOT / "07_results" / "figures" / "matlab_crossval.png"
SOC_CSV = PROJECT_ROOT / "07_results" / "annual" / "soc_profile_for_blast.csv"
BLAST_CSV = PROJECT_ROOT / "07_results" / "annual" / "blast_lfp_results.csv"
MATLAB_SCRIPT = PROJECT_ROOT / "06_src" / "simulation" / "run_blast_crossval.m"

STEPS_PER_WEEK = 672

from ems.ems_bf_lite import EMSConfig, rule_based_dispatch
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

hppc_data = _load_280ah_data()
bat_fit = fit_hppc(hppc_data)
hp_bat = interp_bat(bat_fit, {'bat_kwh': cap['BAT_kWh'],
                               'bat_voltage_nominal': 3.2*48,
                               'bat_rated_kw': cap['BAT_kWh'] * 0.5})

csv_path = str(PROJECT_ROOT / "02_data_processed" / "tradestreet_pipeline" /
               "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv")

print("=" * 70)
print("P4 MATLAB Cross-Validation - BLAST-Lite LFP|Gr")
print("=" * 70)

# === Step 1: 获取SoC轨迹 ===
print("\n[1] 运行1周EMS获取SoC轨迹...")
raw = pd.read_csv(csv_path, parse_dates=['timestamp_local_civil'])
df_2017 = raw[raw['local_civil_year'] == 2017].copy()
df_2017 = df_2017.rename(columns={
    'p_pv_available_reconstructed_nonnegative_kW': 'P_pv_kW',
    'p_building_load_reconstructed_kW': 'P_load_kW',
})
df_2017['P_pv_kW'] = df_2017['P_pv_kW'].ffill().bfill()
df_2017['P_load_kW'] = df_2017['P_load_kW'].ffill().bfill()

pv_w = df_2017['P_pv_kW'].values[:STEPS_PER_WEEK]
load_w = df_2017['P_load_kW'].values[:STEPS_PER_WEEK]

np.random.seed(42)
cmds, state = rule_based_dispatch(load_w, pv_w, cfg)
soc_trace = state['soc_trace']
print(f"  SoC轨迹: {len(soc_trace)}步, 范围[{soc_trace.min():.3f}, {soc_trace.max():.3f}]")

# 导出SoC轨迹为CSV (tsec, soc)
dt_sec = 900  # 15min = 900s
tsec = np.arange(len(soc_trace)) * dt_sec
soc_df = pd.DataFrame({'tsec': tsec, 'soc': soc_trace})
soc_df.to_csv(SOC_CSV, index=False)
print(f"  SoC CSV已导出: {SOC_CSV}")

# === Step 2: 运行MATLAB BLAST-Lite ===
print("\n[2] 运行MATLAB BLAST-Lite LFP|Gr...")
matlab_exe = r"D:\Matlab R2024b\bin\matlab.exe"
cmd = [matlab_exe, '-batch', f"run('{MATLAB_SCRIPT.as_posix()}')"]
print(f"  命令: {cmd[0]} -batch run(...)")
result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=str(MATLAB_SCRIPT.parent))
print(result.stdout)
if result.returncode != 0:
    print(f"  MATLAB STDERR: {result.stderr}")
    print("  [警告] MATLAB执行可能失败, 尝试读取输出文件...")

# === Step 3: 加载BLAST-Lite结果 ===
print("\n[3] 加载BLAST-Lite结果...")
if not BLAST_CSV.exists():
    print(f"  [错误] BLAST结果文件不存在: {BLAST_CSV}")
    print("  请手动运行MATLAB脚本后重试")
    sys.exit(1)

blast = pd.read_csv(BLAST_CSV)
print(f"  BLAST结果: {len(blast)}天, q范围[{blast['q'].min():.4f}, {blast['q'].max():.4f}]")
print(f"  最终: q={blast['q'].iloc[-1]:.4f}, r={blast['r'].iloc[-1]:.4f}, EFC={blast['EFC'].iloc[-1]:.1f}")

# === Step 4: Python模型对比 ===
print("\n[4] Python hp_bat_lite 对比...")

# Python Q(D)映射: Q = 1 - 0.2*D (D=0→Q=1.0, D=1→Q=0.8)
# Python R0(D)映射: R0 = R0_BOL * (1 + 0.1673*D)
# 从年度仿真获取D_bat轨迹
# P1年度仿真D_bat年末=0.9162, 线性近似: D(t) = 0.9162 * t/365

blast_t_days = blast['t_days'].values
blast_q = blast['q'].values
blast_r = blast['r'].values
blast_efc = blast['EFC'].values

# Python模型: 从年度仿真D轨迹计算Q(t)和r(t)
# D_bat年度末=0.9162 (P1结果), 线性外推到2年
D_bat_annual = 0.9162  # P1年度末D
# 线性外推: D(t) = D_bat_annual * t/365
D_py = np.minimum(D_bat_annual * blast_t_days / 365.0, 1.0)
Q_py = 1.0 - 0.2 * D_py  # hp_bat_lite Q(D) = 1 - 0.2*D
R0_ratio_py = 1.0 + 0.1673 * D_py  # hp_bat_lite R0(D) = R0_BOL*(1+0.1673*D)

# 同时用hp_bat函数获取更精确的Q值和r值
Q_py_precise = np.zeros(len(blast_t_days))
R_py_precise = np.zeros(len(blast_t_days))
# Get BOL resistance for normalization
_, r0_bol, _ = hp_bat(0.0)
for i, D in enumerate(D_py):
    q_frac, r0, p_max = hp_bat(D)
    Q_py_precise[i] = q_frac
    R_py_precise[i] = r0 / r0_bol  # actual r ratio from hp_bat r0(D)/r0(0)

# === Step 5: 偏差分析 ===
print("\n[5] 偏差分析...")

# 在1年(365天)和2年(730天)处比较
for t_check in [365, 730]:
    if t_check > blast_t_days[-1]:
        continue
    idx = np.argmin(np.abs(blast_t_days - t_check))
    q_blast = blast_q[idx]
    q_py = Q_py[idx]
    deviation = abs(q_blast - q_py) / max(abs(q_py), 1e-6) * 100

    r_blast = blast_r[idx]
    r_py = R0_ratio_py[idx]
    r_dev = abs(r_blast - r_py) / max(abs(r_py), 1e-6) * 100

    D_at_t = D_py[idx]
    print(f"  t={t_check}天: D_py={D_at_t:.4f}")
    print(f"    Q: BLAST={q_blast:.4f}, Python={q_py:.4f}, 偏差={deviation:.1f}%")
    print(f"    r: BLAST={r_blast:.4f}, Python={r_py:.4f}, 偏差={r_dev:.1f}%")

# 方向一致性
q_decline_blast = blast_q[-1] < blast_q[0]
q_decline_py = Q_py[-1] < Q_py[0]
r_growth_blast = blast_r[-1] > blast_r[0]
r_growth_py = R0_ratio_py[-1] > R0_ratio_py[0]

print(f"\n  方向一致性:")
print(f"    Q下降: BLAST={'是' if q_decline_blast else '否'}, Python={'是' if q_decline_py else '否'} -> {'PASS' if q_decline_blast == q_decline_py else 'FAIL'}")
print(f"    r增长: BLAST={'是' if r_growth_blast else '否'}, Python={'是' if r_growth_py else '否'} -> {'PASS' if r_growth_blast == r_growth_py else 'FAIL'}")

# 量级偏差 (1年处)
idx_1y = np.argmin(np.abs(blast_t_days - 365))
q_dev_1y = abs(blast_q[idx_1y] - Q_py[idx_1y]) / max(abs(Q_py[idx_1y]), 1e-6) * 100
r_dev_1y = abs(blast_r[idx_1y] - R0_ratio_py[idx_1y]) / max(abs(R0_ratio_py[idx_1y]), 1e-6) * 100
magnitude_pass = q_dev_1y < 20 and r_dev_1y < 20
print(f"\n  量级偏差(1年处): Q={q_dev_1y:.1f}%, r={r_dev_1y:.1f}%")
print(f"  阈值<20%: {'PASS' if magnitude_pass else 'FAIL'}")

# === Step 6: 生成图表 ===
print("\n[6] 生成交叉验证图表...")

fig = plt.figure(figsize=(14, 10))
gs = GridSpec(2, 2, hspace=0.35, wspace=0.30, left=0.08, right=0.92, top=0.92, bottom=0.08)

# (a) Q(t) 对比
ax1 = fig.add_subplot(gs[0, 0])
t_years = blast_t_days / 365
ax1.plot(t_years, blast_q, color=C_MATLAB, label='BLAST-Lite LFP|Gr', linewidth=2)
ax1.plot(t_years, Q_py, color=C_PYTHON, label='Python hp_bat_lite', linewidth=2, ls='--')
ax1.plot(t_years, Q_py_precise, color=C_PYTHON, label='Python hp_bat (precise)', linewidth=1, ls=':', alpha=0.5)
ax1.axhline(0.8, color='gray', ls='--', lw=0.8, label='EOL (Q=0.8)')
ax1.set_xlabel('Time (years)')
ax1.set_ylabel('Relative capacity Q')
ax1.set_title(f'(a) Capacity Degradation: Q(t)\nDeviation@1yr: {q_dev_1y:.1f}%', fontsize=10, fontweight='bold')
ax1.legend(fontsize=8)
ax1.grid(True, alpha=0.3)
ax1.set_xlim(0, max(t_years))

# (b) r(t) 对比
ax2 = fig.add_subplot(gs[0, 1])
ax2.plot(t_years, blast_r, color=C_MATLAB, label='BLAST-Lite LFP|Gr', linewidth=2)
ax2.plot(t_years, R0_ratio_py, color=C_PYTHON, label='Python hp_bat_lite', linewidth=2, ls='--')
ax2.plot(t_years, R_py_precise, color=C_PYTHON, label='Python hp_bat (precise)', linewidth=1, ls=':', alpha=0.5)
ax2.set_xlabel('Time (years)')
ax2.set_ylabel('Relative resistance r')
ax2.set_title(f'(b) Resistance Growth: r(t)\nDeviation@1yr: {r_dev_1y:.1f}%', fontsize=10, fontweight='bold')
ax2.legend(fontsize=8)
ax2.grid(True, alpha=0.3)
ax2.set_xlim(0, max(t_years))

# (c) Q分解 (BLAST: 日历+循环)
ax3 = fig.add_subplot(gs[1, 0])
ax3.plot(t_years, blast_q, 'k-', label='Total Q', linewidth=2)
ax3.plot(t_years, 1 - blast['qLossCal'], color=C_BAT, label='1 - Calendar loss', linewidth=1.5, ls='--')
ax3.plot(t_years, 1 - blast['qLossCyc'], color=C_FC, label='1 - Cycling loss', linewidth=1.5, ls='--')
ax3.axhline(0.8, color='gray', ls=':', lw=0.8)
ax3.set_xlabel('Time (years)')
ax3.set_ylabel('Relative capacity')
ax3.set_title('(c) BLAST-Lite Q Decomposition\n(Calendar vs Cycling)', fontsize=10, fontweight='bold')
ax3.legend(fontsize=8)
ax3.grid(True, alpha=0.3)

# (d) D(t) 对比 + EFC
ax4 = fig.add_subplot(gs[1, 1])
# BLAST D(t) = 1 - (Q - 0.8) / 0.2
D_blast = np.maximum(1 - (blast_q - 0.8) / 0.2, 0)
ax4.plot(t_years, D_blast, color=C_MATLAB, label='D from BLAST Q(t)', linewidth=2)
ax4.plot(t_years, D_py, color=C_PYTHON, label='D from Python (P1 linear)', linewidth=2, ls='--')
ax4.axhline(1.0, color='gray', ls='--', lw=0.8, label='EOL (D=1.0)')
ax4r = ax4.twinx()
ax4r.plot(t_years, blast_efc, color=C_EL, label='EFC', linewidth=1, alpha=0.5, ls='-.')
ax4r.set_ylabel('Equivalent Full Cycles', color=C_EL)
ax4.set_xlabel('Time (years)')
ax4.set_ylabel('Degradation D')
ax4.set_title('(d) D(t) Comparison & EFC', fontsize=10, fontweight='bold')
lines1, labels1 = ax4.get_legend_handles_labels()
lines2, labels2 = ax4r.get_legend_handles_labels()
ax4.legend(lines1 + lines2, labels1 + labels2, fontsize=7)
ax4.grid(True, alpha=0.3)

# 汇总
direction_pass = q_decline_blast == q_decline_py and r_growth_blast == r_growth_py
overall_pass = direction_pass and magnitude_pass

fig.suptitle(f'P4 MATLAB Cross-Validation: BLAST-Lite LFP|Gr vs Python hp_bat_lite\n'
             f'Direction: {"PASS" if direction_pass else "FAIL"} | '
             f'Magnitude(<20%): {"PASS" if magnitude_pass else "FAIL"} | '
             f'Overall: {"PASS" if overall_pass else "FAIL"}',
             fontsize=12, fontweight='bold', y=0.98)

fig.savefig(OUT_FIG, dpi=300, bbox_inches='tight')
plt.close(fig)
print(f"  图表已保存: {OUT_FIG}")

# === 汇总 ===
print("\n" + "=" * 70)
print("P4 MATLAB Cross-Validation 完成")
print("=" * 70)
print(f"  方向一致性: Q下降={'PASS' if q_decline_blast==q_decline_py else 'FAIL'}, "
      f"r增长={'PASS' if r_growth_blast==r_growth_py else 'FAIL'}")
print(f"  量级偏差(1年): Q={q_dev_1y:.1f}%, r={r_dev_1y:.1f}% (阈值<20%)")
print(f"  总体验证: {'PASS' if overall_pass else 'FAIL'}")
print(f"  BLAST-Lite: Q(1yr)={blast_q[idx_1y]:.4f}, r(1yr)={blast_r[idx_1y]:.4f}")
print(f"  Python:     Q(1yr)={Q_py[idx_1y]:.4f}, r(1yr)={R0_ratio_py[idx_1y]:.4f}")
print(f"  输出: {OUT_FIG}")
print("=" * 70)
