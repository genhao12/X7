# -*- coding: utf-8 -*-
"""Generate 09_docs/02_治理与口径/13_门控通过路径与BAT-EFC升级方案.docx."""
from __future__ import annotations

from pathlib import Path

import docx
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt

DOC = Path(r"d:\matlab\C\09_docs\02_治理与口径/13_门控通过路径与BAT-EFC升级方案.docx")

INPUT_HASH = "86e12930b6b08cab63dd00e12d5e00ab5afd0a3d1cc6e1122c90b1b52d207dd3"
CONFIG_HASH = "11ed37be41737aaba1558b2eed366457d50724c73e9ac7094ea2f9afd249965f"
SCRIPT_HASH = "42c21ad0a5e6f8451956da552455bf580305709b3ccad85c62c44c00b0b7cb11"


def h1(doc, text):
    p = doc.add_heading(text, level=1)
    return p


def h2(doc, text):
    return doc.add_heading(text, level=2)


def para(doc, text, bold=False):
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.bold = bold
    return p


def table(doc, headers, rows):
    tb = doc.add_table(rows=1 + len(rows), cols=len(headers))
    tb.style = "Light Grid Accent 1"
    for ci, htxt in enumerate(headers):
        tb.rows[0].cells[ci].text = htxt
    for ri, row in enumerate(rows):
        for ci, cell in enumerate(row):
            tb.rows[ri + 1].cells[ci].text = cell
    return tb


def main() -> int:
    doc = docx.Document()

    # Title
    t = doc.add_heading("门控通过路径与 BAT-EFC 升级方案", level=0)
    t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub = doc.add_paragraph()
    run = sub.add_run("单一真值源一致性门控 · 执行前置方案　|　生成日期：2026-08-04 16:51")
    run.italic = True
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER

    para(doc, "本方案说明离网光伏–氢–电池混合微网 Stage2c 下游门控（downstream_gate.json .4，"
              "见 07_results/rectification_v2_stage2c/governance/）从 gate_passed=False 翻转至 True 的必经路径。"
              "门控是“证据门”而非“开关门”：任何标志不能直接手改 JSON 强行通过，必须在对应执行完成并产出"
              "可验证结果后，由治理流程重新生成治理文件时翻转。本方案与《统一结果台账.docx》同为单一真值源，"
              "任何数值若冲突以台账为准。")

    # 一、当前门控状态
    h1(doc, "一、当前门控状态（2026-08-04）")
    table(doc, ["字段", "值", "语义"],
          [
              ["gate_passed", "False", "所有执行/论文标志为 False，尚未通过"],
              ["verified_admitted_count", "149", "Stage2c 扩展域（PV 350–500 kWp）准入组数"],
              ["max_frozen_representatives", "5", "冻结代表配置数（compact/balanced/hydrogen_dominant/battery_dominant/critical）"],
              ["stage3_basic_degradation_allowed", "True", "仅规划级许可，非已执行/非论文可引用"],
              ["robust_six_path_planning_allowed", "True", "仅规划级许可，非已执行/非论文可引用"],
              ["degradation_execution_started", "False", "正式退化执行未启动（degradation_summary.json 仅方法层落地）"],
              ["six_path_execution_started", "False", "六路径正式执行未启动（cross_device_paths.json 仅方法层落地）"],
              ["shapley_allowed", "False", "门控未过（单位未统一，量级不可比）"],
              ["l4_allowed", "False", "门控未过（Stage2c 上为否定性结果，4/5 配置增强耦合）"],
              ["economics_allowed", "False", "门控未过（成本敏感性 80 组为方法层/情景）"],
              ["double_layer_uncertainty_allowed", "False", "门控未过（仅 800 样本原型，真实嵌套 minimum_inner_samples=4000 未执行）"],
              ["legacy_results_paper_citable", "False", "永久 False（旧 C1 等结果已标记 historical invalid，不翻转为可引用）"],
              ["upstream_gate.main_domain_failed", "True", "Stage0-2 主域（PV 160–320 kWp）0 准入 → 主域下游物理推断冻结"],
              ["upstream_gate.stage2c_admission_passed", "True", "Stage2c 扩展域 149 准入 / 5 冻结代表（两事实不得合并）"],
          ])

    # 二、标志通过条件与翻转机制
    h1(doc, "二、标志通过条件与翻转机制")
    para(doc, "以下 False 标志不能手改。每个标志对应一项必须完成的工作；完成后由治理流程重新生成 "
              "downstream_gate.json / run_manifest.json / hash_record.json，将对应标志置 True，并写入证据"
              "（产物路径、输入哈希、测试通过数、闭合残差）。")
    table(doc, ["标志", "当前值", "通过条件（须完成的工作）", "前置依赖"],
          [
              ["degradation_execution_started", "False", "正式跑完 Stage3 基础退化计算，产出论文可引用结果", "—"],
              ["six_path_execution_started", "False", "补齐六路径 D∈{0.3,0.5,0.7} 三水平 + 统一单位（ΔD/yr 与百分比归一化）", "①"],
              ["shapley_allowed", "False", "单位统一后重跑 Shapley C/P 分解（4 联盟），闭合误差<1e-15", "②"],
              ["l4_allowed", "False", "改进 L4 规则（如基于实时 DOD 的自适应 SOC）并在 5 配置上重跑消融证明有效；否则保持 False 并在论文中如实报告", "独立"],
              ["economics_allowed", "False", "BAT-EFC 升级后重跑 EOL/NPC/LCOE + 成本敏感性（5×16=80 组）", "①"],
              ["double_layer_uncertainty_allowed", "False", "真实嵌套双层：外层 Sobol × 内层季节移动块 Bootstrap（minimum_inner_samples=4000）", "①"],
              ["legacy_results_paper_citable", "False", "永久 False，不翻转", "—"],
              ["gate_passed", "False", "上述需要执行的项目全部完成且结果可引用时置 True", "全部"],
          ])

    # 三、执行顺序与产物要求
    h1(doc, "三、执行顺序与产物要求（门控通过后按序执行）")
    para(doc, "① BAT 循环损伤模型升级：BATKCYC_H 运行小时代理 → EFC/能量吞吐（基于 DOD/能量吞吐的循环损伤）模型。"
              "交付：模型代码 + TDD 测试 + 新产物 JSON；完成后更新 degradation_execution_started=True。")
    para(doc, "② 重跑 EOL 寿命与 NPC/LCOE（M4 经济）。交付：更新后 lifetime/economics 产物；完成后更新 economics_allowed=True。")
    para(doc, "③ 统一六路径效应单位及三退化水平（D∈{0.3,0.5,0.7}）→ 重跑跨设备路径。交付：cross_device_paths_v2.json；"
              "完成后更新 six_path_execution_started=True、shapley_allowed=True。")
    para(doc, "④ 启动外层 Sobol × 内层季节移动块 Bootstrap（minimum_inner_samples=4000）。交付：double_layer_full.json；"
              "完成后更新 double_layer_uncertainty_allowed=True。")
    para(doc, "⑤ 成本敏感性与论文图表（F1–F5、T1–T5）定稿。交付：更新后 figures/tables；最终 gate_passed=True"
              "（若 L4 仍为否定性则 l4_allowed 保持 False，并在论文中如实报告）。")

    # 四、治理更新流程
    h1(doc, "四、治理更新流程（每步完成后）")
    for i, step in enumerate([
        "重算并核对契约三哈希（input=sha256(64 号 CSV)=%s；config=sha256(rectification_v2_capacity_gate.json)=%s；script=sha256(build_stage02_governance.py)=%s）" % (INPUT_HASH[:12] + "…", CONFIG_HASH[:12] + "…", SCRIPT_HASH[:12] + "…"),
        "重新生成 downstream_gate.json / run_manifest.json / hash_record.json（沿用 regen_stage2c_governance_round5.py 模式）",
        "同步《统一结果台账.docx》：Table2 门控原文 + Table3 修正记录 + 末尾核验段",
        "运行一致性验证脚本（verify_*.py），全部 PASS",
        "更新 09_docs/02_治理与口径/02_变更日志_change_log.md，记录证据",
    ], start=1):
        para(doc, f"{i}. {step}")

    # 五、门控通过前唯一允许的工作
    h1(doc, "五、门控通过前唯一允许的工作")
    para(doc, "1. BAT-EFC 模型规格规划：能量吞吐/DOD 依赖、替换年语义、与 Stage3 EOL 事件模型接口。")
    para(doc, "2. TDD 用例编写（可新建 test_bat_efc_energy_throughput.py 等，只编写不运行重算）：验证 EFC 模型用"
              "能量吞吐/DOD 计算循环损伤、与 BATKCYC_H 运行小时代理在基准工况下的一致性边界。")

    # 六、禁止事项
    h1(doc, "六、禁止事项")
    for item in [
        "不得直接手改 downstream_gate.json 强行翻转标志。",
        "不得在门控通过前启动 EOL 寿命、NPC/LCOE、六路径、嵌套双层的结果重算。",
        "不得将方法层/原型结果（单层 LHS、成本敏感性 80 组、双层 800 样本原型、L4 消融）作为论文结论引用。",
        "不得断言 α₁/β₁ 与跨设备路径的量级主导（单位未统一）。",
    ]:
        para(doc, "• " + item)

    # 七、门控通过判定清单
    h1(doc, "七、门控通过判定清单（checklist）")
    for item in [
        "[ ] BAT-EFC 模型已实现并通过 TDD",
        "[ ] EOL / NPC / LCOE 已重跑且结果落地",
        "[ ] 六路径单位统一 + 三退化水平（0.3/0.5/0.7）已补齐",
        "[ ] 嵌套双层 minimum_inner_samples=4000 已执行",
        "[ ] 成本敏感性 5×16=80 组已重算",
        "[ ] 各标志 True 均有证据（产物路径 + 哈希 + 测试通过数 + 闭合残差）",
        "[ ] 三份文档与台账一致，一致性验证脚本全 PASS",
        "[ ] gate_passed=True（或列出仍为 False 的项及其论文处理方式）",
    ]:
        para(doc, item)

    tail = doc.add_paragraph()
    run = tail.add_run("注：本方案与《统一结果台账.docx》为单一真值源；所有门控字段直接取自 "
                       "downstream_gate.json(.4) / run_manifest.json(.3) / hash_record.json(.3)。")
    run.italic = True

    doc.save(str(DOC))
    print("saved:", DOC)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
