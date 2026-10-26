# -*- coding: utf-8 -*-
"""Update 工作区全面总结报告.docx with the verified F4-F8 results."""
from datetime import datetime
from pathlib import Path
import json

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor


ROOT = Path(r"D:\matlab\C")
DOCX_PATH = ROOT / "09_docs" / "工作区全面总结报告.docx"


def replace_paragraph_start(doc, prefix, new_text):
    matches = [p for p in doc.paragraphs if p.text.strip().startswith(prefix)]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one paragraph starting with {prefix!r}, got {len(matches)}")
    matches[0].text = new_text
    return matches[0]


def set_cell_text(cell, text, bold=False, align=None):
    cell.text = str(text)
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    for paragraph in cell.paragraphs:
        if align is not None:
            paragraph.alignment = align
        paragraph.paragraph_format.space_before = Pt(0)
        paragraph.paragraph_format.space_after = Pt(0)
        paragraph.paragraph_format.line_spacing = 1.0
        for run in paragraph.runs:
            run.font.name = "Microsoft YaHei"
            run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
            run.font.size = Pt(8.5)
            run.font.bold = bold


def rewrite_table(table, rows):
    while len(table.rows) < len(rows):
        table.add_row()
    while len(table.rows) > len(rows):
        table._tbl.remove(table.rows[-1]._tr)
    for row_index, row_data in enumerate(rows):
        if len(row_data) != len(table.columns):
            raise RuntimeError(
                f"Table width mismatch: expected {len(table.columns)}, got {len(row_data)}"
            )
        for col_index, value in enumerate(row_data):
            alignment = WD_ALIGN_PARAGRAPH.CENTER if col_index < 2 else WD_ALIGN_PARAGRAPH.LEFT
            set_cell_text(table.cell(row_index, col_index), value,
                          bold=(row_index == 0), align=alignment)
    table.rows[0]._tr.get_or_add_trPr().append(
        table.rows[0]._tr._new_tblPr() if False else table.rows[0]._tr.get_or_add_trPr()
    ) if False else None


def add_bullet(doc, text):
    p = doc.add_paragraph(style="List Bullet")
    p.add_run(text)
    return p


with open(ROOT / "07_results" / "lifetime" / "f4_replacement_summary_2017.json",
          encoding="utf-8") as f:
    f4 = json.load(f)
with open(ROOT / "07_results" / "economic" / "f5_timed_economic_summary.json",
          encoding="utf-8") as f:
    f5 = json.load(f)
with open(ROOT / "07_results" / "crossyear" / "f8_crossyear_summary.json",
          encoding="utf-8") as f:
    f8 = json.load(f)

doc = Document(DOCX_PATH)
if len(doc.tables) < 9:
    raise RuntimeError("Unexpected document structure: fewer than nine tables")

# Cover and executive judgement.
doc.paragraphs[0].text = "离网光伏-氢-电池混合储能微电网\n整改阶段结果与当前阻断报告"
doc.paragraphs[2].text = (
    "文档性质：工作区全面总结（F4-F8更新版）\n"
    "更新日期：2026-08-02\n"
    "判定：方法链已完成代码级整改；连续年度可行基线尚未建立"
)
replace_paragraph_start(
    doc, "方法框架可以保留",
    "F4-F8已完成代码实现与回归验证：EOL事件、精确时刻经济贴现、六路径统计门控和路径对齐Shapley均通过代码级验收。"
    "年度验证同时发现周际SOC/H2状态未传递，旧脚本每年重复注入52次初始库存；修正后C5-H2-500/1000在2016-2018年均不满足LPSP≤1%与H2闭合。"
    "当前核心阻断已收敛为：连续年度容量配置不可行、PV容量字段与输入曲线缺少可复现缩放关系、FC/BAT系统级标定仍为情景证据。"
)
replace_paragraph_start(doc, "1.2 未完成有效验证的5项", "1.2 当前验收状态与核心阻断")

# Capacity closure chapter.
replace_paragraph_start(
    doc, "C6配置储氢容量500kg",
    "复核确认问题不止是C5/C6配置混用。年度脚本按周调用调度器时，每周均从配置初始SOC和满罐H2重新开始，形成52次外部库存补贴。"
    "调度器现已增加soc_initial与h2_initial_kg入口，并按实际段首库存计算氢守恒残差。"
)
replace_paragraph_start(
    doc, "H2年末894kg",
    "在周际状态连续后，C5-H2-500的2017年LPSP由旧口径0.17%-0.24%升至31.36%，H2由500kg降至52.19kg；"
    "C5-H2-1000同年LPSP为29.82%，H2降至下限附近。旧低LPSP来自每周库存重置，不能作为容量可行性证据。"
)
replace_paragraph_start(
    doc, "C6/C7在2016年LPSP",
    "F8连续全年验证覆盖C5-H2-500与C5-H2-1000的2016-2018年，共6组；LPSP范围为24.48%-36.66%，全部超过1%，无跨年可准入配置。"
)
replace_paragraph_start(
    doc, "C6的FC/EL 80kW",
    "C6/C7/C5的旧可行性排序基于周初状态重置，已失去准入意义。容量比较必须在连续全年状态、统一PV缩放基准和相同初始库存下重新执行。"
)

# Calibration chapter.
replace_paragraph_start(
    doc, "结论：FC退化只能称",
    "F2已明确FC主观测变量为电压，线性拟合R²=0.9475，10%电压降情景EOL为1882h；缺独立系统尺度验证与严格后验区间，仍只能称“文献数据约束下的情景标定”。"
)
replace_paragraph_start(
    doc, "BAT年度损伤0.769",
    "F3已将k_cal由2e-5/step修正为2e-6/step。连续状态下C5-H2-500的2017年BAT原始损伤为0.1023，但该系统LPSP=31.36%，此数值不能代表可行系统寿命；BAT系统级证据仍为B级，pack换算未完全验证。"
)

# Economic chapter.
replace_paragraph_start(
    doc, "FC年运行6462h",
    "“FC年运行6462h”由把15分钟步数直接当作小时产生，遗漏dt=0.25h。连续状态下2017年FC活动时间为237.75h、等效满负荷时间125.53h，低于1882h情景EOL，因此年度未触发FC更换。"
)
replace_paragraph_start(
    doc, "设备小计544.5万",
    "F5按C5-H2-500重新计算：设备小计750.4万元，电力电子112.6万元，安装150.1万元，初始CAPEX为1013.0万元。H2压缩、干燥及物流仍未纳入工程边界。"
)
replace_paragraph_start(
    doc, "M2与M3声称NPC完全相同",
    "F5已实现按小数年份的更换事件贴现，消除“每年最多更换一次”的时间推进错误。20年情景更换BAT/FC/EL=2/2/3次，NPC=1821.6万元、LCOE=2.602元/kWh；因基线LPSP=31.36%，该结果仅用于算法核验，禁止作为可行系统经济结论。"
)

# Path statistics and Shapley.
replace_paragraph_start(
    doc, "精确显示0.000000",
    "F6已使用52个周窗口、绝对AE阈值1e-4、Bootstrap 5000次的95%区间、方向一致率≥90%、季度复现及NC1/NC3负对照重新判定。α₁(BAT→FC)和β₁(BAT→EL)激活，其余4条未激活。"
)
replace_paragraph_start(doc, "5.2 缺失的7项激活标准", "5.2 七项统计门控已补全")
replace_paragraph_start(
    doc, "α₁的AE=0.004738",
    "F6结果：α₁平均AE=2.7210e-3，95%CI=[2.2279e-3,3.3003e-3]，方向一致率100%；"
    "β₁平均AE=2.5985e-3，95%CI=[2.1418e-3,3.1144e-3]，方向一致率98.1%。NC1与NC3最大残差均为0。"
)
replace_paragraph_start(doc, "6.1 归因变量定义冲突", "6.1 归因变量已按有向路径目标统一")
replace_paragraph_start(doc, "6.2 算术不一致", "6.2 路径对齐闭合验证")
replace_paragraph_start(
    doc, "Shapley表中 phi_C",
    "F7将联盟值、路径AE和Shapley统一为相同源设备、目标设备、健康档位与W1窗口。α₁与β₁的闭合相对误差均为0%，最大二阶交互占比为0%；"
    "在BAT源路径中，C为BAT可用容量接口，P为BAT功率接口，E/I因未进入当前EMS源→目标链而显式记为零归因。"
)

# L4, regression, scope and F1 correction.
replace_paragraph_start(
    doc, "L4在C6配置下同时增加退化",
    "L4在旧C6配置下同时增加退化和LPSP，控制器在该配置上验证失败；同时C6本身未通过连续年度准入，因此L4结果继续保留为否决性扩展试验，不进入核心结论。"
)
replace_paragraph_start(
    doc, "跨年表中的TE_BAT",
    "F8先执行准入筛选：C5-H2-500/1000在2016-2018年均未同时满足LPSP≤1%与H2年末偏差≤5%。因可准入样本数为0，跨年路径稳定性未建立，旧TE跨年表停止使用。"
)
replace_paragraph_start(
    doc, "回归测试只显示α₁",
    "S12已补充β₂(FC→EL)与γ₂(EL→BAT)，六路径符号全部纳入汇总；最新回归为9/9 PASS。新增16项单元测试覆盖周际状态连续、EOL事件、精确时刻贴现、统计门控和Shapley路径闭合。"
)
replace_paragraph_start(
    doc, "本报告为\"整改阶段结果与未决问题报告\"",
    "本报告现定位为“整改阶段结果与当前阻断报告”。F4-F8已完成代码级整改，路径统计和归因闭合可作为方法实现证据；"
    "容量准入、寿命量级及经济数值因无连续年度可行基线而继续冻结，L4保持否决性扩展结论。"
)
replace_paragraph_start(
    doc, "F1验证发现根本问题",
    "F1旧脚本的“周期稳态”基于每周重置SOC/H2，未形成连续年度状态；该验证口径已撤销。F8改用单次连续全年调度并重新执行准入检查。"
)
replace_paragraph_start(
    doc, "结论：仅2017年C5-H2-500配置达到周期稳态",
    "修正后没有配置达到跨年准入：C5-H2-500三年LPSP为36.66%/31.26%/26.06%，C5-H2-1000为35.24%/29.82%/24.48%。"
    "因此“2017年唯一稳态”结论撤销。"
)
replace_paragraph_start(
    doc, "C6_compact（BAT=500/FC=80/EL=80）",
    "当前阻断为PV容量字段未与PV输入曲线建立可复现缩放关系。完成该接口后，必须在连续全年状态下重新搜索配置，再依次重跑F4寿命事件、F5经济和F8跨年验证。"
)

# Update all status tables in place.
rewrite_table(doc.tables[0], [
    ["项目", "原状态", "最新判定"],
    ["容量闭合", "周际SOC/H2重置造成假可行", "未完成：连续年度无可准入配置"],
    ["FC/BAT退化标定", "F2/F3已修正主要量纲", "FC为情景标定；BAT系统级B级"],
    ["六路径统计判据", "缺Bootstrap与负对照", "F6完成；α₁/β₁激活"],
    ["Shapley闭合", "路径AE与归因量不一致", "F7完成；闭合误差0%"],
    ["经济事件模型", "年度整数更换与小时换算错误", "算法完成；系统不准入，数值冻结"],
])
rewrite_table(doc.tables[1], [
    ["验证口径", "2017 LPSP", "解释"],
    ["旧周重置口径", "0.17%-0.24%", "每周补回初始SOC/H2，结果无效"],
    ["连续C5-H2-500", "31.36%", "H2由500kg降至52.19kg"],
    ["连续C5-H2-1000", "29.82%", "H2降至下限附近"],
    ["F8六组范围", "24.48%-36.66%", "2016-2018全部超过1%"],
    ["准入阈值", "≤1%", "满足配置数：0"],
])
rewrite_table(doc.tables[2], [
    ["项目", "最新证据", "判定"],
    ["FC主观测变量", "电压0.841→0.788V，R²=0.9475", "已明确"],
    ["FC情景EOL", "10%电压降，1882h", "情景参数"],
    ["FC系统迁移", "缺独立100kW验证与严格后验", "未完成"],
    ["BAT日历系数", "k_cal=2e-6/step", "量纲Bug已修正"],
    ["BAT连续年损伤", "D=0.1023（失配系统）", "不得外推寿命"],
    ["证据等级", "FC情景标定；BAT系统级B级", "保持限制"],
])
rewrite_table(doc.tables[3], [
    ["分项", "金额(万元)", "F5 C5-H2-500口径"],
    ["PV", "250.1", "300kWp×8337.5元/kWp"],
    ["BAT", "174.0", "800kWh×2175元/kWh"],
    ["FC", "145.0", "100kW×14500元/kW"],
    ["EL", "145.0", "100kW×14500元/kW"],
    ["H2", "36.2", "500kg×725元/kg"],
    ["设备小计", "750.4", "五类设备合计"],
    ["电力电子(15%)", "112.6", "基于设备小计"],
    ["安装(20%)", "150.1", "基于设备小计"],
    ["初始CAPEX", "1013.0", "不含H2物流/压缩扩展边界"],
])
rewrite_table(doc.tables[4], [
    ["门控", "阈值", "F6/F7结果"],
    ["绝对效应", "|AE|≥1e-4", "α₁/β₁ PASS"],
    ["Bootstrap 95%CI", "不跨0", "α₁/β₁ PASS"],
    ["方向一致率", "≥90%", "100%/98.1% PASS"],
    ["季度复现", "四季度同向且过阈值", "α₁/β₁ PASS"],
    ["NC1负对照", "残差≤1e-4", "0 PASS"],
    ["NC3负对照", "残差≤1e-4", "0 PASS"],
    ["归因闭合", "相对误差≤5%", "0% PASS"],
    ["交互占比", "≤30%", "0% PASS"],
])
rewrite_table(doc.tables[5], [
    ["定义层", "F7统一定义", "结论"],
    ["有向路径", "α₁:BAT→FC；β₁:BAT→EL", "输出均为目标设备损伤AE"],
    ["CEPI归因", "C=BAT容量；P=BAT功率；E/I未进入当前链", "E/I显式为零归因"],
    ["闭合口径", "同源、同目标、D=0.9、W1窗口", "两路径闭合误差0%"],
])
rewrite_table(doc.tables[6], [
    ["路径", "期望(v3.0)", "S12测试", "状态"],
    ["α₁", "BAT→FC", "已测", "PASS"],
    ["α₂", "EL→FC", "已测", "PASS"],
    ["β₁", "BAT→EL", "已测", "PASS"],
    ["β₂", "FC→EL", "已测", "PASS"],
    ["γ₁", "FC→BAT", "已测", "PASS"],
    ["γ₂", "EL→BAT", "已测", "PASS"],
])
rewrite_table(doc.tables[7], [
    ["步骤", "任务", "最新状态"],
    ["F1", "连续年度状态与容量闭合", "旧验证撤销；待重搜配置"],
    ["F2", "FC标定补全", "完成；情景标定"],
    ["F3", "BAT pack换算", "主要量纲修正；系统级B级"],
    ["F4", "逐步EOL与更换事件", "事件模型PASS；系统不准入"],
    ["F5", "精确事件经济模型", "算法PASS；结果不准入"],
    ["F6", "六路径统计门控", "PASS；α₁/β₁激活"],
    ["F7", "路径对齐Shapley", "PASS；闭合0%"],
    ["F8", "跨年准入验证", "完成；0个可准入配置"],
    ["下一阶段", "PV缩放接口+连续年度配置重搜", "阻断中"],
])
rewrite_table(doc.tables[8], [
    ["配置", "年份", "LPSP(%)", "H2变化(%)", "H2闭合", "准入"],
    ["C5-H2-500", "2016", "36.66", "-90.0", "FAIL", "FAIL"],
    ["C5-H2-500", "2017", "31.26", "-89.9", "FAIL", "FAIL"],
    ["C5-H2-500", "2018", "26.06", "-90.0", "FAIL", "FAIL"],
    ["C5-H2-1000", "2016", "35.24", "-90.0", "FAIL", "FAIL"],
    ["C5-H2-1000", "2017", "29.82", "-90.0", "FAIL", "FAIL"],
    ["C5-H2-1000", "2018", "24.48", "-90.0", "FAIL", "FAIL"],
])

# Append a concise audit trail once.
if not any(p.text.strip().startswith("第十一章 F4-F8") for p in doc.paragraphs):
    doc.add_page_break()
    doc.add_heading("第十一章 F4-F8更新结果与最终阻断", level=1)
    doc.add_heading("11.1 周际状态连续性根因", level=2)
    doc.add_paragraph(
        "年度仿真必须满足状态连续：第w+1周的SOC(0)和H2(0)分别等于第w周末状态。"
        "本轮新增显式段首状态接口及回归测试，消除每周库存重置。"
    )
    doc.add_heading("11.2 已完成的代码级验收", level=2)
    add_bullet(doc, "F4：逐步EOL事件引擎支持步内事件时刻、多次更换及设备独立计数。")
    add_bullet(doc, "F5：更换成本按小数年份贴现；情景NPC/LCOE因LPSP超限标记为不准入。")
    add_bullet(doc, "F6：52窗口统计门控确认α₁、β₁激活，NC1/NC3残差均为0。")
    add_bullet(doc, "F7：路径对齐Shapley闭合误差0%，最大交互占比0%。")
    add_bullet(doc, "F8：6组连续年度验证全部不准入，跨年稳定性未建立。")
    doc.add_heading("11.3 最终判定", level=2)
    p = doc.add_paragraph(style="Intense Quote")
    p.add_run(
        "方法实现可保留；所有容量、寿命、L4和经济数值结论继续冻结。"
        "恢复数值研究的前置条件是建立PV容量-输入曲线缩放接口，并在连续全年状态下获得LPSP≤1%且H2年末偏差≤5%的配置。"
    )
    doc.add_paragraph(
        "验证证据：16项单元测试通过；S12回归9/9通过；F4-F8脚本退出码均为0。"
        "关键输出位于07_results/lifetime、economic、path_identification和crossyear目录。"
    )

# Keep the existing visual system; normalize new/changed run fonts through styles.
for style_name in ["Normal", "Title", "Heading 1", "Heading 2", "Heading 3",
                   "List Bullet", "Intense Quote"]:
    if style_name in doc.styles:
        style = doc.styles[style_name]
        style.font.name = "Microsoft YaHei"
        style._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")

doc.core_properties.title = "离网光伏-氢-电池混合储能微电网：整改阶段结果与当前阻断报告"
doc.core_properties.subject = "F4-F8更新、连续年度状态修正与跨年准入判定"
doc.core_properties.comments = "Updated from verified workspace results on 2026-08-02"
doc.core_properties.modified = datetime.now()

temp_path = DOCX_PATH.with_name(DOCX_PATH.stem + "_updated.tmp.docx")
doc.save(temp_path)
temp_path.replace(DOCX_PATH)
print(DOCX_PATH)
print(f"paragraphs={len(doc.paragraphs)}, tables={len(doc.tables)}")
