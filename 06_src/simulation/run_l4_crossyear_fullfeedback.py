# -*- coding: utf-8 -*-
"""
L4 EMS 跨年验证 + 全反馈验证 (审计M2/M3补充)

对应审计问题:
  M2: L4未做跨年验证 — 运行2016/2018确认el_k=10.0最优参数不因年份失效
  M3: L4未测试全反馈 — 传递fc_eff_frac/el_eff_frac至dispatch_l4

输出:
  07_results/l4_comparison/l4_crossyear_fullfeedback.csv
  07_results/figures/l4_crossyear_fullfeedback.png
"""
import sys, json, csv, time
import numpy as np
import pandas as pd
from pathlib import Path
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec

PROJECT_ROOT = Path(r"D:\matlab\C")
sys.path.insert(0, str(PROJECT_ROOT / "06_src"))
from utils.fit_table import auto_fit_table
plt.rcParams.update({'font.family': 'sans-serif', 'font.sans-serif': ['Microsoft YaHei', 'SimHei', 'Arial', 'DejaVu Sans'],
    'axes.unicode_minus': False,
    'font.size': 11, 'axes.titlesize': 10, 'axes.labelsize': 10, 'xtick.labelsize': 9,
    'ytick.labelsize': 9, 'legend.fontsize': 8, 'axes.linewidth': 0.8, 'lines.linewidth': 1.5,
    'grid.alpha': 0.25, 'axes.spines.top': False, 'axes.spines.right': False})

C_M2 = '#3288BD'; C_M3 = '#D73027'; C_BOL = '#999999'
C_2016 = '#66C2A5'; C_2017 = '#FC8D62'; C_2018 = '#8DA0CB'
OUT_DIR = PROJECT_ROOT / "07_results" / "l4_comparison"
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_CSV = OUT_DIR / "l4_crossyear_fullfeedback.csv"
OUT_FIG = PROJECT_ROOT / "07_results" / "figures" / "l4_crossyear_fullfeedback.png"
SPW = 672; WPY = 52

from ems.ems_bf_lite import EMSConfig, rule_based_dispatch
from ems.ems_l4_lite import dispatch_l4, L4Config
from simulation.closed_loop_lite import CoalitionState, compute_damage
from components.hp_bat_lite import _load_280ah_data, fit_hppc, interpolate_hp as interp_bat
from components.hp_fc_lite import _load_fc1_data, fit_fc_aging, interpolate_hp as interp_fc
from components.hp_el_lite import _load_el_bol_data, fit_el_aging, interpolate_hp as interp_el

# 加载配置
cfg_path = PROJECT_ROOT / "05_config" / "mvp_c1_config.json"
with open(cfg_path, 'r', encoding='utf-8') as f:
    c1 = json.load(f)
cap = c1['capacity']; ep = c1['ems_parameters']; bc = c1['battery_constraints']
hc = c1['hydrogen_constraints']; msl = c1['minimum_stable_load']; topo = c1['topology']
ems_cfg = EMSConfig(pv_kwp=cap['PV_kWp'], bat_kwh=cap['BAT_kWh'], pemfc_kw=cap['PEMFC_kW'],
    pemel_kw=cap['PEMEL_kW'], h2_kg=cap['H2_kg'], soc_min=bc['soc_min'], soc_max=bc['soc_max'],
    soc_initial=bc['soc_initial'], h2_min_fraction=hc['m_min_fraction'], h2_max_fraction=hc['m_max_fraction'],
    h2_initial_fraction=hc['m_initial_fraction'], h2_leakage_per_day=hc['leakage_rate_per_day'],
    start_stop_threshold=ep['start_stop_threshold_fraction'],
    minimum_run_time_min=float(ep['minimum_run_time']),
    critical_load_fraction=ep['critical_load_fraction'],
    p_aux_fraction=topo['P_aux_fraction_of_installed_power'],
    min_stable_load_fc=msl['PEMFC'], min_stable_load_el=msl['PEMEL'], dt_min=15.0)
bat_rated_kw = cap['BAT_kWh'] * 0.5; bat_cap_kwh = cap['BAT_kWh']
config_dict = {'capacity': cap, 'battery_constraints': bc, 'ems_parameters': ep,
    'hydrogen_constraints': hc, 'minimum_stable_load': msl, 'topology': topo}

print("=" * 70)
print("L4 EMS 跨年验证 + 全反馈验证 (审计M2/M3补充)")
print("=" * 70)

print("\n[0] 构建健康-性能映射...")
hp_bat = interp_bat(fit_hppc(_load_280ah_data()),
    {'bat_kwh': cap['BAT_kWh'], 'bat_voltage_nominal': 3.2*48, 'bat_rated_kw': bat_rated_kw})
hp_fc = interp_fc(fit_fc_aging(_load_fc1_data()),
    {'stack_config': {}, 'fc_system_kw': cap['PEMFC_kW']})
hp_el = interp_el(fit_el_aging(_load_el_bol_data()),
    {'el_system_kw': cap['PEMEL_kW'], 'active_area_cm2': 50.0})
print("  hp_bat/hp_fc/hp_el: OK")

# 最优L4参数 (from run_l4_final.py)
cfg_optimal = L4Config(soc_band_narrow_k=0.0, fc_min_run_extend_k=1.0,
                       el_ramp_limit_k=10.0, d_bat_threshold=0.0)
coal = CoalitionState.from_code('1111')

csv_path = str(PROJECT_ROOT / "02_data_processed" / "tradestreet_pipeline" /
    "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv")
raw = pd.read_csv(csv_path, parse_dates=['timestamp_local_civil'])


def load_year(year):
    """加载指定年份的PV/负荷数据"""
    df = raw[raw['local_civil_year'] == year].copy()
    df = df.rename(columns={
        'p_pv_available_reconstructed_nonnegative_kW': 'P_pv_kW',
        'p_building_load_reconstructed_kW': 'P_load_kW'})
    df['P_pv_kW'] = df['P_pv_kW'].ffill().bfill()
    df['P_load_kW'] = df['P_load_kW'].ffill().bfill()
    pv = df['P_pv_kW'].values; load = df['P_load_kW'].values
    n = min(WPY, len(pv) // SPW)
    assert len(pv) >= 35040, f"{year}年数据不完整: {len(pv)}步 < 35040"
    return pv, load, n


def run_sim(pv_full, load_full, n_weeks, mode, use_l4=True):
    """运行年度仿真。
    mode: 'bol', 'bat_only', 'full'
    use_l4: True=L4 EMS, False=M2固定EMS
    """
    D = {'bat': 0.0, 'fc': 0.0, 'el': 0.0}
    b = f = e = 0.0; lp = 0.0
    for w in range(n_weeks):
        s = w * SPW; e_ = s + SPW
        pv_w = pv_full[s:e_]; load_w = load_full[s:e_]
        kw = {}
        if mode != 'bol':
            D_bat = D['bat']
            if D_bat > 0:
                q, r0, p_max = hp_bat(D_bat)
                kw['bat_p_max_kw'] = bat_rated_kw * q
                kw['bat_capacity_kwh'] = bat_cap_kwh * q
            if mode == 'full':
                D_fc = D['fc']
                if D_fc > 0:
                    eta_fc, _, pmax_fc = hp_fc(D_fc)
                    kw['fc_eff_frac'] = eta_fc; kw['fc_p_max_kw'] = pmax_fc
                D_el = D['el']
                if D_el > 0:
                    eta_el, _, pmax_el = hp_el(D_el)
                    kw['el_eff_frac'] = eta_el; kw['el_p_max_kw'] = pmax_el
        np.random.seed(42)
        if use_l4:
            cmds, state = dispatch_l4(load_w, pv_w, ems_cfg,
                D_bat=D['bat'] if mode != 'bol' else 0.0,
                l4_config=cfg_optimal, **kw)
        else:
            cmds, state = rule_based_dispatch(load_w, pv_w, ems_cfg, **kw)
        dD_state = D if mode != 'bol' else {'bat': 0.0, 'fc': 0.0, 'el': 0.0}
        dD = compute_damage(hp_bat, cmds, config_dict, dt_min=15.0,
                            D_state=dD_state, coalition=coal)
        dD_b = float(np.sum(dD['dD_bat']))
        dD_f = float(np.sum(dD['dD_fc']))
        dD_e = float(np.sum(dD['dD_el']))
        if mode != 'bol':
            D['bat'] = min(D['bat'] + dD_b, 1.0)
            D['fc'] = min(D['fc'] + dD_f, 1.0)
            D['el'] = min(D['el'] + dD_e, 1.0)
        b += dD_b; f += dD_f; e += dD_e
        lp += float(np.sum(state['p_uns']) / max(np.sum(load_w), 1e-6))
    total = b + f + e
    return {'total': total, 'bat': b, 'fc': f, 'el': e,
            'lpsp': lp / n_weeks, 'D_bat_eoy': D['bat'],
            'D_fc_eoy': D['fc'], 'D_el_eoy': D['el']}


# ================================================================
# Part 1: L4 跨年验证 (M2)
# ================================================================
print("\n" + "=" * 70)
print("[Part 1] L4 跨年验证 (审计M2)")
print("=" * 70)
print("目标: 确认 el_k=10.0 最优参数在2016/2018年仍然有效")
print("条件: BAT-only反馈, L4 vs M2 对比\n")

crossyear_results = []
for year in [2016, 2017, 2018]:
    t0 = time.time()
    pv, load, nw = load_year(year)
    print(f"[{year}] {len(pv)}步, {nw}周")

    print(f"  [{year}] BOL基线...")
    r_bol = run_sim(pv, load, nw, 'bol', use_l4=False)

    print(f"  [{year}] M2 BAT-only反馈...")
    r_m2 = run_sim(pv, load, nw, 'bat_only', use_l4=False)
    te_m2 = (r_m2['total'] - r_bol['total']) / abs(r_bol['total']) if abs(r_bol['total']) > 1e-10 else 0

    print(f"  [{year}] M3(L4) BAT-only反馈...")
    r_m3 = run_sim(pv, load, nw, 'bat_only', use_l4=True)
    te_m3 = (r_m3['total'] - r_bol['total']) / abs(r_bol['total']) if abs(r_bol['total']) > 1e-10 else 0

    delta_te = te_m3 - te_m2
    print(f"  [{year}] M2 TE={te_m2*100:+.2f}%, M3 TE={te_m3*100:+.2f}%, ΔTE={delta_te*100:+.2f}%")
    print(f"  [{year}] M3-M2: FC={r_m3['fc']-r_m2['fc']:+.6f}, EL={r_m3['el']-r_m2['el']:+.6f}, LPSP={r_m3['lpsp']-r_m2['lpsp']:+.4f}")
    print(f"  耗时: {time.time()-t0:.1f}s\n")

    crossyear_results.append({
        'year': year, 'r_bol': r_bol, 'r_m2': r_m2, 'r_m3': r_m3,
        'te_m2': te_m2, 'te_m3': te_m3, 'delta_te': delta_te
    })

# 跨年TE方向一致性
te_m2_signs = [1 if r['te_m2'] > 0 else -1 for r in crossyear_results]
te_m3_signs = [1 if r['te_m3'] > 0 else -1 for r in crossyear_results]
te_m2_all_pos = all(s > 0 for s in te_m2_signs)
te_m3_all_pos = all(s > 0 for s in te_m3_signs)
# 跨年M3<M2一致性 (L4减轻退化)
m3_lt_m2_all = all(r['te_m3'] < r['te_m2'] for r in crossyear_results)
# TE变异系数
tes_m2 = [r['te_m2'] for r in crossyear_results]
tes_m3 = [r['te_m3'] for r in crossyear_results]
cv_m2 = np.std(tes_m2) / abs(np.mean(tes_m2)) if abs(np.mean(tes_m2)) > 1e-10 else 0
cv_m3 = np.std(tes_m3) / abs(np.mean(tes_m3)) if abs(np.mean(tes_m3)) > 1e-10 else 0

print(f"\n跨年TE统计:")
print(f"  M2 TE: {[f'{t*100:+.2f}%' for t in tes_m2]}, CV={cv_m2*100:.2f}%")
print(f"  M3 TE: {[f'{t*100:+.2f}%' for t in tes_m3]}, CV={cv_m3*100:.2f}%")
print(f"  M2全TE>0: {te_m2_all_pos}, M3全TE>0: {te_m3_all_pos}")
print(f"  M3<M2 全年一致: {m3_lt_m2_all}")

# ================================================================
# Part 2: L4 全反馈验证 (M3)
# ================================================================
print("\n" + "=" * 70)
print("[Part 2] L4 全反馈验证 (审计M3)")
print("=" * 70)
print("目标: 确认L4在全设备反馈下TE方向")
print("条件: 2017年, BAT+FC+EL退化全反馈\n")

pv17, load17, nw17 = load_year(2017)
print(f"[2017] {len(pv17)}步, {nw17}周")

print("  [Full] BOL基线...")
r_bol_ff = run_sim(pv17, load17, nw17, 'bol', use_l4=False)

print("  [Full] M2 BAT-only反馈...")
r_m2_bo = run_sim(pv17, load17, nw17, 'bat_only', use_l4=False)
te_m2_bo = (r_m2_bo['total'] - r_bol_ff['total']) / abs(r_bol_ff['total']) if abs(r_bol_ff['total']) > 1e-10 else 0

print("  [Full] M2 全设备反馈...")
r_m2_ff = run_sim(pv17, load17, nw17, 'full', use_l4=False)
te_m2_ff = (r_m2_ff['total'] - r_bol_ff['total']) / abs(r_bol_ff['total']) if abs(r_bol_ff['total']) > 1e-10 else 0

print("  [Full] M3(L4) BAT-only反馈...")
r_m3_bo = run_sim(pv17, load17, nw17, 'bat_only', use_l4=True)
te_m3_bo = (r_m3_bo['total'] - r_bol_ff['total']) / abs(r_bol_ff['total']) if abs(r_bol_ff['total']) > 1e-10 else 0

print("  [Full] M3(L4) 全设备反馈...")
r_m3_ff = run_sim(pv17, load17, nw17, 'full', use_l4=True)
te_m3_ff = (r_m3_ff['total'] - r_bol_ff['total']) / abs(r_bol_ff['total']) if abs(r_bol_ff['total']) > 1e-10 else 0

print(f"\n全反馈对比:")
print(f"  {'Mode':<20} {'M2 TE(%)':<15} {'M3 TE(%)':<15} {'ΔTE(M3-M2)':<15}")
print(f"  {'-'*20} {'-'*15} {'-'*15} {'-'*15}")
print(f"  {'BAT-only':<20} {te_m2_bo*100:<+15.2f} {te_m3_bo*100:<+15.2f} {(te_m3_bo-te_m2_bo)*100:<+15.2f}")
print(f"  {'Full-feedback':<20} {te_m2_ff*100:<+15.2f} {te_m3_ff*100:<+15.2f} {(te_m3_ff-te_m2_ff)*100:<+15.2f}")
print(f"  {'Δ(Full-BO)':<20} {(te_m2_ff-te_m2_bo)*100:<+15.2f} {(te_m3_ff-te_m3_bo)*100:<+15.2f}")

print(f"\n全反馈损伤对比:")
print(f"  {'Mode':<20} {'BAT':<12} {'FC':<12} {'EL':<12} {'LPSP(%)':<10}")
print(f"  {'-'*20} {'-'*12} {'-'*12} {'-'*12} {'-'*10}")
print(f"  {'M2 BO':<20} {r_m2_bo['bat']:<12.6f} {r_m2_bo['fc']:<12.6f} {r_m2_bo['el']:<12.6f} {r_m2_bo['lpsp']*100:<10.3f}")
print(f"  {'M2 Full':<20} {r_m2_ff['bat']:<12.6f} {r_m2_ff['fc']:<12.6f} {r_m2_ff['el']:<12.6f} {r_m2_ff['lpsp']*100:<10.3f}")
print(f"  {'M3 BO':<20} {r_m3_bo['bat']:<12.6f} {r_m3_bo['fc']:<12.6f} {r_m3_bo['el']:<12.6f} {r_m3_bo['lpsp']*100:<10.3f}")
print(f"  {'M3 Full':<20} {r_m3_ff['bat']:<12.6f} {r_m3_ff['fc']:<12.6f} {r_m3_ff['el']:<12.6f} {r_m3_ff['lpsp']*100:<10.3f}")

# ================================================================
# Part 3: 验证检查
# ================================================================
print("\n" + "=" * 70)
print("[Part 3] 验证检查")
print("=" * 70)

checks = []

# 跨年检查 (M2)
checks.append(('L4X-01', 'M3 跨年全TE>0 (方向稳健)',
    te_m3_all_pos,
    f"M3 TE={[f'{t*100:+.2f}%' for t in tes_m3]}"))
checks.append(('L4X-02', 'M3 跨年TE方向与M2一致',
    all((te_m3 > 0) == (te_m2 > 0) for te_m3, te_m2 in zip(tes_m3, tes_m2)),
    'M2/M3 全TE>0'))
checks.append(('L4X-03', 'M3<M2 全年一致 (L4减轻退化)',
    m3_lt_m2_all,
    'ΔTE=' + str([f'{r["delta_te"]*100:+.2f}%' for r in crossyear_results])))
checks.append(('L4X-04', 'M3 跨年TE CV<50% (稳定性)',
    cv_m3 < 0.50,
    f"CV={cv_m3*100:.2f}%"))
checks.append(('L4X-05', 'M3 跨年LPSP不显著恶化(差<5%)',
    all(abs(r['r_m3']['lpsp'] - r['r_m2']['lpsp']) < 0.05 for r in crossyear_results),
    'ΔLPSP=' + str([f'{(r["r_m3"]["lpsp"]-r["r_m2"]["lpsp"])*100:+.3f}%' for r in crossyear_results])))

# 全反馈检查 (M3)
checks.append(('L4F-01', 'M3 全反馈TE>0 (方向稳健)',
    te_m3_ff > 0,
    f"M3 Full TE={te_m3_ff*100:+.2f}%"))
checks.append(('L4F-02', 'M3 Full TE < M3 BO TE (自限效应)',
    te_m3_ff < te_m3_bo,
    f"Full={te_m3_ff*100:+.2f}% < BO={te_m3_bo*100:+.2f}%"))
checks.append(('L4F-03', 'M3 Full LPSP >= M3 BO LPSP',
    r_m3_ff['lpsp'] >= r_m3_bo['lpsp'] - 0.001,
    f"Full={r_m3_ff['lpsp']*100:.3f}% >= BO={r_m3_bo['lpsp']*100:.3f}%"))
checks.append(('L4F-04', 'M3 Full TE < M2 Full TE (L4在全反馈下仍减轻退化)',
    te_m3_ff < te_m2_ff,
    f"M3 Full={te_m3_ff*100:+.2f}% < M2 Full={te_m2_ff*100:+.2f}%"))
checks.append(('L4F-05', 'M3 Full FC损伤 < M2 Full FC损伤',
    r_m3_ff['fc'] <= r_m2_ff['fc'],
    f"M3={r_m3_ff['fc']:.6f} vs M2={r_m2_ff['fc']:.6f}"))
checks.append(('L4F-06', 'M3 Full EL损伤 < M2 Full EL损伤',
    r_m3_ff['el'] <= r_m2_ff['el'],
    f"M3={r_m3_ff['el']:.6f} vs M2={r_m2_ff['el']:.6f}"))

np_chk = sum(1 for _, _, p, _ in checks if p)
nt_chk = len(checks)
print(f"\n验证检查 ({np_chk}/{nt_chk} PASS):")
for cid, desc, passed, details in checks:
    print(f"  {cid}: {'PASS' if passed else 'FAIL'} - {desc}")
    print(f"         {details}")

# ================================================================
# Part 4: 导出CSV
# ================================================================
print("\n导出CSV...")
with open(OUT_CSV, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    # 跨年验证
    w.writerow(['=== Part 1: L4 跨年验证 (BAT-only反馈) ==='])
    w.writerow(['Year', 'BOL_total', 'M2_total', 'M2_bat', 'M2_fc', 'M2_el',
        'M2_TE_pct', 'M2_LPSP_pct', 'M3_total', 'M3_bat', 'M3_fc', 'M3_el',
        'M3_TE_pct', 'M3_LPSP_pct', 'Delta_TE_pct', 'M3_D_bat_eoy', 'M3_D_fc_eoy', 'M3_D_el_eoy'])
    for r in crossyear_results:
        rb = r['r_bol']; m2 = r['r_m2']; m3 = r['r_m3']
        w.writerow([r['year'],
            f"{rb['total']:.8f}",
            f"{m2['total']:.8f}", f"{m2['bat']:.8f}", f"{m2['fc']:.8f}", f"{m2['el']:.8f}",
            f"{r['te_m2']*100:.4f}", f"{m2['lpsp']*100:.4f}",
            f"{m3['total']:.8f}", f"{m3['bat']:.8f}", f"{m3['fc']:.8f}", f"{m3['el']:.8f}",
            f"{r['te_m3']*100:.4f}", f"{m3['lpsp']*100:.4f}",
            f"{r['delta_te']*100:.4f}",
            f"{m3['D_bat_eoy']:.6f}", f"{m3['D_fc_eoy']:.6f}", f"{m3['D_el_eoy']:.6f}"])
    w.writerow([])
    # 全反馈验证
    w.writerow(['=== Part 2: L4 全反馈验证 (2017年) ==='])
    w.writerow(['Mode', 'Feedback', 'BOL_total', 'FB_total', 'FB_bat', 'FB_fc', 'FB_el',
        'TE_pct', 'LPSP_pct', 'D_bat_eoy', 'D_fc_eoy', 'D_el_eoy'])
    for label, fb_mode, r, te in [('M2', 'BAT-only', r_m2_bo, te_m2_bo),
                                   ('M2', 'Full', r_m2_ff, te_m2_ff),
                                   ('M3', 'BAT-only', r_m3_bo, te_m3_bo),
                                   ('M3', 'Full', r_m3_ff, te_m3_ff)]:
        w.writerow([label, fb_mode,
            f"{r_bol_ff['total']:.8f}",
            f"{r['total']:.8f}", f"{r['bat']:.8f}", f"{r['fc']:.8f}", f"{r['el']:.8f}",
            f"{te*100:.4f}", f"{r['lpsp']*100:.4f}",
            f"{r['D_bat_eoy']:.6f}", f"{r['D_fc_eoy']:.6f}", f"{r['D_el_eoy']:.6f}"])
    w.writerow([])
    # 检查结果
    w.writerow(['=== Part 3: 验证检查 ==='])
    w.writerow(['ID', 'Description', 'Result', 'Details'])
    for cid, desc, passed, details in checks:
        w.writerow([cid, desc, 'PASS' if passed else 'FAIL', details])
print(f"  CSV: {OUT_CSV}")

# ================================================================
# Part 5: 图表
# ================================================================
print("\n生成图表...")
fig = plt.figure(figsize=(16, 10))
gs = GridSpec(2, 3, hspace=0.35, wspace=0.30, left=0.07, right=0.93, top=0.91, bottom=0.08)

# (a) 跨年TE对比
ax1 = fig.add_subplot(gs[0, 0])
years = [r['year'] for r in crossyear_results]
tes_m2_plot = [r['te_m2']*100 for r in crossyear_results]
tes_m3_plot = [r['te_m3']*100 for r in crossyear_results]
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
deltas = [r['delta_te']*100 for r in crossyear_results]
colors = [C_M3 if d < 0 else 'red' for d in deltas]
ax2.bar(years, deltas, color=colors, alpha=0.8, edgecolor='k', linewidth=0.3)
ax2.axhline(0, color='gray', lw=0.5)
ax2.set_xlabel('Year'); ax2.set_ylabel('ΔTE (M3-M2, %)')
ax2.set_title('(b) L4 减轻退化效应 (跨年)', fontsize=10, fontweight='bold')
ax2.grid(True, alpha=0.3, axis='y')
for i, d in enumerate(deltas):
    ax2.text(years[i], d-0.05 if d < 0 else d+0.02, f'{d:+.2f}%', ha='center', fontsize=9)

# (c) 跨年LPSP
ax3 = fig.add_subplot(gs[0, 2])
lpsp_m2 = [r['r_m2']['lpsp']*100 for r in crossyear_results]
lpsp_m3 = [r['r_m3']['lpsp']*100 for r in crossyear_results]
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
    ['M3<M2 全年', 'PASS' if m3_lt_m2_all else 'FAIL', f'{sum(m3_lt_m2_all for _ in [1])}/3年'],
    ['全反馈M3 TE>0', 'PASS' if te_m3_ff > 0 else 'FAIL', f'{te_m3_ff*100:+.2f}%'],
    ['M3 Full<M2 Full', 'PASS' if te_m3_ff < te_m2_ff else 'FAIL', f'Δ={te_m3_ff*100-te_m2_ff*100:+.2f}%'],
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

print("\n" + "=" * 70)
print("L4 跨年+全反馈验证 完成")
print("=" * 70)
print(f"  跨年: M3 TE={[f'{t*100:+.2f}%' for t in tes_m3]}, CV={cv_m3*100:.2f}%, M3<M2全年={m3_lt_m2_all}")
print(f"  全反馈: M3 BO TE={te_m3_bo*100:+.2f}%, M3 Full TE={te_m3_ff*100:+.2f}%")
print(f"  验证: {np_chk}/{nt_chk} PASS")
print("=" * 70)
