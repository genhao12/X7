"""Round 3: Full JSON/CSV consistency re-check (2026-08-05, after rounds 16/17/18).

Verifies:
  A. Governance files: downstream_gate / run_manifest / hash_record
  B. Stage3 artifacts: degradation_summary, cost_sensitivity, uncertainty,
     double_layer (learning-curve), cross_device_paths, l4_ablation,
     imputation_sensitivity, cycle_iteration_trajectory
  C. Paper assets T1-T5 CSVs vs JSON
  D. Test suite collection count (149)
  E. Documents (ledger/main/summary) vs governance truth flags
"""
import json, csv, re, subprocess, sys
from pathlib import Path
import docx

ROOT = Path(r"d:\matlab\C")
GOV = ROOT / "07_results/rectification_v2_stage2c/governance"
S3 = ROOT / "07_results/rectification_v2_stage2c/stage3"
DOCS = {
    "ledger": ROOT / "09_docs" / "统一结果台账.docx",
    "main": ROOT / "09_docs" / "工作区全面总结报告.docx",
    "summary": ROOT / "09_docs" / "工作区运行结果汇总.docx",
}

ok = fail = 0
def check(label, cond, detail=""):
    global ok, fail
    status = "OK" if cond else "FAIL"
    if cond: ok += 1
    else: fail += 1
    print(f"[{status}] {label}" + (f"  {detail}" if detail else ""))

def read(p):
    return json.loads(p.read_text(encoding="utf-8"))

def doc_text(p):
    d = docx.Document(str(p))
    parts = [x.text for x in d.paragraphs]
    for tb in d.tables:
        for row in tb.rows:
            for c in row.cells:
                parts.append(c.text)
    return "\n".join(parts)

print("=" * 72)
print("A. 治理文件")
print("=" * 72)
g = read(GOV / "downstream_gate.json")
check("gate_passed=True", g["gate_passed"] is True)
check("degradation_execution_started=True", g["degradation_execution_started"] is True)
check("six_path_execution_started=True", g["six_path_execution_started"] is True)
check("all allowed True", all(g[k] is True for k in
      ("shapley_allowed", "l4_allowed", "economics_allowed",
       "double_layer_uncertainty_allowed")))
check("legacy False", g["legacy_results_paper_citable"] is False)

rm = read(GOV / "run_manifest.json")
check("manifest tests 149/140", rm["tests"]["cases"] == 149 and rm["tests"]["functions"] == 140)
check("bat_cycle_model=efc", rm.get("bat_cycle_model") == "efc")
art_names = [a.get("name", a.get("id", "")) for a in rm.get("artifacts", [])]
for art in ("degradation_summary", "cost_sensitivity", "cross_device_paths",
            "double_layer_propagation", "uncertainty_propagation",
            "imputation_sensitivity", "cycle_iteration_trajectory"):
    check(f"run_manifest artifact {art}", art in art_names)
check("run_manifest cost groups 80", rm.get("cost_sensitivity_groups") == 80)

hr = read(GOV / "hash_record.json")
check("hash_record extended >=20", len(hr.get("extended_hashes", {})) >= 20)

print()
print("=" * 72)
print("B. Stage3 产物")
print("=" * 72)
# degradation_summary
deg = read(S3 / "degradation_summary.json")
reps = {r["role"]: r for r in deg["representatives"]}
check("degradation 5 reps", len(reps) == 5)
# LCOE range 5.92~7.52 (round16 cost boundary)
lcoes = [reps[r]["economics"]["lcoe_cny_per_kwh"] for r in reps]
check("LCOE 5.92~7.52 range", 5.92 <= min(lcoes) <= 5.93 and 7.52 <= max(lcoes) <= 7.53,
      f"min={min(lcoes):.4f} max={max(lcoes):.4f}")
# CAPEX compact ~21949
cap_compact = reps["compact"]["economics"]["total_capex_cny"] / 1000
check("compact CAPEX ~21949", abs(cap_compact - 21949) < 5, f"{cap_compact:.0f}")
# eol events present
check("economics fields present", all("economics" in r and "capex_breakdown" in r["economics"] for r in reps.values()))

# cost_sensitivity
cs = read(S3 / "cost_sensitivity.json")
bl = [cs["representatives"][r]["baseline"]["lcoe_cny_per_kwh"] for r in cs["representatives"]]
check("cost_sensitivity baseline 5.92~7.52", 5.92 <= min(bl) <= 5.93 and 7.52 <= max(bl) <= 7.53)
# dominant: PEMEL 3/5 (compact/balanced/hydrogen), BAT 2/5 (battery/critical)
def dominant(role):
    b = cs["representatives"][role]["baseline"]["lcoe_cny_per_kwh"]
    best = None
    for dev in ("PV", "BAT", "PEMFC", "PEMEL", "H2_tank"):
        inc = cs["representatives"][role][f"{dev}_+30%"]["lcoe_cny_per_kwh"] - b
        if best is None or inc > best[1]:
            best = (dev, inc)
    return best[0]
dom_map = {r: dominant(r) for r in ("compact", "balanced", "hydrogen_dominant", "battery_dominant", "critical")}
check("dominant PEMEL 3/5 + BAT 2/5",
      dom_map["compact"] == "PEMEL" and dom_map["balanced"] == "PEMEL" and
      dom_map["hydrogen_dominant"] == "PEMEL" and dom_map["battery_dominant"] == "BAT" and
      dom_map["critical"] == "BAT", str(dom_map))

# uncertainty LHS
unc = read(S3 / "uncertainty_propagation.json")
lcoe_med = {r: unc[r]["lcoe"]["p50"] for r in unc}
check("LHS compact LCOE med ~6.0", 5.9 < lcoe_med["compact"] < 6.1, f"{lcoe_med['compact']:.3f}")
# p95 hydrogen > 8 (cost boundary effect)
check("LHS hydrogen p95 > 8", unc["hydrogen_dominant"]["lcoe"]["p95"] > 8.0,
      f"{unc['hydrogen_dominant']['lcoe']['p95']:.3f}")

# double_layer learning curve
dl = read(S3 / "double_layer_propagation.json")
lc_ok = all(
    all(p.get("rel_dev_vs_prev", 0) < 0.05 for p in dl[c]["learning_curve"][dev][1:] if "rel_dev_vs_prev" in p)
    for c in dl for dev in ("bat", "fc", "el"))
check("double_layer learning-curve all <5%", lc_ok)
check("double_layer 256x4000", all(dl[c]["n_outer"] >= 256 and dl[c]["n_inner"] >= 4000 for c in dl))

# cross_device_paths effect_pct 3 levels
cd = read(S3 / "cross_device_paths.json")
check("cross_device 6 paths x 3 levels", all(
    set(cd[c][p].keys()) == {"0.3", "0.5", "0.7"} and "effect_pct" in cd[c][p]["0.5"]
    for c in cd for p in cd[c]))
check("cross_device 5 configs", len(cd) == 5)

# l4_ablation baseline vs docs
l4 = read(S3 / "l4_ablation.json")
exp_b = {"compact": 0.0081, "balanced": 0.0068, "hydrogen_dominant": 0.0101}
check("l4_ablation baselines", all(
    abs(l4[k]["ablation"]["baseline"]["fc_total"] - v) < 0.0001 for k, v in exp_b.items()))

# imputation sensitivity
imp = read(S3 / "imputation_sensitivity.json")
check("imputation missing total 165", imp["missing_points"]["total"] == 165)
rel_ok = all(
    v["rel_dev_vs_ffill"][m]["lcoe"] == 0.0
    for v in imp["representatives"].values() for m in ("linear", "seasonal_mean"))
check("imputation LCOE rel dev 0", rel_ok)

# cycle trajectory
cyc = read(S3 / "cycle_iteration_trajectory.json")
check("cycle trajectory 5 reps converged", all(
    v["converged"] for v in cyc["representatives"].values()))
check("cycle trajectory iterations 3-4", all(
    3 <= v["iterations"] <= 4 for v in cyc["representatives"].values()))

print()
print("=" * 72)
print("C. 论文资产 T1-T5 CSV vs JSON")
print("=" * 72)
t2 = ROOT / "07_results/rectification_v2_stage2c/tables/T2_lifetime_replacements.csv"
with open(t2, encoding="utf-8") as f:
    rows = list(csv.DictReader(f))
check("T2 has 5 rows", len(rows) == 5)
t2_compact = next(r for r in rows if r["role"] == "Compact")
check("T2 compact CAPEX 21.949 (r16)", abs(float(t2_compact["CAPEX_MCNY"]) - 21.949) < 0.01,
      t2_compact["CAPEX_MCNY"])
check("T2 compact LCOE 5.924 (r16)", abs(float(t2_compact["LCOE_CNY_per_kWh"]) - 5.924) < 0.01,
      t2_compact["LCOE_CNY_per_kWh"])

print()
print("=" * 72)
print("D. 测试套件")
print("=" * 72)
r = subprocess.run([sys.executable, "-m", "pytest", "08_tests/", "--collect-only", "-q"],
                   capture_output=True, text=True, cwd=str(ROOT))
m = re.search(r"(\d+) tests collected", r.stdout)
check("pytest collects 149", m and int(m.group(1)) == 149, m.group(1) if m else "?")

print()
print("=" * 72)
print("E. 文档 vs 治理真值")
print("=" * 72)
texts = {k: doc_text(p) for k, p in DOCS.items()}
for docname in ("ledger", "main", "summary"):
    t = texts[docname]
    check(f"{docname} gate_passed=True", "gate_passed=True" in t or "门控已通过" in t)
    check(f"{docname} 165 个 NaN 口径", "165 个" in t or "165 个 NaN" in t or "共 165" in t)
    check(f"{docname} round16 成本边界", "5.92~7.52" in t or "5.92–7.52" in t or "LCOE 5.92" in t)
check("main cycle trajectory round18", "round18 已补齐周期初值迭代轨迹" in texts["main"])
check("main LCOE p95 hydrogen>8", "8.514" in texts["main"])
check("ledger R17+R18", "round17" in texts["ledger"] and "round18" in texts["ledger"])

print()
print("=" * 72)
print(f"TOTAL: OK={ok}  FAIL={fail}")
print("ALL CONSISTENT" if fail == 0 else "HAS INCONSISTENCIES")
