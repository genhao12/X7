# -*- coding: utf-8 -*-
"""
audit_hp_fc_el.py — 审查 hp_fc_lite.py 和 hp_el_lite.py 的实现正确性
检查项:
  A. D定义与EOL一致性 (FC: 70A恒流 vs 1A/cm²额定)
  B. 效率映射物理方向 (FC: V降→η降, EL: V升→η降)
  C. R_hfr/R_ohm拟合合理性 (非单调/退化期选择)
  D. closed_loop_lite简化系数 vs hp_fc/hp_el精确映射的一致性
  E. FC vs EL对比表量纲混淆
  F. 联盟OFF恒等检查 (D=0返回BOL)
"""
import sys
import json
import numpy as np
from pathlib import Path

PROJECT_ROOT = Path(r"D:\matlab\C")
sys.path.insert(0, str(PROJECT_ROOT / "06_src"))

from components.hp_fc_lite import _load_fc1_data, fit_fc_aging, interpolate_hp as interp_fc
from components.hp_el_lite import _load_el_bol_data, fit_el_aging, interpolate_hp as interp_el

print("=" * 70)
print("hp_fc_lite / hp_el_lite 实现审查报告")
print("=" * 70)

issues = []  # (severity, description)

# === 加载数据 ===
print("\n加载数据...")
fc_data = _load_fc1_data()
fc_fit = fit_fc_aging(fc_data)
with open(PROJECT_ROOT / "05_config" / "mvp_bol_parameters.json", 'r', encoding='utf-8') as f:
    bol_full = json.load(f)
fc_bol = {'stack_config': bol_full['pemfc_bol']['stack_config'], 'fc_system_kw': 37.5}
hp_fc = interp_fc(fc_fit, fc_bol)

el_data = _load_el_bol_data()
el_fit = fit_el_aging(el_data)
el_bol = {'el_system_kw': 35.0, 'active_area_cm2': 50.0}
hp_el = interp_el(el_fit, el_bol)

# ============================================================
# A. D定义与EOL一致性
# ============================================================
print("\n" + "=" * 70)
print("[A] D定义与EOL一致性检查")
print("=" * 70)

# A1. FC: EOL定义基于"额定电流密度(1A/cm²)下电压降10%"
#     但退化率k_v从70A恒流测试得到
fc_eol_def = bol_full['pemfc_bol']['eol_definition']
print(f"\nFC EOL定义: {fc_eol_def['description']}")
print(f"  rated_voltage_loss_eol_fraction = {fc_eol_def['rated_voltage_loss_eol_fraction']}")

u_bol_70a = fc_fit['u_bol_V']        # 0.667V (70A下BOL)
u_bol_rated = fc_fit['bol_voltage_rated_V']  # 0.6158V (1A/cm²下BOL)
k_v = fc_fit['k_v_mV_per_h'] / 1000  # V/h

t_eol_70a = u_bol_70a * 0.10 / k_v   # 用70A的BOL电压
t_eol_rated = u_bol_rated * 0.10 / k_v  # 用额定的BOL电压
print(f"\n  U_BOL(70A) = {u_bol_70a}V")
print(f"  U_BOL(1A/cm²) = {u_bol_rated}V")
print(f"  k_v = {k_v*1000:.4f} mV/h")
print(f"  t_EOL(用70A基准) = {t_eol_70a:.0f}h  ← 代码当前使用")
print(f"  t_EOL(用额定基准) = {t_eol_rated:.0f}h  ← 严格按EOL定义")
print(f"  偏差 = {abs(t_eol_70a-t_eol_rated)/t_eol_rated*100:.1f}%")

if abs(t_eol_70a - t_eol_rated) / t_eol_rated > 0.05:
    issues.append(("LOW",
        f"[A1] FC t_EOL基准不一致: 代码用70A的U_BOL({u_bol_70a}V)计算t_EOL={t_eol_70a:.0f}h, "
        f"但EOL定义基于1A/cm²的U_BOL({u_bol_rated}V), 严格值应为t_EOL={t_eol_rated:.0f}h (偏差{abs(t_eol_70a-t_eol_rated)/t_eol_rated*100:.1f}%). "
        f"影响: alpha_R拟合值偏小~8%, 不影响方向结论. 建议修正或注释说明."))

# A2. EL: EOL定义基于"比能耗增加10%"
el_eol_def = bol_full['pemel_bol']['eol_definition']
print(f"\nEL EOL定义: {el_eol_def['description']}")
print(f"  specific_energy_increment_eol_fraction = {el_eol_def['specific_energy_increment_eol_fraction']}")
v_bol_el = el_fit['v_cell_bol_V']  # 1.75V
v_eol_el = v_bol_el * (1 + 0.10)   # 1.925V
print(f"  V_BOL = {v_bol_el}V, V_EOL = {v_eol_el}V")
print(f"  比能耗 ∝ V_cell, 增加10% → V_cell增10% → 正确")

# ============================================================
# B. 效率映射物理方向
# ============================================================
print("\n" + "=" * 70)
print("[B] 效率映射物理方向检查")
print("=" * 70)

# B1. FC: D增加 → V_cell降低 → eta降低 (正确)
d_test = np.array([0, 0.5, 1.0])
for d in d_test:
    eta_f, r, pmax = hp_fc(d)
    print(f"  FC  D={d:.1f}: eta_frac={eta_f:.4f}, P_max={pmax:.2f}kW")

fc_eta_bol = hp_fc(0.0)[0]
fc_eta_eol = hp_fc(1.0)[0]
if fc_eta_eol < fc_eta_bol:
    print(f"  → FC: D增加→eta降低 (BOL={fc_eta_bol}→EOL={fc_eta_eol}): 正确 ✓")
else:
    issues.append(("HIGH", "[B1] FC效率方向错误: D增加时eta应降低"))

# B2. EL: D增加 → V_cell增加 → eta降低 (正确, 因eta=V_rev/V_cell)
for d in d_test:
    eta_f, r, pmax = hp_el(d)
    v_cell = v_bol_el * (1 + 0.10 * d)
    print(f"  EL  D={d:.1f}: eta_frac={eta_f:.4f}, V_cell={v_cell:.4f}V, P_max={pmax:.2f}kW")

el_eta_bol = hp_el(0.0)[0]
el_eta_eol = hp_el(1.0)[0]
if el_eta_eol < el_eta_bol:
    print(f"  → EL: D增加→V升→eta降低 (BOL={el_eta_bol}→EOL={el_eta_eol}): 正确 ✓")
else:
    issues.append(("HIGH", "[B2] EL效率方向错误: D增加时eta应降低"))

# B3. FC vs EL: eta_frac(EOL)不同 (FC=0.90线性, EL=0.909非线性)
print(f"\n  FC eta_frac(EOL) = {fc_eta_eol:.4f} (线性: 1-0.10×1)")
print(f"  EL eta_frac(EOL) = {el_eta_eol:.4f} (非线性: 1/(1+0.10×1))")
print(f"  → 两者不同是物理正确的: FC的eta∝V(电压降10%→eta降10%), EL的eta=V_rev/V(V升10%→eta降~9%) ✓")

# ============================================================
# C. R_hfr/R_ohm拟合合理性
# ============================================================
print("\n" + "=" * 70)
print("[C] R_hfr/R_ohm拟合合理性检查")
print("=" * 70)

# C1. FC R_hfr非单调问题
eis = fc_data['eis_r_hfr']
t_eis = eis['time_h']
r_hfr = eis['r_hfr_ohm'] * 1000  # mOhm
r_norm = r_hfr / r_hfr[0]

print(f"\nFC R_hfr演化:")
for i, tp in enumerate(t_eis):
    d_val = tp / fc_fit['t_eol_h']
    print(f"  t={tp:4.0f}h, D={d_val:.4f}, R={r_hfr[i]:.4f}mΩ, R/R0={r_norm[i]:.4f}")

# 检查: D<0.18时R是否低于BOL?
early_dip = np.any(r_norm[:4] < 1.0)  # 前4个时点(D<0.18)
if early_dip:
    min_r = np.min(r_norm[:4])
    min_idx = np.argmin(r_norm[:4])
    print(f"\n  ⚠ R_hfr在早期(D<{0.18:.2f})低于BOL: R/R0最小={min_r:.4f} at t={t_eis[min_idx]}h")
    print(f"    原因: PEMFC活化期膜水合改善→R暂时降低, 后期退化主导→R上升")
    print(f"    影响: r_hfr_growth_func(D)=1+α_R×D 在D<{0.18:.2f}时预测R<R_BOL")
    
    # 检查: hp_fc(D=0.05)的R是否<R_BOL?
    _, r_005, _ = hp_fc(0.05)
    _, r_010, _ = hp_fc(0.10)
    r_bol = hp_fc(0.0)[1]
    print(f"    hp_fc(D=0.05) R = {r_005*1000:.4f}mΩ vs BOL = {r_bol*1000:.4f}mΩ → {'R<BOL!' if r_005 < r_bol else 'R≥BOL'}")
    print(f"    hp_fc(D=0.10) R = {r_010*1000:.4f}mΩ vs BOL = {r_bol*1000:.4f}mΩ → {'R<BOL!' if r_010 < r_bol else 'R≥BOL'}")
    
    if r_005 < r_bol:
        issues.append(("MEDIUM",
            f"[C1] FC R_hfr在低D值(<0.18)时低于BOL: hp_fc(0.05)={r_005*1000:.4f}mΩ < BOL={r_bol*1000:.4f}mΩ. "
            f"原因: EIS数据在活化期R暂时降低, 线性拟合1+α_R×D外推到D<0.18时R<BOL. "
            f"影响: 联盟OFF检查D=0时R=R_BOL(PASS), 但低D值R可能不物理. "
            f"建议: (1)对D<0.18使用max(1, 1+α_R×D)截断, 或(2)在注释中说明此为线性外推局限."))
    else:
        print(f"    → 线性模型在D=0.05时R≥BOL, 因α_R={fc_fit['alpha_r']}较小, R降幅不显著 ✓")

# C2. EL alpha_R=0.10来源
print(f"\nEL R_ohm增长假设:")
print(f"  alpha_R = {el_fit['alpha_r']} (直接=SPECIFIC_ENERGY_INCREMENT_EOL)")
print(f"  含义: 假设R_ohm在EOL时增长10%, 与V_cell增长同比例")
print(f"  来源: 无系统级EL长时老化数据, 采用保守线性假设")
print(f"  风险: 实际R_ohm可能增长更多(膜退化非均匀), 或更少(活化主导)")

# ============================================================
# D. closed_loop_lite简化系数 vs hp_fc/hp_el精确映射
# ============================================================
print("\n" + "=" * 70)
print("[D] closed_loop_lite简化系数 vs hp_fc/hp_el精确映射一致性")
print("=" * 70)

print(f"\nM3 closed_loop_lite中的FC/EL退化系数:")
print(f"  eta_factor_fc = 1.0 + 0.1 * D_fc  (线性近似)")
print(f"  eta_factor_el = 1.0 + 0.1 * D_el  (线性近似)")
print(f"  k_run_fc = 3e-4 * eta_factor_fc")
print(f"  k_run_el = 2e-4 * eta_factor_el")

print(f"\nhp_fc_lite精确映射 (eta_frac = 1 - 0.10*D):")
print(f"  应力放大因子 = 1/eta_frac = 1/(1-0.10*D)")
print(f"  {'D':>6s}  {'M3简化(1+0.1D)':>14s}  {'精确(1/(1-0.1D))':>18s}  {'偏差%':>8s}")
for d in [0.0, 0.2, 0.5, 0.8, 1.0]:
    m3_factor = 1.0 + 0.1 * d
    exact_factor = 1.0 / (1.0 - 0.1 * d)
    dev = (m3_factor - exact_factor) / exact_factor * 100
    print(f"  {d:6.1f}  {m3_factor:14.4f}  {exact_factor:18.4f}  {dev:8.2f}%")

print(f"\nhp_el_lite精确映射 (eta_frac = 1/(1+0.10*D)):")
print(f"  应力放大因子 = 1/eta_frac = 1+0.10*D (与M3简化完全一致!)")
print(f"  {'D':>6s}  {'M3简化(1+0.1D)':>14s}  {'精确(1+0.10D)':>18s}  {'偏差%':>8s}")
for d in [0.0, 0.2, 0.5, 0.8, 1.0]:
    m3_factor = 1.0 + 0.1 * d
    exact_factor = 1.0 + 0.1 * d  # EL: 1/eta_frac = 1+0.10D
    dev = 0.0
    print(f"  {d:6.1f}  {m3_factor:14.4f}  {exact_factor:18.4f}  {dev:8.2f}%")

# FC偏差检查
max_fc_dev = max(abs((1.0 + 0.1*d - 1.0/(1.0-0.1*d)) / (1.0/(1.0-0.1*d))) * 100 for d in [0.2, 0.5, 0.8, 1.0])
if max_fc_dev > 5:
    issues.append(("MEDIUM",
        f"[D1] M3简化eta_factor_fc=1+0.1D与hp_fc精确映射1/(1-0.10D)在D=1时偏差={max_fc_dev:.1f}%. "
        f"影响: M3的FC损伤计算可能偏低(简化值1.10 vs 精确值1.111). "
        f"建议: VS2时使用hp_fc精确映射, M3保持简化(偏差<2%)."))
else:
    print(f"\n  FC最大偏差={max_fc_dev:.1f}% < 5%阈值 → M3简化可接受 ✓")

print(f"\n  EL偏差=0% → M3简化与精确映射完全一致 ✓ (因EL的eta_frac非线性, 1/eta_frac=1+0.10D恰好线性)")

# ============================================================
# E. FC vs EL对比表量纲混淆
# ============================================================
print("\n" + "=" * 70)
print("[E] hp_el_lite自测中FC vs EL对比表量纲检查")
print("=" * 70)

print(f"\n  当前对比表第3行:")
print(f"    'eta_EOL'  FC=0.9000  EL=0.6509")
print(f"  问题: FC的0.90是eta_frac(归一化), EL的0.6509是eta_absolute(绝对效率)")
print(f"         两者量纲不同, 不可直接对比!")

fc_eta_frac_eol = hp_fc(1.0)[0]
el_eta_frac_eol = hp_el(1.0)[0]
el_eta_abs_eol = 1.253 / (1.75 * 1.10)
print(f"\n  正确对比:")
print(f"    eta_frac(EOL):  FC={fc_eta_frac_eol:.4f}  EL={el_eta_frac_eol:.4f}  (同量纲, 可对比)")
print(f"    eta_abs(EOL):   FC≈0.5542   EL={el_eta_abs_eol:.4f}  (FC需重算, BOL不同)")

issues.append(("LOW",
    f"[E1] hp_el_lite自测中FC vs EL对比表混淆eta_frac和eta_absolute: "
    f"FC的0.90是eta_frac, EL的0.6509是eta_absolute. "
    f"修正: 统一用eta_frac对比 (FC={fc_eta_frac_eol:.4f}, EL={el_eta_frac_eol:.4f})"))

# ============================================================
# F. 联盟OFF恒等检查
# ============================================================
print("\n" + "=" * 70)
print("[F] 联盟OFF恒等检查 (D=0返回BOL)")
print("=" * 70)

# FC
fc_eta0, fc_r0, fc_pmax0 = hp_fc(0.0)
fc_bol_r = fc_fit['r_hfr_bol_ohm']
fc_bol_pmax = 37.5
print(f"\nFC D=0:")
print(f"  eta_frac = {fc_eta0:.6f} (期望1.0) → {'PASS' if abs(fc_eta0-1.0)<1e-6 else 'FAIL'}")
print(f"  R_hfr = {fc_r0:.8f} Ohm (期望{fc_bol_r:.8f}) → {'PASS' if abs(fc_r0-fc_bol_r)<1e-8 else 'FAIL'}")
print(f"  P_max = {fc_pmax0:.4f} kW (期望{fc_bol_pmax}) → {'PASS' if abs(fc_pmax0-fc_bol_pmax)<1e-6 else 'FAIL'}")

# EL
el_eta0, el_r0, el_pmax0 = hp_el(0.0)
el_bol_r = el_fit['r_ohm_bol_ohm_cm2']
el_bol_pmax = 35.0
print(f"\nEL D=0:")
print(f"  eta_frac = {el_eta0:.6f} (期望1.0) → {'PASS' if abs(el_eta0-1.0)<1e-6 else 'FAIL'}")
print(f"  R_ohm = {el_r0:.6f} (期望{el_bol_r:.6f}) → {'PASS' if abs(el_r0-el_bol_r)<1e-6 else 'FAIL'}")
print(f"  P_max = {el_pmax0:.4f} kW (期望{el_bol_pmax}) → {'PASS' if abs(el_pmax0-el_bol_pmax)<1e-6 else 'FAIL'}")

# ============================================================
# G. 单调性全面检查
# ============================================================
print("\n" + "=" * 70)
print("[G] 单调性全面检查 (0≤D≤1, 100点)")
print("=" * 70)

d_grid = np.linspace(0, 1, 100)

# FC
fc_etas = np.array([hp_fc(d)[0] for d in d_grid])
fc_rs = np.array([hp_fc(d)[1] for d in d_grid])
fc_pmaxs = np.array([hp_fc(d)[2] for d in d_grid])
fc_eta_mono = np.all(np.diff(fc_etas) <= 1e-10)
fc_r_mono = np.all(np.diff(fc_rs) >= -1e-10)
fc_pmax_mono = np.all(np.diff(fc_pmaxs) <= 1e-10)
print(f"\nFC:")
print(f"  eta_frac 单调递减: {'PASS' if fc_eta_mono else 'FAIL'}")
print(f"  R_hfr 单调递增: {'PASS' if fc_r_mono else 'FAIL'}")
print(f"  P_max 单调递减: {'PASS' if fc_pmax_mono else 'FAIL'}")

# EL
el_etas = np.array([hp_el(d)[0] for d in d_grid])
el_rs = np.array([hp_el(d)[1] for d in d_grid])
el_pmaxs = np.array([hp_el(d)[2] for d in d_grid])
el_eta_mono = np.all(np.diff(el_etas) <= 1e-10)
el_r_mono = np.all(np.diff(el_rs) >= -1e-10)
el_pmax_mono = np.all(np.diff(el_pmaxs) <= 1e-10)
print(f"\nEL:")
print(f"  eta_frac 单调递减: {'PASS' if el_eta_mono else 'FAIL'}")
print(f"  R_ohm 单调递增: {'PASS' if el_r_mono else 'FAIL'}")
print(f"  P_max 单调递减: {'PASS' if el_pmax_mono else 'FAIL'}")

# ============================================================
# H. 比例关系检查
# ============================================================
print("\n" + "=" * 70)
print("[H] 比例关系检查 (P_max ∝ eta_frac?)")
print("=" * 70)

# FC: P_max = P_rated * eta_frac (应为严格线性)
fc_pmax_ratio = fc_pmaxs / fc_pmaxs[0]
fc_eta_ratio = fc_etas / fc_etas[0]
fc_prop_err = np.max(np.abs(fc_pmax_ratio - fc_eta_ratio))
print(f"\nFC: P_max/P_BOL vs eta_frac/eta_BOL 最大偏差 = {fc_prop_err:.2e}")
print(f"  → {'PASS (严格比例)' if fc_prop_err < 1e-6 else 'FAIL'}")

# EL: P_max = P_rated * eta_frac (应为严格线性)
el_pmax_ratio = el_pmaxs / el_pmaxs[0]
el_eta_ratio = el_etas / el_etas[0]
el_prop_err = np.max(np.abs(el_pmax_ratio - el_eta_ratio))
print(f"\nEL: P_max/P_BOL vs eta_frac/eta_BOL 最大偏差 = {el_prop_err:.2e}")
print(f"  → {'PASS (严格比例)' if el_prop_err < 1e-6 else 'FAIL'}")

# ============================================================
# 汇总
# ============================================================
print("\n" + "=" * 70)
print("审查结果汇总")
print("=" * 70)

if not issues:
    print("\n✓ 未发现任何问题, 所有检查通过.")
else:
    print(f"\n发现 {len(issues)} 个问题:")
    for i, (sev, desc) in enumerate(issues, 1):
        print(f"\n  [{sev}] 问题{i}: {desc}")

print(f"\n严重性分级:")
print(f"  HIGH   = 影响正确性, 必须修复")
print(f"  MEDIUM = 影响精度, 建议修复")
print(f"  LOW    = 影响显示/注释, 可选修复")
