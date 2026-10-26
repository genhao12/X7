# -*- coding: utf-8 -*-
"""
run_joint_sensitivity.py — 经济联合敏感性分析（审查修改任务 T7）

目的：以折现率/设备成本年下降/替换价格/残值/运维(OPEX)/燃料(H2外购)边界的
**联合采样**替代单因素 ±30%，给出 NPC/LCOE 的稳健区间与单参数边际影响（tornado）。

方法：
  - 6 参数 LHS 采样（scipy qmc, 128 点, seed=20260806）
  - 对 5 个代表配置：每点扩展 20 年现金流（含学习率折扣的替换成本、残值、OPEX 缩放、
    H2 外购成本边界情景）→ NPC/LCOE
  - 输出：各配置 LCOE 90% 经验区间 + 单参数 tornado（该参数 5%/95% 分位组的 LCOE 均值差）

口径：基准 = economic_parameters_v1.0.json（round16 成本边界后）；LCOE = NPC/(20×年供电量)。
输出：07_results/rectification_v2_stage2c/stage3/joint_sensitivity.json
"""
import sys, json
import numpy as np
from pathlib import Path

ROOT = Path(r"d:\matlab\C")
sys.path.insert(0, str(ROOT / "06_src"))

from scipy.stats import qmc
from economics.npc_lite import load_economic_config, compute_equipment_capex, compute_annual_opex

ECON = ROOT / "05_config" / "economic_parameters_v1.0.json"
C1 = ROOT / "05_config" / "mvp_c1_config.json"
SUMM = ROOT / "07_results/rectification_v2_stage2c/stage3/degradation_summary.json"
OUT = ROOT / "07_results/rectification_v2_stage2c/stage3/joint_sensitivity.json"

N_SAMPLES = 128
SEED = 20260806
T = 20

# 参数范围
PARAM_RANGES = {
    "discount_rate":       (0.04, 0.10),   # 折现率
    "capex_learning_yr":   (0.00, 0.02),   # 设备成本年下降率（替换成本）
    "replacement_price":   (0.80, 1.20),   # 替换价格系数
    "residual_fraction":   (0.00, 0.10),   # 残值率
    "opex_scale":          (0.70, 1.30),   # OPEX 缩放
    "h2_purchase_cost":    (0.00, 30.0),   # H2 外购边界（CNY/kg，年度外购 1000 kg 情景）
}
H2_PURCHASE_KG_YR = 1000.0   # 外购情景年量（kg/yr），仅边界展示


def compute_npc_joint(cfg, repl_schedule, annual_energy_mwh, p: dict) -> float:
    """扩展现金流：学习率折扣替换 + OPEX 缩放 + 残值 + H2 外购边界。返回 NPC(元)。"""
    r = p["discount_rate"]
    capex = compute_equipment_capex(cfg)
    opex = compute_annual_opex(cfg, capex) * p["opex_scale"]
    h2_cost = p["h2_purchase_cost"] * H2_PURCHASE_KG_YR

    cash = np.zeros(T + 1)
    cash[0] = capex["total_initial"]
    for year in range(1, T + 1):
        cash[year] += opex + h2_cost
        for device, years in repl_schedule.items():
            if year in years:
                # 基准替换成本 × 价格系数 × 学习率折扣
                repl_base = {
                    "bat": capex["BAT"] * cfg.bat_replacement_fraction,
                    "fc":  capex["PEMFC"] * cfg.pemfc_replacement_fraction,
                    "el":  capex["PEMEL"] * cfg.pemel_replacement_fraction,
                }.get(device, 0.0)
                cash[year] += repl_base * p["replacement_price"] * (1 - p["capex_learning_yr"]) ** year
    cash[T] -= capex["total_initial"] * p["residual_fraction"]  # 残值
    npc = float(np.sum(cash / (1 + r) ** np.arange(T + 1)))
    return npc


def main():
    cfg_base = load_economic_config(str(ECON), str(C1))
    summ = json.loads(SUMM.read_text(encoding="utf-8"))

    sampler = qmc.LatinHypercube(d=len(PARAM_RANGES), seed=SEED)
    U = sampler.random(n=N_SAMPLES)
    # 反变换到参数范围
    names = list(PARAM_RANGES.keys())
    P = {}
    for i, n in enumerate(names):
        lo, hi = PARAM_RANGES[n]
        P[n] = lo + U[:, i] * (hi - lo)

    all_out = {}
    for rep in summ["representatives"]:
        role = rep["role"]
        cfg = load_economic_config(str(ECON), str(C1))
        cfg.pv_kwp = rep["pv_kwp"]; cfg.bat_kwh = rep["bat_kwh"]
        cfg.pemfc_kw = rep["pemfc_kw"]; cfg.pemel_kw = rep["pemel_kw"]
        cfg.h2_kg = rep["h2_kg"]
        repl_schedule = rep.get("economics", {}).get("replacement_schedule")
        if not repl_schedule:
            # 从 eol_events 推导
            ev = rep.get("eol_events", {})
            repl_schedule = {}
            for dev in ("bat", "fc", "el"):
                n_repl = ev.get("replacement_counts", {}).get(dev, 0)
                life = rep.get("lifetime_years", {}).get(dev, 1)
                repl_schedule[dev] = [int(round(life * (k + 1))) for k in range(n_repl) if int(round(life * (k + 1))) <= T]
        annual_energy = rep.get("economics", {}).get("annual_energy_delivered_mwh")
        if not annual_energy:
            annual_energy = 350.0  # 兜底（不应触发）
        lcoes = []
        npcs = []
        for s in range(N_SAMPLES):
            p = {n: float(P[n][s]) for n in names}
            npc = compute_npc_joint(cfg, repl_schedule, annual_energy, p)
            npcs.append(npc)
            lcoes.append(npc / (T * annual_energy) / 1000.0)  # CNY/MWh -> CNY/kWh
        lcoes = np.asarray(lcoes); npcs = np.asarray(npcs)
        lo, hi = np.percentile(lcoes, [5, 95])
        # tornado：单参数 5%/95% 分位组的 LCOE 均值
        tornado = {}
        for n in names:
            v = P[n]
            m_lo = lcoes[v <= np.percentile(v, 25)].mean() if (v <= np.percentile(v, 25)).sum() > 0 else lcoes.mean()
            m_hi = lcoes[v >= np.percentile(v, 75)].mean() if (v >= np.percentile(v, 75)).sum() > 0 else lcoes.mean()
            tornado[n] = {"p25_group_lcoe": round(float(m_lo), 4), "p75_group_lcoe": round(float(m_hi), 4),
                          "swing": round(float(abs(m_hi - m_lo)), 4)}
        all_out[role] = {
            "n_samples": N_SAMPLES,
            "lcoe_p5": round(float(lo), 4), "lcoe_p95": round(float(hi), 4),
            "lcoe_median": round(float(np.median(lcoes)), 4),
            "npc_p5_M": round(float(np.percentile(npcs, 5)) / 1e6, 3),
            "npc_p95_M": round(float(np.percentile(npcs, 95)) / 1e6, 3),
            "tornado": tornado,
        }
        print(f"{role:<18} LCOE joint 90%: [{lo:.3f}, {hi:.3f}] | top swing: "
              f"{max(tornado.items(), key=lambda kv: kv[1]['swing'])}")

    all_out["_method"] = {
        "sampler": "LHS (scipy qmc)",
        "n_samples": N_SAMPLES,
        "seed": SEED,
        "params": PARAM_RANGES,
        "h2_purchase_scenario": f"{H2_PURCHASE_KG_YR} kg/yr 外购边界情景（基准 0，离网自产）",
        "note": "LCOE = NPC/(20×年供电量)；年供电量取自 degradation_summary economics；"
                "基准参数与 economic_parameters_v1.0.json（round16 成本边界后）一致；"
                "经验 90% 区间（非统计置信区间）；单参数 tornado 为 25%/75% 分组均值差",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(all_out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"结果已写入: {OUT}")


if __name__ == "__main__":
    main()
