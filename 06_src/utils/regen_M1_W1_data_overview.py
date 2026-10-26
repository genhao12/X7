# -*- coding: utf-8 -*-
"""
regen_M1_W1_data_overview.py
重新生成 M1_W1_data_overview.png — 全部数值基于数据与配置文件
- 三个子图(PV / Load / Net load) 标签统一放在右上方 (loc='upper right')
- 半透明边框背景避免遮挡数据
- 数据来源: 20_TradeStreet_15min_building_load_UTC_primary_local_calendar_layer.csv
  W1窗口: 2017-01-01 ~ 2017-01-07 (UTC), 672 步 (7 天 × 96)
- 容量参数来源: 05_config/mvp_c1_config.json (C1配置)
- 所有统计量由数据实时计算，不使用硬编码值
"""
import csv
import json
from pathlib import Path
from datetime import datetime

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# --- 路径 ---
ROOT = Path(r"D:\matlab\C")
PIPELINE = ROOT / "02_data_processed" / "tradestreet_pipeline"
SRC_CSV = PIPELINE / "20_TradeStreet_15min_building_load_UTC_primary_local_calendar_layer.csv"
C1_CONFIG = ROOT / "05_config" / "mvp_c1_config.json"
OUT_PNG = ROOT / "07_results" / "figures" / "M1_W1_data_overview.png"

# --- 颜色(与 plot_style.py PALETTE 保持一致) ---
C_PV = '#FC8D62'        # 橙
C_LOAD = '#66C2A5'      # 青绿
C_SURPLUS = '#ABDDA4'   # 浅绿
C_DEFICIT = '#D73027'   # 红
C_REF = '#999999'       # 灰
C_FC = '#FDAE61'        # 橙黄
C_EL = '#74ADD1'        # 浅蓝

# === 从配置文件读取容量参数 (不硬编码) ===
with open(C1_CONFIG, 'r') as f:
    cfg = json.load(f)
PV_RATED = cfg['capacity']['PV_kWp']        # 120.0 kWp
BAT_KWH = cfg['capacity']['BAT_kWh']        # 135.0 kWh
FC_RATED = cfg['capacity']['PEMFC_kW']      # 37.5 kW
EL_RATED = cfg['capacity']['PEMEL_kW']      # 35.0 kW
CRITICAL_FRAC = cfg['ems_parameters']['critical_load_fraction']  # 0.5

print(f"=== C1 Configuration (from mvp_c1_config.json) ===")
print(f"PV rated:  {PV_RATED} kWp")
print(f"BAT:       {BAT_KWH} kWh (0.5C = {BAT_KWH*0.5:.1f} kW)")
print(f"PEMFC:     {FC_RATED} kW")
print(f"PEMEL:     {EL_RATED} kW")
print(f"Critical load fraction: {CRITICAL_FRAC}")

# === 读取 W1 窗口数据 ===
rows = []
with open(SRC_CSV, 'r') as f:
    reader = csv.DictReader(f)
    for r in reader:
        ts = r['timestamp_utc']
        if '2017-01-01' <= ts < '2017-01-08':
            rows.append(r)

assert len(rows) == 672, f"Expected 672 rows, got {len(rows)}"

# 解析为数值
ts_local = [datetime.strptime(r['timestamp_local'], '%Y-%m-%d %H:%M:%S') for r in rows]
pv = [float(r['p_pv_model_kW_nonnegative'] or 0) for r in rows]
ld = [float(r['p_building_load_candidate_kW_equals_p_net_plus_pv_model_minus_p_bess'] or 0) for r in rows]
net = [p - l for p, l in zip(pv, ld)]

# === 由数据实时计算所有统计量 (不硬编码) ===
max_pv = max(pv)
mean_load = sum(ld) / len(ld)
max_load = max(ld)
n_surplus = sum(1 for n in net if n > 0)
n_deficit = sum(1 for n in net if n <= 0)
pct_surplus = n_surplus / len(net) * 100
pct_deficit = n_deficit / len(net) * 100
net_max = max(net)     # 最大富余
net_min = min(net)     # 最大亏缺(负值)

# Critical load 参考线: critical_load_fraction × peak_load
LOAD_CRITICAL = CRITICAL_FRAC * max_load   # 0.5 × 95.52 = 47.76 kW

print(f"\n=== W1 Window Verified Statistics (computed from data) ===")
print(f"Rows: {len(rows)} (7 days x 96 = 672)")
print(f"PV max:       {max_pv:.2f} kW")
print(f"PV mean:      {sum(pv)/len(pv):.2f} kW")
print(f"Load max:     {max_load:.2f} kW")
print(f"Load mean:    {mean_load:.2f} kW")
print(f"Critical (0.5 x peak): {LOAD_CRITICAL:.2f} kW")
print(f"Surplus: {n_surplus} steps ({pct_surplus:.2f}%)")
print(f"Deficit: {n_deficit} steps ({pct_deficit:.2f}%)")
print(f"Net max surplus:  {net_max:.2f} kW")
print(f"Net max deficit:  {net_min:.2f} kW")

# --- 绘图 ---
plt.rcParams.update({'axes.unicode_minus': False, 
    'font.family': 'sans-serif',
    'font.sans-serif': ['Microsoft YaHei', 'SimHei', 'Arial', 'DejaVu Sans'],
    'font.size': 11,
    'axes.titlesize': 13,
    'axes.labelsize': 12,
    'xtick.labelsize': 10,
    'ytick.labelsize': 10,
    'legend.fontsize': 9,
    'axes.linewidth': 0.8,
    'lines.linewidth': 1.4,
    'grid.alpha': 0.25,
    'grid.linewidth': 0.5,
})

fig, axes = plt.subplots(3, 1, figsize=(12, 9), sharex=True)
fig.subplots_adjust(left=0.09, right=0.97, top=0.92, bottom=0.10, hspace=0.18)

# === Subplot 1: PV power ===
ax1 = axes[0]
ax1.fill_between(ts_local, 0, pv, color=C_PV, alpha=0.7, label='PV output')
ax1.axhline(PV_RATED, color='#444444', linestyle='--', linewidth=1.0,
            label=f'PV rated ({PV_RATED:.0f} kWp)')
ax1.set_ylabel('PV power (kW)')
# Y轴上限基于数据: max_pv * 1.15, 且至少容纳 PV rated 线
ax1.set_ylim(0, max(max_pv * 1.15, PV_RATED * 1.1, 220))
ax1.set_xlim(ts_local[0], ts_local[-1])
ax1.grid(True, axis='y')
ax1.legend(loc='upper right', framealpha=0.9, edgecolor='#CCCCCC', fancybox=False)

# === Subplot 2: Load ===
ax2 = axes[1]
ax2.fill_between(ts_local, 0, ld, color=C_LOAD, alpha=0.7, label='Building load')
# Mean 参考线: 使用实际计算的均值
ax2.axhline(mean_load, color=C_REF, linestyle=':', linewidth=1.2,
            label=f'Mean ({mean_load:.1f} kW)')
# Critical 参考线: critical_load_fraction × peak_load
ax2.axhline(LOAD_CRITICAL, color='#FF8C42', linestyle='--', linewidth=1.2,
            label=f'Critical ({CRITICAL_FRAC:.0f}x peak = {LOAD_CRITICAL:.1f} kW)')
ax2.set_ylabel('Load (kW)')
# Y轴上限基于数据: max_load * 1.15
ax2.set_ylim(0, max_load * 1.15)
ax2.grid(True, axis='y')
ax2.legend(loc='upper right', framealpha=0.9, edgecolor='#CCCCCC', fancybox=False)

# === Subplot 3: Net load (PV - Load) ===
ax3 = axes[2]
# 正值=富余(PV>Load), 负值=亏缺(PV<Load)
surplus_vals = [n if n > 0 else 0 for n in net]
deficit_vals = [n if n < 0 else 0 for n in net]
ax3.fill_between(ts_local, 0, surplus_vals, color=C_SURPLUS, alpha=0.75,
                 label=f'Surplus ({pct_surplus:.1f}%)')
ax3.fill_between(ts_local, 0, deficit_vals, color=C_DEFICIT, alpha=0.75,
                 label=f'Deficit ({pct_deficit:.1f}%)')
# FC rated 参考线 (正值, 可消纳的亏缺功率上限)
ax3.axhline(FC_RATED, color=C_FC, linestyle='--', linewidth=1.2,
            label=f'FC rated ({FC_RATED:.1f} kW)')
# EL rated 参考线 (负值, 可吸收的富余功率上限)
ax3.axhline(-EL_RATED, color=C_EL, linestyle='-.', linewidth=1.2,
            label=f'-EL rated ({-EL_RATED:.0f} kW)')
ax3.axhline(0, color='#444444', linewidth=0.6)
ax3.set_ylabel('Net load (kW)')
# Y轴范围基于实际数据: 留 10% 余量
ax3.set_ylim(net_min * 1.15, net_max * 1.15)
ax3.grid(True, axis='y')
ax3.legend(loc='upper right', framealpha=0.9, edgecolor='#CCCCCC', fancybox=False)

# X 轴格式
ax3.xaxis.set_major_locator(mdates.DayLocator())
ax3.xaxis.set_major_formatter(mdates.DateFormatter('%b %d'))
ax3.set_xlabel('Date (local, America/Los_Angeles)')

# --- 标题(使用数据计算的统计量) ---
fig.suptitle(
    f"W1 Window (2017 Jan 1-7): UCSD TradeStreet Data | "
    f"Surplus {pct_surplus:.1f}% | Max PV {max_pv:.0f} kW | Mean Load {mean_load:.1f} kW",
    fontsize=12, fontweight='bold', y=0.965
)

# --- 底部说明 (Fig caption) ---
bat_kw = BAT_KWH * 0.5  # 0.5C 功率
caption = (
    f"Fig. 1. PV output, building load, and net load for the W1 window (7 days, 15-min, 672 steps). "
    f"C1 config: PV={PV_RATED:.0f}kWp, BAT={BAT_KWH:.0f}kWh (0.5C={bat_kw:.1f}kW), "
    f"FC={FC_RATED:.1f}kW, EL={EL_RATED:.0f}kW. "
    f"Critical line = {CRITICAL_FRAC:.0f}x peak load. "
    f"All legends placed at upper right with semi-transparent frames. "
    f"Source: UCSD TradeStreet pipeline (file 20, UTC primary axis)."
)
fig.text(0.5, 0.01, caption, ha='center', va='bottom', fontsize=8.5,
         style='italic', color='#555555', wrap=True)

# --- 保存 ---
OUT_PNG.parent.mkdir(parents=True, exist_ok=True)
fig.savefig(str(OUT_PNG), dpi=200, bbox_inches='tight', pad_inches=0.08)
plt.close(fig)
print(f"\nSaved: {OUT_PNG} ({OUT_PNG.stat().st_size // 1024} KB)")
