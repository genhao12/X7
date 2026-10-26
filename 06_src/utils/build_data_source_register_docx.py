"""生成项目数据来源与可信度台账 Word 文档。"""
import sys
sys.path.insert(0, r'D:\matlab\C\运行脚本')
from data_register_content import SECTIONS, INTRO_PARAS, CONCLUSION_PARAS
from docx_docx_helpers import build_doc

build_doc(SECTIONS, INTRO_PARAS, CONCLUSION_PARAS,
          out_path=r'D:\matlab\C\数据文献\76_项目06_项目梳理/09_数据来源与可信度台账.docx')
print('done')
