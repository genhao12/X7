# -*- coding: utf-8 -*-
"""
VS7 不确定性验证 (V3层) — MVP简化版   [v2 后验版 2026-08-28]
LHS参数采样 + Bootstrap重采样 + PRCC/Spearman相关性

8个不确定参数 (LHS采样):
  P1 beta_Q_scale:   BAT容量退化率缩放 (标称1.0, [0.8, 1.2])
  P2 k_cyc_mult:     BAT循环损伤系数缩放 —— v2改为**后验采样**
                     先验(v1): U[0.5,1.5] (CI宽度/均值=66.7%)
                     后验(v2): a_d ~ N(a_d_mean, a_d_sd) 经**逆CDF**接入LHS,
                              再 /A_D_POINT(5.0714e-4) 得乘子 (CI宽度/均值≈21.2%)
  P3 k_cal_mult:     BAT日历项缩放 ([0.4, 1.0]) —— 对应 Naumann 2-5%/年
                     (恒定速率日历在 AE=DEG-BOL 差值中相减抵消,
                      仅经交互项 k_int*sqrt(d_cal*d_cyc) 生效)
  P4 k_int:          日历-循环交互项 ([0, 0.5], 文献先验, 非标定)
  P5 k_run_fc_mult:  FC运行损伤系数缩放 (标称1.0, [0.8, 1.2])
  P6 k_run_el_mult:  EL运行损伤系数缩放 (标称1.0, [0.8, 1.2])
  P7 eta_degrade:    FC/EL效率退化率 (标称0.1, [0.08, 0.12])
  P8 fc_model_branch:FC损伤模型分支 (0=legacy closed_loop_lite, 1=四机制)

输出指标:
  AE_FC / AE_EL: 原口径 (BAT退化D=0.9 对 FC/EL 损伤的放大效应) —— 与v1可直接比
  AE_BAT:        BAT自身损伤差 (v2新增; 后验a_d/日历/交互项在此生效)
  AE_TOTAL:      三设备合计 (v2新增; 后验收窄的主要观察对象)

检查项:
  R05: Bootstrap同号率 (AE符号一致>=95%)
  R06: LHS覆盖 (30样本, 8参数)
  R07: PRCC显著性 (|PRCC|>0.3 for >=1 parameter)
  R08: 后验收窄 (v2新增) AE_TOTAL 的 LHS 95%CI 宽度相对先验收窄
"""
import sys, json, csv
import numpy as np
import pandas as pd
from pathlib import Path
from scipy.stats import spearmanr, norm

import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec

PROJECT_ROOT = Path(r"D:\matlab\C")
sys.path.insert(0, str(PROJECT_ROOT / "06_src"))
from utils.fit_table import auto_fit_table

plt.rcParams.update({'axes.unicode_minus': False, 
    'font.family': 'sans-serif', 'font.sans-serif': ['Microsoft YaHei', 'SimHei', 'Arial', 'DejaVu Sans'],
    'font.size': 11, 'axes.titlesize': 10, 'axes.labelsize': 10,
    'xtick.labelsize': 9, 'ytick.labelsize': 9, 'legend.fontsize': 8,
    'axes.linewidth': 0.8, 'lines.linewidth': 1.5, 'grid.alpha': 0.25,
    'axes.spines.top': False, 'axes.spines.right': False,
})

C_PASS = '#2ca02c'; C_FAIL = '#d62728'; C_WARN = '#ff7f0e'
C_FC = '#D73027'; C_EL = '#66C2A5'; C_BAT = '#3288BD'

OUT_FIG = PROJECT_ROOT / "07_results" / "figures" / "VS7_uncertainty.png"
OUT_CSV = PROJECT_ROOT / "07_results" / "annual" / "VS7_uncertainty_summary.csv"

N_LHS = 30
N_BOOTSTRAP = 1000

print("=" * 70)
print("VS7 不确定性验证 (V3层) — MVP简化版")
print("=" * 70)

# === 加载配置 ===
from components.data_lite import load_data
from ems.ems_bf_lite import EMSConfig, rule_based_dispatch
from simulation.closed_loop_lite import (CoalitionState, compute_damage,
                                         BAT_K_CAL_H)
from components.hp_bat_lite import _load_280ah_data, fit_hppc, interpolate_hp as interp_bat
# v2 新增: 电池后验 + 日历-循环耦合 + FC四机制分支
from components.bat_posterior import fit_posterior, combined_damage, A_D_POINT
from components import fc_four_mechanism

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
bat_rated_kw = cap['BAT_kWh'] * 0.5
bat_capacity_kwh = cap['BAT_kWh']

hppc_data = _load_280ah_data()
bat_fit = fit_hppc(hppc_data)
hp_bat_nominal = interp_bat(bat_fit, {'bat_kwh': cap['BAT_kWh'], 'bat_voltage_nominal': 3.2*48, 'bat_rated_kw': bat_rated_kw})

config_dict = {
    'capacity': cap, 'battery_constraints': bat_c,
    'ems_parameters': ems_p, 'hydrogen_constraints': h2_c,
    'minimum_stable_load': msl, 'topology': topo,
}

# 加载W1数据
load_csv = str(PROJECT_ROOT / "02_data_processed" / "tradestreet_pipeline" /
               "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv")
data = load_data(load_csv, str(config_path), year=2017, window_days=7)
df = data['data']
pv_kw_orig = df['P_pv_kW'].values
load_kw_orig = df['P_load_kW'].values
n_steps = len(pv_kw_orig)

# --- v2: 日历/循环拆分常数 (VS7窗口=672步 x 0.25h = 168h) ---
DT_H = 15.0 / 60.0                    # 0.25 h/步
CAL_NOM_STEP = BAT_K_CAL_H * DT_H     # 标称日历损伤/步 = 5.708e-6*0.25
CAL_NOM_WIN = CAL_NOM_STEP * n_steps  # 标称日历损伤/窗口 ≈ 9.59e-4
FC_RATED_KW = cap['PEMFC_kW']

print(f"\n[v2] 电池后验 / 日历拆分常数:")
print(f"  BAT_K_CAL_H={BAT_K_CAL_H:.6e}/h  窗口标称日历D={CAL_NOM_WIN:.6e} "
      f"({n_steps}步 x {DT_H}h)")
print(f"  A_D_POINT(legacy点估计)={A_D_POINT:.6e}  FC_RATED={FC_RATED_KW} kW")

# === 辅助函数: 创建缩放后的hp_bat ===
def make_scaled_hp_bat(hp_bat_orig, beta_Q_scale):
    """创建beta_Q缩放的hp_bat函数.
    q_frac_scaled = 1 - beta_Q_scale * (1 - q_frac_orig)
    """
    def hp_bat_scaled(D):
        q_frac, r0, p_max = hp_bat_orig(D)
        q_scaled = 1.0 - beta_Q_scale * (1.0 - q_frac)
        p_max_scaled = bat_rated_kw * q_scaled
        return (q_scaled, r0, p_max_scaled)
    return hp_bat_scaled

def compute_ae_for_params(hp_bat_func, pv_kw, load_kw, k_cyc_mult=1.0,
                           k_cal_mult=1.0, k_int=0.0,
                           k_run_fc_mult=1.0, k_run_el_mult=1.0,
                           eta_degrade=0.10, fc_branch=0, D_test=0.90):
    """计算给定参数下的AE值.

    v2 返回4个指标: (AE_FC, AE_EL, AE_BAT, AE_TOTAL)
      AE_FC/AE_EL : 与v1同口径 (BAT退化D=0.9 对 FC/EL 损伤的放大效应)
      AE_BAT      : BAT自身损伤差 (v2新增; 后验a_d / 日历 / 交互项在此生效)
      AE_TOTAL    : 三设备合计 (v2新增; 后验收窄的主要观察对象)

    BAT损伤拆分 (closed_loop_lite L241: dD_bat = BAT_K_CAL_H*dt_h + dD_cyc_bat):
      d_cal = k_cal_mult * CAL_NOM_WIN
      d_cyc = k_cyc_mult * (sum(dD_bat) - CAL_NOM_WIN)
      d_bat = combined_damage(d_cal, d_cyc, k_int)
    注: 恒定速率日历项在 DEG-BOL 差值中相减抵消, 故 k_cal_mult 仅经交互项
        k_int*sqrt(d_cal*d_cyc) 影响 AE —— k_int=0 时 k_cal_mult 严格惰性。
    """
    coalition_full = CoalitionState.from_code('1111')
    results = {}

    for run_label, D_state in [('BOL', {'bat': 0.0, 'fc': 0.0, 'el': 0.0}),
                                 ('DEG', {'bat': D_test, 'fc': 0.0, 'el': 0.0})]:
        q_frac, r0, p_max = hp_bat_func(D_state['bat'])
        bat_p_max = bat_rated_kw * q_frac if D_state['bat'] > 0 else None
        bat_cap = bat_capacity_kwh * q_frac if D_state['bat'] > 0 else None

        # 保存随机状态, 避免seed干扰外部采样(Bootstrap)
        rng_state = np.random.get_state()
        np.random.seed(42)
        cmds, _ = rule_based_dispatch(load_kw, pv_kw, cfg,
                                       bat_p_max_kw=bat_p_max, bat_capacity_kwh=bat_cap)
        np.random.set_state(rng_state)
        dD = compute_damage(hp_bat_func, cmds, config_dict, dt_min=15.0,
                            D_state=D_state, coalition=coalition_full,
                            eta_degrade=eta_degrade)

        # --- BAT: 日历/循环拆分 + 后验乘子 + 交互项合成 (v2) ---
        d_bat_raw = float(np.sum(dD['dD_bat']))
        d_cal = k_cal_mult * CAL_NOM_WIN
        d_cyc = k_cyc_mult * max(d_bat_raw - CAL_NOM_WIN, 0.0)
        dD_bat = float(combined_damage(d_cal, d_cyc, k_int))

        # --- FC: legacy(closed_loop_lite) 或 四机制分支 (v2) ---
        if fc_branch == 1:
            p_fc = np.asarray(cmds[:, 1], dtype=float)
            dD_fc = float(np.sum(fc_four_mechanism.fc_damage_increments(
                p_fc, FC_RATED_KW, DT_H))) * k_run_fc_mult
        else:
            # 原路径: 运行损伤缩放, eta_degrade影响eta_factor
            dD_fc = float(np.sum(dD['dD_fc'])) * k_run_fc_mult

        # --- EL: 同理 ---
        dD_el = float(np.sum(dD['dD_el'])) * k_run_el_mult

        results[run_label] = {'dD_bat': dD_bat, 'dD_fc': dD_fc, 'dD_el': dD_el}

    ae_fc = results['DEG']['dD_fc'] - results['BOL']['dD_fc']
    ae_el = results['DEG']['dD_el'] - results['BOL']['dD_el']
    ae_bat = results['DEG']['dD_bat'] - results['BOL']['dD_bat']
    ae_total = ae_fc + ae_el + ae_bat
    return ae_fc, ae_el, ae_bat, ae_total

# === 步骤0: 电池循环损伤系数后验 ===
POST = fit_posterior()
print(f"\n[0] 电池循环损伤系数后验 (bat_posterior):")
print(f"    a_d = {POST.a_d_mean:.6e} +- {POST.a_d_sd:.3e}   (R2={POST.r2}, n={POST.n_points})")
print(f"    乘子口径 = a_d / {A_D_POINT:.6e}  ->  均值 {POST.a_d_mean/A_D_POINT:.4f}, "
      f"sd {POST.a_d_sd/A_D_POINT:.4f}")
print(f"    后验95%CI宽度/均值 = {2*1.96*POST.a_d_sd/POST.a_d_mean*100:.2f}%"
      f"   vs  先验 U[0.5,1.5] = 66.70%")

# === 步骤1: LHS采样 ===
print(f"\n[1] LHS采样: {N_LHS}样本 x 8参数...")

# 8参数: (lo,hi)=均匀区间; None=特殊变换(见 build_samples)
param_ranges = {
    'beta_Q_scale':    (0.8, 1.2),
    'k_cyc_mult':      None,      # 后验/先验专用变换
    'k_cal_mult':      (0.4, 1.0),
    'k_int':           (0.0, 0.5),
    'k_run_fc_mult':   (0.8, 1.2),
    'k_run_el_mult':   (0.8, 1.2),
    'eta_degrade':     (0.08, 0.12),
    'fc_model_branch': None,      # 0/1 等权
}
param_names = list(param_ranges.keys())

# --- 分层均匀矩阵 U (保LHS填充性) ---
# 关键: 后验经**逆CDF**接入U, 不破坏分层结构 (直接 rng.normal 会破坏)
np.random.seed(123)
U = np.zeros((N_LHS, len(param_names)))
for j in range(len(param_names)):
    perm = np.random.permutation(N_LHS)
    for i in range(N_LHS):
        U[i, j] = (perm[i] + np.random.uniform()) / N_LHS


def build_samples(u_mat, cyc_mode):
    """u_mat -> 参数样本矩阵。cyc_mode: 'prior'(v1 U[0.5,1.5]) | 'posterior'(v2后验)。"""
    S = np.zeros_like(u_mat)
    for j, pname in enumerate(param_names):
        u = u_mat[:, j]
        if pname == 'k_cyc_mult':
            if cyc_mode == 'prior':
                S[:, j] = 0.5 + 1.0 * u                      # v1先验
            else:
                a_d = POST.a_d_mean + POST.a_d_sd * norm.ppf(  # v2后验逆CDF
                    np.clip(u, 1e-6, 1.0 - 1e-6))
                S[:, j] = a_d / A_D_POINT
        elif pname == 'fc_model_branch':
            S[:, j] = (u >= 0.5).astype(float)               # 0/1 等权
        else:
            lo, hi = param_ranges[pname]
            S[:, j] = lo + u * (hi - lo)
    return S


lhs_samples = build_samples(U, 'posterior')        # 主结果 (v2)
lhs_samples_prior = build_samples(U, 'prior')      # 对照 (v1, 同U=共同随机数)

print(f"  LHS样本形状: {lhs_samples.shape}")
print(f"  参数范围 (后验口径):")
for j, pname in enumerate(param_names):
    print(f"    {pname}: [{lhs_samples[:, j].min():.4f}, {lhs_samples[:, j].max():.4f}], "
          f"mean={lhs_samples[:, j].mean():.4f}")
print(f"  [对照] k_cyc_mult  先验[{lhs_samples_prior[:, 1].min():.4f}, "
      f"{lhs_samples_prior[:, 1].max():.4f}]  vs  后验[{lhs_samples[:, 1].min():.4f}, "
      f"{lhs_samples[:, 1].max():.4f}]")

# === 步骤2: LHS运行 (后验主结果 + 先验对照) ===
AE_KEYS = ['AE_FC', 'AE_EL', 'AE_BAT', 'AE_TOTAL']
IDX = {n: j for j, n in enumerate(param_names)}


def run_ensemble(S, tag):
    """跑一组LHS样本, 返回 {AE_KEY: array(N_LHS)}。"""
    out = {k: np.zeros(N_LHS) for k in AE_KEYS}
    for i in range(N_LHS):
        hp_scaled = make_scaled_hp_bat(hp_bat_nominal, S[i, IDX['beta_Q_scale']])
        ae_fc, ae_el, ae_bat, ae_tot = compute_ae_for_params(
            hp_scaled, pv_kw_orig, load_kw_orig,
            k_cyc_mult=S[i, IDX['k_cyc_mult']],
            k_cal_mult=S[i, IDX['k_cal_mult']],
            k_int=S[i, IDX['k_int']],
            k_run_fc_mult=S[i, IDX['k_run_fc_mult']],
            k_run_el_mult=S[i, IDX['k_run_el_mult']],
            eta_degrade=S[i, IDX['eta_degrade']],
            fc_branch=int(S[i, IDX['fc_model_branch']]),
            D_test=0.90,
        )
        out['AE_FC'][i] = ae_fc
        out['AE_EL'][i] = ae_el
        out['AE_BAT'][i] = ae_bat
        out['AE_TOTAL'][i] = ae_tot
        if (i + 1) % 10 == 0:
            print(f"    [{tag}] {i+1}/{N_LHS}", flush=True)
    return out


print(f"\n[2] LHS运行: {N_LHS}样本 x 2口径(后验/先验) x {len(AE_KEYS)}指标...")
lhs_ae = run_ensemble(lhs_samples, 'posterior')
lhs_ae_prior = run_ensemble(lhs_samples_prior, 'prior ')

# 向后兼容别名 (绘图/PRCC/CI 使用)
lhs_ae_fc, lhs_ae_el = lhs_ae['AE_FC'], lhs_ae['AE_EL']

print(f"\n  后验口径 AE 统计:")
for k in AE_KEYS:
    v = lhs_ae[k]
    print(f"    {k:>9s}: mean={np.mean(v):+.6f}, std={np.std(v):.6f}, "
          f"range=[{v.min():+.6f}, {v.max():+.6f}]")

# --- 四机制分支效应 (fc_model_branch = 0 vs 1) ---
br = lhs_samples[:, IDX['fc_model_branch']]
print(f"\n  四机制分支效应 (legacy=0 vs 四机制=1):")
branch_eff = {}
for k in AE_KEYS:
    v = lhs_ae[k]
    m0, m1 = v[br == 0], v[br == 1]
    ratio = float(m1.mean() / m0.mean()) if abs(m0.mean()) > 1e-15 else float('nan')
    branch_eff[k] = {'legacy_mean': float(m0.mean()), 'four_mean': float(m1.mean()),
                     'n_legacy': int(len(m0)), 'n_four': int(len(m1)), 'ratio': ratio}
    print(f"    {k:>9s}: legacy {m0.mean():+.6f} (n={len(m0)}) -> "
          f"四机制 {m1.mean():+.6f} (n={len(m1)})   放大 {ratio:.3f}x")
print(f"    注: 分支只改 FC 损伤 -> AE_EL/AE_BAT 比值应≈1.0, 偏离属小样本分组抽样噪声。")

# === 步骤2b: R08 后验收窄 (LHS 95%CI 宽度对比, 共同随机数U) ===
print(f"\n[2b] R08 后验收窄 (LHS 95%CI 宽度, 先验 vs 后验):")
ci_tbl = {}
for k in AE_KEYS:
    p, q = lhs_ae_prior[k], lhs_ae[k]
    p_lo, p_hi = np.percentile(p, [2.5, 97.5])
    q_lo, q_hi = np.percentile(q, [2.5, 97.5])
    w_p, w_q = float(p_hi - p_lo), float(q_hi - q_lo)
    narrow = (1.0 - w_q / w_p) * 100.0 if w_p > 1e-15 else float('nan')
    ci_tbl[k] = {'prior_ci': [p_lo, p_hi], 'prior_w': w_p,
                 'post_ci': [q_lo, q_hi], 'post_w': w_q,
                 'narrow_pct': narrow}
    print(f"    {k:>9s}: 先验[{p_lo:+.6f},{p_hi:+.6f}] 宽{w_p:.6f} -> "
          f"后验[{q_lo:+.6f},{q_hi:+.6f}] 宽{w_q:.6f}   收窄 {narrow:+.1f}%")
r08_pass = bool(ci_tbl['AE_TOTAL']['narrow_pct'] > 0)
print(f"  R08: {'PASS' if r08_pass else 'FAIL'} (AE_TOTAL 后验CI相对先验收窄 > 0)")

# === 步骤3: R06 LHS覆盖检查 ===
print(f"\n[3] R06 LHS覆盖检查:")
r06_pass = True
for j, pname in enumerate(param_names):
    sample_min = lhs_samples[:, j].min()
    sample_max = lhs_samples[:, j].max()
    rng_ = param_ranges[pname]
    if rng_ is None:
        # 非均匀参数: 无(lo,hi)区间可比, 报告实际取值范围
        print(f"  {pname}: range=[{sample_min:.4f}, {sample_max:.4f}], "
              f"非均匀(后验/分支) -> 跳过区间覆盖检查")
        continue
    lo, hi = rng_
    # LHS理论边界: 分层中心距边界 1/(2N)。原判据1%对N=30不可达(实际1.67%),
    # 故改用 1/N 作为可达判据; 并真正置位 r06_pass (旧实现恒为True=空判据)。
    tol_frac = max(1.0 / N_LHS, 0.01)
    covered = (sample_min <= lo + tol_frac * (hi - lo)
               and sample_max >= hi - tol_frac * (hi - lo))
    if not covered:
        r06_pass = False
    print(f"  {pname}: range=[{sample_min:.4f}, {sample_max:.4f}], target=[{lo}, {hi}], "
          f"tol={tol_frac*100:.2f}% -> {'PASS' if covered else 'FAIL'}")
print(f"  R06: {'PASS' if r06_pass else 'FAIL'} ({N_LHS} samples, {len(param_names)} params)")

# === 步骤4: R07 PRCC显著性 ===
# 注: 旧实现用的是 Spearman(=SRC), **不是**偏秩相关(PRCC)。LHS小样本下参数间会
#     偶然产生秩相关(实测 k_cal_mult ~ beta_Q_scale rho=-0.46), 使无物理通路的
#     参数出现伪显著。故改为真 PRCC(对其余参数回归去混杂后的残差相关),
#     并同时诊断参数间秩相关。
print(f"\n[4] R07 PRCC(偏秩相关) / SRC(Spearman):")


def _rk(a):
    return np.argsort(np.argsort(np.asarray(a, dtype=float))).astype(float)


def _pval_from_r(r, n, n_confound):
    """PRCC 的 t 检验 p 值 (df = n - 2 - n_confound)。"""
    from scipy.stats import t as student_t
    df = n - 2 - n_confound
    if df <= 0 or abs(r) >= 1.0:
        return float('nan')
    tstat = r * np.sqrt(df / max(1.0 - r * r, 1e-15))
    return float(2.0 * student_t.sf(abs(tstat), df))


def partial_rank_corr(X, y):
    """真 PRCC: 秩变换后, 用其余参数回归去混杂, 取残差相关。"""
    n, p = X.shape
    Rx = np.column_stack([_rk(X[:, j]) for j in range(p)])
    Ry = _rk(y)
    out = np.zeros(p)
    for j in range(p):
        others = [k for k in range(p) if k != j]
        A = np.column_stack([Rx[:, others], np.ones(n)])
        bx, *_ = np.linalg.lstsq(A, Rx[:, j], rcond=None)
        by, *_ = np.linalg.lstsq(A, Ry, rcond=None)
        rx = Rx[:, j] - A @ bx
        ry = Ry - A @ by
        sx, sy = rx.std(), ry.std()
        out[j] = 0.0 if sx < 1e-12 or sy < 1e-12 else float(np.corrcoef(rx, ry)[0, 1])
    return out


# --- 参数间秩相关诊断 (SRC 混杂来源) ---
print(f"  参数间秩相关诊断 (|rho|>0.4 提示混杂风险, N={N_LHS}):")
_maxc = 0.0
for a in range(len(param_names)):
    for b_ in range(a + 1, len(param_names)):
        c = float(np.corrcoef(_rk(lhs_samples[:, a]), _rk(lhs_samples[:, b_]))[0, 1])
        if abs(c) > 0.4:
            print(f"    ! {param_names[a]} ~ {param_names[b_]}: rho={c:+.3f}")
        _maxc = max(_maxc, abs(c))
print(f"    参数间最大 |rho| = {_maxc:.3f}")

prcc_results = {}
r07_pass = False
for target_name, target_vals in [(k, lhs_ae[k]) for k in AE_KEYS]:
    prccs = partial_rank_corr(lhs_samples, target_vals)
    print(f"  {target_name}:")
    for j, pname in enumerate(param_names):
        rho_s, pval_s = spearmanr(lhs_samples[:, j], target_vals)
        rho_p = float(prccs[j])
        pval_p = _pval_from_r(rho_p, N_LHS, len(param_names) - 1)
        prcc_results[(target_name, pname)] = {
            'rho': rho_p, 'pval': pval_p,
            'rho_src': float(rho_s), 'pval_src': float(pval_s)}
        significant = abs(rho_p) > 0.3
        if significant:
            r07_pass = True
        flag = ''
        if abs(rho_s) > 0.3 and abs(rho_p) <= 0.3:
            flag = '   <- SRC显著/PRCC不显著: 疑似混杂伪相关'
        elif abs(rho_p) > 0.3 and abs(rho_s) <= 0.3:
            flag = '   <- PRCC显著/SRC不显著: 疑似被混杂抑制'
        print(f"    {pname:<16s}: PRCC={rho_p:+.3f} (p={pval_p:.4f})   "
              f"SRC={rho_s:+.3f} (p={pval_s:.4f}) -> "
              f"{'SIGNIFICANT' if significant else 'ns'}{flag}")

print(f"  R07: {'PASS' if r07_pass else 'FAIL'} (|rho|>0.3 for >=1 param)")

# === 步骤4b: R09 结构惰性/伪相关检验 ===
# 动机: PRCC 只去除**线性秩**混杂, 而损伤模型是强非线性的; N=30 时残余空间中
#       仍可能出现 |PRCC|≈0.6 的偶然对齐 (实测 k_cal_mult -> AE_EL PRCC=-0.597,
#       而 k_cal_mult 根本不进 compute_damage, 二者无任何物理通路)。
# 办法: 直接**单参数扰动**测数值响应。若响应严格为 0 -> 结构上无通路,
#       则该参数对该 AE 的任何非零 PRCC/SRC 一律判为伪相关并从 R07 中剔除。
print(f"\n[4b] R09 结构惰性/伪相关检验 (单参数扰动; 数值响应=0 即结构无通路):")

# ⚠️ 结构检验是**点条件**的: "惰性"= 在该名义点上无通路。
# 关键陷阱: k_cal_mult 的唯一通路是交互项 k_int*sqrt(d_cal*d_cyc)。若名义点取
#          k_int=0, 等于人为关断该通路 -> 会把真实的**条件效应**误判为伪相关。
#          故名义点必须把调节变量置于激活区 (k_int=0.25 而非 0)。
NOMINAL_VEC = {
    'beta_Q_scale': 1.0, 'k_cyc_mult': 1.0, 'k_cal_mult': 1.0, 'k_int': 0.25,
    'k_run_fc_mult': 1.0, 'k_run_el_mult': 1.0, 'eta_degrade': 0.10,
    'fc_model_branch': 0.0,
}
PERTURB_VEC = {
    'beta_Q_scale': 1.15, 'k_cyc_mult': 1.10, 'k_cal_mult': 0.70, 'k_int': 0.45,
    'k_run_fc_mult': 1.10, 'k_run_el_mult': 1.10, 'eta_degrade': 0.115,
    'fc_model_branch': 1.0,
}
print(f"     名义点 k_int={NOMINAL_VEC['k_int']} (调节变量置于激活区, "
      f"避免关断 k_cal_mult 的交互项通路); 惰性判定为该点上的结论。")


def eval_vec(vec):
    hp = make_scaled_hp_bat(hp_bat_nominal, vec['beta_Q_scale'])
    return np.array(compute_ae_for_params(
        hp, pv_kw_orig, load_kw_orig,
        k_cyc_mult=vec['k_cyc_mult'], k_cal_mult=vec['k_cal_mult'],
        k_int=vec['k_int'], k_run_fc_mult=vec['k_run_fc_mult'],
        k_run_el_mult=vec['k_run_el_mult'], eta_degrade=vec['eta_degrade'],
        fc_branch=int(vec['fc_model_branch']), D_test=0.90))


base_vec = eval_vec(NOMINAL_VEC)
inert = {}
r09_spurious = []
for pname in param_names:
    v2 = dict(NOMINAL_VEC)
    v2[pname] = PERTURB_VEC[pname]
    delta = np.abs(eval_vec(v2) - base_vec)
    inert[pname] = {k: bool(delta[i] < 1e-15) for i, k in enumerate(AE_KEYS)}
    print(f"    {pname:<16s} {NOMINAL_VEC[pname]:.3f}->{PERTURB_VEC[pname]:<5.3f} | "
          + "  ".join(f"{k}={'惰性' if inert[pname][k] else '有效'}" for k in AE_KEYS))
    for i, k in enumerate(AE_KEYS):
        if inert[pname][k] and abs(prcc_results[(k, pname)]['rho']) > 0.3:
            r09_spurious.append((k, pname, float(prcc_results[(k, pname)]['rho'])))

print(f"\n  伪相关清单 (结构上惰性, 却被 PRCC 判为显著):")
if r09_spurious:
    for k, p, rho in r09_spurious:
        print(f"    ! {p} -> {k}: PRCC={rho:+.3f} 但单参扰动响应=0 "
              f"-> **伪相关, 已从 R07 剔除, 不得作归因结论**")
else:
    print(f"    (无)")
# 依据 R09 剔除伪相关后重算 R07
r07_true = [(k, p) for (k, p), v in prcc_results.items()
            if abs(v['rho']) > 0.3 and not inert[p][k]]
r07_pass = len(r07_true) > 0
r09_pass = True   # 诊断完成且伪相关已剔除
print(f"  R07(剔除伪相关后): {len(r07_true)} 项真显著 -> {'PASS' if r07_pass else 'FAIL'}")
print(f"  R09: PASS ({len(r09_spurious)} 项伪相关已检出并剔除)")

# === 步骤5: Bootstrap重采样 ===
print(f"\n[5] Bootstrap重采样: {N_BOOTSTRAP}次...")

boot_ae = {k: np.zeros(N_BOOTSTRAP) for k in AE_KEYS}

np.random.seed(456)
for b in range(N_BOOTSTRAP):
    # 有放回重采样672步
    indices = np.random.choice(n_steps, size=n_steps, replace=True)
    pv_boot = pv_kw_orig[indices]
    load_boot = load_kw_orig[indices]

    # 参数保持**标称**(点估计): Bootstrap 隔离"数据重采样(aleatory)"不确定性,
    # 参数(认知)不确定性由 LHS/R08 承担 —— 两类不确定性不混叠。
    ae_fc, ae_el, ae_bat, ae_tot = compute_ae_for_params(
        hp_bat_nominal, pv_boot, load_boot, D_test=0.90
    )
    boot_ae['AE_FC'][b] = ae_fc
    boot_ae['AE_EL'][b] = ae_el
    boot_ae['AE_BAT'][b] = ae_bat
    boot_ae['AE_TOTAL'][b] = ae_tot
    if (b + 1) % 250 == 0:
        print(f"    [bootstrap] {b+1}/{N_BOOTSTRAP}", flush=True)

# 向后兼容别名 (绘图使用)
boot_ae_fc, boot_ae_el = boot_ae['AE_FC'], boot_ae['AE_EL']

# === 步骤6: R05 Bootstrap同号率 ===
print(f"\n[6] R05 Bootstrap同号率:")
r05_pass = True
for target_name, target_vals in [(k, boot_ae[k]) for k in AE_KEYS]:
    n_positive = np.sum(target_vals > 0)
    n_negative = np.sum(target_vals < 0)
    n_zero = np.sum(target_vals == 0)
    majority_pct = max(n_positive, n_negative) / N_BOOTSTRAP * 100
    pass_r05 = majority_pct >= 95
    if not pass_r05:
        r05_pass = False
    ci_low = np.percentile(target_vals, 2.5)
    ci_high = np.percentile(target_vals, 97.5)
    print(f"  {target_name}: +{n_positive}/-{n_negative}/0={n_zero}, "
          f"majority={majority_pct:.1f}%, CI=[{ci_low:.6f}, {ci_high:.6f}] -> {'PASS' if pass_r05 else 'FAIL'}")

print(f"  R05: {'PASS' if r05_pass else 'FAIL'} (>=95% same sign)")

# === VS7 结果汇总 ===
print("\n" + "=" * 70)
print("VS7 验证结果汇总")
print("=" * 70)

results = {
    'R05': {'metric': 'Bootstrap同号率', 'value': f'>={min(np.sum(boot_ae_fc>0), np.sum(boot_ae_el>0))/N_BOOTSTRAP*100:.0f}%', 'threshold': '>=95%', 'pass': r05_pass},
    'R06': {'metric': 'LHS覆盖', 'value': f'{N_LHS}x{len(param_names)}', 'threshold': 'covered', 'pass': r06_pass},
    'R07': {'metric': 'PRCC显著性(剔伪)', 'value': f'{len(r07_true)} significant', 'threshold': '|rho|>0.3', 'pass': r07_pass},
    'R08': {'metric': '后验收窄(AE_TOTAL CI)', 'value': f'{ci_tbl["AE_TOTAL"]["narrow_pct"]:+.1f}%', 'threshold': '>0%', 'pass': r08_pass},
    'R09': {'metric': '伪相关检出', 'value': f'{len(r09_spurious)} 项已剔除', 'threshold': '检出并剔除', 'pass': r09_pass},
}

for check_id, r in results.items():
    print(f"  {check_id}: {r['metric']} = {r['value']} (阈值{r['threshold']}) -> {'PASS' if r['pass'] else 'FAIL'}")

all_pass = all(r['pass'] for r in results.values())
print(f"\n  VS7 总体: {'PASS' if all_pass else 'FAIL'}")

# === 生成图表 ===
print("\n[绘图] 生成VS7不确定性图表...")

fig = plt.figure(figsize=(15, 10))
gs = GridSpec(3, 2, hspace=0.45, wspace=0.35, left=0.08, right=0.78, top=0.92, bottom=0.06)

# (a) LHS AE分布直方图
ax1 = fig.add_subplot(gs[0, 0])
ax1.hist(lhs_ae_fc, bins=15, alpha=0.6, color=C_FC, edgecolor='k', linewidth=0.4, label='AE(FC)')
ax1.hist(lhs_ae_el, bins=15, alpha=0.6, color=C_EL, edgecolor='k', linewidth=0.4, label='AE(EL)')
ax1.axvline(0, color='k', lw=0.8)
ax1.set_xlabel('AE value')
ax1.set_ylabel('Frequency')
ax1.set_title(f'(a) LHS AE Distribution ({N_LHS} samples)', fontsize=10, fontweight='bold')
ax1.legend(fontsize=8)
ax1.grid(True, alpha=0.3)

# (b) Bootstrap AE分布
ax2 = fig.add_subplot(gs[0, 1])
ax2.hist(boot_ae_fc, bins=15, alpha=0.6, color=C_FC, edgecolor='k', linewidth=0.4, label='AE(FC)')
ax2.hist(boot_ae_el, bins=15, alpha=0.6, color=C_EL, edgecolor='k', linewidth=0.4, label='AE(EL)')
ax2.axvline(0, color='k', lw=0.8)
# 95% CI
for target, color, label in [(boot_ae_fc, C_FC, 'FC'), (boot_ae_el, C_EL, 'EL')]:
    ci_low = np.percentile(target, 2.5)
    ci_high = np.percentile(target, 97.5)
    ax2.axvline(ci_low, color=color, ls='--', lw=1, alpha=0.7)
    ax2.axvline(ci_high, color=color, ls='--', lw=1, alpha=0.7)
ax2.set_xlabel('AE value')
ax2.set_ylabel('Frequency')
ax2.set_title(f'(b) Bootstrap AE Distribution ({N_BOOTSTRAP} resamples) | R05: {"PASS" if r05_pass else "FAIL"}', fontsize=10, fontweight='bold')
ax2.legend(fontsize=8)
ax2.grid(True, alpha=0.3)

# (c) PRCC柱状图
ax3 = fig.add_subplot(gs[1, 0])
x_pos = np.arange(len(param_names))
width = 0.35
fc_rhos = [prcc_results[('AE_FC', p)]['rho'] for p in param_names]
el_rhos = [prcc_results[('AE_EL', p)]['rho'] for p in param_names]
bars1 = ax3.bar(x_pos - width/2, fc_rhos, width, color=C_FC, edgecolor='k', linewidth=0.4, label='AE(FC)')
bars2 = ax3.bar(x_pos + width/2, el_rhos, width, color=C_EL, edgecolor='k', linewidth=0.4, label='AE(EL)')
ax3.axhline(0.3, color=C_FAIL, ls='--', lw=1, label='|0.3| threshold')
ax3.axhline(-0.3, color=C_FAIL, ls='--', lw=1)
ax3.axhline(0, color='k', lw=0.8)
ax3.set_xticks(x_pos)
ax3.set_xticklabels(param_names, fontsize=8, rotation=20)
ax3.set_ylabel('PRCC (partial rank corr)')
ax3.set_title(f'(c) PRCC Sensitivity | R07: {"PASS" if r07_pass else "FAIL"}', fontsize=10, fontweight='bold')
ax3.legend(fontsize=7)
ax3.grid(True, alpha=0.3, axis='y')

# (d) LHS参数散点图 (AE_FC vs beta_Q_scale)
ax4 = fig.add_subplot(gs[1, 1])
for j, pname in enumerate(param_names):
    ax4.scatter(lhs_samples[:, j], lhs_ae_fc, alpha=0.5, s=20, label=pname)
ax4.set_xlabel('Parameter value (normalized)')
ax4.set_ylabel('AE(FC)')
ax4.set_title('(d) LHS Scatter: AE(FC) vs Parameters', fontsize=10, fontweight='bold')
ax4.legend(fontsize=7, ncol=2)
ax4.grid(True, alpha=0.3)

# (e) Bootstrap CI对比
ax5 = fig.add_subplot(gs[2, 0])
# 按中位数排序
sort_idx = np.argsort(boot_ae_fc)
ax5.plot(range(N_BOOTSTRAP), boot_ae_fc[sort_idx], color=C_FC, alpha=0.7, lw=0.8, label='AE(FC) sorted')
sort_idx2 = np.argsort(boot_ae_el)
ax5.plot(range(N_BOOTSTRAP), boot_ae_el[sort_idx2], color=C_EL, alpha=0.7, lw=0.8, label='AE(EL) sorted')
ax5.axhline(0, color='k', lw=0.8)
# 95% CI bands
fc_ci = np.percentile(boot_ae_fc, [2.5, 97.5])
el_ci = np.percentile(boot_ae_el, [2.5, 97.5])
ax5.axhspan(fc_ci[0], fc_ci[1], alpha=0.1, color=C_FC, label=f'FC 95% CI [{fc_ci[0]:.5f}, {fc_ci[1]:.5f}]')
ax5.axhspan(el_ci[0], el_ci[1], alpha=0.1, color=C_EL, label=f'EL 95% CI [{el_ci[0]:.5f}, {el_ci[1]:.5f}]')
ax5.set_xlabel('Bootstrap sample (sorted)')
ax5.set_ylabel('AE value')
ax5.set_title('(e) Bootstrap Sorted AE with 95% CI', fontsize=10, fontweight='bold')
ax5.legend(fontsize=7)
ax5.grid(True, alpha=0.3)

# (f) 检查汇总表
ax6 = fig.add_subplot(gs[2, 1])
ax6.axis('off')
table_data = []
for cid, r in results.items():
    table_data.append([cid, r['metric'][:20], r['value'][:18], r['threshold'][:12], 'P' if r['pass'] else 'F'])

col_labels = ['Check', 'Metric', 'Value', 'Threshold', 'Pass']
table = ax6.table(cellText=table_data, colLabels=col_labels, loc='center',
                  cellLoc='center', colColours=['#4477AA']*5,
                  colWidths=[0.12, 0.44, 0.17, 0.17, 0.10])
table.auto_set_font_size(False)
table.set_fontsize(7.0)
auto_fit_table(ax6, table)
for i, row in enumerate(table_data):
    cell = table[i + 1, 4]
    cell.set_facecolor(C_PASS + '40' if row[4] == 'P' else C_FAIL + '40')
ax6.set_title('(f) Check Summary', fontsize=10, fontweight='bold', pad=10)

fig.suptitle('VS7 Uncertainty Verification (V3 Layer)\n'
             f'LHS({N_LHS}) + Bootstrap({N_BOOTSTRAP}) + PRCC | '
             f'Overall: {"PASS" if all_pass else "FAIL"}',
             fontsize=12, fontweight='bold', y=0.98)

result_text = f"VS7 Overall: {'PASS' if all_pass else 'FAIL'} | "
for cid, r in results.items():
    result_text += f"{cid}: {'P' if r['pass'] else 'F'} | "
fig.text(0.08, 0.01, result_text, fontsize=8, fontweight='bold',
         color=C_PASS if all_pass else C_FAIL)

fig.savefig(OUT_FIG, dpi=300, bbox_inches='tight')
plt.close(fig)
print(f"  Figure saved: {OUT_FIG} ({OUT_FIG.stat().st_size // 1024} KB)")

# === Export CSV ===
with open(OUT_CSV, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    # LHS results (后验口径)
    w.writerow(['Sample'] + param_names + AE_KEYS)
    for i in range(N_LHS):
        w.writerow([i] + [f"{lhs_samples[i, j]:.6f}" for j in range(len(param_names))]
                   + [f"{lhs_ae[k][i]:.10f}" for k in AE_KEYS])
    w.writerow([])

    # LHS 先验对照 (同U共同随机数)
    w.writerow(['Sample_prior'] + param_names + AE_KEYS)
    for i in range(N_LHS):
        w.writerow([i] + [f"{lhs_samples_prior[i, j]:.6f}" for j in range(len(param_names))]
                   + [f"{lhs_ae_prior[k][i]:.10f}" for k in AE_KEYS])
    w.writerow([])

    # R08 先验 vs 后验 CI
    w.writerow(['R08_Target', 'prior_CI_lo', 'prior_CI_hi', 'prior_width',
                'post_CI_lo', 'post_CI_hi', 'post_width', 'narrow_pct'])
    for k in AE_KEYS:
        t = ci_tbl[k]
        w.writerow([k, f"{t['prior_ci'][0]:.10f}", f"{t['prior_ci'][1]:.10f}",
                    f"{t['prior_w']:.10f}", f"{t['post_ci'][0]:.10f}",
                    f"{t['post_ci'][1]:.10f}", f"{t['post_w']:.10f}",
                    f"{t['narrow_pct']:.2f}"])
    w.writerow([])

    # Bootstrap results
    w.writerow(['Bootstrap'] + AE_KEYS)
    for b in range(N_BOOTSTRAP):
        w.writerow([b] + [f"{boot_ae[k][b]:.10f}" for k in AE_KEYS])
    w.writerow([])

    # PRCC results
    w.writerow(['Target', 'Parameter', 'PRCC_rho', 'PRCC_p', 'SRC_rho', 'SRC_p', 'Significant'])
    for (target, param), vals in prcc_results.items():
        sig = abs(vals['rho']) > 0.3
        w.writerow([target, param, f"{vals['rho']:.6f}", f"{vals['pval']:.6f}",
                    f"{vals.get('rho_src', float('nan')):.6f}",
                    f"{vals.get('pval_src', float('nan')):.6f}", sig])
    w.writerow([])

    # 四机制分支效应
    w.writerow(['Branch_effect', 'legacy_mean', 'four_mech_mean', 'n_legacy', 'n_four', 'ratio'])
    for k in AE_KEYS:
        t = branch_eff[k]
        w.writerow([k, f"{t['legacy_mean']:.10f}", f"{t['four_mean']:.10f}",
                    t['n_legacy'], t['n_four'], f"{t['ratio']:.4f}"])
    w.writerow([])

    # Check summary
    w.writerow(['Check', 'Metric', 'Value', 'Threshold', 'Pass'])
    for cid, r in results.items():
        w.writerow([cid, r['metric'], r['value'], r['threshold'], r['pass']])

print(f"  CSV saved: {OUT_CSV}")

print("\n" + "=" * 70)
print("Methodology (v2 后验版):")
print(f"  LHS: Latin Hypercube Sampling of {len(param_names)} uncertain parameters")
print("       k_cyc_mult 经**逆CDF**接入LHS分层矩阵U (保空间填充性, 非直接正态抽样)")
print("  Bootstrap: Resample 672-step window with replacement (参数保持标称)")
print("  PRCC: **偏秩相关** (partial rank correlation) —— 对其余参数回归去混杂后取残差相关")
print("        (v1 用的是 Spearman/SRC, 小样本LHS下会漏进伪相关; 两者已在[4]并列给出)")
print("  R05: AE sign consistency in bootstrap (>=95%)  [aleatory]")
print("  R06: LHS parameter space coverage (非均匀参数跳过区间检查)")
print("  R07: |PRCC| > 0.3, 且经 R09 剔除结构惰性参数的伪相关后仍显著")
print("  R08: AE_TOTAL 后验口径 LHS 95%CI 相对先验收窄 > 0  [epistemic]")
print("  R09: 结构惰性/伪相关硬判据 (单参扰动数值响应=0 即无通路, 自动从 R07 剔除)")
print("")
print("  [v2 结构性说明 —— 参数有效性边界]")
print("   * AE_FC / AE_EL 只含 FC/EL 损伤差 -> k_cyc_mult 对其**无影响**")
print("     (v1的 k_cyc_mult 实为惰性参数; 后验仅经 AE_BAT/AE_TOTAL 生效)")
print("   * k_cal_mult 缩放恒定速率日历项, 在 DEG-BOL 差值中相减抵消;")
print("     仅经交互项 k_int*sqrt(d_cal*d_cyc) 生效 (k_int=0 时严格惰性)")
print("   * fc_model_branch=1 (四机制) 直接改变 dD_fc -> 对 AE_FC 有效")
print("   * eta_degrade: DEG 运行 FC/EL 的 D 恒为 0 -> 对四项 AE 全部惰性")
print("     (R09 实测判定; 属 VS7 实验设计特征, 非模型缺陷)")
print("=" * 70)
print(f"\nVS7 Uncertainty Verification: {'PASS' if all_pass else 'FAIL'}")
print(f"{'='*70}")
