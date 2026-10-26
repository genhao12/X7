# -*- coding: utf-8 -*-
"""FF03 B+ 判据单元验证：从 run_full_feedback.py 提取 _p1_te_reference 真实源码并
在受控环境中执行，覆盖 正常 / STALE / MISSING 三种情形。不触发任何仿真。
"""
import ast, io, os, shutil, sys, tempfile
from pathlib import Path

ROOT = Path(r"D:\matlab\C")
SRC = ROOT / "06_src" / "simulation" / "run_full_feedback.py"

# 1) 提取函数与其依赖常量的真实源码
tree = ast.parse(SRC.read_text(encoding="utf-8"))
want = {"_p1_te_reference", "P1_TE_CSV", "P1_TE_INPUT", "P1_TE_TOL"}
segs = []
for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name == "_p1_te_reference":
        segs.append(ast.get_source_segment(SRC.read_text(encoding="utf-8"), node))
    if isinstance(node, ast.Assign):
        tgt = node.targets[0]
        if isinstance(tgt, ast.Name) and tgt.id in want:
            segs.append(ast.get_source_segment(SRC.read_text(encoding="utf-8"), node))
assert len(segs) >= 4, f"提取失败: {len(segs)}"
code = "\n".join(segs)

# 2) 构造受控沙箱（把 P1_TE_CSV/P1_TE_INPUT 指到临时目录）
tmp = Path(tempfile.mkdtemp(prefix="ff03_test_"))
ns = {"PROJECT_ROOT": tmp, "csv": __import__("csv")}
exec(code, ns)

src_csv = ROOT / "07_results" / "annual" / "capped_te_analysis.csv"
src_inp = ROOT / "07_results" / "annual" / "annual_damage_summary.csv"
ok = True

def check(name, expect_pass, desc):
    global ok
    print(f"  {name}: {'PASS' if expect_pass else 'FAIL'} — {desc}")
    ok = ok and expect_pass

# 情形 A：正常（把两个真实文件复制进沙箱的 07_results/annual/ 下，保证 mtime 输出>=输入）
ann = tmp / "07_results" / "annual"; ann.mkdir(parents=True, exist_ok=True)
p_csv, p_inp = ann / "capped_te_analysis.csv", ann / "annual_damage_summary.csv"
shutil.copy2(src_csv, p_csv); shutil.copy2(src_inp, p_inp)
os.utime(p_inp, (1000000000, 1000000000)); os.utime(p_csv, (2000000000, 2000000000))
te, st = ns["_p1_te_reference"]()
check("A 正常", te is not None and st == "OK" and abs(te - 0.0159) < 1e-9,
      f"te={te}, status={st} (期望 0.0159/OK)")

# 情形 B：STALE（输出 mtime 早于输入）
os.utime(p_csv, (900000000, 900000000))
te, st = ns["_p1_te_reference"]()
check("B STALE", te is None and st.startswith("STALE"), f"status={st[:70]}")

# 情形 C：MISSING（删掉参考文件）
p_csv.unlink()
te, st = ns["_p1_te_reference"]()
check("C MISSING", te is None and st.startswith("MISSING"), f"status={st[:70]}")

# 情形 D：真实文件（不受沙箱 mtime 干扰，验证当前工作区实际状态）
te, st = ns["_p1_te_reference"]() if False else (None, None)
# 直接用真实路径再跑一遍（绕过沙箱）
ns2 = {"PROJECT_ROOT": ROOT, "csv": __import__("csv")}
exec(code, ns2)
te, st = ns2["_p1_te_reference"]()
check("D 真实工作区", te is not None and st == "OK" and abs(te - 0.0159) < 1e-9,
      f"te={te}, status={st}")

shutil.rmtree(tmp, ignore_errors=True)
print("\nFF03 B+ 判据验证:", "全部 PASS" if ok else "存在 FAIL")
sys.exit(0 if ok else 1)
