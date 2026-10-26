# -*- coding: utf-8 -*-
"""Round-10 main report: refresh Tables 11/14/16/17 from final EFC JSONs
(degradation_summary.json, uncertainty_propagation.json) and fix P77/P154.
"""
from __future__ import annotations

import json
from pathlib import Path

import docx

ROOT = Path(r"d:\matlab\C")
DOC = ROOT / "09_docs" / "工作区全面总结报告.docx"

deg = json.loads((ROOT / "07_results/rectification_v2_stage2c/stage3/degradation_summary.json").read_text(encoding="utf-8"))
unc = json.loads((ROOT / "07_results/rectification_v2_stage2c/stage3/uncertainty_propagation.json").read_text(encoding="utf-8"))
FX = 7.25


ROLE_MAP = {
    "最紧凑": "compact", "平衡型": "balanced", "氢主导": "hydrogen_dominant",
    "电池主导": "battery_dominant", "临界型": "critical",
}


def deg_by_role(role):
    for r in deg["representatives"]:
        if r["role"] == ROLE_MAP.get(role, role):
            return r
    raise KeyError(role)


def unc_key(cn):
    return ROLE_MAP.get(cn, cn)


def set_para(doc, needle, new_text):
    for para in doc.paragraphs:
        if needle in para.text:
            for run in para.runs:
                run.text = ""
            para.runs[0].text = new_text
            print(f"[PARA] {needle[:22]!r} replaced")
            return True
    print(f"[WARN] para not found: {needle!r}")
    return False


def main() -> int:
    doc = docx.Document(str(DOC))

    # ---- Table11: lifetime (EFC degradation schedule) ----
    t11 = doc.tables[11]
    # header: drop legacy column -> 10 columns
    t11.rows[0].cells[1].text = "BAT年损伤"
    t11.rows[0].cells[2].text = "FC年损伤"
    t11.rows[0].cells[3].text = "EL年损伤"
    t11.rows[0].cells[4].text = "BAT寿命(EOL)"
    t11.rows[0].cells[5].text = "FC寿命(EOL)"
    t11.rows[0].cells[6].text = "EL寿命(EOL)"
    t11.rows[0].cells[7].text = "BAT替换"
    t11.rows[0].cells[8].text = "FC替换"
    t11.rows[0].cells[9].text = "EL替换"
    for row in t11.rows[1:]:
        role = row.cells[0].text.strip()
        r = deg_by_role(role)
        ad = r["avg_annual_damage"]
        lt = r["lifetime_years"]
        rc = r.get("eol_events", {}).get("replacement_counts", {})
        row.cells[1].text = f"{ad['bat']:.3f}"
        row.cells[2].text = f"{ad['fc']:.3f}"
        row.cells[3].text = f"{ad['el']:.3f}"
        row.cells[4].text = f"{lt['bat']:.1f}y"
        row.cells[5].text = f"{lt['fc']:.1f}y"
        row.cells[6].text = f"{lt['el']:.1f}y"
        row.cells[7].text = str(rc.get("bat", 0))
        row.cells[8].text = str(rc.get("fc", 0))
        row.cells[9].text = str(rc.get("el", 0))
    print("Table11 refreshed")

    # ---- Table14: economics (EFC) ----
    t14 = doc.tables[14]
    for row in t14.rows[1:]:
        role = row.cells[0].text.strip()
        r = deg_by_role(role)
        ec = r["economics"]
        repl = ec.get("replacements", {})
        row.cells[1].text = f"{ec['total_capex_cny']/1000:.0f}"
        row.cells[2].text = f"{ec['npc_cny']/1000:.0f}"
        row.cells[3].text = f"{ec['lcoe_cny_per_kwh']:.2f}"
        row.cells[4].text = f"{ec['lcoe_usd_per_kwh']:.2f}"
        row.cells[5].text = f"{len(repl.get('BAT', []))}/{len(repl.get('PEMFC', []))}/{len(repl.get('PEMEL', []))}"
    print("Table14 refreshed")

    # ---- Table16: LHS lifetime (EFC params) ----
    t16 = doc.tables[16]
    for row in t16.rows[1:]:
        cfg = unc_key(row.cells[0].text.strip())
        u = unc[cfg]
        bl, fl, el = u["bat_lifetime"], u["fc_lifetime"], u["el_lifetime"]
        row.cells[1].text = f"{bl['p50']:.1f}y"
        row.cells[2].text = f"[{bl['p05']:.1f}, {bl['p95']:.1f}]"
        row.cells[3].text = f"{fl['p50']:.1f}y"
        row.cells[4].text = f"[{fl['p05']:.1f}, {fl['p95']:.1f}]"
        row.cells[5].text = f"{el['p50']:.1f}y"
        row.cells[6].text = f"[{el['p05']:.1f}, {el['p95']:.1f}]"
    print("Table16 refreshed")

    # ---- Table17: LHS LCOE (EFC params) ----
    t17 = doc.tables[17]
    for row in t17.rows[1:]:
        cfg = unc_key(row.cells[0].text.strip())
        lc = unc[cfg]["lcoe"]
        row.cells[1].text = f"{lc['p50']:.3f}"
        row.cells[2].text = f"[{lc['p05']:.3f}, {lc['p95']:.3f}]"
        row.cells[3].text = f"{lc['p50']/FX:.3f}"
        row.cells[4].text = f"[{lc['p05']/FX:.3f}, {lc['p95']/FX:.3f}]"
    print("Table17 refreshed")

    # ---- P77 heading ----
    set_para(doc, "情景寿命估计（开环BOL调度 + EOL事件模型）：",
             "情景寿命估计（EFC 循环损伤模型 + EOL 事件模型，退化调度；数据源 degradation_summary.json）：")

    # ---- P154 progress: execution done ----
    set_para(doc, "嵌套双层传播执行中",
             "执行顺序进度（BAT-EFC 循环损伤升级已完成；EOL/经济已重跑；六路径单位已统一；"
             "嵌套双层传播已完成（256×4000，单年气象窗口））：")

    doc.save(str(DOC))
    print("saved:", DOC)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
