# -*- coding: utf-8 -*-
"""
l4_crossyear_fullfeedback.png 修复脚本（轻量重绘版）

背景: 原图 l4_crossyear_fullfeedback.png 分辨率异常 (753102x2880, 21.7亿像素),
      超出查看器解码上限无法显示。根因: 生成时 figsize/dpi 被异常放大。
修复: 不重跑仿真, 直接从已有 CSV (07_results/l4_comparison/l4_crossyear_fullfeedback.csv)
      读取全部绘图数据, 按原脚本绘图逻辑重新渲染为 4800x3000 正常 PNG。

运行环境: DL 环境 (D:/Anaconda/envs/DL/python.exe, 含 matplotlib)
运行命令: D:/Anaconda/envs/DL/python.exe 06_src/simulation/redraw_l4_crossyear_fullfeedback.py
输出: 07_results/figures/l4_crossyear_fullfeedback_fixed.png (4800x3000, 可查看)
"""
import sys, csv
from pathlib import Path
import numpy as np
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec

PROJECT_ROOT = Path(r"D:\matlab\C")
sys.path.insert(0, str(PROJECT_ROOT / "06_src"))
from utils.fit_table import auto_fit_table
OUT_CSV = PROJECT_ROOT / "07_results" / "l4_comparison" / "l4_crossyear_fullfeedback.csv"
OUT_FIG = PROJECT_ROOT / "07_results" / "figures" / "l4_crossyear_fullfeedback.png"

plt.rcParams.update({'font.family': 'sans-serif', 'font.sans-serif': ['Microsoft YaHei', 'SimHei', 'Arial', 'DejaVu Sans'],
    'axes.unicode_minus': False,
    'font.size': 11, 'axes.titlesize': 10, 'axes.labelsize': 10, 'xtick.labelsize': 9,
    'ytick.labelsize': 9, 'legend.fontsize': 8, 'axes.linewidth': 0.8, 'lines.linewidth': 1.5,
    'grid.alpha': 0.25, 'axes.spines.top': False, 'axes.spines.right': False})
C_M2 = '#3288BD'; C_M3 = '#D73027'

# ---------- 1. 读取 CSV ----------
with open(OUT_CSV, 'r', encoding='utf-8-sig') as f:
    rows = [r for r in csv.reader(f) if r]

# 定位 Part 1 / Part 2 / Part 3
part1_start = next(i for i, r in enumerate(rows) if 'Part 1' in r[0])
part2_start = next(i for i, r in enumerate(rows) if 'Part 2' in r[0])
part3_start = next(i for i, r in enumerate(rows) if 'Part 3' in r[0])

# Part 1: 跨年 (数据行 part1_start+2 .. part2_start-1)
cross = []
for r in rows[part1_start + 2:part2_start]:
    if len(r) >= 17:
        cross.append(dict(
            year=int(r[0]),
            te_m2=float(r[6]), lpsp_m2=float(r[7]),
            te_m3=float(r[11]), lpsp_m3=float(r[12]),
            delta=float(r[13])))

# Part 2: 全反馈 (4 行: M2 BO / M2 Full / M3 BO / M3 Full)
fb = {}
for r in rows[part2_start + 2:part3_start]:
    if len(r) >= 10:
        key = (r[0].strip(), r[1].strip())
        fb[key] = dict(
            te=float(r[6]), lpsp=float(r[7]),
            bat=float(r[8]), fc=float(r[9]), el=float(r[10]))

# Part 3: 检查数
checks = [r for r in rows[part3_start + 2:] if len(r) >= 3]
np_chk = sum(1 for r in checks if r[2].strip() == 'PASS')
nt_chk = len(checks)

# ---------- 2. 组装绘图变量 (与原脚本一致) ----------
years = [c['year'] for c in cross]
tes_m2_plot = [c['te_m2'] for c in cross]
tes_m3_plot = [c['te_m3'] for c in cross]
deltas = [c['delta'] for c in cross]
lpsp_m2 = [c['lpsp_m2'] for c in cross]
lpsp_m3 = [c['lpsp_m3'] for c in cross]

te_m2_bo = fb[('M2', 'BAT-only')]['te'] / 100.0
te_m2_ff = fb[('M2', 'Full')]['te'] / 100.0
te_m3_bo = fb[('M3', 'BAT-only')]['te'] / 100.0
te_m3_ff = fb[('M3', 'Full')]['te'] / 100.0
r_m2_bo = fb[('M2', 'BAT-only')]; r_m2_ff = fb[('M2', 'Full')]
r_m3_bo = fb[('M3', 'BAT-only')]; r_m3_ff = fb[('M3', 'Full')]

tes_m3 = [c['te_m3'] / 100.0 for c in cross]
cv_m3 = float(np.std(tes_m3) / np.mean(tes_m3))
te_m3_all_pos = all(t > 0 for t in tes_m3)
m3_lt_m2_all = all(c['delta'] < 0 for c in cross)

# ---------- 3. 绘图 (复刻原脚本 2x3 GridSpec) ----------
fig = plt.figure(figsize=(16, 10))
gs = GridSpec(2, 3, hspace=0.35, wspace=0.30, left=0.07, right=0.93, top=0.91, bottom=0.08)

# (a) 跨年TE对比
ax1 = fig.add_subplot(gs[0, 0])
x = np.arange(len(years))
ax1.bar(x - 0.2, tes_m2_plot, 0.35, label='M2 (fixed EMS)', color=C_M2, alpha=0.8, edgecolor='k', linewidth=0.3)
ax1.bar(x + 0.2, tes_m3_plot, 0.35, label='M3 (L4 EMS)', color=C_M3, alpha=0.8, edgecolor='k', linewidth=0.3)
ax1.axhline(0, color='gray', lw=0.5)
ax1.set_xticks(x); ax1.set_xticklabels(years)
ax1.set_xlabel('Year'); ax1.set_ylabel('TE (%)')
ax1.set_title('(a) L4 跨年TE对比 (BAT-only反馈)', fontsize=10, fontweight='bold')
ax1.legend(fontsize=8); ax1.grid(True, alpha=0.3, axis='y')
for i, (t2, t3) in enumerate(zip(tes_m2_plot, tes_m3_plot)):
    ax1.text(i-0.2, t2+0.1, f'{t2:.2f}', ha='center', fontsize=8)
    ax1.text(i+0.2, t3+0.1, f'{t3:.2f}', ha='center', fontsize=8)

# (b) 跨年ΔTE
ax2 = fig.add_subplot(gs[0, 1])
colors = [C_M3 if d < 0 else 'red' for d in deltas]
ax2.bar(years, deltas, color=colors, alpha=0.8, edgecolor='k', linewidth=0.3)
ax2.axhline(0, color='gray', lw=0.5)
ax2.set_xlabel('Year'); ax2.set_ylabel('dTE (M3-M2, %)')
ax2.set_title('(b) L4 减轻退化效应 (跨年)', fontsize=10, fontweight='bold')
ax2.grid(True, alpha=0.3, axis='y')
for i, d in enumerate(deltas):
    ax2.text(years[i], d-0.05 if d < 0 else d+0.02, f'{d:+.2f}%', ha='center', fontsize=9)

# (c) 跨年LPSP
ax3 = fig.add_subplot(gs[0, 2])
ax3.bar(x - 0.2, lpsp_m2, 0.35, label='M2', color=C_M2, alpha=0.8, edgecolor='k', linewidth=0.3)
ax3.bar(x + 0.2, lpsp_m3, 0.35, label='M3', color=C_M3, alpha=0.8, edgecolor='k', linewidth=0.3)
ax3.set_xticks(x); ax3.set_xticklabels(years)
ax3.set_xlabel('Year'); ax3.set_ylabel('LPSP (%)')
ax3.set_title('(c) 跨年LPSP对比', fontsize=10, fontweight='bold')
ax3.legend(fontsize=8); ax3.grid(True, alpha=0.3, axis='y')

# (d) 全反馈TE对比
ax4 = fig.add_subplot(gs[1, 0])
modes = ['BAT-only', 'Full-feedback']
m2_tes = [te_m2_bo*100, te_m2_ff*100]
m3_tes = [te_m3_bo*100, te_m3_ff*100]
x2 = np.arange(len(modes))
ax4.bar(x2 - 0.2, m2_tes, 0.35, label='M2', color=C_M2, alpha=0.8, edgecolor='k', linewidth=0.3)
ax4.bar(x2 + 0.2, m3_tes, 0.35, label='M3', color=C_M3, alpha=0.8, edgecolor='k', linewidth=0.3)
ax4.axhline(0, color='gray', lw=0.5)
ax4.set_xticks(x2); ax4.set_xticklabels(modes)
ax4.set_xlabel('Feedback Mode'); ax4.set_ylabel('TE (%)')
ax4.set_title('(d) L4 全反馈TE对比 (2017)', fontsize=10, fontweight='bold')
ax4.legend(fontsize=8); ax4.grid(True, alpha=0.3, axis='y')
for i, (t2, t3) in enumerate(zip(m2_tes, m3_tes)):
    ax4.text(i-0.2, t2+0.1, f'{t2:.2f}', ha='center', fontsize=8)
    ax4.text(i+0.2, t3+0.1, f'{t3:.2f}', ha='center', fontsize=8)

# (e) 全反馈设备级损伤
ax5 = fig.add_subplot(gs[1, 1])
labels = ['M2 BO', 'M2 Full', 'M3 BO', 'M3 Full']
bat_v = [r_m2_bo['bat'], r_m2_ff['bat'], r_m3_bo['bat'], r_m3_ff['bat']]
fc_v = [r_m2_bo['fc'], r_m2_ff['fc'], r_m3_bo['fc'], r_m3_ff['fc']]
el_v = [r_m2_bo['el'], r_m2_ff['el'], r_m3_bo['el'], r_m3_ff['el']]
x3 = np.arange(len(labels))
ax5.bar(x3 - 0.2, bat_v, 0.2, label='BAT', color=C_M2, alpha=0.8)
ax5.bar(x3, fc_v, 0.2, label='FC', color=C_M3, alpha=0.8)
ax5.bar(x3 + 0.2, el_v, 0.2, label='EL', color='#66C2A5', alpha=0.8)
ax5.set_xticks(x3); ax5.set_xticklabels(labels, fontsize=8)
ax5.set_ylabel('Annual Damage')
ax5.set_title('(e) 全反馈设备级损伤 (2017)', fontsize=10, fontweight='bold')
ax5.legend(fontsize=8); ax5.grid(True, alpha=0.3, axis='y')

# (f) 汇总表
ax6 = fig.add_subplot(gs[1, 2]); ax6.axis('off')
td = [
    ['跨年M3 TE>0', 'PASS' if te_m3_all_pos else 'FAIL', f'CV={cv_m3*100:.2f}%'],
    ['M3<M2 全年', 'PASS' if m3_lt_m2_all else 'FAIL', f'{sum(1 for c in cross if c["delta"]<0)}/3年'],
    ['全反馈M3 TE>0', 'PASS' if te_m3_ff > 0 else 'FAIL', f'{te_m3_ff*100:+.2f}%'],
    ['M3 Full<M2 Full', 'PASS' if te_m3_ff < te_m2_ff else 'FAIL', f'd={te_m3_ff*100-te_m2_ff*100:+.2f}%'],
    ['验证检查', f'{np_chk}/{nt_chk}', 'PASS' if np_chk == nt_chk else 'PARTIAL'],
]
table = ax6.table(cellText=td, colLabels=['Check', 'Result', 'Detail'],
    loc='center', cellLoc='center', colColours=['#4477AA']*3)
table.auto_set_font_size(False); table.set_fontsize(7.0)
auto_fit_table(ax6, table)
ax6.set_title('(f) 汇总', fontsize=10, fontweight='bold', pad=10)

fig.suptitle(f'L4 EMS 跨年+全反馈验证 (审计M2/M3补充)\n'
    f'最优L4Config: R1=0, R2=1.0, R3=10.0 | {np_chk}/{nt_chk} PASS',
    fontsize=12, fontweight='bold', y=0.98)
fig.savefig(OUT_FIG, dpi=300, bbox_inches='tight'); plt.close(fig)
print(f"  图表: {OUT_FIG}")
print(f"  跨年: M3 TE={[f'{t*100:+.2f}%' for t in tes_m3]}, CV={cv_m3*100:.2f}%, M3<M2全年={m3_lt_m2_all}")
print(f"  全反馈: M3 BO TE={te_m3_bo*100:+.2f}%, M3 Full TE={te_m3_ff*100:+.2f}%")
print(f"  验证: {np_chk}/{nt_chk} PASS")
