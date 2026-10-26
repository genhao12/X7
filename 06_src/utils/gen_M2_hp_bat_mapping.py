# -*- coding: utf-8 -*-
"""
gen_M2_hp_bat_mapping.py
生成 M2_hp_bat_mapping.png — BAT健康-性能映射图
- 5个子图: OCV(SoC) / R0(T,SoC)热力图 / Q(D)容量衰减 / R0(D)阻抗增长 / HP映射曲线
- 标签统一放在右上方, 半透明边框不遮挡数据
- 所有数值基于280Ah实测数据 + 拟合结果实时计算
"""
import sys
import json
import csv
import numpy as np
from pathlib import Path
from datetime import datetime

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.gridspec import GridSpec

# 导入M2模块
PROJECT_ROOT = Path(r"D:\matlab\C")
sys.path.insert(0, str(PROJECT_ROOT / "06_src"))
from components.hp_bat_lite import (
    _load_280ah_data, fit_hppc, interpolate_hp, validate_hp_func,
    _soh_to_d, _d_to_soh, SOH_EOL
)

OUT_PNG = PROJECT_ROOT / "07_results" / "figures" / "M2_hp_bat_mapping.png"

# --- 颜色 ---
C_DATA = '#3288BD'       # 蓝(实测数据点)
C_FIT = '#D73027'        # 红(拟合曲线)
C_EXTRAP = '#FDAE61'     # 橙(外推段)
C_Q = '#66C2A5'          # 青绿(容量)
C_R0 = '#FC8D62'         # 橙(阻抗)
C_PMAX = '#ABDDA4'       # 浅绿(功率)
C_REF = '#999999'        # 灰(参考线)

# === 加载数据并拟合 ===
print("[1] 加载280Ah数据...")
hppc_data = _load_280ah_data()

print("[2] 拟合HPPC...")
fit_result = fit_hppc(hppc_data)

print("[3] 构建HP映射...")
bat_bol = {
    'bat_kwh': 135.0,
    'bat_voltage_nominal': 3.2 * 48,
    'bat_rated_kw': 135.0 * 0.5,
}
hp_func = interpolate_hp(fit_result, bat_bol)

# === 提取绘图数据 ===
# OCV(SoC)
ocv_data = hppc_data['ocv_soc']
ocv_soc = np.array([d['SoC'] for d in ocv_data])
ocv_25c = np.array([d['OCV_25C_V'] for d in ocv_data])

# R0(T,SoC) BOL 热力图
hppc = hppc_data['bol_hppc']
T_vals = sorted(set(d['T_C'] for d in hppc))
soc_vals = sorted(set(d['SoC'] for d in hppc))
R0_grid = np.zeros((len(T_vals), len(soc_vals)))
for d in hppc:
    ti = T_vals.index(d['T_C'])
    si = soc_vals.index(d['SoC'])
    R0_grid[ti, si] = d['R0_Ohm'] * 1000  # mOhm

# 老化数据
aging = hppc_data['aging']
cycles_arr = np.array([d['cycles'] for d in aging])
soh_arr = np.array([d['SoH'] for d in aging])
r0_aging = np.array([d['R0_Ohm'] for d in aging])
cap_ah = np.array([d['Capacity_Ah'] for d in aging])
d_arr = np.array([_soh_to_d(s) for s in soh_arr])
q_frac_arr = soh_arr / soh_arr[0]
r0_growth_arr = r0_aging / r0_aging[0]

# 拟合曲线 (含外推)
d_fit = np.linspace(0, 1, 101)
q_fit = np.array([fit_result['capacity_decay_func'](d) for d in d_fit])
r0_growth_fit = np.array([fit_result['r0_growth_func'](d) for d in d_fit])
d_data_max = d_arr[-1]  # 数据边界

# HP映射曲线
hp_q = np.array([hp_func(d)[0] for d in d_fit])
hp_r0 = np.array([hp_func(d)[1] * 1000 for d in d_fit])  # mOhm
hp_pmax = np.array([hp_func(d)[2] for d in d_fit])

# === 绘图 ===
plt.rcParams.update({'axes.unicode_minus': False, 
    'font.family': 'sans-serif',
    'font.sans-serif': ['Microsoft YaHei', 'SimHei', 'Arial', 'DejaVu Sans'],
    'font.size': 10,
    'axes.titlesize': 11,
    'axes.labelsize': 10,
    'xtick.labelsize': 9,
    'ytick.labelsize': 9,
    'legend.fontsize': 8.5,
    'axes.linewidth': 0.8,
    'lines.linewidth': 1.4,
    'lines.markersize': 5,
    'grid.alpha': 0.25,
    'grid.linewidth': 0.5,
})

fig = plt.figure(figsize=(14, 10))
gs = GridSpec(2, 3, figure=fig, hspace=0.35, wspace=0.32,
              left=0.07, right=0.96, top=0.91, bottom=0.08)

# --- 子图1: OCV(SoC) ---
ax1 = fig.add_subplot(gs[0, 0])
ax1.plot(ocv_soc, ocv_25c, 'o', color=C_DATA, label='280Ah data (25C)')
soc_dense = np.linspace(0.05, 0.95, 100)
ocv_fit = fit_result['ocv_soc_func'](soc_dense)
ax1.plot(soc_dense, ocv_fit, '-', color=C_FIT, label=f'Linear interp (R²={fit_result["r2_ocv"]:.2f})')
ax1.set_xlabel('SoC')
ax1.set_ylabel('OCV (V)')
ax1.set_title('(a) OCV-SoC Curve')
ax1.set_xlim(0, 1)
ax1.grid(True, axis='y')
ax1.legend(loc='upper right', framealpha=0.9, edgecolor='#CCCCCC')

# --- 子图2: R0(T,SoC) BOL 热力图 ---
ax2 = fig.add_subplot(gs[0, 1])
im = ax2.pcolormesh(soc_vals, T_vals, R0_grid, shading='auto', cmap='YlOrRd')
cb = fig.colorbar(im, ax=ax2, label='R0 (mOhm)')
ax2.set_xlabel('SoC')
ax2.set_ylabel('Temperature (C)')
ax2.set_title('(b) BOL R0(T, SoC) Heatmap')
# 标注25C/SoC=0.5参考点
ax2.plot(0.5, 25, 's', color='white', markersize=8, markeredgecolor='black', markeredgewidth=1.5)
ax2.annotate(f'{R0_grid[T_vals.index(25), soc_vals.index(0.45)]:.3f}mOhm',
             xy=(0.5, 25), xytext=(0.55, 20), fontsize=8, color='white',
             arrowprops=dict(arrowstyle='->', color='white'))

# --- 子图3: Q(D) 容量衰减 ---
ax3 = fig.add_subplot(gs[0, 2])
# 数据段
mask_data = d_fit <= d_data_max
mask_extrap = d_fit > d_data_max
ax3.plot(d_arr, q_frac_arr, 'o', color=C_DATA, label=f'280Ah data ({len(aging)} pts)')
ax3.plot(d_fit[mask_data], q_fit[mask_data], '-', color=C_Q, linewidth=2,
         label=f'Linear fit (R²={fit_result["r2_q"]:.2f})')
ax3.plot(d_fit[mask_extrap], q_fit[mask_extrap], '--', color=C_EXTRAP, linewidth=1.5,
         label='Extrapolation')
ax3.axvline(d_data_max, color=C_REF, linestyle=':', linewidth=0.8, label=f'Data bound (D={d_data_max:.3f})')
ax3.axhline(SOH_EOL, color='#D73027', linestyle='--', linewidth=0.8, alpha=0.5, label=f'EOL (Q={SOH_EOL})')
ax3.set_xlabel('Degradation D')
ax3.set_ylabel('Capacity retention Q(D)')
ax3.set_title('(c) Capacity Decay Q(D)')
ax3.set_xlim(-0.02, 1.02)
ax3.set_ylim(0.78, 1.02)
ax3.grid(True, axis='y')
ax3.legend(loc='upper right', framealpha=0.9, edgecolor='#CCCCCC', fontsize=7.5)

# --- 子图4: R0(D) 阻抗增长 ---
ax4 = fig.add_subplot(gs[1, 0])
# 全部数据点 (含活化期)
ax4.plot(d_arr, r0_growth_arr, 'o', color=C_DATA, markersize=4,
         label=f'280Ah data ({len(aging)} pts)')
# 退化段数据点高亮
mask_aging = cycles_arr >= 200
ax4.plot(d_arr[mask_aging], r0_growth_arr[mask_aging], 's', color=C_R0, markersize=5,
         label=f'Aging phase (200+ cyc)')
# 拟合 (数据段)
ax4.plot(d_fit[mask_data], r0_growth_fit[mask_data], '-', color=C_R0, linewidth=2,
         label=f'Fit (R²={fit_result["r2_r0"]:.2f})')
# 外推
ax4.plot(d_fit[mask_extrap], r0_growth_fit[mask_extrap], '--', color=C_EXTRAP, linewidth=1.5,
         label='Extrapolation')
ax4.axvline(d_data_max, color=C_REF, linestyle=':', linewidth=0.8, label=f'Data bound')
ax4.set_xlabel('Degradation D')
ax4.set_ylabel('R0 growth factor')
ax4.set_title('(d) Resistance Growth R0(D)/R0_BOL')
ax4.set_xlim(-0.02, 1.02)
ax4.grid(True, axis='y')
ax4.legend(loc='upper right', framealpha=0.9, edgecolor='#CCCCCC', fontsize=7.5)

# --- 子图5: HP映射曲线 (Q/R0/Pmax vs D) ---
ax5 = fig.add_subplot(gs[1, 1])
ax5.plot(d_fit, hp_q, '-', color=C_Q, linewidth=2, label=f'Q(D) [beta={fit_result["beta_q"]:.3f}]')
ax5.plot(d_fit, hp_pmax / hp_func(0)[2], '--', color=C_PMAX, linewidth=1.5,
         label=f'P_max(D)/P_rated')
ax5.axhline(1.0, color=C_REF, linestyle=':', linewidth=0.6)
ax5.axhline(SOH_EOL, color='#D73027', linestyle='--', linewidth=0.6, alpha=0.5)
ax5.set_xlabel('Degradation D')
ax5.set_ylabel('Normalized value')
ax5.set_title('(e) Health-Performance Map')
ax5.set_xlim(-0.02, 1.02)
ax5.set_ylim(0.75, 1.05)
ax5.grid(True, axis='y')
ax5.legend(loc='upper right', framealpha=0.9, edgecolor='#CCCCCC')

# --- 子图6: R0(D) 绝对值 + 验证标记 ---
ax6 = fig.add_subplot(gs[1, 2])
ax6.plot(d_fit, hp_r0, '-', color=C_R0, linewidth=2, label=f'R0(D) [alpha={fit_result["alpha_r"]:.4f}]')
# 标注关键点
for d_mark, label in [(0.0, 'BOL'), (0.5, 'D=0.5'), (1.0, 'EOL')]:
    q, r0, pmax = hp_func(d_mark)
    ax6.plot(d_mark, r0 * 1000, 'o', color='black', markersize=6, zorder=5)
    ax6.annotate(f'{label}\n{r0*1000:.3f}mOhm', xy=(d_mark, r0*1000),
                 xytext=(d_mark+0.05, r0*1000+0.008), fontsize=7.5,
                 arrowprops=dict(arrowstyle='->', color='black', lw=0.8))
ax6.axvline(d_data_max, color=C_REF, linestyle=':', linewidth=0.8, label=f'Data bound')
ax6.set_xlabel('Degradation D')
ax6.set_ylabel('R0 (mOhm)')
ax6.set_title('(f) R0(D) Absolute Value')
ax6.set_xlim(-0.02, 1.02)
ax6.grid(True, axis='y')
ax6.legend(loc='upper right', framealpha=0.9, edgecolor='#CCCCCC', fontsize=8)

# --- 标题 ---
fig.suptitle(
    f"M2 BAT Health-Performance Mapping | "
    f"280Ah LFP (15 aging pts, D=0~{d_data_max:.3f}) | "
    f"Q(D)=1-{fit_result['beta_q']:.2f}D (R²={fit_result['r2_q']:.2f}) | "
    f"R0 growth alpha={fit_result['alpha_r']:.4f} (R²={fit_result['r2_r0']:.2f}) | "
    f"EOL: Q=0.8, R0={hp_func(1.0)[1]*1000:.3f}mOhm",
    fontsize=10.5, fontweight='bold', y=0.965
)

# --- 底部说明 ---
caption = (
    "Fig. 2. BAT health-performance mapping from 280Ah LFP characterization data (Zenodo, CC license). "
    "(a) OCV-SoC at 25C from entropy analysis. (b) BOL R0(T,SoC) from HPPC 5T x 10SoC. "
    "(c) Capacity decay Q(D)=1-beta_Q*D, linear fit on 15 aging points. "
    "(d) Resistance growth R0(D)/R0_BOL, fit on aging phase (200+ cycles) due to bathtub curve. "
    "(e) Normalized Q(D) and P_max(D)/P_rated. (f) R0(D) absolute with BOL/D=0.5/EOL markers. "
    "D=1-(SoH-0.8)/0.2. Data covers D=0~0.354; D>0.354 is linear extrapolation. "
    "Source: 280Ah LFP (TEMPEST LIC) + BLAST-Lite 250Ah cross-validation."
)
fig.text(0.5, 0.005, caption, ha='center', va='bottom', fontsize=7.5,
         style='italic', color='#555555', wrap=True)

# --- 保存 ---
OUT_PNG.parent.mkdir(parents=True, exist_ok=True)
fig.savefig(str(OUT_PNG), dpi=200, bbox_inches='tight', pad_inches=0.08)
plt.close(fig)
print(f"\nSaved: {OUT_PNG} ({OUT_PNG.stat().st_size // 1024} KB)")

# === 验证报告 ===
print("\n[4] 验证报告:")
report = validate_hp_func(hp_func, bat_bol)
for k, v in report.items():
    if k != 'values':
        print(f"  {k}: {v}")
print("\n  Key values:")
for d_key, vals in report['values'].items():
    if isinstance(vals, dict):
        print(f"    {d_key}: Q={vals['Q']}, R0={vals['R0_Ohm']}Ohm, P_max={vals['P_max_kW']}kW")
    else:
        print(f"    {d_key}: {vals}")
