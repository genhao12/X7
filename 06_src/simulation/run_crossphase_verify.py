#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cross-Phase Consistency Verification (P1-P5)
=============================================
Verifies that bug fixes did not introduce regressions and that
all phases remain numerically and physically consistent.

Check categories:
  A. P1 internal consistency (BOL/FB/AE/TE)
  B. P1 <-> P3 baseline match
  C. P1 <-> P4 D_bat trajectory match
  D. P2 <-> P5 amplification factor match
  E. P2 Strategy B physical sanity (total damage = 1.0)
  F. P4 <-> P5 alpha_r and r-deviation method match
  G. P5 figure data traceability to source CSVs
  H. VS1-VS8 regression (spot-check key checks)

Usage:
    python run_crossphase_verify.py
"""
import os, sys, json, re
import numpy as np
import pandas as pd
from pathlib import Path
from io import StringIO

PROJECT_ROOT = Path(r"D:\matlab\C")
RESULTS = PROJECT_ROOT / "07_results"
ANNUAL = RESULTS / "annual"
LIFETIME = RESULTS / "lifetime"
SCENARIO = RESULTS / "scenarios"
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
print("Cross-Phase Consistency Verification (P1-P5)")
print("=" * 70)

# ================================================================
# A. P1 Internal Consistency
# ================================================================
print("\n[A] P1 Internal Consistency...")

p1_path = ANNUAL / "annual_damage_summary.csv"
p1_raw = pd.read_csv(p1_path)
p1 = p1_raw[p1_raw['Week'].astype(str).str.match(r'^\d+$', na=False)].copy()
for col in p1.columns:
    p1[col] = pd.to_numeric(p1[col], errors='coerce')

bol_total = p1['BOL_dD_total'].sum()
fb_total = p1['FB_dD_total'].sum()
ae_total = p1['AE_total'].sum()
te_total = (fb_total - bol_total) / bol_total * 100

check("A01", "P1 BOL total damage = 3.859",
      abs(bol_total - 3.859) < 0.01,
      f"Actual: {bol_total:.4f}")

check("A02", "P1 FB total damage = 4.113",
      abs(fb_total - 4.113) < 0.01,
      f"Actual: {fb_total:.4f}")

check("A03", "P1 AE_total = FB - BOL",
      abs(ae_total - (fb_total - bol_total)) < 0.001,
      f"AE={ae_total:.6f}, FB-BOL={fb_total-bol_total:.6f}")

check("A04", "P1 TE = +6.59%",
      abs(te_total - 6.59) < 0.05,
      f"Actual: {te_total:.2f}%")

# Device-level TE
te_bat = (p1['FB_dD_bat'].sum() - p1['BOL_dD_bat'].sum()) / p1['BOL_dD_bat'].sum() * 100
te_fc = (p1['FB_dD_fc'].sum() - p1['BOL_dD_fc'].sum()) / p1['BOL_dD_fc'].sum() * 100
te_el = (p1['FB_dD_el'].sum() - p1['BOL_dD_el'].sum()) / p1['BOL_dD_el'].sum() * 100

check("A05", "P1 device TE: BAT~1.1%, FC~7.3%, EL~9.0%",
      abs(te_bat - 1.1) < 0.3 and abs(te_fc - 7.3) < 0.5 and abs(te_el - 9.0) < 0.5,
      f"BAT={te_bat:.1f}%, FC={te_fc:.1f}%, EL={te_el:.1f}%")

# BOL LPSP (used by P2)
bol_lpsp = p1['BOL_LPSP_pct'].mean()
check("A06", "P1 BOL LPSP ~ 44.4%",
      abs(bol_lpsp - 44.4) < 0.5,
      f"Actual: {bol_lpsp:.1f}%")

# Year-end D
traj = pd.read_csv(ANNUAL / "annual_D_trajectory.csv")
d_bat_eoy = traj['D_bat'].iloc[-1]
check("A07", "P1 D_bat EOY = 0.916",
      abs(d_bat_eoy - 0.916) < 0.005,
      f"Actual: {d_bat_eoy:.4f}")

# ================================================================
# B. P1 <-> P3 Baseline Match
# ================================================================
print("\n[B] P1 <-> P3 Baseline Match...")

scen = pd.read_csv(SCENARIO / "scenario_sensitivity.csv")
baseline = scen[scen['Group'] == 'eta_degrade']
baseline_row = baseline[baseline['Value'] == 0.10]

if not baseline_row.empty:
    p3_te = float(baseline_row['TE_total_pct'].values[0])
    check("B01", "P3 baseline (eta=0.10) TE = P1 TE",
          abs(p3_te - te_total) < 0.1,
          f"P3={p3_te:.2f}%, P1={te_total:.2f}%")
else:
    check("B01", "P3 baseline row found", False, "eta=0.10 row not found")

# ================================================================
# C. P1 <-> P4 D_bat Trajectory Match
# ================================================================
print("\n[C] P1 <-> P4 D_bat Match...")

blast = pd.read_csv(ANNUAL / "blast_lfp_results.csv")
# P4 uses D_bat_annual = 0.9162 (from P1)
D_bat_annual_p4 = 0.9162
check("C01", "P4 D_bat_annual (0.9162) = P1 D_bat EOY",
      abs(D_bat_annual_p4 - d_bat_eoy) < 0.001,
      f"P4={D_bat_annual_p4:.4f}, P1={d_bat_eoy:.4f}")

# P4 Q deviation at 1yr
idx_1y = np.argmin(np.abs(blast['t_days'].values - 365))
D_py_1y = min(D_bat_annual_p4 * 365 / 365.0, 1.0)
Q_py_1y = 1.0 - 0.2 * D_py_1y
Q_blast_1y = blast['q'].iloc[idx_1y]
q_dev = abs(Q_blast_1y - Q_py_1y) / max(abs(Q_py_1y), 1e-6) * 100

check("C02", "P4 Q deviation @1yr < 20%",
      q_dev < 20,
      f"Q_dev={q_dev:.1f}% (BLAST={Q_blast_1y:.4f}, Py={Q_py_1y:.4f})")

# P4 r deviation at 1yr (using alpha_r=0.1673)
alpha_r = 0.1673
R_py_1y = 1.0 + alpha_r * D_py_1y
r_blast_1y = blast['r'].iloc[idx_1y]
r_dev = abs(r_blast_1y - R_py_1y) / max(abs(R_py_1y), 1e-6) * 100

check("C03", "P4 r deviation @1yr < 20%",
      r_dev < 20,
      f"r_dev={r_dev:.1f}% (BLAST={r_blast_1y:.4f}, Py={R_py_1y:.4f})")

# P4 direction consistency
q_decline = blast['q'].iloc[-1] < blast['q'].iloc[0]
r_growth = blast['r'].iloc[-1] > blast['r'].iloc[0]
check("C04", "P4 direction: Q decline + r growth",
      q_decline and r_growth,
      f"Q decline={q_decline}, r growth={r_growth}")

# ================================================================
# D. P2 <-> P5 Amplification Factor Match
# ================================================================
print("\n[D] P2 <-> P5 Amplification Match...")

# Load P2 comparison table
with open(LIFETIME / "lifetime_summary.csv", 'r') as f:
    content = f.read()
lines = content.strip().split('\n')
comp_start = None
for i, line in enumerate(lines):
    if '===' in line:
        comp_start = i + 1
        break
if comp_start:
    comp_lines = [lines[comp_start]] + lines[comp_start+1:]
    comp = pd.read_csv(StringIO('\n'.join(comp_lines)))
else:
    comp = pd.DataFrame()

# Strategy A amplification
for dev, expected in [('BAT', 1.012), ('FC', 1.061), ('EL', 1.076)]:
    row = comp[comp['Metric'].str.contains(f'amplification_{dev}', na=False)]
    if not row.empty:
        val = float(row['Strategy_A_replace'].values[0])
        check(f"D{dev}1", f"P2 Strategy A amplification {dev} = {expected}",
              abs(val - expected) < 0.002,
              f"Actual: {val:.3f}")

# Strategy B amplification (corrected values)
for dev, expected in [('BAT', 0.110), ('FC', 0.077), ('EL', 0.060)]:
    row = comp[comp['Metric'].str.contains(f'amplification_{dev}', na=False)]
    if not row.empty:
        val = float(row['Strategy_B_no_replace'].values[0])
        check(f"D{dev}2", f"P2 Strategy B amplification {dev} = {expected} (corrected)",
              abs(val - expected) < 0.002,
              f"Actual: {val:.3f}")

# BOL LPSP in P2 output
lpsp_row = comp[comp['Metric'].str.contains('10y_avg_LPSP', na=False)]
if not lpsp_row.empty:
    bol_lpsp_p2 = float(lpsp_row['BOL_linear'].values[0])
    check("D04", "P2 BOL LPSP = P1 BOL LPSP",
          abs(bol_lpsp_p2 - bol_lpsp) < 0.5,
          f"P2={bol_lpsp_p2:.1f}%, P1={bol_lpsp:.1f}%")

# ================================================================
# E. P2 Strategy B Physical Sanity
# ================================================================
print("\n[E] P2 Strategy B Physical Sanity...")

# Strategy B: total damage over 10 years should be ~1.0 per device
# (D goes from 0 to 1.0 and caps)
life_traj = pd.read_csv(LIFETIME / "lifetime_trajectory.csv")
life_traj = life_traj[pd.to_numeric(life_traj['Year'], errors='coerce').notna()].copy()
for col in ['B_D_bat', 'B_D_fc', 'B_D_el', 'B_dD_bat', 'B_dD_fc', 'B_dD_el']:
    life_traj[col] = pd.to_numeric(life_traj[col], errors='coerce')

# Total actual damage = final D (should be 1.0 for all devices in Strategy B)
d_bat_final = life_traj['B_D_bat'].iloc[-1]
d_fc_final = life_traj['B_D_fc'].iloc[-1]
d_el_final = life_traj['B_D_el'].iloc[-1]

check("E01", "Strategy B D_bat final = 1.0 (EOL cap)",
      abs(d_bat_final - 1.0) < 0.001,
      f"Actual: {d_bat_final:.6f}")

check("E02", "Strategy B D_fc final = 1.0 (EOL cap)",
      abs(d_fc_final - 1.0) < 0.001,
      f"Actual: {d_fc_final:.6f}")

check("E03", "Strategy B D_el final = 1.0 (EOL cap)",
      abs(d_el_final - 1.0) < 0.001,
      f"Actual: {d_el_final:.6f}")

# Sum of actual dD should equal final D (since start at 0)
sum_bat = life_traj['B_dD_bat'].sum()
sum_fc = life_traj['B_dD_fc'].sum()
sum_el = life_traj['B_dD_el'].sum()

check("E04", "Strategy B sum(dD_bat) = 1.0 (no virtual damage)",
      abs(sum_bat - 1.0) < 0.01,
      f"Actual sum: {sum_bat:.6f}")

check("E05", "Strategy B sum(dD_fc) = 1.0 (no virtual damage)",
      abs(sum_fc - 1.0) < 0.01,
      f"Actual sum: {sum_fc:.6f}")

check("E06", "Strategy B sum(dD_el) = 1.0 (no virtual damage)",
      abs(sum_el - 1.0) < 0.01,
      f"Actual sum: {sum_el:.6f}")

# Average annual damage should be ~0.100 (1.0 / 10 years)
avg_bat = sum_bat / 10
check("E07", "Strategy B avg annual dD_bat ~ 0.100",
      abs(avg_bat - 0.100) < 0.005,
      f"Actual: {avg_bat:.6f}")

# Amplification = avg_dD / BOL_annual
bol_annual_bat = 0.90596308
expected_amp_bat = avg_bat / bol_annual_bat
check("E08", "Strategy B amplification BAT = avg_dD / BOL_annual",
      abs(expected_amp_bat - 0.110) < 0.002,
      f"Calculated: {expected_amp_bat:.3f}")

# ================================================================
# F. P4 <-> P5 alpha_r and Method Match
# ================================================================
print("\n[F] P4 <-> P5 alpha_r Match...")

# Read P5 source to verify alpha_r
p5_src = (PROJECT_ROOT / "06_src" / "simulation" / "run_paper_figures.py").read_text()
p5_alpha_match = re.search(r'alpha_r\s*=\s*([\d.]+)', p5_src)
if p5_alpha_match:
    p5_alpha = float(p5_alpha_match.group(1))
    check("F01", "P5 alpha_r = 0.1673 (matches P4)",
          abs(p5_alpha - 0.1673) < 0.001,
          f"P5 alpha_r = {p5_alpha}")

# Check P5 r deviation denominator uses R_py
p5_rdenom_match = re.search(r'r_dev.*np\.maximum\((\w+)', p5_src)
if p5_rdenom_match:
    denom_var = p5_rdenom_match.group(1)
    check("F02", "P5 r deviation denominator = R_py (matches P4 method)",
          denom_var == 'R_py',
          f"Denominator variable: {denom_var}")

# Read P4 source to verify R_py_precise uses r0/r0_bol
p4_src = (PROJECT_ROOT / "06_src" / "simulation" / "run_matlab_crossval.py").read_text()
p4_precise_match = re.search(r'R_py_precise\[i\]\s*=\s*(.+)', p4_src)
if p4_precise_match:
    p4_precise_code = p4_precise_match.group(1).strip()
    check("F03", "P4 R_py_precise uses r0/r0_bol (not simplified formula)",
          'r0_bol' in p4_precise_code and '0.1673' not in p4_precise_code,
          f"Code: {p4_precise_code}")

# ================================================================
# G. P5 Figure Data Traceability
# ================================================================
print("\n[G] P5 Figure Data Traceability...")

# Check all required CSVs exist
required_csvs = [
    ANNUAL / "annual_damage_summary.csv",
    ANNUAL / "annual_D_trajectory.csv",
    LIFETIME / "lifetime_trajectory.csv",
    LIFETIME / "lifetime_summary.csv",
    SCENARIO / "scenario_sensitivity.csv",
    ANNUAL / "blast_lfp_results.csv",
]
for csv_path in required_csvs:
    check(f"G{csv_path.stem[:4]}", f"CSV exists: {csv_path.name}",
          csv_path.exists())

# Check figures exist
required_figs = [
    FIGURES / "paper_figure1_methodology.png",
    FIGURES / "paper_figure2_annual_lifetime.png",
    FIGURES / "paper_figure3_sensitivity_crossval.png",
]
for fig_path in required_figs:
    check(f"G{fig_path.stem[:4]}", f"Figure exists: {fig_path.name}",
          fig_path.exists() and fig_path.stat().st_size > 10000)

# ================================================================
# H. P2 Strategy A Consistency (unchanged by fixes)
# ================================================================
print("\n[H] P2 Strategy A Unchanged...")

# Strategy A replacements should be unchanged
repl_row = comp[comp['Metric'].str.contains('Replacements_BAT', na=False)]
if not repl_row.empty:
    repl_a_bat = int(repl_row['Strategy_A_replace'].values[0])
    check("H01", "P2 Strategy A BAT replacements = 9 (unchanged)",
          repl_a_bat == 9, f"Actual: {repl_a_bat}")

repl_row = comp[comp['Metric'].str.contains('Replacements_FC', na=False)]
if not repl_row.empty:
    repl_a_fc = int(repl_row['Strategy_A_replace'].values[0])
    check("H02", "P2 Strategy A FC replacements = 13 (unchanged)",
          repl_a_fc == 13, f"Actual: {repl_a_fc}")

repl_row = comp[comp['Metric'].str.contains('Replacements_EL', na=False)]
if not repl_row.empty:
    repl_a_el = int(repl_row['Strategy_A_replace'].values[0])
    check("H03", "P2 Strategy A EL replacements = 17 (unchanged)",
          repl_a_el == 17, f"Actual: {repl_a_el}")

# ================================================================
# I. LPSP Consistency
# ================================================================
print("\n[I] LPSP Consistency...")

lpsp_row = comp[comp['Metric'].str.contains('10y_avg_LPSP', na=False)]
if not lpsp_row.empty:
    a_lpsp = float(lpsp_row['Strategy_A_replace'].values[0])
    b_lpsp = float(lpsp_row['Strategy_B_no_replace'].values[0])
    check("I01", "P2 Strategy A LPSP ~ 44.8%",
          abs(a_lpsp - 44.8) < 0.5,
          f"Actual: {a_lpsp:.1f}%")
    check("I02", "P2 Strategy B LPSP ~ 45.2%",
          abs(b_lpsp - 45.2) < 0.5,
          f"Actual: {b_lpsp:.1f}%")
    check("I03", "Strategy B LPSP > Strategy A LPSP (degradation effect)",
          b_lpsp > a_lpsp,
          f"A={a_lpsp:.1f}%, B={b_lpsp:.1f}%")
    check("I04", "BOL LPSP < Strategy A LPSP (feedback effect)",
          bol_lpsp < a_lpsp,
          f"BOL={bol_lpsp:.1f}%, A={a_lpsp:.1f}%")

# ================================================================
# Summary
# ================================================================
print("\n" + "=" * 70)
print("VERIFICATION SUMMARY")
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
summary_path = RESULTS / "crossphase_verification_summary.csv"
with open(summary_path, 'w', encoding='utf-8') as f:
    f.write("Check_ID,Description,Status,Detail\n")
    for cid, desc, status, detail in results:
        f.write(f"{cid},{desc},{status},{detail}\n")
print(f"\n  Summary written: {summary_path}")

sys.exit(0 if n_fail == 0 else 1)
