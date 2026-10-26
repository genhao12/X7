# -*- coding: utf-8 -*-
"""Round-4 consistency fixes for 工作区运行结果汇总.docx (v2, robust).

Add "方法层" qualifier to Stage3 method-layer PASS rows, locating the
"是否通过" row by its row-label (not hard-coded index).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import docx

DOC = Path(r"d:\matlab\C\09_docs\工作区运行结果汇总.docx")
BACKUP = Path(r"d:\matlab\C\09_docs\工作区运行结果汇总_backup_20260804_round4.docx")

NEW_PASS = {
    "run_degradation_uncertainty.py": "方法层通过（128 点 LHS 经验 90% 不确定性区间已给出；闭合性自检）",
    "run_cost_sensitivity.py": "方法层通过（基线复现 Stage3 相对偏差 <1e-9；单价单调；全设备包络线性；economics_allowed=False 不可引用）",
    "run_cross_device_paths.py": "方法层通过（六路径覆盖 6/6、七健康 7/7、数值泄漏残差 0；shapley_allowed=False 不可引用）",
}


def main() -> int:
    if not BACKUP.exists():
        shutil.copy2(DOC, BACKUP)
    doc = docx.Document(str(DOC))
    hits = 0
    for tb in doc.tables:
        if len(tb.rows) < 3:
            continue
        # find script row
        script = ""
        for row in tb.rows:
            if row.cells[0].text.strip() == "脚本" and len(row.cells) > 1:
                script = row.cells[1].text
                break
        if not script:
            continue
        for key, new_txt in NEW_PASS.items():
            if key in script:
                # find "是否通过" row
                for row in tb.rows:
                    if row.cells[0].text.strip() == "是否通过" and len(row.cells) > 1:
                        old = row.cells[1].text
                        row.cells[1].text = new_txt
                        print(f"  [OK] {key}: {old[:30]!r} -> {new_txt[:30]!r}")
                        hits += 1
                        break
    print("hits:", hits)
    if hits != len(NEW_PASS):
        print("WARNING: expected", len(NEW_PASS), "fixes, got", hits)
    doc.save(str(DOC))
    print("saved:", DOC)
    return 0 if hits == len(NEW_PASS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
