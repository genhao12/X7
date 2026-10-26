# -*- coding: utf-8 -*-
"""批量修复所有含中文的绘图脚本字体配置：
1) 将 Arial/DejaVu Sans 字体列表替换为中文优先列表
2) 为 rcParams 字典补齐 axes.unicode_minus=False
3) 对无 font.sans-serif 但含中文的绘图脚本注入统一字体配置
只处理输出 PNG（savefig）且含中文字符的脚本。
"""
import os, re, io

ROOTS = [r'D:/matlab/C/06_src/simulation', r'D:/matlab/C/06_src/utils']
CN_RE = re.compile(r'[\u4e00-\u9fff]')
CN_FONT_LIST = "['Microsoft YaHei', 'SimHei', 'Arial', 'DejaVu Sans']"

# 字体列表替换规则（顺序敏感：先长后短避免子串误替换）
FONT_REPLACEMENTS = [
    (re.compile(r"\['Arial', 'DejaVu Sans', 'Helvetica'\]"), CN_FONT_LIST),
    (re.compile(r"\['Arial', 'DejaVu Sans'\]"), CN_FONT_LIST),
    (re.compile(r"\['Arial','DejaVu Sans'\]"), CN_FONT_LIST),
    (re.compile(r'\["Arial", "DejaVu Sans"\]'), '["Microsoft YaHei", "SimHei", "Arial", "DejaVu Sans"]'),
]

# rcParams.update({...}) 字典块（值无嵌套 dict，可安全匹配到第一个 })）
RCPARAMS_BLOCK = re.compile(r'(plt\.rcParams\.update\(\{)', re.S)
DICT_INSERT_POS = re.compile(r"plt\.rcParams\.update\(\{")

# 注入模板（无 font.sans-serif 的脚本）：在 import matplotlib 之后插入
INJECT_TEMPLATE = ("\n# ===== 中文显示修复（统一注入） =====\n"
                   "import matplotlib as _mpl\n"
                   "_mpl.rcParams['font.family'] = 'sans-serif'\n"
                   "_mpl.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Arial', 'DejaVu Sans']\n"
                   "_mpl.rcParams['axes.unicode_minus'] = False\n"
                   "# ===== end 中文显示修复 =====\n")

def is_plot_script(src):
    return ('savefig' in src) or ('.png' in src and 'matplotlib' in src)

def fix_file(path):
    src = open(path, encoding='utf-8', errors='ignore').read()
    if not CN_RE.search(src):
        return None  # 无中文，跳过
    if not is_plot_script(src):
        return None  # 非绘图脚本，跳过
    orig = src
    log = []

    # 1) 替换字体列表
    for pat, rep in FONT_REPLACEMENTS:
        if pat.search(src):
            src = pat.sub(rep, src)
            log.append('字体列表已替换')

    # 2) 若已有 rcParams.update 块且缺 unicode_minus，则补入
    if 'unicode_minus' not in src and RCPARAMS_BLOCK.search(src):
        m = DICT_INSERT_POS.search(src)
        if m:
            pos = m.end()  # 在 '{' 之后
            src = src[:pos] + "'axes.unicode_minus': False, " + src[pos:]
            log.append('已补 unicode_minus')

    # 3) 无 font.sans-serif 且无 rcParams → 注入独立配置
    if 'font.sans-serif' not in src and 'matplotlib' in src:
        # 在 import matplotlib 之后、第一次使用时插入
        im = re.search(r'import matplotlib(?:\.pyplot)?\s+(?:as\s+\w+)?\s*', src)
        if im:
            insert_at = im.end()
            src = src[:insert_at] + INJECT_TEMPLATE + src[insert_at:]
            log.append('已注入字体配置')

    if src != orig:
        with io.open(path, 'w', encoding='utf-8', newline='') as f:
            f.write(src)
        return log
    return ['未变更（已含中文配置或无需处理）']

if __name__ == '__main__':
    total = 0
    for root in ROOTS:
        for fn in sorted(os.listdir(root)):
            if not fn.endswith('.py'):
                continue
            p = os.path.join(root, fn)
            try:
                res = fix_file(p)
            except Exception as e:
                print(f'ERROR {fn}: {e}')
                continue
            if res:
                total += 1
                print(f'[{", ".join(res)}] {fn}')
    print(f'\n共处理 {total} 个脚本')
