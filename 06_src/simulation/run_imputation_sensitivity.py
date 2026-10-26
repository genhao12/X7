"""Imputation sensitivity for 165 load NaN points (2016=101, 2017=62, 2018=2).

Compares three filling strategies on p_building_load_reconstructed_kW:
  A. ffill/bfill        (current pipeline default)
  B. linear interpolate
  C. seasonal same-month/same-day-type mean of other years
Impact assessed on: LPSP / SOC-H2 closure (admission metrics), annual damage
(D/yr), EOL lifetime (20y event model), LCOE — for the 5 Stage2c representatives.
"""
import sys, json
import numpy as np
import pandas as pd
from pathlib import Path

ROOT = Path(r"d:\matlab\C")
sys.path.insert(0, str(ROOT / "06_src"))

from ems.ems_stage2b import Stage2BEMSConfig, EMSState, dispatch_stage2b
from components.annual_input_contract import scale_pv_available
from simulation.run_stage3_full_analysis import (
    build_c1_config, compute_annual_damage, compute_lifetime_with_events,
    compute_economics, hp_bat_default,
)

DATA = ROOT / "02_data_processed/tradestreet_pipeline/64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv"
ADM = ROOT / "07_results/rectification_v2_stage2c/search/verified_admissions.json"

raw = pd.read_csv(DATA, parse_dates=["timestamp_local_civil"])
years = {}
for y in (2016, 2017, 2018):
    src = raw.loc[raw["local_civil_year"] == y].copy()
    years[y] = {
        "load": src["p_building_load_reconstructed_kW"].to_numpy(dtype=float),
        "pv": src["p_pv_available_reconstructed_nonnegative_kW"].to_numpy(dtype=float),
        "day_type": src["day_type"].to_numpy(dtype=object),
        "month": src["timestamp_local_civil"].dt.month.to_numpy(),
        "day": src["timestamp_local_civil"].dt.day.to_numpy(),
        "hour": src["timestamp_local_civil"].dt.hour.to_numpy(),
        "minute": src["timestamp_local_civil"].dt.minute.to_numpy(),
    }

def load_fill_method(method: str) -> dict:
    """Return per-year load filled by the given method."""
    out = {}
    for y in years:
        load = years[y]["load"].copy()
        if method == "ffill_bfill":
            out[y] = pd.Series(load).ffill().bfill().to_numpy(dtype=float)
        elif method == "linear":
            s = pd.Series(load)
            out[y] = s.interpolate(method="linear", limit_direction="both").to_numpy(dtype=float)
        elif method == "seasonal_mean":
            # same month+day+hour+minute mean over other years (2016/2017/2018)
            load3 = {yy: years[yy]["load"] for yy in years}
            filled = load.copy()
            nan_mask = np.isnan(filled)
            for idx in np.where(nan_mask)[0]:
                m = years[y]["month"][idx]; d = years[y]["day"][idx]
                h = years[y]["hour"][idx]; mi = years[y]["minute"][idx]
                vals = []
                for yy in (2016, 2017, 2018):
                    if yy == y:
                        continue
                    yy_mask = ((years[yy]["month"] == m) & (years[yy]["day"] == d) &
                               (years[yy]["hour"] == h) & (years[yy]["minute"] == mi))
                    vv = load3[yy][yy_mask]
                    vv = vv[~np.isnan(vv)]
                    if vv.size:
                        vals.append(vv.mean())
                filled[idx] = np.mean(vals) if vals else np.nan
            # last-resort global mean
            if np.isnan(filled).any():
                gm = np.nanmean(load)
                filled[np.isnan(filled)] = gm
            out[y] = filled
        else:
            raise ValueError(method)
    return out

def run_one(rep: dict, load_filled: dict, year: int = 2017) -> dict:
    pv_kwp = float(rep["pv_kwp"]); bat_kwh = float(rep["bat_kwh"])
    pemfc_kw = float(rep["pemfc_kw"]); pemel_kw = float(rep["pemel_kw"])
    h2_kg = float(rep["h2_kg"])
    cfg_ems = Stage2BEMSConfig(pv_kwp=pv_kwp, bat_kwh=bat_kwh,
                               pemfc_kw=pemfc_kw, pemel_kw=pemel_kw, h2_kg=h2_kg)
    c1 = build_c1_config(rep)

    cmds_by_year = {}
    lpsp_by_year = {}
    soc_closure_by_year = {}
    h2_closure_by_year = {}
    for y in (2016, 2017, 2018):
        frame = years[y]
        pv_scaled = scale_pv_available(frame["pv"], nominal_kw=pv_kwp)
        load = load_filled[y]
        initial = EMSState(soc=0.50, h2_kg=h2_kg * 0.50)
        res = dispatch_stage2b(load, pv_scaled, cfg_ems, strategy="battery_first",
                               initial_state=initial)
        cmds_by_year[y] = res.commands
        trace = res.trace if hasattr(res, "trace") else {}
        # LPSP = sum(p_uns)*dt / sum(load)*dt (p_uns: unserved power trace)
        if isinstance(trace, dict) and "p_uns" in trace:
            unserved_kwh = float(np.sum(np.asarray(trace["p_uns"])) * 0.25)
        else:
            unserved_kwh = 0.0
        lpsp_by_year[y] = unserved_kwh / max(float(np.sum(load) * 0.25), 1e-9)
        if isinstance(trace, dict) and "soc" in trace:
            soc = np.asarray(trace["soc"])
            soc_closure_by_year[y] = abs(soc[-1] - soc[0])
            h2 = np.asarray(trace.get("h2_kg", np.full(len(soc), h2_kg)))
            h2_closure_by_year[y] = abs(h2[-1] - h2[0]) / max(h2_kg, 1e-9)
        else:
            soc_closure_by_year[y] = float("nan")
            h2_closure_by_year[y] = float("nan")

    # annual damage: 3-year mean
    dmg_by_year = {y: compute_annual_damage(cmds_by_year[y], c1,
                                            D_state={"bat": 0.0, "fc": 0.0, "el": 0.0})
                   for y in (2016, 2017, 2018)}
    dD_bat = float(np.mean([dmg_by_year[y]["dD_bat"] for y in (2016, 2017, 2018)]))
    dD_fc = float(np.mean([dmg_by_year[y]["dD_fc"] for y in (2016, 2017, 2018)]))
    dD_el = float(np.mean([dmg_by_year[y]["dD_el"] for y in (2016, 2017, 2018)]))

    eol = compute_lifetime_with_events(cmds_by_year, c1, hp_bat_func=hp_bat_default,
                                       horizon_years=20,
                                       downtime_steps={"bat": 96, "fc": 48, "el": 48})
    econ = compute_economics(rep, {"dD_bat": dD_bat, "dD_fc": dD_fc, "dD_el": dD_el}, eol)
    return {
        "dD_bat": dD_bat, "dD_fc": dD_fc, "dD_el": dD_el,
        "life_bat": eol["lifetime_years"]["bat"], "life_fc": eol["lifetime_years"]["fc"],
        "life_el": eol["lifetime_years"]["el"],
        "lcoe": econ["lcoe_cny_per_kwh"], "npc": econ["npc_cny"],
        "lpsp_2017": lpsp_by_year[2017],
        "soc_closure_2017": soc_closure_by_year[2017],
        "h2_closure_2017": h2_closure_by_year[2017],
    }


def main():
    adm = json.loads(ADM.read_text(encoding="utf-8"))
    reps = adm["representatives"]
    methods = ["ffill_bfill", "linear", "seasonal_mean"]
    filled = {m: load_fill_method(m) for m in methods}

    result = {
        "schema_version": "imputation_sensitivity.1",
        "missing_points": {"total": 165, "by_year": {"2016": 101, "2017": 62, "2018": 2},
                           "fraction": 165 / 105216},
        "methods": {"ffill_bfill": "前向-后向填充（现状）",
                    "linear": "线性插值",
                    "seasonal_mean": "同月同日同时刻 3 年均值填充"},
        "representatives": {},
    }
    for rep in reps:
        role = rep["representative_role"]
        base = None
        rows = {}
        for m in methods:
            r = run_one(rep, filled[m])
            rows[m] = r
            if base is None:
                base = r
        # relative deviation vs ffill/bfill (current baseline)
        rel = {}
        for m in methods:
            if m == "ffill_bfill":
                rel[m] = 0.0
            else:
                rel[m] = {
                    "dD_bat": abs(rows[m]["dD_bat"] - rows["ffill_bfill"]["dD_bat"]) / max(rows["ffill_bfill"]["dD_bat"], 1e-12),
                    "life_bat": abs(rows[m]["life_bat"] - rows["ffill_bfill"]["life_bat"]) / max(rows["ffill_bfill"]["life_bat"], 1e-12),
                    "lcoe": abs(rows[m]["lcoe"] - rows["ffill_bfill"]["lcoe"]) / max(rows["ffill_bfill"]["lcoe"], 1e-12),
                    "lpsp": abs(rows[m]["lpsp_2017"] - rows["ffill_bfill"]["lpsp_2017"]),
                }
        result["representatives"][role] = {"methods": rows, "rel_dev_vs_ffill": rel}

    out = ROOT / "07_results/rectification_v2_stage2c/stage3/imputation_sensitivity.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    # report
    print("=== Imputation sensitivity (165 load NaN: 2016=101, 2017=62, 2018=2) ===")
    print("config | method | dD_bat | life_bat | LCOE | LPSP_2017")
    for role, v in result["representatives"].items():
        for m in methods:
            r = v["methods"][m]
            print(f"{role:18s} {m:14s} {r['dD_bat']:.5f} {r['life_bat']:.2f}y {r['lcoe']:.4f} {r['lpsp_2017']:.4e}")
        print()
    print("max rel dev (vs ffill/bfill):")
    for role, v in result["representatives"].items():
        for m in methods[1:]:
            rd = v["rel_dev_vs_ffill"][m]
            print(f"  {role} {m}: dD {rd['dD_bat']*100:.4f}%  life {rd['life_bat']*100:.4f}%  LCOE {rd['lcoe']*100:.4f}%  LPSP {rd['lpsp']:.2e}")


if __name__ == "__main__":
    main()
