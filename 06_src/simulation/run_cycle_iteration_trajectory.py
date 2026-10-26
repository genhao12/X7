"""Cycle initial-value iteration trajectory for the 3-year block (2016-2018).

Goal: demonstrate that the "3-year block closure" (SOC/H2 first-vs-last delta)
is not merely a one-shot tolerance check but converges under fixed-point
iteration on the initial state.  Each iteration runs 2016->2017->2018 with the
end-of-year SOC/H2 fed forward as the next year's initial state; the final
year-end state becomes the next iteration's starting state.  We record the
per-iteration |SOC_end - SOC_start| and |H2_end - H2_start|/H2_rated closure.

Convergence criterion mirrors the PSA cyclic-steady-state fixed-point
iteration (Chem. Eng. Sci. 2016): stop when both closures drop below a
threshold for the admission gate (5%) — we additionally require the
sequential-difference between consecutive iterations to be small.
"""
import sys, json
import numpy as np
from pathlib import Path

ROOT = Path(r"d:\matlab\C")
sys.path.insert(0, str(ROOT / "06_src"))

from ems.ems_stage2b import Stage2BEMSConfig, EMSState, dispatch_stage2b
from components.annual_input_contract import load_evaluation_years, scale_pv_available

DATA = ROOT / "02_data_processed/tradestreet_pipeline/64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv"
ADM = ROOT / "07_results/rectification_v2_stage2c/search/verified_admissions.json"

SOCC_TOL = 0.05   # admission gate: |SOC_first-last| <= 5% of SOC range (0.8)
H2C_TOL = 0.05    # admission gate: |H2_first-last| / H2_rated <= 5%
SEQ_TOL = 1e-4    # sequential closure change between iterations (converged)
MAX_IT = 10


def run_three_year_block(cfg_ems, load_by_year, pv_by_year, init_soc, init_h2):
    """Run 2016->2017->2018 chained; return (end_soc, end_h2, per_year_soc_end)."""
    s = EMSState(soc=init_soc, h2_kg=init_h2)
    year_end_soc = {}
    for y in (2016, 2017, 2018):
        r = dispatch_stage2b(load_by_year[y], pv_by_year[y], cfg_ems,
                             strategy="battery_first", initial_state=s)
        trace = r.trace
        end_soc = float(np.asarray(trace["soc"])[-1])
        end_h2 = float(np.asarray(trace["h2_kg"])[-1])
        year_end_soc[y] = end_soc
        s = EMSState(soc=end_soc, h2_kg=end_h2)
    return s.soc, s.h2_kg, year_end_soc


def main():
    loaded = load_evaluation_years(DATA)
    adm = json.loads(ADM.read_text(encoding="utf-8"))
    reps = adm["representatives"]

    result = {
        "schema_version": "cycle_iteration_trajectory.1",
        "method": "fixed-point iteration on 3-year-block initial state (2016->2017->2018 chained)",
        "criterion": {
            "closure_tol": {"soc": SOCC_TOL, "h2": H2C_TOL},
            "sequential_tol": SEQ_TOL,
            "reference": "Chem. Eng. Sci. 2016, rigorous criterion for approach to cyclic steady-state (PSA fixed-point iteration)",
        },
        "representatives": {},
    }

    for rep in reps:
        role = rep["representative_role"]
        pv_kwp = float(rep["pv_kwp"]); bat_kwh = float(rep["bat_kwh"])
        pemfc_kw = float(rep["pemfc_kw"]); pemel_kw = float(rep["pemel_kw"])
        h2_kg = float(rep["h2_kg"])
        cfg_ems = Stage2BEMSConfig(pv_kwp=pv_kwp, bat_kwh=bat_kwh,
                                   pemfc_kw=pemfc_kw, pemel_kw=pemel_kw, h2_kg=h2_kg)
        load_by_year = {y: loaded[y]["load_kw"].to_numpy(dtype=float) for y in (2016, 2017, 2018)}
        pv_by_year = {y: scale_pv_available(loaded[y]["pv_raw_kw"].to_numpy(dtype=float),
                                            nominal_kw=pv_kwp) for y in (2016, 2017, 2018)}

        init_soc, init_h2 = 0.50, 0.50 * h2_kg
        traj = []
        prev_soc_closure = prev_h2_closure = None
        converged = False
        for it in range(1, MAX_IT + 1):
            end_soc, end_h2, year_end_soc = run_three_year_block(
                cfg_ems, load_by_year, pv_by_year, init_soc, init_h2)
            soc_closure = abs(end_soc - init_soc)          # SOC fraction delta
            h2_closure = abs(end_h2 - init_h2) / h2_kg     # normalized H2 delta
            record = {
                "iter": it,
                "init_soc": round(init_soc, 6),
                "init_h2_frac": round(init_h2 / h2_kg, 6),
                "end_soc": round(end_soc, 6),
                "end_h2_frac": round(end_h2 / h2_kg, 6),
                "soc_closure": round(soc_closure, 8),
                "h2_closure": round(h2_closure, 8),
                "year_end_soc": {str(y): round(v, 6) for y, v in year_end_soc.items()},
            }
            if prev_soc_closure is not None:
                record["d_soc_closure_vs_prev"] = round(abs(soc_closure - prev_soc_closure), 8)
                record["d_h2_closure_vs_prev"] = round(abs(h2_closure - prev_h2_closure), 8)
            traj.append(record)
            prev_soc_closure, prev_h2_closure = soc_closure, h2_closure
            # fixed-point converged: closure within gate tolerance AND sequential change small
            if soc_closure <= SOCC_TOL and h2_closure <= H2C_TOL and \
               it >= 2 and abs(soc_closure - traj[-2]["soc_closure"]) <= SEQ_TOL and \
               abs(h2_closure - traj[-2]["h2_closure"]) <= SEQ_TOL:
                converged = True
                break
            init_soc, init_h2 = end_soc, end_h2   # feed year-end state as next start

        result["representatives"][role] = {
            "converged": converged,
            "iterations": len(traj),
            "final_soc_closure": traj[-1]["soc_closure"],
            "final_h2_closure": traj[-1]["h2_closure"],
            "trajectory": traj,
        }

    out = ROOT / "07_results/rectification_v2_stage2c/stage3/cycle_iteration_trajectory.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    # report
    print("=== Cycle initial-value iteration trajectory (3-year block) ===")
    for role, v in result["representatives"].items():
        print(f"\n{role}: converged={v['converged']} iterations={v['iterations']}")
        print(f"  final soc_closure={v['final_soc_closure']:.2e}  h2_closure={v['final_h2_closure']:.2e}")
        for rec in v["trajectory"]:
            print(f"    it{rec['iter']}: soc_cl={rec['soc_closure']:.2e} h2_cl={rec['h2_closure']:.2e}  soc {rec['init_soc']:.4f}->{rec['end_soc']:.4f}")


if __name__ == "__main__":
    main()
