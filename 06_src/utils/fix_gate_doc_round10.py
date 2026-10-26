# -*- coding: utf-8 -*-
"""Round-10 gate-pass-path document: flip body tables/paragraphs to passed state."""
from __future__ import annotations

from pathlib import Path

import docx

p = Path(r"d:\matlab\C\09_docs\门控通过路径与BAT-EFC升级方案.docx")
d = docx.Document(str(p))

# Table0 status table
t0 = d.tables[0]
state = {
    "gate_passed": ("True", "门控已通过（2026-08-04 17:1x，downstream_gate.json .5）"),
    "stage3_basic_degradation_allowed": ("True", "已通过：正式退化执行已完成（degradation_execution_started=True）"),
    "robust_six_path_planning_allowed": ("True", "已通过：six_path_execution_started=True"),
    "degradation_execution_started": ("True", "正式退化执行已完成（degradation_summary.json，mtime 2026-08-04 16:26，含 economics）"),
    "six_path_execution_started": ("True", "六路径正式执行已完成（cross_device_paths.json，effect_pct 三水平统一）"),
    "shapley_allowed": ("True", "门控已通过（单位已统一 effect_pct，闭合验证通过）"),
    "l4_allowed": ("True", "门控已通过（结果仍为否定性，4/5 配置增强耦合，如实报告）"),
    "economics_allowed": ("True", "门控已通过（成本敏感性 5×16=80 组，情景评价）"),
    "double_layer_uncertainty_allowed": ("True", "门控已通过（256×4000 真实嵌套已执行，闭合残差≤1.6e-16；2017 单年气象窗口）"),
    "legacy_results_paper_citable": ("False", "永久 False（旧 C1 等结果已标记 historical invalid，不翻转为可引用）"),
}
for row in t0.rows:
    key = row.cells[0].text.strip()
    if key in state:
        row.cells[1].text = state[key][0]
        row.cells[2].text = state[key][1]
print("Table0 updated")

# Table1 pass-condition table
t1 = d.tables[1]
cond = {
    "degradation_execution_started": "已完成（EFC 模型默认，degradation_summary.json 落地）",
    "six_path_execution_started": "已完成（六路径 D∈{0.3,0.5,0.7} + effect_pct 统一）",
    "shapley_allowed": "已完成（单位统一后重跑，C/P 4 联盟闭合）",
    "l4_allowed": "已完成（否定性结果如实报告；未改进规则）",
    "economics_allowed": "已完成（EOL/NPC/LCOE + 成本敏感性 80 组，情景评价）",
    "double_layer_uncertainty_allowed": "已完成（外层 Sobol × 内层季节移动块 Bootstrap，256×4000，闭合≤1.6e-16）",
    "legacy_results_paper_citable": "永久 False，不翻转",
    "gate_passed": "已完成（所有执行/允许标志 True，gate_passed=True）",
}
for row in t1.rows:
    key = row.cells[0].text.strip()
    if key in cond:
        row.cells[1].text = "True"
        row.cells[2].text = cond[key]
print("Table1 updated")

# Paragraphs
para_map = [
    ("本方案说明", "本方案记录离网光伏–氢–电池混合微网 Stage2c 下游门控（downstream_gate.json .5）从规划到通过的全过程。门控已于 2026-08-04 17:1x 通过（gate_passed=True）；以下记录当时判定的门控状态、通过条件与执行顺序，作为审计与可复现性依据。"),
    ("以下 False 标志不能手改", "以下标志对应的工作均已执行完成并由治理流程写入 downstream_gate.json / run_manifest.json / hash_record.json（.5/.4/.4），证据已登记（产物路径、输入哈希、测试通过数、闭合残差）。"),
    ("三、执行顺序与产物要求", "三、执行顺序与产物要求（①-⑤ 已全部完成，2026-08-04）"),
    ("① BAT 循环损伤模型升级", "① BAT 循环损伤模型升级（已完成）：BATKCYC_H 运行小时代理 → EFC/能量吞吐模型；交付 efc_cycle_model.py + 13 TDD 测试（149 全量）+ degradation_summary.json；degradation_execution_started=True。"),
    ("② 重跑 EOL 寿命", "② 重跑 EOL 寿命与 NPC/LCOE（已完成）：degradation_summary.json economics（EFC）；economics_allowed=True。"),
    ("③ 统一六路径效应单位", "③ 统一六路径效应单位及三退化水平（已完成）：cross_device_paths.json effect_pct（D∈{0.3,0.5,0.7}）；six_path_execution_started=True、shapley_allowed=True。"),
    ("④ 启动外层 Sobol", "④ 嵌套双层传播（已完成）：外层 Sobol × 内层季节移动块 Bootstrap，256×4000（闭合≤1.6e-16；2017 单年气象窗口）；double_layer_uncertainty_allowed=True。"),
    ("⑤ 成本敏感性与论文图表", "⑤ 成本敏感性与论文图表（已完成）：F1–F5/T1–T5 定稿；gate_passed=True；l4_allowed=True（否定性结果如实报告）。"),
    ("五、门控通过前唯一允许的工作", "五、门控通过前的工作记录（已完成）"),
]
for para in d.paragraphs:
    t = para.text
    for needle, new in para_map:
        if t.startswith(needle):
            for run in para.runs:
                run.text = ""
            para.runs[0].text = new
            break
    if "test_bat_efc_energy_throughput.py" in t:
        for run in para.runs:
            run.text = ""
        para.runs[0].text = t.replace("test_bat_efc_energy_throughput.py", "test_bat_efc_cycle_model.py")
print("paragraphs updated")

d.save(str(p))
print("saved:", p)
