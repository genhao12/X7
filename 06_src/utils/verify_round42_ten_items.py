# -*- coding: utf-8 -*-
"""round42 十项修改意见核验脚本（2026-08-07 二次审查）"""
import json
from pathlib import Path

S = Path("D:/matlab/C/07_results/rectification_v2_stage2c/stage3")
D = Path("D:/matlab/C/09_docs")
checks = []


def ok(name, cond, detail=""):
    checks.append((name, bool(cond), detail))


# ── 意见1：TE 冲突 + EL 寿命残留 ──
ok("1a 149组三年链式TE文件", (S / "representative_coverage_3yr.json").exists())
paper = (D / "01_论文/04_定稿_摘要与结论.md").read_text(encoding="utf-8")
ok("1b EL寿命标LEGACY", "历史 EL EOL≈6.2y 仅作 LEGACY 对照" in paper)
ok("1c 论文无未标注2.404残留", "2.404" not in paper)

# ── 意见2：LPSP 可靠性约束 ──
adm = (D / "02_治理与口径/09_准入算法与策略对照.md").read_text(encoding="utf-8")
ok("2a LPSP公式", "Σ(p_uns × dt) / Σ(load × dt)" in adm)
ok("2b LPSP单位", "无量纲能量缺失比" in adm)
ok("2c LPSP阈值<1%", "逐年（yearly_lpsp）与三年聚合" in adm and "< 1%" in adm)
ok("2d 负荷假设", "无关键/可削减负荷分级" in adm)

# ── 意见3：气象15min真实性 ──
pv = json.load(open(S / "pv_reconstruction_validation.json", encoding="utf-8"))
ok("3a 三城市重构", len(pv["nasa_reconstruction"]) == 3)
ok("3b 能量守恒误差报告", "mean_rel_err_pct" in pv["nasa_reconstruction"][0]["energy_conservation"])
ok("3c UCSD实测产能校验", "annual_yield_kwh_per_kwp" in pv["ucsd_measured"])
ok("3d PV模型参数", "pv_model" in pv["nasa_reconstruction"][0])

# ── 意见4：TE 显著性口径 ──
v2 = json.load(open(S / "te_significance_v2.json", encoding="utf-8"))
ok("4a 周块重采样", v2["compact"]["resample_object"].startswith("周块"))
ok("4b 95%CI+效应量", "boot_95ci_weekly_pct" in v2["compact"] and "effect_size_cohens_d" in v2["compact"])
ok("4c 参数不确定性", "param_uncertainty" in v2["compact"])
ok("4d 措辞=基准情景方向稳定", v2["compact"]["verdict"] == "基准情景方向稳定（周块 90%CI 不跨 0）")

# ── 意见5：策略消融有效性 ──
da = json.load(open(S / "da_closedloop_te.json", encoding="utf-8"))
confs = [k for k in da if not k.startswith("_")]
ok("5a da闭环5配置", len(confs) == 5)
ok("5b 语义不同+不作论文表", "语义不同" in da["_method"]["conclusion"] and "不作论文结果表" in da["_method"]["conclusion"])
ok("5c 运行统计ops", "fc_run_h" in da["compact"]["ops"] and "h2_min_kg" in da["compact"]["ops"])
sa = json.load(open(S / "strategy_ablation.json", encoding="utf-8"))
ok("5d _ops D偏置敏感性", "D_bat_heavy_0.7" in sa["_ops"])

# ── 意见6：验证术语 ──
summ = (D / "06_项目梳理/04_项目全流程总结_论文基础.md").read_text(encoding="utf-8")
ok("6a FC1=实现核验", "实现核验" in summ)
ok("6b FC2=外部验证", "外部验证" in summ)
ok("6c 无因果中介残留", "因果中介" not in (D / "01_论文/04_定稿_摘要与结论.md").read_text(encoding="utf-8"))

# ── 意见7：EL 模型补强 ──
el = json.load(open(S / "el_condition_mapping.json", encoding="utf-8"))
f = el["four_component_decomposition"]["wavePacWave-Noacc_4-200.csv"]["formulas"]
ok("7a 4项公式", all(k in f for k in ("d_steady", "d_temp", "d_startstop", "d_ramp")))
ok("7b 等权声明", f["weights"]["steady"] == 1.0)
ok("7c LOO验证", el.get("leave_one_out_validation", {}).get("mean_rel_err_pct", 9) < 5)
ok("7d BOT/EOT映射", len(el.get("bot_eot_polarization", [])) >= 4)

# ── 意见8：不确定性与经济 ──
sob = json.load(open(S / "sobol_convergence.json", encoding="utf-8"))
ok("8a Sobol 128/256/512", sob["n_outer_tested"] == [128, 256, 512])
econ = json.load(open("D:/matlab/C/05_config/economic_parameters_v1.0.json", encoding="utf-8"))
ok("8b 经济基准年2026", econ["currency"]["price_year"] == 2026)
ok("8c 双区间定义文档", "5.92–7.52" in (D / "02_治理与口径/06_口径_经济双区间.md").read_text(encoding="utf-8"))

# ── 意见9：论文结构 ──
ok("9a 3项主创新", "3 项唯一主创新" in (D / "02_治理与口径/11_创新贡献_3项可验证.md").read_text(encoding="utf-8"))
ok("9b 可复现性章节", "可复现性" in (D / "01_论文/04_定稿_摘要与结论.md").read_text(encoding="utf-8"))

# ── 意见10：结论限定 + 数据溯源 ──
ok("10a 方向稳定结论", "方向稳定" in paper)
ok("10b 条件性应用结果", "条件性应用结果" in paper)
ok("10c 数据附录逐项", "SHA-256(前16)" in (D / "02_治理与口径/04_数据附录_许可哈希与预处理.md").read_text(encoding="utf-8"))

print("=== round42 十项意见核验（二次审查 2026-08-07）===")
for name, cond, detail in checks:
    print(("PASS " if cond else "FAIL ") + name + (" | " + detail if detail else ""))
npass = sum(1 for _, c, _ in checks if c)
print()
print(f"总计: {npass}/{len(checks)} PASS")
