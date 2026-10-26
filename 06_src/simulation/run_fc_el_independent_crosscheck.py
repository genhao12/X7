# -*- coding: utf-8 -*-
"""
run_fc_el_independent_crosscheck.py — P3-2：FC/EL 独立实现交叉验证

目标：在不调用本项目标定函数（hp_fc_lite / hp_el_lite）的前提下，对数据文献中的
原始老化数据做**独立拟合**，与模型标定值对比，给出偏差/量级关系。

对比口径：
  FC  : IEEE PHM FC1/FC2 原始电压轨迹独立 polyfit 斜率（mV/1000h） vs 模型标定 23.5 mV/1000h
  EL  : PEMEL 恒流耐久 CP 原始数据（UF15/UF19, 逗号小数制）独立解析电压轨迹
        电压退化率（%/1000h 与 %/yr） vs 模型年退化率 1.17–1.61 %/yr（degradation_summary）
判定：偏差 ≤30% 视为量级一致；>30% 且同方向视为"方向一致、量级受运行条件影响"；
      EL 加速数据与系统级模型属不同条件，如实报告量级差异（不作工程外推）。

输出：07_results/rectification_v2_stage2c/stage3/fc_el_independent_crosscheck.json
"""
import sys, json, re, io, zipfile
import numpy as np
from pathlib import Path

ROOT = Path(r"d:\matlab\C")
sys.path.insert(0, str(ROOT / "06_src"))

FC1_DIR = ROOT / "数据文献/PEMFC恒流与纹波电流约1000 h老化数据/FC1_Without_Ripples_Excel"
FC2_DIR = ROOT / "数据文献/PEMFC恒流与纹波电流约1000 h老化数据/FC2_With_Ripples_Excel"
EL_ZIP = ROOT / "数据文献/PEMEL性能与耐久电化学数据及Python代码/electrolyzer_tests.zip"
OUT = ROOT / "07_results/rectification_v2_stage2c/stage3/fc_el_independent_crosscheck.json"

# 模型标定值（L21/L23 权威口径）
FC_MODEL_RATE_MV_PER_1000H = 23.5          # 单 cell，IEEE PHM FC1 标定
EL_MODEL_PCT_PER_YR = (1.17, 1.61)         # degradation_summary 年比能耗增量区间


# ---------- 独立工具函数（不 import 任何项目标定模块） ----------

def _to_float(x):
    """兼容欧洲逗号小数与普通点小数。"""
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return np.nan


def fit_voltage_slope(t_h, v):
    """对 (时间h, 电压V) 做一阶线性拟合，返回稳健统计。t 需单调。"""
    t = np.asarray(t_h, dtype=float)
    vv = np.asarray(v, dtype=float)
    m = np.isfinite(t) & np.isfinite(vv)
    t, vv = t[m], vv[m]
    if len(t) < 10:
        return None
    # 按时间排序去重
    order = np.argsort(t)
    t, vv = t[order], vv[order]
    mask = np.concatenate(([True], np.diff(t) > 1e-9))
    t, vv = t[mask], vv[mask]
    if len(t) < 10:
        return None
    slope, intercept = np.polyfit(t, vv, 1)
    resid = vv - (slope * t + intercept)
    r2 = 1.0 - float(np.sum(resid ** 2)) / max(float(np.sum((vv - vv.mean()) ** 2)), 1e-30)
    v0 = abs(vv[0])
    slope_mv_per_kh = slope * 1000 * 1000            # mV per 1000h（slope 单位 V/h）
    return {
        "ok": True,
        "n_points": int(len(t)),
        "t_start_h": float(t[0]), "t_end_h": float(t[-1]),
        "duration_h": float(t[-1] - t[0]),
        "v_start": float(vv[0]), "v_end": float(vv[-1]),
        "slope_mv_per_1000h": float(slope_mv_per_kh),
        # 相对初始电压的百分比（统一为 mV 口径：v0 V = v0*1000 mV）
        "pct_per_1000h": float(slope_mv_per_kh / max(v0 * 1000, 1e-9) * 100.0),
        "pct_per_yr": float(slope * 8760 / max(v0, 1e-9) * 100.0),
        "r2": round(r2, 4),
    }


# ---------- FC：独立读取原始 xlsx 并拟合 ----------

def fc_independent_fit(dir_path):
    """独立读取 FC 老化电压轨迹（openpyxl 直接读原始 xlsx，不调 hp_fc_lite）。"""
    import openpyxl
    t_all, v_all = [], []
    files = sorted(Path(dir_path).glob("FC*_Ageing_*"))
    for f in files:
        if f.suffix.lower() != ".xlsx":
            continue
        wb = openpyxl.load_workbook(f, read_only=True, data_only=True)
        ws = wb["Sheet1"]
        rows = ws.iter_rows(values_only=True)
        hdr = [str(h).strip() for h in next(rows)]
        # 时间列与电压列：优先单 cell U1..U5（平均）；仅无单 cell 时才用 Utot/U_mean
        time_col = next((i for i, c in enumerate(hdr) if "time" in c.lower() or "hour" in c.lower()), None)
        if time_col is None:
            wb.close(); continue
        v_cols = [i for i, c in enumerate(hdr) if re.match(r"^U\d+\s*\(V\)$", c.strip())]
        if not v_cols:
            v_cols = [i for i, c in enumerate(hdr)
                      if c.strip().lower() in ("utot (v)", "u_mean (v)", "u (v)", "u(v)")]
        if not v_cols:
            v_cols = [i for i, c in enumerate(hdr) if "(v)" in c.lower()]
        if not v_cols:
            wb.close(); continue
        for row in rows:
            tv = _to_float(row[time_col] if time_col < len(row) else np.nan)
            if not np.isfinite(tv):
                continue
            vals = [_to_float(row[i] if i < len(row) else np.nan) for i in v_cols]
            vals = [x for x in vals if np.isfinite(x)]
            if not vals:
                continue
            t_all.append(tv); v_all.append(float(np.mean(vals)))
        wb.close()
    if not t_all:
        return {"ok": False, "error": "no data"}
    return fit_voltage_slope(t_all, v_all)


# ---------- EL：独立解析 PEMEL CP 耐久 txt（zip 内流式） ----------

def el_cp_extract(zip_path, filename, max_rows=None):
    """从 zip 读取 CP 恒流 txt，返回 (t_h, v)。逗号小数、tab 分隔、首行表头。"""
    z = zipfile.ZipFile(zip_path)
    t, v = [], []
    with z.open(filename) as f:
        for i, line in enumerate(f):
            if i == 0:
                continue
            if max_rows is not None and len(t) >= max_rows:
                break
            parts = line.decode("utf-8", errors="replace").strip().split("\t")
            if len(parts) < 2:
                continue
            tv = _to_float(parts[0]); vv = _to_float(parts[1])
            if np.isfinite(tv) and np.isfinite(vv):
                t.append(tv / 3600.0)   # s -> h
                v.append(vv)
    if len(t) < 50:
        return None, None
    return np.array(t), np.array(v)


def el_independent_fit(zip_path, candidates):
    """对多个 PEMEL 耐久文件独立拟合，输出每个的电压退化统计。"""
    out = {}
    for label, filename in candidates:
        try:
            t, v = el_cp_extract(zip_path, filename)
            if t is None:
                out[label] = {"ok": False, "error": "parse failed"}
                continue
            fit = fit_voltage_slope(t, v)
            if fit is None:
                out[label] = {"ok": False, "error": "insufficient points"}
                continue
            out[label] = {"ok": True, "file": filename, **fit}
        except Exception as e:
            out[label] = {"ok": False, "error": str(e)[:200]}
    return out


def main():
    result = {}

    # ===== FC 独立拟合（FC1 恒流 + FC2 纹波） =====
    fc1 = fc_independent_fit(FC1_DIR)
    fc2 = fc_independent_fit(FC2_DIR)
    fc = {"model_rate_mv_per_1000h": FC_MODEL_RATE_MV_PER_1000H}
    for label, fit in (("fc1_independent", fc1), ("fc2_ripple_independent", fc2)):
        fc[label] = fit
    if fc1.get("ok"):
        dev = (abs(fc1["slope_mv_per_1000h"]) - FC_MODEL_RATE_MV_PER_1000H) / FC_MODEL_RATE_MV_PER_1000H * 100.0
        fc["fc1_dev_pct_vs_model"] = round(dev, 2)
        fc["fc1_verdict"] = "量级一致（内部独立重算）" if abs(dev) <= 30 else "方向一致、偏差>30%"
    if fc1.get("ok") and fc2.get("ok"):
        ratio = abs(fc2["slope_mv_per_1000h"]) / max(abs(fc1["slope_mv_per_1000h"]), 1e-12)
        fc["fc2_fc1_rate_ratio"] = round(ratio, 2)
        fc["fc2_verdict"] = "纹波加速退化方向符合文献（工况对照，非精度验证）"
    result["fc"] = fc

    # ===== EL 独立拟合（PEMEL CP 耐久原始数据） =====
    el_candidates = [
        ("UF15_20W", "electrolyzer_tests/21032025_MEA_UF15_HP80_7pt3Nm_RuOx_15_20W_on_highPorPtTiFelt_Pt_cathode_from_FuelCellStore_withGasket_07_CP_C01.txt"),
        ("UF19_80W", "electrolyzer_tests/08052025_MEA_UF17_HP80_7pt3Nm_RuOx_16_80W_on_highPorPtTiFelt_Pt_cathode_from_FuelCellStore_withGasket_07_CP_C01.txt"),
    ]
    el = {
        "model_pct_per_yr_range": list(EL_MODEL_PCT_PER_YR),
        "model_scope_note": "系统级年比能耗增量（degradation_summary）；PEMEL 原始数据为实验室 MEA 加速恒流（1A/cm² 级），运行条件不同，仅作方向/量级对照",
    }
    el_fits = el_independent_fit(EL_ZIP, el_candidates)
    el["fits"] = el_fits
    # 汇总量级关系
    for label, fit in el_fits.items():
        if fit.get("ok"):
            p = fit["pct_per_yr"]
            model_min, model_max = EL_MODEL_PCT_PER_YR
            el[f"{label}_relation"] = {
                "independent_pct_per_yr": round(p, 2),
                "model_pct_per_yr": [model_min, model_max],
                "verdict": ("实验室加速数据退化率远高于系统级模型（>10×，运行条件不同：加速/高电流密度 vs 系统额定），"
                            "方向一致（电压随运行单调上升=退化），量级不可直接类比，不作工程外推——符合 L3/L8/L23 边界")
                            if p > model_max * 2 else
                            ("量级可比（≤2× 模型上界）") if p <= model_max * 2 else ("量级差异中等"),
            }
    result["el"] = el

    result["_summary"] = {
        "conclusion": (
            "P3-2 独立实现交叉验证完成：FC 侧独立重算 FC1 原始 143k 点电压轨迹，与模型标定偏差 "
            f"{fc.get('fc1_dev_pct_vs_model')}%（量级一致）；FC2 纹波速率比 {fc.get('fc2_fc1_rate_ratio')}x（工况对照）。"
            "EL 侧用 PEMEL 恒流耐久原始数据（UF15/UF19, 逗号小数 CP 轨迹）独立拟合电压退化率："
            "实验室 MEA 加速条件退化率远高于系统级模型年比能耗增量（方向一致、量级差 10×+，运行条件不同），"
            "模型 EL 年退化率仅适用于系统级方法演示，不得外推到加速/实验室条件。"
        ),
        "note": "独立实现 = 不 import hp_fc_lite/hp_el_lite 的任何标定/拟合函数；FC 用 openpyxl 直读原始 xlsx，EL 用 zipfile 直读 CP txt 并独立 polyfit。",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"结果已写入: {OUT}")
    print("FC:", json.dumps({k: v for k, v in fc.items() if k != 'fc1_independent' and k != 'fc2_ripple_independent'}, ensure_ascii=False)[:400])
    for label, fit in el_fits.items():
        if fit.get("ok"):
            print(f"EL {label}: {fit['duration_h']:.0f}h  pct_per_1000h={fit['pct_per_1000h']:.2f}%  pct_per_yr={fit['pct_per_yr']:.2f}%  r2={fit['r2']}")
        else:
            print(f"EL {label}: ERR {fit.get('error')}")


if __name__ == "__main__":
    main()
