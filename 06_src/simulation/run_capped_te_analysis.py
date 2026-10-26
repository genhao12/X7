# -*- coding: utf-8 -*-
"""
H1修复: EOL封顶TE分析
计算封顶后的TE值 (FC/EL年度损伤>1.0时, 超出部分为幻影损伤)
"""
import sys, csv
import numpy as np
from pathlib import Path

PROJECT_ROOT = Path(r"D:\matlab\C")
ANNUAL_CSV = PROJECT_ROOT / "07_results" / "annual" / "annual_damage_summary.csv"
OUTPUT_DIR = PROJECT_ROOT / "07_results" / "annual"

def _is_float(s):
    try:
        float(s)
        return True
    except (ValueError, TypeError):
        return False

print("=" * 70)
print("H1修复: EOL封顶TE分析")
print("=" * 70)

# 读取P1年度仿真结果
with open(ANNUAL_CSV, 'r') as f:
    reader = csv.DictReader(f)
    rows = [r for r in reader
            if r.get('Week') and _is_float(r['Week'])
            and r.get('Load_mean_kW') and _is_float(r['Load_mean_kW'])]

print(f"\n读取 {len(rows)} 周数据")

# 提取每周损伤增量
bol_dD_bat = [float(r['BOL_dD_bat']) for r in rows]
bol_dD_fc  = [float(r['BOL_dD_fc']) for r in rows]
bol_dD_el  = [float(r['BOL_dD_el']) for r in rows]
fb_dD_bat  = [float(r['FB_dD_bat']) for r in rows]
fb_dD_fc   = [float(r['FB_dD_fc']) for r in rows]
fb_dD_el   = [float(r['FB_dD_el']) for r in rows]
fb_D_fc_end  = [float(r['FB_D_fc_end']) for r in rows]
fb_D_el_end  = [float(r['FB_D_el_end']) for r in rows]

# === 未封顶TE (当前报告值) ===
bol_bat = sum(bol_dD_bat); fb_bat = sum(fb_dD_bat)
bol_fc  = sum(bol_dD_fc);  fb_fc  = sum(fb_dD_fc)
bol_el  = sum(bol_dD_el);  fb_el  = sum(fb_dD_el)
bol_total = bol_bat + bol_fc + bol_el
fb_total  = fb_bat + fb_fc + fb_el

te_bat_uncapped = (fb_bat - bol_bat) / abs(bol_bat) * 100 if abs(bol_bat) > 1e-10 else float('nan')
te_fc_uncapped  = (fb_fc - bol_fc) / abs(bol_fc) * 100 if abs(bol_fc) > 1e-10 else float('nan')
te_el_uncapped  = (fb_el - bol_el) / abs(bol_el) * 100 if abs(bol_el) > 1e-10 else float('nan')
te_total_uncapped = (fb_total - bol_total) / abs(bol_total) * 100 if abs(bol_total) > 1e-10 else float('nan')

print("\n--- 未封顶TE (当前报告值) ---")
print(f"  BOL:  BAT={bol_bat:.6f}, FC={bol_fc:.6f}, EL={bol_el:.6f}, Total={bol_total:.6f}")
print(f"  FB:   BAT={fb_bat:.6f}, FC={fb_fc:.6f}, EL={fb_el:.6f}, Total={fb_total:.6f}")
print(f"  TE:   BAT={te_bat_uncapped:+.2f}%, FC={te_fc_uncapped:+.2f}%, EL={te_el_uncapped:+.2f}%, Total={te_total_uncapped:+.2f}%")

# === EOL封顶TE ===
# 跟踪D累积, D>=1.0时停止计入损伤

def compute_capped_damage(dD_list, D_end_list=None):
    """计算EOL封顶后的损伤: D累积到1.0后, 后续dD不计入"""
    D_accum = 0.0
    capped_damage = 0.0
    eol_week = None
    for i, dD in enumerate(dD_list):
        if D_accum >= 1.0:
            # 已达EOL, 后续损伤为幻影
            if eol_week is None:
                eol_week = i  # 0-indexed
            continue
        # 本周损伤可能使D超过1.0
        remaining = 1.0 - D_accum
        if dD >= remaining:
            # 本周到达EOL
            capped_damage += remaining  # 只计入到达EOL的部分
            D_accum = 1.0
            eol_week = i + 1  # 1-indexed
        else:
            capped_damage += dD
            D_accum += dD
    return capped_damage, eol_week, D_accum

# BOL封顶 (BOL模式D不累积, 但如果跟踪BOL的D累积)
bol_bat_cap, bol_bat_eol, bol_bat_D = compute_capped_damage(bol_dD_bat)
bol_fc_cap,  bol_fc_eol,  bol_fc_D  = compute_capped_damage(bol_dD_fc)
bol_el_cap,  bol_el_eol,  bol_el_D  = compute_capped_damage(bol_dD_el)

# Feedback封顶
fb_bat_cap, fb_bat_eol, fb_bat_D = compute_capped_damage(fb_dD_bat)
fb_fc_cap,  fb_fc_eol,  fb_fc_D  = compute_capped_damage(fb_dD_fc)
fb_el_cap,  fb_el_eol,  fb_el_D  = compute_capped_damage(fb_dD_el)

bol_total_cap = bol_bat_cap + bol_fc_cap + bol_el_cap
fb_total_cap  = fb_bat_cap + fb_fc_cap + fb_el_cap

te_bat_capped = (fb_bat_cap - bol_bat_cap) / abs(bol_bat_cap) * 100 if abs(bol_bat_cap) > 1e-10 else float('nan')
te_fc_capped  = (fb_fc_cap - bol_fc_cap) / abs(bol_fc_cap) * 100 if abs(bol_fc_cap) > 1e-10 else float('nan')
te_el_capped  = (fb_el_cap - bol_el_cap) / abs(bol_el_cap) * 100 if abs(bol_el_cap) > 1e-10 else float('nan')
te_total_capped = (fb_total_cap - bol_total_cap) / abs(bol_total_cap) * 100 if abs(bol_total_cap) > 1e-10 else float('nan')

print("\n--- EOL封顶TE ---")
print(f"  BOL封顶: BAT={bol_bat_cap:.6f} (EOL wk {bol_bat_eol}), "
      f"FC={bol_fc_cap:.6f} (EOL wk {bol_fc_eol}), EL={bol_el_cap:.6f} (EOL wk {bol_el_eol})")
print(f"  BOL封顶合计: {bol_total_cap:.6f}")
print(f"  FB封顶:  BAT={fb_bat_cap:.6f} (EOL wk {fb_bat_eol}), "
      f"FC={fb_fc_cap:.6f} (EOL wk {fb_fc_eol}), EL={fb_el_cap:.6f} (EOL wk {fb_el_eol})")
print(f"  FB封顶合计: {fb_total_cap:.6f}")
print(f"  TE封顶:  BAT={te_bat_capped:+.2f}%, FC={te_fc_capped:+.2f}%, EL={te_el_capped:+.2f}%, Total={te_total_capped:+.2f}%")

# === 对比表 ===
print("\n--- TE对比表 ---")
print(f"  {'设备':>8s}  {'未封顶TE':>12s}  {'封顶TE':>12s}  {'幻影占比':>10s}  {'BOL EOL周':>10s}  {'FB EOL周':>10s}")
print(f"  {'-'*8}  {'-'*12}  {'-'*12}  {'-'*10}  {'-'*10}  {'-'*10}")
for name, te_u, te_c, bol_eol, fb_eol in [
    ('BAT', te_bat_uncapped, te_bat_capped, bol_bat_eol, fb_bat_eol),
    ('FC',  te_fc_uncapped,  te_fc_capped,  bol_fc_eol,  fb_fc_eol),
    ('EL',  te_el_uncapped,  te_el_capped,  bol_el_eol,  fb_el_eol),
    ('Total', te_total_uncapped, te_total_capped, '-', '-'),
]:
    phantom = abs(te_u - te_c) / abs(te_u) * 100 if abs(te_u) > 1e-10 else 0
    eol_bol = f"wk{bol_eol}" if bol_eol else "N/A"
    eol_fb  = f"wk{fb_eol}" if fb_eol else "N/A"
    print(f"  {name:>8s}  {te_u:>+11.2f}%  {te_c:>+11.2f}%  {phantom:>9.1f}%  {eol_bol:>10s}  {eol_fb:>10s}")

# === 关键结论 ===
print("\n--- 关键结论 ---")
print(f"1. BAT未达EOL (D={fb_bat_D:.4f}), TE_BAT={te_bat_capped:+.2f}% — 可靠的退化放大效应")
print(f"2. FC在约第{fb_fc_eol}周达EOL, TE_FC从{te_fc_uncapped:+.2f}%降至{te_fc_capped:+.2f}% — "
      f"幻影损伤占{abs(te_fc_uncapped-te_fc_capped)/abs(te_fc_uncapped)*100:.0f}%")
print(f"3. EL在约第{fb_el_eol}周达EOL, TE_EL从{te_el_uncapped:+.2f}%降至{te_el_capped:+.2f}% — "
      f"幻影损伤占{abs(te_el_uncapped-te_el_capped)/abs(te_el_uncapped)*100:.0f}%")
print(f"4. TE_total: 未封顶={te_total_uncapped:+.2f}%, 封顶={te_total_capped:+.2f}% — "
      f"幻影贡献{abs(te_total_uncapped-te_total_capped):.2f}pp ({abs(te_total_uncapped-te_total_capped)/abs(te_total_uncapped)*100:.0f}%)")
print(f"5. BAT-only TE={te_bat_capped:+.2f}%为最可靠指标, FC/EL TE为上界")

# === 保存结果 ===
csv_path = OUTPUT_DIR / "capped_te_analysis.csv"
with open(csv_path, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['=== EOL封顶TE分析 ==='])
    w.writerow(['设备', 'BOL未封顶', 'BOL封顶', 'FB未封顶', 'FB封顶', 'TE未封顶(%)', 'TE封顶(%)', '幻影占比(%)', 'BOL EOL周', 'FB EOL周'])
    for name, b_u, b_c, f_u, f_c, t_u, t_c, bol_eol, fb_eol in [
        ('BAT', bol_bat, bol_bat_cap, fb_bat, fb_bat_cap, te_bat_uncapped, te_bat_capped, bol_bat_eol, fb_bat_eol),
        ('FC',  bol_fc,  bol_fc_cap,  fb_fc,  fb_fc_cap,  te_fc_uncapped,  te_fc_capped,  bol_fc_eol,  fb_fc_eol),
        ('EL',  bol_el,  bol_el_cap,  fb_el,  fb_el_cap,  te_el_uncapped,  te_el_capped,  bol_el_eol,  fb_el_eol),
    ]:
        phantom = abs(t_u - t_c) / abs(t_u) * 100 if abs(t_u) > 1e-10 else 0
        eol_bol = f"wk{bol_eol}" if bol_eol else "N/A"
        eol_fb  = f"wk{fb_eol}" if fb_eol else "N/A"
        w.writerow([name, f"{b_u:.6f}", f"{b_c:.6f}", f"{f_u:.6f}", f"{f_c:.6f}",
                     f"{t_u:+.2f}", f"{t_c:+.2f}", f"{phantom:.1f}", eol_bol, eol_fb])
    w.writerow(['Total', f"{bol_total:.6f}", f"{bol_total_cap:.6f}",
                f"{fb_total:.6f}", f"{fb_total_cap:.6f}",
                f"{te_total_uncapped:+.2f}", f"{te_total_capped:+.2f}",
                f"{abs(te_total_uncapped-te_total_capped)/abs(te_total_uncapped)*100:.1f}", '-', '-'])
print(f"\n结果已保存: {csv_path}")
