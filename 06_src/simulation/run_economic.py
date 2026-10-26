# -*- coding: utf-8 -*-
"""
M4 run_economic.py — M2 vs M3 经济对比 + 成本敏感性分析
"""
import sys, json, csv, copy
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "06_src"))

from economics.npc_lite import (
    EconomicConfig, load_economic_config, compute_equipment_capex,
    compute_annual_opex, compute_replacement_cost,
    build_replacement_schedule, compute_npc,
    compute_continuous_damage_cost, run_sensitivity,
    print_npc_summary
)

ECON_PARAMS = PROJECT_ROOT / "05_config" / "economic_parameters_v1.0.json"
C1_CONFIG = PROJECT_ROOT / "05_config" / "mvp_c1_config.json"
ANNUAL_CSV = PROJECT_ROOT / "07_results" / "annual" / "annual_damage_summary.csv"
OUTPUT_DIR = PROJECT_ROOT / "07_results" / "economic"
FIG_DIR = PROJECT_ROOT / "07_results" / "figures"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
FIG_DIR.mkdir(parents=True, exist_ok=True)

plt.rcParams.update({
    'font.size': 9, 'axes.labelsize': 10, 'axes.titlesize': 11,
    'xtick.labelsize': 8, 'ytick.labelsize': 8, 'legend.fontsize': 8,
    'figure.dpi': 300, 'savefig.dpi': 300, 'savefig.bbox': 'tight',
    'font.family': 'sans-serif',
    'font.sans-serif': ['SimHei', 'Microsoft YaHei', 'DejaVu Sans'],
    'axes.unicode_minus': False,
})

def _is_float(s):
    try:
        float(s)
        return True
    except (ValueError, TypeError):
        return False

# ============================================================
# 1. 加载配置和损伤数据
# ============================================================
print("=" * 70)
print("M4 经济评价: M2 vs M3 NPC/LCOE 对比 + 敏感性分析")
print("=" * 70)

config = load_economic_config(str(ECON_PARAMS), str(C1_CONFIG))
print(f"\n贴现率: {config.discount_rate*100:.1f}% | 评价年限: {config.horizon_years}年 | 币种: CNY")

# 从P1结果读取年度损伤和供电量
print("\n--- 读取P1年度仿真结果 ---")
with open(ANNUAL_CSV, 'r') as f:
    reader = csv.DictReader(f)
    rows = [r for r in reader
            if r.get('Week') and _is_float(r['Week'])
            and r.get('Load_mean_kW') and _is_float(r['Load_mean_kW'])]

load_means = [float(r['Load_mean_kW']) for r in rows]
weekly_lpsp = [float(r['FB_LPSP_pct']) / 100 for r in rows]
steps_per_week = 672  # 7 days × 96 steps/day
dt_h = 0.25  # 15 min

# 年度负荷 = Σ(Load_mean × 672 × 0.25) / 1000
annual_load_mwh = sum(lm * steps_per_week * dt_h for lm in load_means) / 1000
# M2 LPSP: 简单算术平均 (与P1和L4一致, 修复H3: 原能量加权法导致方法不一致)
lpsp_m2 = float(np.mean(weekly_lpsp))
# 年度供电 = 年度负荷 × (1 - LPSP)
annual_delivered_mwh = annual_load_mwh * (1 - lpsp_m2)

print(f"  年度负荷: {annual_load_mwh:.1f} MWh | M2 LPSP(简单平均): {lpsp_m2*100:.2f}% | 年度供电: {annual_delivered_mwh:.1f} MWh")

# M2年度损伤 = 52周损伤增量之和 (非年末D值, 因FC/EL被cap=1.0)
m2_damage = {
    'BAT': sum(float(r['FB_dD_bat']) for r in rows),
    'PEMFC': sum(float(r['FB_dD_fc']) for r in rows),
    'PEMEL': sum(float(r['FB_dD_el']) for r in rows)
}
# M3年度损伤 (L4 final validation)
m3_damage = {'BAT': 0.91637570, 'PEMFC': 1.38597855, 'PEMEL': 1.80443033}
m3_lpsp = 0.4482  # M3 LPSP from L4 final validation (FB_LPSP_pct=44.82)
m3_annual_delivered = annual_load_mwh * (1 - m3_lpsp)

print(f"M2损伤: BAT={m2_damage['BAT']:.4f}, FC={m2_damage['PEMFC']:.4f}, EL={m2_damage['PEMEL']:.4f}")
print(f"M3损伤: BAT={m3_damage['BAT']:.4f}, FC={m3_damage['PEMFC']:.4f}, EL={m3_damage['PEMEL']:.4f}")

# ============================================================
# 2. CAPEX + 替换计划
# ============================================================
print("\n--- CAPEX分解 ---")
capex = compute_equipment_capex(config)
for k, v in capex.items():
    print(f"  {k:25s}: {v:>12,.0f} 元")
annual_opex = compute_annual_opex(config, capex)
print(f"  {'年度OPEX':25s}: {annual_opex:>12,.0f} 元/yr")

print("\n--- 20年替换计划 ---")
m2_sched = build_replacement_schedule(m2_damage, config, capex)
m3_sched = build_replacement_schedule(m3_damage, config, capex)
print(f"{'设备':20s} {'M2':>6s} {'M3':>6s} {'差异':>6s}")
for dev in ['BAT', 'PEMFC', 'PEMEL', 'power_electronics']:
    m2n, m3n = len(m2_sched[dev]), len(m3_sched[dev])
    print(f"{dev:20s} {m2n:>6d} {m3n:>6d} {m3n-m2n:>+6d}")

# ============================================================
# 3. NPC/LCOE计算 (离散法 + 连续法)
# ============================================================
print("\n--- 离散法NPC/LCOE ---")
m2_d = compute_npc(config, m2_sched, annual_delivered_mwh, label='M2_discrete')
m3_d = compute_npc(config, m3_sched, m3_annual_delivered, label='M3_discrete')
print_npc_summary(m2_d)
print_npc_summary(m3_d)

print("\n--- 连续法NPC/LCOE ---")
m2_c = compute_continuous_damage_cost(config, m2_damage, annual_delivered_mwh, label='M2_cont')
m3_c = compute_continuous_damage_cost(config, m3_damage, m3_annual_delivered, label='M3_cont')
print(f"M2_cont: NPC={m2_c['npc_million_cny']:.4f}百万, LCOE={m2_c['lcoe_cny_per_kwh']:.4f}元/kWh")
print(f"M3_cont: NPC={m3_c['npc_million_cny']:.4f}百万, LCOE={m3_c['lcoe_cny_per_kwh']:.4f}元/kWh")
cont_diff = m3_c['npc_cny'] - m2_c['npc_cny']
cont_pct = cont_diff / m2_c['npc_cny'] * 100
print(f"连续法NPC差异: {cont_diff:+,.0f}元 ({cont_pct:+.4f}%)")

# 各设备替换成本占比
print("\n--- 各设备替换成本 (M2离散法) ---")
r, T = config.discount_rate, config.horizon_years
for dev in ['BAT', 'PEMFC', 'PEMEL', 'power_electronics']:
    rc = compute_replacement_cost(config, capex, dev)
    years = m2_sched[dev]
    pv = sum(rc / (1 + r) ** yr for yr in years if yr <= T)
    pct = pv / m2_d['replacement_pv_cny'] * 100
    print(f"  {dev:20s}: {pv/1e6:.4f}百万 ({pct:.1f}%), {len(years)}x{rc:,.0f}元")

# ============================================================
# 4. 敏感性分析
# ============================================================
print("\n--- CAPEX敏感性 (M2) ---")
m2_sens = run_sensitivity(config, m2_damage, annual_delivered_mwh, label='M2')
print(f"{'场景':12s} {'NPC(百万)':>12s} {'LCOE':>10s} {'vs基准':>10s}")
for res in m2_sens:
    base = m2_d['npc_cny']
    dp = (res['npc_cny'] - base) / base * 100
    if res['sensitivity_type'] == 'CAPEX':
        print(f"CAPEX{res['sensitivity_value']:+.0%}    {res['npc_million_cny']:>12.4f} {res['lcoe_cny_per_kwh']:>10.4f} {dp:>+10.2f}%")
    elif res['sensitivity_type'] == 'discount_rate':
        print(f"r={res['sensitivity_value']:.0%}        {res['npc_million_cny']:>12.4f} {res['lcoe_cny_per_kwh']:>10.4f} {dp:>+10.2f}%")

# ============================================================
# 5. 保存CSV
# ============================================================
print("\n--- 保存结果 ---")
csv_path = OUTPUT_DIR / "economic_comparison.csv"
with open(csv_path, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['=== 经济评价配置 ==='])
    w.writerow(['贴现率', config.discount_rate, 'frozen_parameters_v2.2'])
    w.writerow(['评价年限', config.horizon_years, 'frozen_parameters_v2.2'])
    w.writerow(['初始CAPEX', f"{capex['total_initial']:.0f}", '元'])
    w.writerow(['年度OPEX', f"{annual_opex:.0f}", '元/yr'])
    w.writerow(['年度负荷', f"{annual_load_mwh:.1f}", 'MWh/yr'])
    w.writerow(['M2 LPSP', f"{lpsp_m2*100:.2f}", '%'])
    w.writerow(['M2年度供电', f"{annual_delivered_mwh:.1f}", 'MWh/yr'])
    w.writerow(['M3 LPSP', f"{m3_lpsp*100:.2f}", '%'])
    w.writerow(['M3年度供电', f"{m3_annual_delivered:.1f}", 'MWh/yr'])
    w.writerow([])
    w.writerow(['=== 年度损伤 ==='])
    w.writerow(['模式', 'BAT', 'PEMFC', 'PEMEL', '来源'])
    w.writerow(['M2', f"{m2_damage['BAT']:.6f}", f"{m2_damage['PEMFC']:.6f}", f"{m2_damage['PEMEL']:.6f}", 'P1反馈模式'])
    w.writerow(['M3', f"{m3_damage['BAT']:.6f}", f"{m3_damage['PEMFC']:.6f}", f"{m3_damage['PEMEL']:.6f}", 'L4最终验证'])
    w.writerow([])
    w.writerow(['=== 20年替换次数 ==='])
    w.writerow(['设备', 'M2', 'M3', '差异', '单次替换成本(元)'])
    for dev in ['BAT', 'PEMFC', 'PEMEL', 'power_electronics']:
        rc = compute_replacement_cost(config, capex, dev)
        w.writerow([dev, len(m2_sched[dev]), len(m3_sched[dev]), len(m3_sched[dev])-len(m2_sched[dev]), f"{rc:.0f}"])
    w.writerow([])
    w.writerow(['=== 离散法NPC/LCOE ==='])
    w.writerow(['模式', 'NPC(元)', 'NPC(百万)', 'LCOE(元/kWh)', 'CAPEX现值', 'OPEX现值', '替换现值'])
    w.writerow(['M2', f"{m2_d['npc_cny']:.2f}", f"{m2_d['npc_million_cny']:.4f}", f"{m2_d['lcoe_cny_per_kwh']:.4f}",
                f"{m2_d['capex_pv_cny']:.2f}", f"{m2_d['opex_pv_cny']:.2f}", f"{m2_d['replacement_pv_cny']:.2f}"])
    w.writerow(['M3', f"{m3_d['npc_cny']:.2f}", f"{m3_d['npc_million_cny']:.4f}", f"{m3_d['lcoe_cny_per_kwh']:.4f}",
                f"{m3_d['capex_pv_cny']:.2f}", f"{m3_d['opex_pv_cny']:.2f}", f"{m3_d['replacement_pv_cny']:.2f}"])
    w.writerow([])
    w.writerow(['=== 连续法NPC/LCOE ==='])
    w.writerow(['模式', 'NPC(元)', 'NPC(百万)', 'LCOE(元/kWh)', 'CAPEX现值', 'OPEX现值', '替换现值'])
    w.writerow(['M2', f"{m2_c['npc_cny']:.2f}", f"{m2_c['npc_million_cny']:.4f}", f"{m2_c['lcoe_cny_per_kwh']:.4f}",
                f"{m2_c['capex_pv_cny']:.2f}", f"{m2_c['opex_pv_cny']:.2f}", f"{m2_c['replacement_pv_cny']:.2f}"])
    w.writerow(['M3', f"{m3_c['npc_cny']:.2f}", f"{m3_c['npc_million_cny']:.4f}", f"{m3_c['lcoe_cny_per_kwh']:.4f}",
                f"{m3_c['capex_pv_cny']:.2f}", f"{m3_c['opex_pv_cny']:.2f}", f"{m3_c['replacement_pv_cny']:.2f}"])
    w.writerow(['差异', f"{cont_diff:+.2f}", f"{cont_diff/1e6:+.4f}", f"{m3_c['lcoe_cny_per_kwh']-m2_c['lcoe_cny_per_kwh']:+.6f}", '', '', ''])
    w.writerow([])
    w.writerow(['=== CAPEX敏感性 (M2离散法) ==='])
    w.writerow(['CAPEX变化', 'NPC(元)', 'NPC(百万)', 'LCOE(元/kWh)', 'vs基准(%)'])
    for res in m2_sens:
        if res['sensitivity_type'] == 'CAPEX':
            dp = (res['npc_cny'] - m2_d['npc_cny']) / m2_d['npc_cny'] * 100
            w.writerow([f"{res['sensitivity_value']:+.0%}", f"{res['npc_cny']:.2f}", f"{res['npc_million_cny']:.4f}", f"{res['lcoe_cny_per_kwh']:.4f}", f"{dp:+.2f}"])
    w.writerow([])
    w.writerow(['=== 贴现率敏感性 (M2离散法) ==='])
    w.writerow(['贴现率', 'NPC(元)', 'NPC(百万)', 'LCOE(元/kWh)', 'vs基准(%)'])
    for res in m2_sens:
        if res['sensitivity_type'] == 'discount_rate':
            dp = (res['npc_cny'] - m2_d['npc_cny']) / m2_d['npc_cny'] * 100
            w.writerow([f"{res['sensitivity_value']:.0%}", f"{res['npc_cny']:.2f}", f"{res['npc_million_cny']:.4f}", f"{res['lcoe_cny_per_kwh']:.4f}", f"{dp:+.2f}"])
    w.writerow([])
    w.writerow(['=== 逐年现金流 (M2离散法) ==='])
    w.writerow(['年份', '现金流(元)', '贴现现金流(元)', '累计贴现(元)'])
    cum = 0
    for t in range(T + 1):
        cum += m2_d['discounted_cash_flows'][t]
        w.writerow([t, f"{m2_d['cash_flows'][t]:.2f}", f"{m2_d['discounted_cash_flows'][t]:.2f}", f"{cum:.2f}"])
print(f"  CSV: {csv_path}")

# ============================================================
# 6. 图表1: NPC对比 + 成本结构
# ============================================================
fig, axes = plt.subplots(1, 3, figsize=(14, 4.5))

# (a) 离散法NPC对比
ax = axes[0]
labels = ['M2\n(基准EMS)', 'M3\n(L4 EMS)']
npcs = [m2_d['npc_million_cny'], m3_d['npc_million_cny']]
bars = ax.bar(labels, npcs, color=['#4472C4', '#ED7D31'], width=0.5, edgecolor='black', linewidth=0.5)
ax.set_ylabel('NPC (百万元)')
ax.set_title('(a) 离散法NPC对比')
ax.set_ylim(0, max(npcs) * 1.15)
for bar, npc in zip(bars, npcs):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + max(npcs)*0.02, f'{npc:.2f}', ha='center', va='bottom', fontsize=9, fontweight='bold')
ax.grid(axis='y', alpha=0.3); ax.set_axisbelow(True)

# (b) 成本结构分解
ax = axes[1]
cats = ['CAPEX', 'OPEX', '替换成本']
m2v = [m2_d['capex_pv_cny']/1e6, m2_d['opex_pv_cny']/1e6, m2_d['replacement_pv_cny']/1e6]
m3v = [m3_d['capex_pv_cny']/1e6, m3_d['opex_pv_cny']/1e6, m3_d['replacement_pv_cny']/1e6]
x = np.arange(len(cats)); wd = 0.3
b1 = ax.bar(x - wd/2, m2v, wd, label='M2', color='#4472C4', edgecolor='black', linewidth=0.5)
b2 = ax.bar(x + wd/2, m3v, wd, label='M3', color='#ED7D31', edgecolor='black', linewidth=0.5)
ax.set_ylabel('现值 (百万元)'); ax.set_title('(b) 成本结构分解')
ax.set_xticks(x); ax.set_xticklabels(cats); ax.legend(framealpha=0.9)
ax.grid(axis='y', alpha=0.3); ax.set_axisbelow(True)
for bars in [b1, b2]:
    for bar in bars:
        ax.text(bar.get_x()+bar.get_width()/2, bar.get_height()+0.3, f'{bar.get_height():.1f}', ha='center', va='bottom', fontsize=8)

# (c) 离散法 vs 连续法
ax = axes[2]
methods = ['离散法', '连续法']
m2a = [m2_d['npc_million_cny'], m2_c['npc_million_cny']]
m3a = [m3_d['npc_million_cny'], m3_c['npc_million_cny']]
xc = np.arange(len(methods))
b1 = ax.bar(xc - wd/2, m2a, wd, label='M2', color='#4472C4', edgecolor='black', linewidth=0.5)
b2 = ax.bar(xc + wd/2, m3a, wd, label='M3', color='#ED7D31', edgecolor='black', linewidth=0.5)
ax.set_ylabel('NPC (百万元)'); ax.set_title('(c) 离散法 vs 连续法')
ax.set_xticks(xc); ax.set_xticklabels(methods); ax.legend(framealpha=0.9)
ax.grid(axis='y', alpha=0.3); ax.set_axisbelow(True)
for bars in [b1, b2]:
    for bar in bars:
        ax.text(bar.get_x()+bar.get_width()/2, bar.get_height()+0.3, f'{bar.get_height():.2f}', ha='center', va='bottom', fontsize=8)

fig.suptitle('M4: M2 vs M3 经济评价 (20年NPC, 贴现率6%, CNY)', fontsize=13, fontweight='bold', y=1.02)
plt.tight_layout()
fig1_path = FIG_DIR / "economic_npc_comparison.png"
plt.savefig(fig1_path); plt.close()
print(f"  图表1: {fig1_path}")

# ============================================================
# 7. 图表2: 敏感性分析
# ============================================================
fig, axes = plt.subplots(1, 2, figsize=(10, 4.5))

# (a) CAPEX敏感性
ax = axes[0]
cv = [r['sensitivity_value']*100 for r in m2_sens if r['sensitivity_type']=='CAPEX']
cn = [r['npc_million_cny'] for r in m2_sens if r['sensitivity_type']=='CAPEX']
cl = [r['lcoe_cny_per_kwh'] for r in m2_sens if r['sensitivity_type']=='CAPEX']
ax2 = ax.twinx()
l1, = ax.plot(cv, cn, 'o-', color='#4472C4', lw=2, ms=6, label='NPC')
l2, = ax2.plot(cv, cl, 's--', color='#ED7D31', lw=2, ms=6, label='LCOE')
ax.axvline(x=0, color='gray', ls=':', alpha=0.5)
ax.set_xlabel('CAPEX变化 (%)'); ax.set_ylabel('NPC (百万元)', color='#4472C4')
ax2.set_ylabel('LCOE (元/kWh)', color='#ED7D31')
ax.set_title('(a) CAPEX敏感性 (M2)')
ax.grid(alpha=0.3); ax.set_axisbelow(True)
ax.legend([l1, l2], ['NPC', 'LCOE'], loc='upper left', framealpha=0.9)

# (b) 贴现率敏感性
ax = axes[1]
dv = [r['sensitivity_value']*100 for r in m2_sens if r['sensitivity_type']=='discount_rate']
dn = [r['npc_million_cny'] for r in m2_sens if r['sensitivity_type']=='discount_rate']
dl = [r['lcoe_cny_per_kwh'] for r in m2_sens if r['sensitivity_type']=='discount_rate']
ax2 = ax.twinx()
l1, = ax.plot(dv, dn, 'o-', color='#4472C4', lw=2, ms=6, label='NPC')
l2, = ax2.plot(dv, dl, 's--', color='#ED7D31', lw=2, ms=6, label='LCOE')
ax.axvline(x=6, color='gray', ls=':', alpha=0.5)
ax.set_xlabel('贴现率 (%)'); ax.set_ylabel('NPC (百万元)', color='#4472C4')
ax2.set_ylabel('LCOE (元/kWh)', color='#ED7D31')
ax.set_title('(b) 贴现率敏感性 (M2)')
ax.grid(alpha=0.3); ax.set_axisbelow(True)
ax.legend([l1, l2], ['NPC', 'LCOE'], loc='upper right', framealpha=0.9)

fig.suptitle('M4: NPC/LCOE 成本敏感性分析', fontsize=13, fontweight='bold', y=1.02)
plt.tight_layout()
fig2_path = FIG_DIR / "economic_sensitivity.png"
plt.savefig(fig2_path); plt.close()
print(f"  图表2: {fig2_path}")

# ============================================================
# 8. 图表3: 逐年现金流
# ============================================================
fig, ax = plt.subplots(figsize=(8, 4.5))
years = np.arange(0, T + 1)
ax.bar(years - 0.15, m2_d['cash_flows']/1e6, width=0.3, label='M2', color='#4472C4', alpha=0.8, edgecolor='black', linewidth=0.3)
ax.bar(years + 0.15, m3_d['cash_flows']/1e6, width=0.3, label='M3', color='#ED7D31', alpha=0.8, edgecolor='black', linewidth=0.3)
ax.set_xlabel('年份'); ax.set_ylabel('现金流 (百万元)')
ax.set_title('M4: M2 vs M3 逐年现金流 (离散法)')
ax.legend(framealpha=0.9); ax.grid(axis='y', alpha=0.3); ax.set_axisbelow(True)
ax.set_xlim(-0.5, T + 0.5)
fig3_path = FIG_DIR / "economic_cashflow.png"
plt.savefig(fig3_path); plt.close()
print(f"  图表3: {fig3_path}")

# ============================================================
# 9. 验证检查
# ============================================================
print("\n=== 验证检查 ===")
checks = []
checks.append(('ECO-01', '离散法CAPEX+OPEX+Repl=NPC',
    abs(m2_d['capex_pv_cny']+m2_d['opex_pv_cny']+m2_d['replacement_pv_cny']-m2_d['npc_cny'])/m2_d['npc_cny']<1e-6,
    f"sum={m2_d['capex_pv_cny']+m2_d['opex_pv_cny']+m2_d['replacement_pv_cny']:.0f}"))
checks.append(('ECO-02', '离散法M2 NPC=M3 NPC (整数替换相同)',
    m2_d['npc_cny']==m3_d['npc_cny'], f"M2={m2_d['npc_cny']:.0f}, M3={m3_d['npc_cny']:.0f}"))
checks.append(('ECO-03', '连续法M3 NPC < M2 NPC',
    m3_c['npc_cny']<m2_c['npc_cny'], f"M3={m3_c['npc_cny']:.0f} < M2={m2_c['npc_cny']:.0f}"))
checks.append(('ECO-04', '连续法M3 LCOE ≈ M2 LCOE (±2%)',
    abs(m3_c['lcoe_cny_per_kwh'] - m2_c['lcoe_cny_per_kwh']) / m2_c['lcoe_cny_per_kwh'] < 0.02,
    f"M3={m3_c['lcoe_cny_per_kwh']:.4f} vs M2={m2_c['lcoe_cny_per_kwh']:.4f} (diff={m3_c['lcoe_cny_per_kwh']-m2_c['lcoe_cny_per_kwh']:+.4f})"))
checks.append(('ECO-05', '替换成本>50% NPC',
    m2_d['replacement_fraction']>50, f"{m2_d['replacement_fraction']:.1f}%"))
cn_sens = [r['npc_cny'] for r in m2_sens if r['sensitivity_type']=='CAPEX']
checks.append(('ECO-06', 'CAPEX↑→NPC↑',
    all(cn_sens[i]<cn_sens[i+1] for i in range(len(cn_sens)-1)), f"{[f'{n/1e6:.1f}' for n in cn_sens]}"))
dr_sens = [r['npc_cny'] for r in m2_sens if r['sensitivity_type']=='discount_rate']
checks.append(('ECO-07', '贴现率↑→NPC↓',
    all(dr_sens[i]>dr_sens[i+1] for i in range(len(dr_sens)-1)), f"{[f'{n/1e6:.1f}' for n in dr_sens]}"))
checks.append(('ECO-08', 'LCOE在合理范围(1-10元/kWh)',
    1<m2_d['lcoe_cny_per_kwh']<10, f"LCOE={m2_d['lcoe_cny_per_kwh']:.4f}"))
checks.append(('ECO-09', '连续法经济影响可测(>0)',
    abs(cont_diff)>0, f"diff={cont_diff:.2f}元"))
checks.append(('ECO-10', 'M2 20yr替换次数合理',
    15<=len(m2_sched['BAT'])<=20 and 24<=len(m2_sched['PEMFC'])<=30 and 32<=len(m2_sched['PEMEL'])<=40,
    f"BAT={len(m2_sched['BAT'])},FC={len(m2_sched['PEMFC'])},EL={len(m2_sched['PEMEL'])}"))

all_pass = True
for cid, desc, result, details in checks:
    status = 'PASS' if result else 'FAIL'
    if not result: all_pass = False
    print(f"  {cid}: {desc}: {status} ({details})")

print(f"\n{'='*70}")
print(f"M4经济评价: {'全部PASS' if all_pass else '存在FAIL'}")
print(f"{'='*70}")

print(f"\n=== 关键结论 ===")
print(f"1. 离散法: M2=M3 NPC={m2_d['npc_million_cny']:.4f}百万 (替换次数相同)")
print(f"2. 连续法: M3比M2节省 {abs(cont_diff):,.0f}元 ({abs(cont_pct):.4f}%)")
print(f"3. 替换成本占NPC {m2_d['replacement_fraction']:.1f}% (主导项)")
print(f"4. CAPEX±30%: NPC {cn_sens[0]/1e6:.2f}~{cn_sens[-1]/1e6:.2f}百万")
print(f"5. 贴现率4~10%: NPC {dr_sens[0]/1e6:.2f}~{dr_sens[-1]/1e6:.2f}百万")
print(f"6. LCOE={m2_d['lcoe_cny_per_kwh']:.4f}元/kWh (离网高成本系统)")
print(f"7. L4 EMS经济影响在离散替换层面为零(损伤差异<整数阈值)")
