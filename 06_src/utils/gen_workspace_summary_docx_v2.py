# -*- coding: utf-8 -*-
"""生成工作区全面总结报告 Word 文档（修正版）"""
from pathlib import Path
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

doc = Document()
for section in doc.sections:
    section.top_margin = Cm(2.5); section.bottom_margin = Cm(2.5)
    section.left_margin = Cm(2.5); section.right_margin = Cm(2.5)
style = doc.styles['Normal']; style.font.name = 'Times New Roman'; style.font.size = Pt(11)
style.paragraph_format.space_after = Pt(6); style.paragraph_format.line_spacing = 1.15
for i in range(1, 4):
    hs = doc.styles[f'Heading {i}']; hs.font.name = 'Times New Roman'
    hs.font.color.rgb = RGBColor(0x1F, 0x49, 0x7D)

def add_table(headers, rows):
    table = doc.add_table(rows=1, cols=len(headers), style='Light Grid Accent 1')
    for j, h in enumerate(headers):
        table.rows[0].cells[j].text = h
        for p in table.rows[0].cells[j].paragraphs:
            for r in p.runs: r.font.bold = True; r.font.size = Pt(9)
    for row_data in rows:
        row = table.add_row()
        for j, val in enumerate(row_data):
            row.cells[j].text = str(val)
            for p in row.cells[j].paragraphs:
                for r in p.runs: r.font.size = Pt(9)

# ===== 封面 =====
title = doc.add_heading('', level=0); title.alignment = WD_ALIGN_PARAGRAPH.CENTER
run = title.add_run('离网光伏-氢-电池混合储能微电网\n系统级退化耦合方法与应用研究\n工作区全面总结报告（修正版）')
run.font.size = Pt(20); run.font.color.rgb = RGBColor(0xC0, 0x00, 0x00)
doc.add_paragraph()
p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
p.add_run('生成日期：2026-08-02\n文档定位：指导修正实施，尚不适合作为论文结果依据').font.size = Pt(12)
doc.add_page_break()

# ===== 第一章 =====
doc.add_heading('第一章 项目概述与文档定位', level=1)
doc.add_paragraph(
    '本项目为离网光伏-氢-电池混合储能微电网的系统级退化耦合方法与情景研究。'
    '本报告为外部审查触发根本性修正后的内部审计总结。'
    '修正前结论"无附加条件进入论文撰写"已被撤销，因存在LPSP=44%远超≤1%约束、幻影损伤保留错误代码、'
    '零路径为EMS结构预设、总损伤求和无物理意义等根本性缺陷。'
)
doc.add_heading('1.1 修正前后对比', level=2)
add_table(
    ['维度', '修正前(已撤销)', '修正后(当前)'],
    [
        ['基线配置', 'C1(PV120/BAT135/FC37.5/EL35/H2_25)', 'C5_feasible(PV300/BAT800/FC100/EL100/H2_1000)'],
        ['LPSP', '41.91-47.79%(远超≤1%)', '0.23%(≤1%, PASS)'],
        ['EOL守卫', '无(保留错误代码,文档层缓解)', '已实现(D≥1.0→dD=0,代码层修复)'],
        ['TE报告', '总TE单一数字(+6.59%)', '设备级TE向量[-0.15%,-2.30%,+25.10%]'],
        ['PEMFC证据', 'A级', 'C级(1kW→100kW线性外推)'],
        ['PEMEL证据', 'A级', 'B/C级(实验室→100kW线性假设)'],
        ['文档定位', '"无附加条件进入论文撰写"', '内部审计总结,残余工作待完成'],
    ]
)

doc.add_heading('1.2 外部审查指出的6项根本性缺陷', level=2)
defects = [
    'LPSP=41.91-47.79%远超原方案≤1%的可靠性约束,容量与EMS基线不可行',
    'FC/EL损伤超过EOL后仍继续累计形成"幻影损伤",保留错误代码以维持Shapley闭合不可接受',
    '1.55×10⁻¹⁶只能证明计算恒等式正确,不能证明物理模型可信',
    '四条零路径源于EMS只响应BAT退化,属于程序结构预设,不能表述为反事实辨识发现',
    '不同物理含义的三种归一化损伤直接求和、1.1年BAT寿命及10年内9/13/18次更换,使寿命与经济结果缺乏工程意义',
    'L4全反馈收益仅0.04个百分点,只能称概念验证;PEMFC/PEMEL迁移后参数证据应降为B/C',
]
for i, d in enumerate(defects, 1):
    doc.add_paragraph(f'{i}. {d}')
doc.add_page_break()

# ===== 第二章 =====
doc.add_heading('第二章 根本性修正措施', level=1)

doc.add_heading('2.1 修正1：容量配置调整（LPSP≤1%，超大容量兜底）', level=2)
doc.add_paragraph(
    '根因发现：EMS的p_aux = 2% × installed_power逻辑导致PV增大时辅机功耗膨胀'
    '（PV=720kWp时p_aux=28.5kW，占夜间负荷65%）。'
    '修复为p_aux = 2% × max(BAT功率, FC功率, EL功率)后，C1的LPSP从44%降至39%，但仍不满足。'
    'C5_feasible配置(PV=300/BAT=800/FC=100/EL=100/H2=1000)实现LPSP=0.23%，'
    '但属于超大容量兜底，明显超出原搜索范围（原范围PV 90-150/BAT 90-180/FC 30-45/EL 25-45/H2 10-40）。'
    '需验证多组可行容量，确认结果对配置的稳健性。'
)
add_table(
    ['参数', 'C1(原配置)', 'C5_feasible(修正)', '放大倍数'],
    [
        ['PV_kWp', '120', '300', '2.5x'],
        ['BAT_kWh', '135', '800', '5.9x'],
        ['PEMFC_kW', '37.5', '100', '2.7x'],
        ['PEMEL_kW', '35', '100', '2.9x'],
        ['H2_kg', '25', '1000', '40x'],
        ['LPSP(%)', '44.40', '0.23', '—'],
        ['约束满足', 'FAIL', 'PASS', '—'],
    ]
)

doc.add_heading('2.2 修正2：EOL守卫实现（消除幻影损伤，但FC退化标定仍不可信）', level=2)
doc.add_paragraph(
    '代码修改：closed_loop_lite.py:compute_damage()添加EOL守卫——当D_state≥1.0时dD=0。'
    'BOL基线修正：BOL基线也累积退化（D_state更新），使EOL守卫在BOL模式下也触发。'
    '此前BOL基线D始终保持0，导致EOL守卫不触发，FC的BOL损伤=2.38（含幻影）。'
)
doc.add_paragraph(
    '关键认知修正：此前"保留错误代码以维持Shapley闭合"的决定是错误的。'
    '1.55×10⁻¹⁶的闭合残差只证明计算恒等式正确，不证明物理模型可信。'
    'EOL守卫使Shapley路径效应分解的损伤差分反映真实退化传导，需重新验证闭合性。'
)
doc.add_paragraph(
    '残余问题：BOL燃料电池一年内即到EOL（D=1.0），说明FC退化标定仍不可信。'
    'FC退化模型基于IEEE PHM 2014 1kW堆数据线性外推至100kW（C级证据），退化速率可能被高估。'
    '需补充系统级FC退化数据或调整退化模型参数。'
)

doc.add_heading('2.3 修正3：设备级TE向量替代总损伤求和', level=2)
doc.add_paragraph(
    '修正前：总损伤 = BAT + FC + EL（三种不同归一化量直接求和），TE_total = +6.59%。'
    '修正后：设备级TE向量 TE = [TE_BAT, TE_FC, TE_EL]，每个分量有独立物理含义。'
    '不再使用单一"总TE"数字，避免量纲混合的误导。'
)

doc.add_heading('2.4 修正4：PEMFC/PEMEL证据等级降级', level=2)
add_table(
    ['设备', '原始数据证据', '迁移后证据', '降级原因'],
    [
        ['BAT(LFP)', 'A级(280Ah实测+BLAST-Lite)', 'A级(不降级)', '有交叉验证(Q dev=11.9%)'],
        ['PEMFC', 'A级(IEEE PHM 2014 1kW堆)', 'C级', '1kW→100kW线性外推,无系统级验证'],
        ['PEMEL', 'A级(NAICNO BOL参数)', 'B/C级', '实验室→100kW线性假设,无长时老化数据'],
    ]
)

doc.add_heading('2.5 修正5：文档重定位', level=2)
doc.add_paragraph(
    '所有审查报告从"无附加条件进入论文撰写"重定位为"内部审计总结，尚不适合作为科学验收结论"。'
    '在重跑全部仿真并重新验证Shapley闭合、路径辨识、经济评价之前，不适合作为论文撰写依据。'
)
doc.add_page_break()

# ===== 第三章 =====
doc.add_heading('第三章 修正后P1年度仿真结果', level=1)
doc.add_paragraph('配置：C5_feasible + EOL守卫 + 2017年UCSD数据')

add_table(
    ['指标', 'BOL基线', 'BAT-only反馈', '全设备反馈', 'TE_BO(%)', 'TE_FF(%)'],
    [
        ['BAT损伤', '0.736', '0.735', '0.735', '-0.15', '-0.15'],
        ['FC损伤', '1.037(封顶)', '1.013(封顶)', '1.013(封顶)', '-2.30', '-2.29'],
        ['EL损伤', '0.167', '0.209', '0.210', '+25.10', '+25.66'],
        ['LPSP(%)', '0.23', '0.24', '0.28', '—', '—'],
        ['D年末FC', '1.000', '1.000', '1.000', '封顶', '封顶'],
        ['D年末EL', '0.167', '0.209', '0.210', '远未达EOL', '远未达EOL'],
    ]
)

doc.add_heading('3.1 关键发现（单配置初步信号，非确认结论）', level=2)
findings = [
    'LPSP=0.23% ≤ 1%，基线可行性约束满足（仅C5单配置，需验证多组可行容量）',
    'FC在BOL和反馈模式下均达EOL(D=1.0)，EOL守卫使两者损伤差异很小(1.037 vs 1.013)。但BOL FC一年内即到EOL，说明FC退化标定仍不可信（C级证据，退化速率可能被高估）',
    'EL在年内远未达EOL(D=0.17-0.21)，TE_EL=+25.10%为真实退化传导效应的初步信号(无幻影)',
    'BAT退化反馈对EL有正传导效应的初步信号(+25%)，BAT→EL路径为单配置初步信号（非确认结论，待六路径/Shapley/跨年重跑后确认）',
    'BAT退化反馈对BAT自身(TE=-0.15%)和FC(TE=-2.30%)有微弱负效应的初步信号',
    '设备级TE向量[-0.15%, -2.30%, +25.10%]比此前总TE=+6.59%更有物理意义（但基于单一可行配置，需多配置验证稳健性）',
]
for f in findings:
    doc.add_paragraph(f, style='List Bullet')

doc.add_heading('3.2 与修正前对比', level=2)
add_table(
    ['指标', '修正前(C1,无EOL守卫)', '修正后(C5+EOL守卫)', '变化'],
    [
        ['LPSP(%)', '44.40(FAIL)', '0.23(PASS)', '根本改善'],
        ['FC年末D', '1.000(含幻影)', '1.000(EOL封顶)', '消除幻影'],
        ['EL年末D', '1.000(含幻影)', '0.209(真实)', '消除幻影'],
        ['TE报告方式', '总TE=+6.59%(单一数字)', 'TE向量[-0.15,-2.30,+25.10]', '物理意义提升'],
        ['BAT TE', '+1.13%(主结论)', '-0.15%(设备级分量)', '方向变化'],
    ]
)
doc.add_page_break()

# ===== 第四章 =====
doc.add_heading('第四章 残余工作（进入论文撰写前必须完成）', level=1)
doc.add_paragraph('以下7项工作必须全部完成后，方可重新评估是否进入论文撰写阶段：')

add_table(
    ['编号', '残余工作', '原因', '工作量'],
    [
        ['R1', '验证多组可行容量', 'C5为超大容量兜底,超出原搜索范围,需确认结果对配置的稳健性', '中(多组容量搜索)'],
        ['R2', '重跑全部仿真(P1-P5/VS1-VS8/G1-G14/L4/M4)', '可行配置+EOL守卫改变了所有基础数据', '高(420+次运行)'],
        ['R3', '全设备反馈下重新辨识六路径', '区分结构性零路径(EMS不响应)与统计零路径', '中'],
        ['R4', '重算Shapley路径效应分解', 'EOL守卫改变损伤差分性质,闭合性需重新验证', '中'],
        ['R5', '重算经济评价(NPC/LCOE)', '可行配置CAPEX远高于C1', '中'],
        ['R6', '跨年稳定性验证', '可行配置在2016/2018年的LPSP和TE需验证', '中'],
        ['R7', 'FC退化标定可信度评估', 'BOL FC一年内即到EOL,退化速率可能被高估(C级证据)', '高(需补充数据或调整模型)'],
    ]
)

doc.add_heading('4.1 R2: 结构性零路径 vs 统计零路径', level=2)
doc.add_paragraph(
    '修正前：四条零路径(FC→BAT/EL→BAT/EL→FC/FC→EL)被表述为"反事实辨识发现"。'
    '实际为EMS结构预设——battery_first策略仅响应BAT退化，FC/EL退化不反馈到调度。'
    '修正后：在全设备反馈模式下重新辨识，区分：'
)
add_table(
    ['路径类型', '定义', '修正后处理'],
    [
        ['结构性零路径', 'EMS不响应该设备退化(策略预设)', '如实声明为EMS设计限制,非辨识发现'],
        ['统计零路径', '全设备反馈下仍为零(无退化传导)', '作为反事实辨识发现报告'],
    ]
)
doc.add_page_break()

# ===== 第五章 =====
doc.add_heading('第五章 目录结构与文件清单', level=1)

doc.add_heading('5.1 项目目录', level=2)
add_table(
    ['目录', '内容'],
    [
        ['01_data_raw/', '原始模板+literature_extracts(19文件)'],
        ['02_data_processed/', 'tradestreet_pipeline(UCSD微网,76 CSV)'],
        ['03_coupling/', '六候选耦合路径'],
        ['04_models/', '退化模型+XGBoost'],
        ['05_config/', '冻结参数+C1/C5配置+经济参数(新增c5_feasible_config.json)'],
        ['06_src/', '组件/EMS/辨识/仿真/经济+utils(59 py)'],
        ['07_results/', '年度/寿命/情景/Shapley/图表/经济(新增p1_revised_c5_eolguard.csv)'],
        ['08_tests/', '单元/集成/回归+archive(50 py)'],
        ['09_docs/', '审计/限制/验证报告+方案+论文规范(新增03_核验与审计/09_审计_内部总结_修正版.md)'],
        ['数据文献/', '547文件参考库'],
    ]
)

doc.add_heading('5.2 本轮修正新增/修改文件', level=2)
add_table(
    ['文件', '类型', '内容'],
    [
        ['05_config/c5_feasible_config.json', '新增', 'C5可行配置(LPSP=0.23%)'],
        ['06_src/simulation/search_feasible_config2.py', '新增', '容量搜索脚本'],
        ['06_src/simulation/run_p1_revised.py', '新增', '修正后P1仿真(C5+EOL守卫)'],
        ['07_results/annual/p1_revised_c5_eolguard.csv', '新增', '修正后P1结果'],
        ['06_src/ems/ems_bf_lite.py', '修改', 'p_aux修复(基于运行功率非装机)'],
        ['06_src/simulation/closed_loop_lite.py', '修改', 'EOL守卫(D≥1.0→dD=0)'],
        ['09_docs/03_核验与审计/09_审计_内部总结_修正版.md', '新增', '内部审计总结(本报告md版)'],
    ]
)
doc.add_page_break()

# ===== 第六章 =====
doc.add_heading('第六章 Limitations更新', level=1)
doc.add_paragraph(
    '02_治理与口径/01_限制声明_limitations.md已从20项(L1-L20)扩展并修正。修正前的L13(幻影损伤)/L19(代码层缓解)/L20(FC/EL双重不确定性)'
    '已被代码层EOL守卫替代。新增限制项反映修正后的状态：'
)
add_table(
    ['编号', '内容', '状态'],
    [
        ['L1-L12', '原有限制(反馈范围/配置特异性/绝对寿命/量纲等)', '保留,需基于C5重跑后更新'],
        ['L13(原)', '幻影损伤(FC/EL TE 100%幻影驱动)', '已被EOL守卫修复(代码层)'],
        ['L14-L17(原)', 'L4残余/LPSP修正/EL替换/冷启动', '保留,需基于C5重跑后更新'],
        ['L18(原)', 'L4全反馈效应极小(0.04pp)', '待C5重跑后重新评估'],
        ['L19(原)', '幻影损伤代码层缓解策略', '已被EOL守卫替代(不再需要文档层缓解)'],
        ['L20(原)', 'FC/EL双重不确定性', '保留,PEMFC降为C级/PEMEL降为B/C级'],
        ['L21(新)', 'C5配置CAPEX远高于C1', '经济评价需重新计算'],
        ['L22(新)', '结构性零路径(EMS设计限制)', '需在全设备反馈下重新辨识'],
    ]
)
doc.add_page_break()

# ===== 第七章 =====
doc.add_heading('第七章 方法论框架（保留，待重新验证）', level=1)
doc.add_paragraph(
    '方法论框架本身设计合理，但所有验证结果需基于C5配置+EOL守卫重新运行后方可确认：'
)
doc.add_heading('7.1 五步因果链', level=2)
doc.add_paragraph('健康-性能动态接口 → 六候选闭环反事实路径 → Shapley路径效应分解 → 稀疏激活 → 决策偏差', style='Intense Quote')

doc.add_heading('7.2 六候选路径（待全设备反馈下重新辨识）', level=2)
add_table(
    ['路径', '方向', '修正前状态', '修正后待验证'],
    [
        ['α₁', 'BAT→FC', '激活', '待重跑验证(修正后P1显示-2.30%初步信号)'],
        ['β₁', 'BAT→EL', '激活', '待重跑验证(修正后P1显示+25.10%初步信号,可能为主路径)'],
        ['α₂', 'FC→BAT', '零路径(结构预设)', '需全设备反馈下重新辨识'],
        ['β₂', 'EL→BAT', '零路径(结构预设)', '需全设备反馈下重新辨识'],
        ['γ₁', 'FC→EL', '零路径(结构预设)', '需全设备反馈下重新辨识'],
        ['γ₂', 'EL→FC', '零路径(结构预设)', '需全设备反馈下重新辨识'],
    ]
)

doc.add_heading('7.3 代码实现', level=2)
add_table(
    ['模块', '路径', '修正内容'],
    [
        ['组件层', '06_src/components/', 'hp_bat/fc/el_lite.py(健康-性能映射,不变)'],
        ['EMS层', '06_src/ems/', 'ems_bf_lite.py(p_aux修复)+ems_l4_lite.py'],
        ['仿真层', '06_src/simulation/', 'closed_loop_lite.py(EOL守卫)+run_p1_revised.py'],
        ['辨识层', '06_src/identification/', 'mediation_lite.py(待Shapley重算)'],
        ['经济层', '06_src/economics/', 'npc_lite.py(待C5经济重算)'],
    ]
)
doc.add_page_break()

# ===== 第八章 =====
doc.add_heading('第八章 审查演进与文档定位', level=1)

doc.add_heading('8.1 审查演进（5次）', level=2)
add_table(
    ['审查', '日期', '结论', '关键发现/动作'],
    [
        ['复审', '2026-08-01', '通过', 'H1幻影损伤未发现'],
        ['审计后独立审查', '2026-08-02上午', '部分通过', '发现3关键缺口'],
        ['更新版', '2026-08-02下午', '通过', '3缺口修补(文档层)'],
        ['最终版', '2026-08-02', '通过', '3残余问题修复(论文撰写规范)'],
        ['外部审查', '2026-08-02', '撤销', '6项根本性缺陷,撤销"通过"结论'],
    ]
)

doc.add_heading('8.2 外部审查的纠正', level=2)
doc.add_paragraph(
    '外部审查准确指出：修正前的"通过"结论基于文档层缓解而非代码层修复，'
    '且基线LPSP=44%不可行使所有结果失去工程意义。'
    '文档层缓解（TE双报告/L4分级表述/证据标注）不能替代代码层修复（EOL守卫）和基线修正（C5配置）。'
)

doc.add_heading('8.3 当前文档定位', level=2)
doc.add_paragraph(
    '本报告为内部审计总结，尚不适合作为科学验收结论。'
    '在完成第五章残余工作（R1-R5）并重新验证全部结果后，方可重新评估是否进入论文撰写阶段。'
)

# ===== 第九章 =====
doc.add_heading('第九章 关键结论（修正后）', level=1)
doc.add_heading('9.1 已完成的修正（代码层，待多配置验证）', level=2)
confirmed = [
    'C5_feasible配置满足LPSP≤1%约束(0.23%)，但为超大容量兜底，需验证多组可行容量',
    'EOL守卫已实现(D≥1.0→dD=0)，幻影损伤消除，但BOL FC一年内即到EOL，退化标定不可信',
    '设备级TE向量[-0.15%, -2.30%, +25.10%]替代总TE单一数字（单配置初步信号）',
    'PEMFC证据降为C级，PEMEL降为B/C级',
    'BAT→EL正传导效应为单配置初步信号(+25.10%)，非确认结论',
]
for c in confirmed:
    doc.add_paragraph(c, style='List Bullet')

doc.add_heading('9.2 待验证的结论', level=2)
pending = [
    '六候选路径在全设备反馈下的激活/零路径分类',
    'Shapley路径效应分解在EOL守卫下的闭合性',
    'L4 EMS在C5配置下的效果',
    '20年NPC/LCOE在C5配置下的经济评价',
    'C5配置在2016/2018年的跨年稳定性',
]
for p in pending:
    doc.add_paragraph(p, style='List Bullet')

doc.add_heading('9.3 修正前的错误结论（已撤销）', level=2)
revoked = [
    '"无附加条件进入论文撰写" — 撤销(基线LPSP不可行)',
    '"TE=+6.59%为BAT-only上界" — 撤销(含幻影损伤,且基线不可行)',
    '"BAT TE=+1.13%为主结论" — 撤销(基于不可行基线C1)',
    '"Shapley闭合残差1.55e-16证明方法可信" — 撤销(只证明计算恒等式)',
    '"四条零路径为反事实辨识发现" — 撤销(为EMS结构预设)',
]
for r in revoked:
    doc.add_paragraph(r, style='List Bullet')

# 保存
out_path = Path(r'D:\matlab\C\09_docs\06_项目梳理/07_工作区全面总结报告.docx')
doc.save(str(out_path))
print(f'文档已生成: {out_path}')
print(f'段落数: {len(doc.paragraphs)}, 表格数: {len(doc.tables)}')
