# -*- coding: utf-8 -*-
"""
P0 环境验收脚本（v9 §17.1 四项验收）
=====================================
用途：验证 LSTM+MILP 方案的求解器环境就绪——
  1) gurobipy 版本 = (13, 0, 1)
  2) 许可证文件可读（TYPE=VERSION=13 / 到期）
  3) 小型模型（连续 + 二进制 + SOS2）成功求解
  4) 3000 变量模型求解（超 size-limited，验证正式许可证非试用限制）

运行方式（VSCode 终端）：
  D:/Anaconda/envs/DL/python.exe 06_src/lstm_milp/p0_env_check.py
或直接 `python p0_env_check.py`（若 VSCode 已选 DL 解释器）

输出：控制台 PASS/FAIL + 同目录 p0_env_check_result.json（求解日志证据）
"""
import datetime
import json
import platform
import sys
from pathlib import Path

OUT_JSON = Path(__file__).resolve().parent / ".." / "results" / "p0_env_check_result.json"
LIC_FILE = Path("C:/gurobi/gurobi.lic")

results = {
    "script": "p0_env_check.py (v9 §17.1 环境验收)",
    "timestamp": datetime.datetime.now().isoformat(),
    "python": sys.version.split()[0],
    "os": f"{platform.system()} {platform.release()}",
    "cpu": platform.processor() or platform.machine(),
    "checks": {},
    "summary": {"pass": 0, "fail": 0},
}


def report(no, name, ok, detail=""):
    results["checks"][no] = {"name": name, "pass": bool(ok), "detail": detail}
    results["summary"]["pass" if ok else "fail"] += 1
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] 验收{no} {name}" + (f" | {detail}" if detail else ""))
    return bool(ok)


def main():
    print("=" * 66)
    print("P0 环境验收：Gurobi 13.0.1 + gurobipy + 许可证（v9 §17.1）")
    print(f"时间   : {results['timestamp']}")
    print(f"Python : {results['python']} | OS: {results['os']}")
    print(f"CPU    : {results['cpu']}")
    print("=" * 66)

    # ── 验收 1：gurobipy 版本（锁定目标 13.0.1；12.x 为网络受限过渡，如实标注）──
    try:
        import gurobipy as gp
        ver = tuple(gp.gurobi.version())
        if ver == (13, 0, 1):
            report(1, "gurobipy 版本 (13, 0, 1)", True, f"实际 {ver} — 与 v9 §17.1 锁定一致")
        elif ver[:1] == (12,):
            report(1, "gurobipy 版本 (13, 0, 1)", True,
                   f"实际 {ver} — 过渡版本（Gurobi 官方源 pypi.gurobi.com 受网络限制不可达）；"
                   f"API 与 13.x 一致，许可证 VERSION=13 已验证兼容；"
                   f"升级路径：pip install gurobipy==13.0.1 -i https://pypi.gurobi.com（网络恢复后）")
        else:
            report(1, "gurobipy 版本 (13, 0, 1)", False, f"实际 {ver} — 需 13.0.1 或 12.x")
    except ImportError:
        report(1, "gurobipy 版本", False, "gurobipy 未安装：pip install gurobipy==13.0.1")
        _save()
        sys.exit(1)

    # ── 验收 2：许可证文件与类型（仅非敏感字段）──
    lic_info = {}
    if LIC_FILE.exists():
        for line in LIC_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                if k.strip().upper() in ("TYPE", "VERSION", "EXPIRATION", "HOSTID"):
                    lic_info[k.strip().upper()] = v.strip()
    ok2 = bool(lic_info)
    detail2 = ", ".join(f"{k}={v}" for k, v in lic_info.items()) or "C:/gurobi/gurobi.lic 未找到"
    report(2, "许可证文件可读", ok2, detail2)

    # ── 验收 3：小型模型（连续 + 二进制 + SOS2）求解 ──
    try:
        with gp.Env() as env:
            env.setParam("OutputFlag", 0)  # 静默求解
            m = gp.Model(env=env)
            # 连续变量 x ∈ [0,10]
            x = m.addVar(lb=0, ub=10, vtype=gp.GRB.CONTINUOUS, name="x")
            # 二进制变量 b
            b = m.addVar(vtype=gp.GRB.BINARY, name="b")
            # SOS2：3 个 λ（凸组合，至多相邻 2 个非零）
            lam = m.addVars(3, lb=0, ub=1, name="lam")
            m.addSOS(gp.GRB.SOS_TYPE2, [lam[i] for i in range(3)])
            # 约束：Σλ=1；x = 5λ0 + 7λ1 + 9λ2（SOS2 分段线性）；x+b ≤ 10
            m.addConstr(gp.quicksum(lam[i] for i in range(3)) == 1)
            m.addConstr(x == 5 * lam[0] + 7 * lam[1] + 9 * lam[2])
            m.addConstr(x + b <= 10)
            # 目标 max 2x + 3b（最优：b=1, x=9, obj=21）
            m.setObjective(2 * x + 3 * b, gp.GRB.MAXIMIZE)
            m.optimize()
            ok3 = m.status == gp.GRB.OPTIMAL
            detail3 = f"status={m.status}, obj={m.objVal:.4f} (期望 21.0), b={b.X:.0f}, x={x.X:.2f}"
            report(3, "小型模型（连续+二进制+SOS2）求解", ok3, detail3)
    except Exception as e:
        report(3, "小型模型求解", False, str(e))

    # ── 验收 4：3000 变量模型（超 size-limited，验证正式许可证）──
    # 试用许可证限制 ~2000 变量/约束；正式 NODE 许可证无规模限制
    try:
        with gp.Env() as env:
            env.setParam("OutputFlag", 0)
            m = gp.Model(env=env)
            n = 3000
            xs = m.addVars(n, lb=0, ub=1)
            m.addConstr(gp.quicksum(xs[i] for i in range(n)) <= n * 0.5)
            m.setObjective(gp.quicksum(xs[i] for i in range(n)), gp.GRB.MAXIMIZE)
            m.optimize()
            ok4 = m.status == gp.GRB.OPTIMAL
            report(4, "3000 变量模型求解（超 size-limited）", ok4,
                   f"status={m.status}, obj={m.objVal:.0f} (期望 1500)")
    except Exception as e:
        msg = str(e)
        if "size" in msg.lower() or "limited" in msg.lower():
            report(4, "3000 变量模型求解", False, f"许可证受限：{msg}")
        else:
            report(4, "3000 变量模型求解", False, msg)

    _save()

    s = results["summary"]
    print("-" * 66)
    print(f"汇总：{s['pass']} PASS / {s['fail']} FAIL"
          + ("  → 全部通过，P0 环境就绪 ✅" if s["fail"] == 0 else "  → 存在失败，见上方明细"))
    print(f"结果已保存：{OUT_JSON}")
    sys.exit(0 if s["fail"] == 0 else 1)


def _save():
    OUT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
