# -*- coding: utf-8 -*-
"""B2: S4口径对照 —— 999h 真实动态工况下 legacy vs 四机制 FC 退化回放 (缺口③)

背景
----
原 S4 (`run_s4_fc_recalibration.py`) **不是工况回放式仿真**: 它直接读 999h
效率 CSV 做线性拟合 -> 算 EOL -> 写标定 JSON, 内部没有功率/电流轨迹,
也没有 compute_damage 调用。因此本脚本**新写**回放链路:

    电流密度参考周期 (70 min) 重复至 999h
        -> legacy 模型   (closed_loop_lite: FC_K_RUN_H/FC_K_START)
        -> 四机制模型    (fc_four_mechanism: Refaat 2026 + FC1 锚定)
        -> D 累计 -> 效率轨迹 eff = eff_bol * (1 - 0.10 * D)
        -> 与实测 0.541 -> 0.451 (相对降 16.64%) 对照

数据
----
数据文献/PEMFC真实动态工况性能衰减数据/
  Time_current_reference_cycle.csv : 4194点, 时间[min] 0->70, 电流密度[A/cm2] 0->1.75
  Efficiencies.csv                 : 1..999h, 效率 0.541 -> 0.451

归一化基准 (关键假设, 双轨报告)
----
四机制参数锚定自 FC1: 额定电流密度 1.0 A/cm2 (`fc_four_mechanism.FC1_LOAD_FRACTION`
= 70A/(1.0 A/cm2 * 100cm2))。而本动态周期峰值 1.75 A/cm2。故:
  主口径   J_RATED = 1.0  A/cm2  (FC1口径, 与模型锚定一致) -> p_pu 峰值 1.75
  敏感性   J_RATED = 1.75 A/cm2  (周期峰值归一化)           -> p_pu 峰值 1.00
两者结果并列报告。

判定
----
  比值 r = D_model(999h) / D_measured(999h),  D_measured = 16.64% / 10% = 1.6636
    r in [0.8, 1.2]  -> PASS (落在 ±20% 内)
    r >  1.2         -> 偏激进 -> 按用户规则建议 fc_four_mechanism 标定等级降为 C 级
    r <  0.8         -> 偏保守 -> 记录 regime 差异 (加速试验 vs 10y@500h/yr 标定),
                        不触发 C 级 (非"激进")
legacy 的 ±20% 结果并列打印 (预期 FAIL, 属 regime 差异非 bug)。
"""
import sys, json, csv
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(r"D:\matlab\C")
sys.path.insert(0, str(PROJECT_ROOT / "06_src"))

from components import fc_four_mechanism as fcm
from simulation.closed_loop_lite import FC_K_RUN_H, FC_K_START

DATA_DIR = PROJECT_ROOT / "数据文献" / "PEMFC真实动态工况性能衰减数据"
CYCLE_CSV = DATA_DIR / "Time_current_reference_cycle.csv"
EFF_CSV = DATA_DIR / "Efficiencies.csv"

TARGET_HOURS = 999.0
VOLTAGE_LOSS_EOL = 0.10          # D=1 <-> 效率/电压下降 10% (与 S4 eol_eff_drop 一致)
START_THRESHOLD_FRAC = 0.275     # 与 closed_loop_lite / fc_four_mechanism 默认一致
RATED_KW = 1.0                   # 以 p.u. 口径计算 (p_fc_kw = p_pu)

OUT_JSON = PROJECT_ROOT / "07_results" / "gap_closure" / "s4_fc_four_mechanism_check.json"
OUT_CSV = PROJECT_ROOT / "07_results" / "gap_closure" / "s4_fc_four_mechanism_traj.csv"

print("=" * 74)
print("B2: S4口径对照 — 999h动态工况 legacy vs 四机制 FC退化回放 (缺口③)")
print("=" * 74)

# ---------------------------------------------------------------- 1 实测效率
dfe = pd.read_csv(EFF_CSV, sep=';', header=0)
hrs = pd.to_numeric(dfe.iloc[:, 0], errors='coerce')
eff = pd.to_numeric(dfe.iloc[:, 1], errors='coerce')
m = hrs.notna() & eff.notna()
hrs, eff = hrs[m].values, eff[m].values

eff_bol, eff_eol = float(eff[0]), float(eff[-1])
meas_rel_drop = (eff_bol - eff_eol) / eff_bol
D_measured = meas_rel_drop / VOLTAGE_LOSS_EOL

print(f"\n[1] 实测效率退化 ({hrs[0]:.0f}–{hrs[-1]:.0f} h, {len(hrs)}点)")
print(f"    效率 {eff_bol:.4f} -> {eff_eol:.4f}   相对降 {meas_rel_drop*100:.2f}%")
print(f"    折算 D_measured = {meas_rel_drop*100:.2f}% / {VOLTAGE_LOSS_EOL*100:.0f}% "
      f"= {D_measured:.4f}   (即约 {TARGET_HOURS/D_measured:.0f} h 到 EOL)")

# ---------------------------------------------------------------- 2 工况周期
dfc = pd.read_csv(CYCLE_CSV, sep=';', header=0)
t_min = pd.to_numeric(dfc.iloc[:, 0], errors='coerce')
j_den = pd.to_numeric(dfc.iloc[:, 1], errors='coerce')
mask = t_min.notna() & j_den.notna()
t_min, j_den = t_min[mask].values, j_den[mask].values

span_min = float(t_min[-1] - t_min[0])
dts = np.diff(t_min)
dt_h = float(np.mean(dts)) / 60.0
print(f"\n[2] 电流密度参考周期 ({len(t_min)}点)")
print(f"    时间 {t_min[0]:.2f} -> {t_min[-1]:.2f} min (跨度 {span_min:.2f} min)")
print(f"    电流密度 {j_den.min():.4f} -> {j_den.max():.4f} A/cm2 (均值 {j_den.mean():.4f})")
print(f"    dt: mean={dt_h*3600:.4f}s  min={dts.min()/60*3600:.4f}s  "
      f"max={dts.max()/60*3600:.4f}s (近均匀, 按均值统一步长)")
print(f"    周期首/末值: j[0]={j_den[0]:.4f}, j[-1]={j_den[-1]:.4f} A/cm2")

# ------------------------------------------------- 3 平铺至 999h
n_rep = int(np.ceil(TARGET_HOURS * 60.0 / span_min))
n_target = int(np.ceil(TARGET_HOURS / dt_h))
j_tiled = np.tile(j_den, n_rep)[:n_target]
t_tiled_h = np.arange(n_target) * dt_h
print(f"\n[3] 回放拼接: {n_rep} 个周期 -> 截断到 {n_target} 步 "
      f"({t_tiled_h[-1]:.2f} h, 目标 {TARGET_HOURS:.0f} h)")

# ------------------------------------------------- 4 启停事件 (legacy与四机制共用)
def count_starts(p_pu, thr=START_THRESHOLD_FRAC):
    active = p_pu >= thr
    s = np.zeros(len(p_pu))
    if len(p_pu) > 1:
        s[1:] = (active[1:] & ~active[:-1]).astype(float)
    return s


def legacy_increments(p_pu, dt_hours, eta_factor=1.0):
    """closed_loop_lite legacy: dD = FC_K_RUN_H*eta*p_pu*dt + FC_K_START*starts"""
    s = count_starts(p_pu)
    return FC_K_RUN_H * eta_factor * p_pu * dt_hours + FC_K_START * s


def mechanism_breakdown(p_pu, dt_hours, params):
    """四机制四项分解 (线性叠加, 逐项置零其余系数)。"""
    parts = {}
    for name, kw in (('low_load', dict(d_high_uv_h=0.0, d_trans_uv_per_pu=0.0, d_cycle_uv=0.0)),
                     ('high_load', dict(d_low_uv_h=0.0, d_trans_uv_per_pu=0.0, d_cycle_uv=0.0)),
                     ('transient', dict(d_low_uv_h=0.0, d_high_uv_h=0.0, d_cycle_uv=0.0)),
                     ('startstop', dict(d_low_uv_h=0.0, d_high_uv_h=0.0, d_trans_uv_per_pu=0.0))):
        pp = replace(params, **kw)
        parts[name] = float(np.sum(fcm.fc_damage_increments(
            p_pu, RATED_KW, dt_hours, pp, START_THRESHOLD_FRAC)))
    return parts


def evaluate(j_rated, tag):
    p_pu = j_tiled / j_rated
    p_fc_kw = p_pu * RATED_KW

    # --- legacy ---
    d_leg = legacy_increments(p_pu, dt_h)
    D_leg = float(np.sum(d_leg))

    # --- 四机制 ---
    params = fcm.default_params()
    d_four = fcm.fc_damage_increments(p_fc_kw, RATED_KW, dt_h, params,
                                      START_THRESHOLD_FRAC)
    D_four = float(np.sum(d_four))
    parts = mechanism_breakdown(p_fc_kw, dt_h, params)

    # --- 内置对照交叉校验 ---
    chk = fcm.compare_to_legacy(p_fc_kw, RATED_KW, dt_h,
                                start_threshold_frac=START_THRESHOLD_FRAC)
    assert abs(chk['legacy_total_D'] - D_leg) < 1e-9 * max(1.0, abs(D_leg)), \
        "legacy 口径与 compare_to_legacy 不一致"

    n_starts = int(np.sum(count_starts(p_pu)))
    r_leg = D_leg / D_measured
    r_four = D_four / D_measured

    # --- 效率轨迹 ---
    def to_eff(d_inc):
        d_cum = np.cumsum(d_inc)
        return eff_bol * (1.0 - VOLTAGE_LOSS_EOL * d_cum)

    eff_leg = to_eff(d_leg)
    eff_four = to_eff(d_four)

    print(f"\n[{tag}] J_rated = {j_rated:.3f} A/cm2  "
          f"(p_pu 峰值 {p_pu.max():.3f}, 均值 {p_pu.mean():.3f})")
    print(f"    启停次数(999h)      : {n_starts}")
    print(f"    legacy  D(999h)     : {D_leg:.4f}  -> 效率 {eff_bol:.4f}->{eff_leg[-1]:.4f} "
          f"(相对降 {(eff_bol-eff_leg[-1])/eff_bol*100:6.2f}%)  比值 r_leg  = {r_leg:.3f}")
    print(f"    四机制  D(999h)     : {D_four:.4f}  -> 效率 {eff_bol:.4f}->{eff_four[-1]:.4f} "
          f"(相对降 {(eff_bol-eff_four[-1])/eff_bol*100:6.2f}%)  比值 r_four = {r_four:.3f}")
    print(f"    四机制/legacy 放大   : {D_four/max(D_leg,1e-12):.3f}x")
    print(f"    四机制四项分解 D     : " + "  ".join(
        f"{k}={v:.4f}" for k, v in parts.items()) +
        f"   (线性闭合残差 {abs(sum(parts.values())-D_four):.2e})")
    print(f"    ±20% 判据            : legacy {'PASS' if 0.8<=r_leg<=1.2 else 'FAIL'}"
          f" / 四机制 {'PASS' if 0.8<=r_four<=1.2 else 'FAIL'}")

    return dict(j_rated=j_rated, p_pu_peak=float(p_pu.max()),
                p_pu_mean=float(p_pu.mean()), n_starts=n_starts,
                D_legacy=D_leg, D_four_mech=D_four,
                eff_end_legacy=float(eff_leg[-1]), eff_end_four=float(eff_four[-1]),
                rel_drop_legacy_pct=float((eff_bol - eff_leg[-1]) / eff_bol * 100),
                rel_drop_four_pct=float((eff_bol - eff_four[-1]) / eff_bol * 100),
                ratio_legacy=r_leg, ratio_four=r_four,
                ratio_four_over_legacy=float(D_four / max(D_leg, 1e-12)),
                mechanism_breakdown=parts,
                pass_legacy=bool(0.8 <= r_leg <= 1.2),
                pass_four=bool(0.8 <= r_four <= 1.2),
                _eff_leg=eff_leg, _eff_four=eff_four)


main = evaluate(1.0, "4a 主口径: FC1额定")
sens = evaluate(1.75, "4b 敏感性: 峰值归一化")

# ------------------------------------------------- 5 判定
r = main['ratio_four']
if 0.8 <= r <= 1.2:
    verdict, action = "PASS", "维持现标定等级 (A/B 级按现有声明)"
elif r > 1.2:
    verdict, action = ("FAIL(偏激进)",
                       "Refaat 启停/瞬态系数对本机组偏激进 -> "
                       "建议在 fc_four_mechanism.py 头部将标定等级降为 C 级")
else:
    verdict, action = ("FAIL(偏保守)",
                       "模型对该加速试验偏保守, 属 regime 差异 "
                       "(999h加速试验 vs legacy 10y@500h/yr / FC1恒流锚定); "
                       "不触发 C 级降级 (非激进), 在 limitations 记录边界")

print("\n" + "=" * 74)
print("B2 判定 (主口径 J_rated=1.0 A/cm2)")
print("=" * 74)
print(f"  实测 D_measured(999h)   : {D_measured:.4f}")
print(f"  legacy  / 实测          : {main['ratio_legacy']:.3f}  "
      f"({'PASS' if main['pass_legacy'] else 'FAIL'})  <- 预期FAIL: regime差异非bug")
print(f"  四机制  / 实测          : {main['ratio_four']:.3f}  "
      f"({'PASS' if main['pass_four'] else 'FAIL'})")
print(f"  四机制 / legacy 放大     : {main['ratio_four_over_legacy']:.3f}x")
print(f"  判定                    : {verdict}")
print(f"  建议动作                : {action}")

# ------------------------------------------------- 6 输出
OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
payload = {
    'source': {
        'cycle_csv': str(CYCLE_CSV), 'eff_csv': str(EFF_CSV),
        'n_points_cycle': int(len(j_den)), 'cycle_span_min': span_min,
        'j_min': float(j_den.min()), 'j_max': float(j_den.max()),
        'j_mean': float(j_den.mean()), 'dt_h': dt_h,
        'n_repeat': n_rep, 'n_steps_999h': n_target,
    },
    'measured': {
        'eff_bol': eff_bol, 'eff_eol': eff_eol,
        'rel_drop_pct': float(meas_rel_drop * 100), 'D_measured': float(D_measured),
    },
    'main_j_rated_1.0': {k: v for k, v in main.items() if not k.startswith('_')},
    'sens_j_rated_1.75': {k: v for k, v in sens.items() if not k.startswith('_')},
    'verdict': verdict, 'recommended_action': action,
    'notes': [
        'legacy ±20% 判据预期 FAIL: 999h为加速动态耐久试验(D≈1.66, ~600h到EOL), '
        'legacy标定目标为10y@500h/yr(~5000h), 属regime差异非模型缺陷。',
        '主口径 J_rated=1.0 A/cm2 (FC1锚定); 敏感性 J_rated=1.75 (周期峰值归一化)。',
        '效率映射 eff = eff_bol*(1-0.10*D), 与 S4 eol_eff_drop=0.10 及 '
        'fc_four_mechanism VOLTAGE_LOSS_EOL=0.10 一致。',
    ],
}
with open(OUT_JSON, 'w', encoding='utf-8') as f:
    json.dump(payload, f, indent=2, ensure_ascii=False)
print(f"\n[JSON] {OUT_JSON}")

# 轨迹 CSV (等间隔抽样 ~1500 行)
step = max(1, n_target // 1500)
with open(OUT_CSV, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['t_h', 'eff_measured_interp', 'eff_legacy', 'eff_four_mech',
                'D_legacy_cum', 'D_four_cum'])
    eff_meas_interp = np.interp(t_tiled_h, hrs, eff)
    D_leg_cum = np.cumsum(legacy_increments(j_tiled / 1.0, dt_h))
    D_four_cum = np.cumsum(fcm.fc_damage_increments(
        j_tiled / 1.0 * RATED_KW, RATED_KW, dt_h, None, START_THRESHOLD_FRAC))
    for i in range(0, n_target, step):
        w.writerow([f"{t_tiled_h[i]:.4f}", f"{eff_meas_interp[i]:.6f}",
                    f"{main['_eff_leg'][i]:.6f}", f"{main['_eff_four'][i]:.6f}",
                    f"{D_leg_cum[i]:.8f}", f"{D_four_cum[i]:.8f}"])
print(f"[CSV ] {OUT_CSV}")

print("\n" + "=" * 74)
print("Methodology:")
print("  legacy : dD = FC_K_RUN_H*p_pu*dt_h + FC_K_START*starts  (closed_loop_lite)")
print("  四机制 : dV = D_low*t_low + D_high*t_high + D_trans*|dP| + D_cycle*n_starts")
print("           (Refaat 2026 表2, 时间项经 FC1 恒流 23.5uV/1000h 锚定 s=2.311)")
print("  D->效率: eff = eff_bol*(1 - 0.10*D)")
print("=" * 74)
