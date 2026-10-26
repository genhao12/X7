# -*- coding: utf-8 -*-
"""
run_fc_el_param_posterior.py — FC/EL 参数后验区间与误差传播（审查修改任务 T3）

目的：FC/EL 目前主要依赖线性退化 + 文献区间核验，只能支持方向性结论。
本脚本将 L21 外部核验得到的偏差/区间**传播**到 FC/EL 年损伤与寿命，
给出参数后验区间下的寿命区间（误差传播），明确仿真寿命不是设备真实寿命。

参数不确定带（来自 L21 核验，均比实测值保守）：
  - FC 电压退化率：实测 FC2 纹波速率比 1.92x、动态效率退化 1.75–8.74 %/1000h
      → 参数后验带取 [0.5, 2.0]×标定值（覆盖纹波加速与工况差异）
  - EL 年退化率：模型 5 配置 1.17–1.61 %/yr vs Buttler&Spliethoff 2018 文献 1.50–5.01 %/yr
      → 参数后验带取 [1.0, 3.0]×模型标称（覆盖文献上界）

输出：07_results/rectification_v2_stage2c/stage3/fc_el_param_posterior.json
"""
import sys, json
import numpy as np
from pathlib import Path

ROOT = Path(r"d:\matlab\C")
SUMM = ROOT / "07_results/rectification_v2_stage2c/stage3/degradation_summary.json"
OUT = ROOT / "07_results/rectification_v2_stage2c/stage3/fc_el_param_posterior.json"

# 参数后验带（因子）
FC_RATE_BAND = (0.5, 2.0)      # ×标定（FC2 纹波 1.92x + 动态工况差异）
EL_RATE_BAND = (1.0, 3.0)      # ×标称（文献上界 5.01/1.61 ≈ 3.1）

def main():
    d = json.loads(SUMM.read_text(encoding="utf-8"))
    all_out = {}
    for rep in d["representatives"]:
        role = rep["role"]
        fc_dD = rep["avg_annual_damage"]["fc"]
        el_dD = rep["avg_annual_damage"]["el"]
        fc_life = rep["lifetime_years"]["fc"]
        el_life = rep["lifetime_years"]["el"]
        # 后验区间（因子带 × 标称）
        fc_dD_lo, fc_dD_hi = fc_dD * FC_RATE_BAND[0], fc_dD * FC_RATE_BAND[1]
        el_dD_lo, el_dD_hi = el_dD * EL_RATE_BAND[0], el_dD * EL_RATE_BAND[1]
        fc_life_lo, fc_life_hi = 1.0 / (fc_dD_hi) if fc_dD_hi > 0 else np.inf, 1.0 / (fc_dD_lo) if fc_dD_lo > 0 else np.inf
        el_life_lo, el_life_hi = 1.0 / (el_dD_hi) if el_dD_hi > 0 else np.inf, 1.0 / (el_dD_lo) if el_dD_lo > 0 else np.inf
        all_out[role] = {
            "fc_annual_dD": {"nominal": round(fc_dD, 4), "posterior_band": [round(fc_dD_lo, 4), round(fc_dD_hi, 4)]},
            "fc_lifetime_y": {"nominal": round(fc_life, 2), "posterior_band": [round(fc_life_lo, 2), round(fc_life_hi, 2)]},
            "el_annual_dD": {"nominal": round(el_dD, 4), "posterior_band": [round(el_dD_lo, 4), round(el_dD_hi, 4)]},
            "el_lifetime_y": {"nominal": round(el_life, 2), "posterior_band": [round(el_life_lo, 2), round(el_life_hi, 2)]},
        }
        print(f"{role:<18} FC life {fc_life:.1f}y -> [{fc_life_lo:.1f}, {fc_life_hi:.1f}]y | EL life {el_life:.1f}y -> [{el_life_lo:.1f}, {el_life_hi:.1f}]y")

    all_out["_method"] = {
        "fc_rate_band": list(FC_RATE_BAND),
        "el_rate_band": list(EL_RATE_BAND),
        "evidence": "L21: FC2 ripple ratio 1.92x, dynamic eta degradation 1.75-8.74 %/1000h; EL within Buttler&Spliethoff 2018 range (1.50-5.01 %/yr)",
        "conclusion": "FC/EL 寿命为参数后验区间下的仿真产物，区间覆盖工况/文献不确定度；"
                      "任何单一寿命数值（如 FC 9.7y）均不作设备真实寿命或工程结论（L3/L8/L21）",
        "note": "后验带为保守因子带（非统计后验分布）；误差传播为区间算术",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(all_out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"结果已写入: {OUT}")


if __name__ == "__main__":
    main()
