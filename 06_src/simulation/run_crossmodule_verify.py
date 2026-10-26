#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cross-Module Verification: M3 (L4 EMS) + M4 (Economics) vs P1-P5
=================================================================
Extends run_crossphase_verify.py to verify M3 and M4 consistency
with the existing P1-P5 simulation pipeline.

Check categories:
  J. M3 L4 EMS data consistency
  K. M3 <-> P1 damage/LPSP match
  L. M4 NPC/LCOE internal consistency
  M. M4 <-> P1/P2 replacement schedule match
  N. M4 sensitivity properties
  O. Cross-module traceability (M3→M4 data flow)

Usage:
    python run_crossmodule_verify.py
"""
import os, sys, json, csv, re
import numpy as np
import pandas as pd
from pathlib import Path
from io import StringIO

PROJECT_ROOT = Path(r"D:\matlab\C")
RESULTS = PROJECT_ROOT / "07_results"
ANNUAL = RESULTS / "annual"
LIFETIME = RESULTS / "lifetime"
L4_DIR = RESULTS / "l4_comparison"
ECON_DIR = RESULTS / "economic"
FIGURES = RESULTS / "figures"

PASS = "PASS"
FAIL = "FAIL"
results = []

def check(check_id, description, condition, detail=""):
    status = PASS if condition else FAIL
    results.append((check_id, description, status, detail))
    marker = "[OK]" if condition else "[!!]"
    print(f"  {marker} {check_id}: {description} -> {status}")
    if detail:
        print(f"       {detail}")
    return condition

print("=" * 70)
print("Cross-Module Verification: M3 (L4 EMS) + M4 (Economics) vs P1-P5")
print("=" * 70)

# ================================================================
# Helper: load P1 data
# ================================================================
p1_raw = pd.read_csv(ANNUAL / "annual_damage_summary.csv")
p1 = p1_raw[p1_raw['Week'].astype(str).str.match(r'^\d+$', na=False)].copy()
for col in p1.columns:
    p1[col] = pd.to_numeric(p1[col], errors='coerce')

p1_fb_bat = p1['FB_dD_bat'].sum()
p1_fb_fc = p1['FB_dD_fc'].sum()
p1_fb_el = p1['FB_dD_el'].sum()
p1_fb_total = p1['FB_dD_total'].sum()
p1_bol_total = p1['BOL_dD_total'].sum()
p1_te = (p1_fb_total - p1_bol_total) / p1_bol_total * 100
p1_fb_lpsp = p1['FB_LPSP_pct'].mean()

# ================================================================
# J. M3 L4 EMS Data Consistency
# ================================================================
print("\n[J] M3 L4 EMS Data Consistency...")

l4_final_path = L4_DIR / "l4_final_validation.csv"
with open(l4_final_path, 'r') as f:
    l4_content = f.read()

# Parse M2 and M3 rows from the "Full 52-week Comparison" section
l4_lines = l4_content.strip().split('\n')
m2_row = None
m3_row = None
for line in l4_lines:
    if line.startswith('M2,'):
        m2_row = line
    elif line.startswith('M3,'):
        m3_row = line

def parse_l4_row(row_str):
    """Parse M2/M3 row from l4_final_validation.csv"""
    parts = row_str.split(',')
    return {
        'BOL_total': float(parts[1]),
        'BOL_bat': float(parts[2]),
        'BOL_fc': float(parts[3]),
        'BOL_el': float(parts[4]),
        'FB_total': float(parts[5]),
        'FB_bat': float(parts[6]),
        'FB_fc': float(parts[7]),
        'FB_el': float(parts[8]),
        'AE_total': float(parts[9]),
        'TE_pct': float(parts[10]),
        'BOL_LPSP': float(parts[11]),
        'FB_LPSP': float(parts[12]),
        'FB_curt_MWh': float(parts[13]),
        'D_bat_eoy': float(parts[14]),
        'D_fc_eoy': float(parts[15]),
        'D_el_eoy': float(parts[16]),
        'fc_starts': int(parts[17]),
        'el_starts': int(parts[18]),
    }

if m2_row and m3_row:
    m2 = parse_l4_row(m2_row)
    m3 = parse_l4_row(m3_row)

    check("J01", "M3 file has M2 row (52-week data)",
          m2_row is not None, f"TE={m2['TE_pct']:.2f}%")
    check("J02", "M3 file has M3 row (52-week data)",
          m3_row is not None, f"TE={m3['TE_pct']:.2f}%")

    check("J03", "M2 TE = +6.59% (matches P1)",
          abs(m2['TE_pct'] - 6.59) < 0.05,
          f"Actual: {m2['TE_pct']:.2f}%")

    check("J04", "M3 TE = +6.42% (reduced from M2)",
          abs(m3['TE_pct'] - 6.42) < 0.05,
          f"Actual: {m3['TE_pct']:.2f}%")

    check("J05", "M3 TE < M2 TE (L4 reduces coupling error)",
          m3['TE_pct'] < m2['TE_pct'],
          f"M3={m3['TE_pct']:.2f}% < M2={m2['TE_pct']:.2f}%")

    check("J06", "M3 FC damage <= M2 FC damage",
          m3['FB_fc'] <= m2['FB_fc'],
          f"M3={m3['FB_fc']:.6f} vs M2={m2['FB_fc']:.6f}")

    check("J07", "M3 EL damage <= M2 EL damage",
          m3['FB_el'] <= m2['FB_el'],
          f"M3={m3['FB_el']:.6f} vs M2={m2['FB_el']:.6f}")

    check("J08", "M3 LPSP increase < 1% vs M2",
          abs(m3['FB_LPSP'] - m2['FB_LPSP']) < 1.0,
          f"M3={m3['FB_LPSP']:.2f}%, M2={m2['FB_LPSP']:.2f}%")

    check("J09", "M3 FC start-stops <= M2",
          m3['fc_starts'] <= m2['fc_starts'],
          f"M3={m3['fc_starts']} vs M2={m2['fc_starts']}")

    check("J10", "M3 D_bat EOY = 0.916 (same as P1)",
          abs(m3['D_bat_eoy'] - 0.916) < 0.005,
          f"Actual: {m3['D_bat_eoy']:.4f}")

    check("J11", "M3 D_fc EOY = 1.0 (EOL reached)",
          abs(m3['D_fc_eoy'] - 1.0) < 0.001,
          f"Actual: {m3['D_fc_eoy']:.4f}")

    check("J12", "M3 D_el EOY = 1.0 (EOL reached)",
          abs(m3['D_el_eoy'] - 1.0) < 0.001,
          f"Actual: {m3['D_el_eoy']:.4f}")
else:
    check("J01", "M3 file has M2/M3 rows", False, "Rows not found")
    m2 = {'TE_pct': 0, 'FB_fc': 0, 'FB_el': 0, 'FB_LPSP': 0, 'fc_starts': 0}
    m3 = {'TE_pct': 0, 'FB_fc': 0, 'FB_el': 0, 'FB_LPSP': 0, 'fc_starts': 0}

# Check L4 checks (7/7 PASS)
l4_checks_section = False
for line in l4_lines:
    if '=== Checks ===' in line:
        l4_checks_section = True
    if l4_checks_section and 'PASS' in line:
        pass  # count passes
l4_pass_count = sum(1 for line in l4_lines if 'PASS' in line and 'L4F' in line)
check("J13", "M3 L4 final checks: 7/7 PASS",
      l4_pass_count >= 7, f"Found {l4_pass_count} PASS checks")

# ================================================================
# K. M3 <-> P1 Damage/LPSP Match
# ================================================================
print("\n[K] M3 <-> P1 Damage Match...")

if m2_row:
    # M2 from L4 validation should match P1 feedback mode
    check("K01", "M2 FB_bat = P1 FB_dD_bat sum",
          abs(m2['FB_bat'] - p1_fb_bat) < 0.001,
          f"L4={m2['FB_bat']:.6f}, P1={p1_fb_bat:.6f}")

    check("K02", "M2 FB_fc = P1 FB_dD_fc sum",
          abs(m2['FB_fc'] - p1_fb_fc) < 0.001,
          f"L4={m2['FB_fc']:.6f}, P1={p1_fb_fc:.6f}")

    check("K03", "M2 FB_el = P1 FB_dD_el sum",
          abs(m2['FB_el'] - p1_fb_el) < 0.001,
          f"L4={m2['FB_el']:.6f}, P1={p1_fb_el:.6f}")

    check("K04", "M2 TE = P1 TE (+6.59%)",
          abs(m2['TE_pct'] - p1_te) < 0.05,
          f"L4={m2['TE_pct']:.2f}%, P1={p1_te:.2f}%")

    check("K05", "M2 BOL_total = P1 BOL_total",
          abs(m2['BOL_total'] - p1_bol_total) < 0.001,
          f"L4={m2['BOL_total']:.6f}, P1={p1_bol_total:.6f}")

    # M3 damage should be close to M2 (small reduction)
    check("K06", "M3 FB_bat ~ M2 FB_bat (within 0.1%)",
          abs(m3['FB_bat'] - m2['FB_bat']) / m2['FB_bat'] < 0.001,
          f"M3={m3['FB_bat']:.6f}, M2={m2['FB_bat']:.6f}")

    check("K07", "M3 FB_fc < M2 FB_fc (L4 reduces FC damage)",
          m3['FB_fc'] < m2['FB_fc'],
          f"M3={m3['FB_fc']:.6f} < M2={m2['FB_fc']:.6f}")

    check("K08", "M3 FB_el < M2 FB_el (L4 reduces EL damage)",
          m3['FB_el'] < m2['FB_el'],
          f"M3={m3['FB_el']:.6f} < M2={m2['FB_el']:.6f}")

    # LPSP consistency
    check("K09", "M2 FB_LPSP ~ P1 FB_LPSP (~44.8%)",
          abs(m2['FB_LPSP'] - p1_fb_lpsp) < 1.0,
          f"L4={m2['FB_LPSP']:.2f}%, P1={p1_fb_lpsp:.2f}%")

    check("K10", "M3 FB_LPSP ~ M2 FB_LPSP (within 0.5%)",
          abs(m3['FB_LPSP'] - m2['FB_LPSP']) < 0.5,
          f"M3={m3['FB_LPSP']:.2f}%, M2={m2['FB_LPSP']:.2f}%")

# ================================================================
# L. M4 NPC/LCOE Internal Consistency
# ================================================================
print("\n[L] M4 NPC/LCOE Internal Consistency...")

econ_path = ECON_DIR / "economic_comparison.csv"
check("L01", "M4 economic_comparison.csv exists",
      econ_path.exists())

if econ_path.exists():
    with open(econ_path, 'r', encoding='utf-8') as f:
        econ_content = f.read()

    # Parse key values from CSV
    econ_lines = econ_content.strip().split('\n')

    # Find NPC values
    m2_npc = None
    m3_npc = None
    m2_lcoe = None
    m3_lcoe = None
    m2_repl_pv = None
    m2_capex_pv = None
    m2_opex_pv = None
    m2_cont_npc = None
    m3_cont_npc = None

    in_discrete = False
    in_continuous = False
    for line in econ_lines:
        if '=== 离散法NPC/LCOE ===' in line:
            in_discrete = True
            in_continuous = False
        elif '=== 连续法NPC/LCOE ===' in line:
            in_discrete = False
            in_continuous = True
        elif '=== CAPEX敏感性' in line:
            in_discrete = False
            in_continuous = False

        parts = line.split(',')
        if in_discrete and len(parts) >= 7:
            if parts[0] == 'M2':
                m2_npc = float(parts[1])
                m2_lcoe = float(parts[3])
                m2_capex_pv = float(parts[4])
                m2_opex_pv = float(parts[5])
                m2_repl_pv = float(parts[6])
            elif parts[0] == 'M3':
                m3_npc = float(parts[1])
                m3_lcoe = float(parts[3])
        elif in_continuous and len(parts) >= 7:
            if parts[0] == 'M2':
                m2_cont_npc = float(parts[1])
            elif parts[0] == 'M3':
                m3_cont_npc = float(parts[1])

    if m2_npc:
        check("L02", "M2 NPC > 0",
              m2_npc > 0, f"NPC={m2_npc:.0f}")

        check("L03", "M2 NPC in range 10-50 million CNY",
              10e6 < m2_npc < 50e6,
              f"NPC={m2_npc/1e6:.2f}M")

        check("L04", "M2 LCOE in range 1-10 CNY/kWh",
              1 < m2_lcoe < 10,
              f"LCOE={m2_lcoe:.4f}")

        # NPC = CAPEX + OPEX + Replacement
        npc_sum = m2_capex_pv + m2_opex_pv + m2_repl_pv
        check("L05", "M2 CAPEX+OPEX+Repl = NPC",
              abs(npc_sum - m2_npc) / m2_npc < 1e-4,
              f"sum={npc_sum:.0f} vs NPC={m2_npc:.0f}")

        # Replacement is dominant
        repl_frac = m2_repl_pv / m2_npc * 100
        check("L06", "Replacement cost > 50% of NPC",
              repl_frac > 50,
              f"Replacement={repl_frac:.1f}%")

    if m2_npc and m3_npc:
        check("L07", "Discrete: M2 NPC = M3 NPC (same replacements)",
              m2_npc == m3_npc,
              f"M2={m2_npc:.0f}, M3={m3_npc:.0f}")

    if m2_cont_npc and m3_cont_npc:
        check("L08", "Continuous: M3 NPC < M2 NPC (damage reduction)",
              m3_cont_npc < m2_cont_npc,
              f"M3={m3_cont_npc:.0f} < M2={m2_cont_npc:.0f}")

        cont_diff = m3_cont_npc - m2_cont_npc
        check("L09", "Continuous NPC difference is small (<1%)",
              abs(cont_diff) / m2_cont_npc < 0.01,
              f"diff={cont_diff:.0f} ({cont_diff/m2_cont_npc*100:.4f}%)")

# ================================================================
# M. M4 <-> P1/P2 Replacement Schedule Match
# ================================================================
print("\n[M] M4 <-> P1/P2 Replacement Match...")

# P2 Strategy A 10yr replacements: BAT=9, FC=13, EL=17
# M4 20yr extrapolation: BAT=18, FC=27, EL=36 (approximately 2x)

# Parse M4 replacement counts from CSV
m4_repl = {}
in_repl = False
for line in econ_lines:
    if '=== 20年替换次数 ===' in line:
        in_repl = True
        continue
    elif '===' in line and in_repl:
        in_repl = False
    if in_repl and line.strip():
        parts = line.split(',')
        if parts[0] in ['BAT', 'PEMFC', 'PEMEL', 'power_electronics']:
            m4_repl[parts[0]] = int(parts[1])

if m4_repl:
    # 20yr should be approximately 2x of 10yr P2 values
    check("M01", "M4 BAT 20yr repl ~ 2x P2 10yr (9→18)",
          15 <= m4_repl.get('BAT', 0) <= 20,
          f"M4={m4_repl.get('BAT', '?')}, P2_10yr=9")

    check("M02", "M4 PEMFC 20yr repl ~ 2x P2 10yr (13→27)",
          24 <= m4_repl.get('PEMFC', 0) <= 30,
          f"M4={m4_repl.get('PEMFC', '?')}, P2_10yr=13")

    check("M03", "M4 PEMEL 20yr repl ~ 2x P2 10yr (17→36)",
          32 <= m4_repl.get('PEMEL', 0) <= 40,
          f"M4={m4_repl.get('PEMEL', '?')}, P2_10yr=17")

    check("M04", "M4 power_electronics = 1 (15yr design life)",
          m4_repl.get('power_electronics', 0) == 1,
          f"M4={m4_repl.get('power_electronics', '?')}")

# M4 damage rates should match P1 feedback mode
m4_damage = {}
in_damage = False
for line in econ_lines:
    if '=== 年度损伤 ===' in line:
        in_damage = True
        continue
    elif '===' in line and in_damage:
        in_damage = False
    if in_damage and line.strip():
        parts = line.split(',')
        if parts[0] == 'M2':
            m4_damage['BAT'] = float(parts[1])
            m4_damage['PEMFC'] = float(parts[2])
            m4_damage['PEMEL'] = float(parts[3])

if m4_damage:
    check("M05", "M4 M2 BAT damage = P1 feedback BAT damage",
          abs(m4_damage['BAT'] - p1_fb_bat) < 0.001,
          f"M4={m4_damage['BAT']:.6f}, P1={p1_fb_bat:.6f}")

    check("M06", "M4 M2 PEMFC damage = P1 feedback FC damage",
          abs(m4_damage['PEMFC'] - p1_fb_fc) < 0.001,
          f"M4={m4_damage['PEMFC']:.6f}, P1={p1_fb_fc:.6f}")

    check("M07", "M4 M2 PEMEL damage = P1 feedback EL damage",
          abs(m4_damage['PEMEL'] - p1_fb_el) < 0.001,
          f"M4={m4_damage['PEMEL']:.6f}, P1={p1_fb_el:.6f}")

# ================================================================
# N. M4 Sensitivity Properties
# ================================================================
print("\n[N] M4 Sensitivity Properties...")

# Parse sensitivity data
capex_sens = []
disc_sens = []
in_capex_sens = False
in_disc_sens = False
for line in econ_lines:
    if '=== CAPEX敏感性' in line:
        in_capex_sens = True
        in_disc_sens = False
        continue
    elif '=== 贴现率敏感性' in line:
        in_capex_sens = False
        in_disc_sens = True
        continue
    elif '===' in line and (in_capex_sens or in_disc_sens):
        in_capex_sens = False
        in_disc_sens = False

    if in_capex_sens and line.strip():
        parts = line.split(',')
        if len(parts) >= 5 and parts[0].startswith(('+', '-')):
            capex_sens.append(float(parts[1]))
    elif in_disc_sens and line.strip():
        parts = line.split(',')
        if len(parts) >= 5 and '%' in parts[0]:
            disc_sens.append(float(parts[1]))

if len(capex_sens) >= 5:
    check("N01", "CAPEX sensitivity: NPC increases with CAPEX",
          all(capex_sens[i] < capex_sens[i+1] for i in range(len(capex_sens)-1)),
          f"NPCs={[f'{n/1e6:.1f}' for n in capex_sens]}")

    check("N02", "CAPEX -30% gives ~30% NPC reduction",
          abs((capex_sens[0] - capex_sens[2]) / capex_sens[2] + 0.30) < 0.02,
          f"Reduction={abs((capex_sens[0]-capex_sens[2])/capex_sens[2]*100):.1f}%")

    check("N03", "CAPEX +30% gives ~30% NPC increase",
          abs((capex_sens[-1] - capex_sens[2]) / capex_sens[2] - 0.30) < 0.02,
          f"Increase={abs((capex_sens[-1]-capex_sens[2])/capex_sens[2]*100):.1f}%")

if len(disc_sens) >= 5:
    check("N04", "Discount rate: NPC decreases with rate (high replacement ratio)",
          all(disc_sens[i] > disc_sens[i+1] for i in range(len(disc_sens)-1)),
          f"NPCs={[f'{n/1e6:.1f}' for n in disc_sens]}")

# ================================================================
# O. Cross-Module Traceability
# ================================================================
print("\n[O] Cross-Module Traceability...")

# Check all output files exist
output_files = [
    (L4_DIR / "l4_final_validation.csv", "M3 L4 final validation"),
    (L4_DIR / "l4_m2_m3_comparison.csv", "M3 L4 M2 vs M3 comparison"),
    (ECON_DIR / "economic_comparison.csv", "M4 economic comparison"),
    (FIGURES / "economic_npc_comparison.png", "M4 NPC figure"),
    (FIGURES / "economic_sensitivity.png", "M4 sensitivity figure"),
    (FIGURES / "economic_cashflow.png", "M4 cashflow figure"),
    (FIGURES / "l4_final_validation.png", "M3 L4 final figure"),
    (PROJECT_ROOT / "05_config" / "economic_parameters_v1.0.json", "M4 economic config"),
    (PROJECT_ROOT / "06_src" / "economics" / "npc_lite.py", "M4 NPC module"),
    (PROJECT_ROOT / "06_src" / "ems" / "ems_l4_lite.py", "M3 L4 EMS module"),
]

for fpath, desc in output_files:
    check(f"O{desc[:3]}", f"Exists: {desc} ({fpath.name})",
          fpath.exists() and fpath.stat().st_size > 100,
          f"size={fpath.stat().st_size if fpath.exists() else 0}")

# M3→M4 data flow verification
# M4 uses M3 damage values (0.9164, 1.3860, 1.8044) which come from L4 final validation
m3_expected = {'BAT': 0.91637570, 'PEMFC': 1.38597855, 'PEMEL': 1.80443033}

m4_m3_damage = {}
in_damage = False
for line in econ_lines:
    if '=== 年度损伤 ===' in line:
        in_damage = True
        continue
    elif '===' in line and in_damage:
        in_damage = False
    if in_damage and line.strip():
        parts = line.split(',')
        if parts[0] == 'M3':
            m4_m3_damage['BAT'] = float(parts[1])
            m4_m3_damage['PEMFC'] = float(parts[2])
            m4_m3_damage['PEMEL'] = float(parts[3])

if m4_m3_damage:
    check("O_M3", "M4 M3 damage = M3 L4 final validation damage",
          abs(m4_m3_damage['BAT'] - m3_expected['BAT']) < 0.001 and
          abs(m4_m3_damage['PEMFC'] - m3_expected['PEMFC']) < 0.001 and
          abs(m4_m3_damage['PEMEL'] - m3_expected['PEMEL']) < 0.001,
          f"M4={m4_m3_damage}, M3_expected={m3_expected}")

# M3 damage should come from L4 final validation
if m3_row:
    check("O_M3b", "M3 L4 damage values match M4 input",
          abs(m3['FB_bat'] - m3_expected['BAT']) < 0.001 and
          abs(m3['FB_fc'] - m3_expected['PEMFC']) < 0.001 and
          abs(m3['FB_el'] - m3_expected['PEMEL']) < 0.001,
          f"L4_BAT={m3['FB_bat']:.6f}, M4_BAT={m3_expected['BAT']:.6f}")

# ================================================================
# Summary
# ================================================================
print("\n" + "=" * 70)
print("CROSS-MODULE VERIFICATION SUMMARY")
print("=" * 70)

n_pass = sum(1 for r in results if r[2] == PASS)
n_fail = sum(1 for r in results if r[2] == FAIL)
n_total = len(results)

print(f"  Total checks: {n_total}")
print(f"  PASS: {n_pass}")
print(f"  FAIL: {n_fail}")

if n_fail > 0:
    print(f"\n  FAILED checks:")
    for cid, desc, status, detail in results:
        if status == FAIL:
            print(f"    {cid}: {desc}")
            if detail:
                print(f"      -> {detail}")

overall = "PASS" if n_fail == 0 else "FAIL"
print(f"\n  Overall: {overall}")
print("=" * 70)

# Write summary CSV
summary_path = RESULTS / "crossmodule_verification_summary.csv"
with open(summary_path, 'w', encoding='utf-8') as f:
    f.write("Check_ID,Description,Status,Detail\n")
    for cid, desc, status, detail in results:
        # Escape commas in detail
        safe_detail = detail.replace(',', ';') if detail else ''
        f.write(f"{cid},{desc},{status},{safe_detail}\n")
print(f"\n  Summary written: {summary_path}")

sys.exit(0 if n_fail == 0 else 1)
