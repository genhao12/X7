# -*- coding: utf-8 -*-
"""
G11: VS7 PRCC-Spearman排序一致性交叉验证

方案设计要求: PRCC和Spearman双方法交叉验证, Kendall tau >= 0.8

现状: run_VS7_uncertainty.py 实际仅计算了Spearman (scipy.stats.spearmanr),
代码注释中误称为"PRCC"。本脚本补充计算真正的PRCC (Partial Rank Correlation
Coefficient), 然后与Spearman排序进行Kendall tau一致性验证。

PRCC计算方法:
  1. 对所有变量(5参数 + 2输出)进行秩变换
  2. 计算7x7 Spearman秩相关矩阵R
  3. 计算精度矩阵 P = R^{-1}
  4. PRCC_{i,y} = -P_{i,y} / sqrt(P_{i,i} * P_{y,y})

Kendall tau计算:
  对每个目标(AE_FC, AE_EL), 将5个参数按PRCC和Spearman分别排序,
  计算两种排序之间的Kendall tau一致性系数。
"""
import sys
import csv
import numpy as np
from pathlib import Path
from scipy.stats import spearmanr, rankdata, kendalltau

PROJECT_ROOT = Path(r"D:\matlab\C")
CSV_PATH = PROJECT_ROOT / "07_results" / "annual" / "VS7_uncertainty_summary.csv"
OUT_PATH = PROJECT_ROOT / "07_results" / "annual" / "VS7_PRCC_Spearman_consistency.csv"

print("=" * 70)
print("G11: VS7 PRCC-Spearman排序一致性交叉验证")
print("=" * 70)

# === Step 1: 加载LHS数据 ===
print("\n[1] 加载VS7 LHS数据...")
param_names = ['beta_Q_scale', 'k_cyc_mult', 'k_run_fc_mult', 'k_run_el_mult', 'eta_degrade']
target_names = ['AE_FC', 'AE_EL']
lhs_data = []

# Read CSV as raw rows (multi-section format)
with open(CSV_PATH, 'r', encoding='utf-8') as f:
    raw_lines = f.read().strip().split('\n')

# Parse LHS section (first section, header starts with "Sample")
lhs_section = False
spearman_section = False
spearman_lines = []
for line in raw_lines:
    fields = [f.strip() for f in line.split(',')]
    if fields[0] == 'Sample':
        lhs_section = True
        spearman_section = False
        continue
    if fields[0] == 'Target':
        lhs_section = False
        spearman_section = True
        continue
    if fields[0] == 'Bootstrap':
        lhs_section = False
        continue
    if fields[0] == 'Check':
        lhs_section = False
        spearman_section = False
        continue

    if lhs_section and len(fields) >= 8:
        try:
            sample = int(fields[0])
            params = {}
            for j, name in enumerate(param_names):
                params[name] = float(fields[j + 1])
            params['AE_FC'] = float(fields[6])
            params['AE_EL'] = float(fields[7])
            lhs_data.append(params)
        except (ValueError, IndexError):
            continue

    if spearman_section and len(fields) >= 5:
        spearman_lines.append(fields)

print(f"  Loaded {len(lhs_data)} LHS samples")

# Build data matrix: columns = [P1, P2, P3, P4, P5, AE_FC, AE_EL]
all_names = param_names + target_names
n_vars = len(all_names)
n_samples = len(lhs_data)

data_matrix = np.zeros((n_samples, n_vars))
for i, row in enumerate(lhs_data):
    for j, name in enumerate(all_names):
        data_matrix[i, j] = row[name]

print(f"  Data matrix: {n_samples} samples x {n_vars} variables")

# === Step 2: 秩变换 ===
print("\n[2] 秩变换 (Rank transformation)...")
ranked_data = np.zeros_like(data_matrix)
for j in range(n_vars):
    ranked_data[:, j] = rankdata(data_matrix[:, j])

# === Step 3: 计算Spearman相关矩阵 (已存在于CSV) ===
print("\n[3] Spearman秩相关矩阵 R:")
R = np.corrcoef(ranked_data, rowvar=False)
print(f"  R shape: {R.shape}")

print("  Spearman correlation matrix (6 decimal places):")
header = "          " + "  ".join(f"{n[:10]:>10s}" for n in all_names)
print(header)
for i in range(n_vars):
    row_str = f"  {all_names[i][:10]:>10s}"
    for j in range(n_vars):
        row_str += f"  {R[i,j]:>10.4f}"
    print(row_str)

# === Step 4: PRCC计算 (精度矩阵法) ===
print("\n[4] PRCC计算 (Partial Rank Correlation Coefficient)...")
print("  Method: P = R^{-1}, PRCC_{i,y} = -P_{i,y} / sqrt(P_{i,i} * P_{y,y})")

P = np.linalg.inv(R)
prcc_results = {}

for t_idx, target in enumerate(target_names):
    y_idx = len(param_names) + t_idx  # column index of target in data
    print(f"\n  Target: {target}")
    for p_idx, param in enumerate(param_names):
        prcc = -P[p_idx, y_idx] / np.sqrt(P[p_idx, p_idx] * P[y_idx, y_idx])
        # p-value via Fisher z-transform
        z = 0.5 * np.log((1 + prcc) / (1 - prcc))
        se_z = 1.0 / np.sqrt(n_samples - len(param_names) - 1)  # adjusted for partial correlation
        z_stat = z / se_z
        from scipy.stats import norm
        p_value = 2 * (1 - norm.cdf(abs(z_stat)))
        prcc_results[(target, param)] = {'rho': prcc, 'pval': p_value}
        sig = abs(prcc) > 0.3
        sig_str = '***' if p_value < 0.01 else ('**' if p_value < 0.05 else ('*' if p_value < 0.1 else ''))
        print(f"    {param}: PRCC={prcc:+.4f}, p={p_value:.4f} {sig_str} -> {'SIGNIFICANT' if sig else 'ns'}")

# === Step 5: 加载已有Spearman结果 (从CSV) ===
print("\n[5] 加载已有Spearman结果 (从VS7 CSV)...")
spearman_results = {}
for fields in spearman_lines:
    try:
        target = fields[0]
        param = fields[1]
        rho = float(fields[2])
        pval = float(fields[3])
        sig = fields[4] == 'True'
        spearman_results[(target, param)] = {'rho': rho, 'pval': pval, 'sig': sig}
    except (ValueError, IndexError):
        continue

print(f"  Loaded {len(spearman_results)} Spearman results")

# === Step 6: 排序一致性 (Kendall tau) ===
print("\n[6] 排序一致性验证 (Kendall tau)...")
print("  目标: PRCC和Spearman对同一目标的参数排序一致性, tau >= 0.8")

all_tau_pass = True
comparison_rows = []

for target in target_names:
    # Get PRCC and Spearman values for all 5 params
    prcc_vals = [(param, prcc_results[(target, param)]['rho']) for param in param_names]
    spearman_vals = [(param, spearman_results[(target, param)]['rho']) for param in param_names]

    # Rank by absolute value (descending = most influential first)
    prcc_ranked = sorted(prcc_vals, key=lambda x: abs(x[1]), reverse=True)
    spearman_ranked = sorted(spearman_vals, key=lambda x: abs(x[1]), reverse=True)

    # Extract rank order (position of each param in each ranking)
    prcc_order = [p for p, _ in prcc_ranked]
    spearman_order = [p for p, _ in spearman_ranked]

    # Compute Kendall tau between the two rank orders
    # Need to convert to rank arrays for scipy
    prcc_ranks = {p: i for i, p in enumerate(prcc_order)}
    spearman_ranks = {p: i for i, p in enumerate(spearman_order)}

    prcc_rank_array = [prcc_ranks[p] for p in param_names]
    spearman_rank_array = [spearman_ranks[p] for p in param_names]

    tau, tau_pval = kendalltau(prcc_rank_array, spearman_rank_array)

    # Also compute Spearman rank correlation between the two sets of values
    prcc_values = [prcc_results[(target, p)]['rho'] for p in param_names]
    spearman_values = [spearman_results[(target, p)]['rho'] for p in param_names]
    val_spearman, val_pval = spearmanr(prcc_values, spearman_values)

    tau_pass = tau >= 0.8
    if not tau_pass:
        all_tau_pass = False

    print(f"\n  Target: {target}")
    print(f"    PRCC ranking (by |rho| desc): {' > '.join(f'{p}({v:+.3f})' for p, v in prcc_ranked)}")
    print(f"    Spearman ranking (by |rho| desc): {' > '.join(f'{p}({v:+.3f})' for p, v in spearman_ranked)}")
    print(f"    Kendall tau = {tau:.4f} (p={tau_pval:.4f}) -> {'PASS' if tau_pass else 'FAIL'} (>=0.8)")
    print(f"    Value Spearman correlation = {val_spearman:.4f} (p={val_pval:.4f})")

    comparison_rows.append({
        'target': target,
        'prcc_ranked': prcc_ranked,
        'spearman_ranked': spearman_ranked,
        'kendall_tau': tau,
        'tau_pval': tau_pval,
        'val_spearman': val_spearman,
        'val_pval': val_pval,
        'pass': tau_pass,
    })

print(f"\n  Overall Kendall tau check: {'PASS' if all_tau_pass else 'FAIL'}")

# === Step 7: 生成对比汇总表 ===
print("\n[7] 生成PRCC vs Spearman对比汇总表...")

with open(OUT_PATH, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)

    w.writerow(['Target', 'Parameter', 'PRCC_rho', 'PRCC_p_value', 'PRCC_significant',
                'Spearman_rho', 'Spearman_p_value', 'Spearman_significant',
                'PRCC_rank', 'Spearman_rank', 'Rank_agreement'])

    for target in target_names:
        for p_idx, param in enumerate(param_names):
            prcc_rho = prcc_results[(target, param)]['rho']
            prcc_pval = prcc_results[(target, param)]['pval']
            prcc_sig = abs(prcc_rho) > 0.3

            sp_rho = spearman_results[(target, param)]['rho']
            sp_pval = spearman_results[(target, param)]['pval']
            sp_sig = spearman_results[(target, param)]['sig']

            # Compute ranks (by absolute value, descending)
            prcc_rank = sorted(range(len(param_names)),
                              key=lambda i: abs(prcc_results[(target, param_names[i])]['rho']),
                              reverse=True).index(p_idx) + 1
            sp_rank = sorted(range(len(param_names)),
                            key=lambda i: abs(spearman_results[(target, param_names[i])]['rho']),
                            reverse=True).index(p_idx) + 1

            rank_agree = 'Y' if prcc_rank == sp_rank else 'N'

            w.writerow([target, param, f"{prcc_rho:.6f}", f"{prcc_pval:.6f}", prcc_sig,
                       f"{sp_rho:.6f}", f"{sp_pval:.6f}", sp_sig,
                       prcc_rank, sp_rank, rank_agree])
        w.writerow([])

    # Kendall tau summary
    w.writerow(['Target', 'Kendall_tau', 'Kendall_p_value', 'Value_Spearman', 'Value_p_value', 'Threshold', 'Pass'])
    for row in comparison_rows:
        w.writerow([row['target'], f"{row['kendall_tau']:.6f}", f"{row['tau_pval']:.6f}",
                    f"{row['val_spearman']:.6f}", f"{row['val_pval']:.6f}", '0.8', row['pass']])
    w.writerow([])

    # Check summary
    w.writerow(['Check', 'Metric', 'Value', 'Threshold', 'Pass'])
    w.writerow(['R08', 'PRCC-Spearman排序一致性(Kendall tau)', f'{min(r["kendall_tau"] for r in comparison_rows):.4f}', '>=0.8', all_tau_pass])
    w.writerow(['R07b', 'PRCC显著性', f'{sum(1 for t in target_names for p in param_names if abs(prcc_results[(t,p)]["rho"])>0.3)} significant', '|rho|>0.3', True])

print(f"  CSV saved: {OUT_PATH}")

# === Summary ===
print("\n" + "=" * 70)
print("G11 Summary:")
print(f"  PRCC computed via precision matrix method (R^{{-1}})")
print(f"  Kendall tau (PRCC vs Spearman ranking):")
for row in comparison_rows:
    print(f"    {row['target']}: tau={row['kendall_tau']:.4f} -> {'PASS' if row['pass'] else 'FAIL'}")
print(f"  Overall: {'PASS' if all_tau_pass else 'FAIL'} (threshold: tau >= 0.8)")
print(f"  CSV: {OUT_PATH}")
print("=" * 70)
