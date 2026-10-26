"""Quick feasibility scan for expanded PV capacity domain.

Tests PV = 350, 400, 450, 500 kWp with proportional EL and other equipment sizing.
Runs 3-year cycle (2016-2017-2018) with battery_first strategy and reports LPSP.
This is a diagnostic tool only -- no search, no gate, no frozen modules.
"""
import sys
import json
import itertools
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "06_src"))

from ems.ems_stage2b import (
    Stage2BEMSConfig, EMSState, dispatch_stage2b,
    causal_persistence_forecast,
)
from components.annual_input_contract import load_evaluation_years, scale_pv_available

ROOT = Path(__file__).resolve().parents[2]
DATA_PATH = ROOT / "02_data_processed" / "tradestreet_pipeline" / "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv"
CONFIG_PATH = ROOT / "05_config" / "rectification_v2_stage2b.json"


def load_scaled_data(pv_kwp):
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    loaded = load_evaluation_years(DATA_PATH)
    prepared = {}
    for year, frame in loaded.items():
        pv_raw = frame["pv_raw_kw"].to_numpy(dtype=float)
        pv_scaled = scale_pv_available(pv_raw, nominal_kw=pv_kwp)
        prepared[year] = {
            "load": frame["load_kw"].to_numpy(dtype=float),
            "pv": pv_scaled,
        }
    return prepared


def run_three_year_cycle(prepared, cfg_ems, max_reps=50):
    """Run 2016-2017-2018 cycle until state convergence."""
    years_order = [2016, 2017, 2018]
    state = EMSState(soc=0.5, h2_kg=cfg_ems.h2_kg * 0.5)
    yearly_results = {}

    for rep in range(max_reps):
        prev_soc = state.soc
        prev_h2 = state.h2_kg
        prev_fc_on = state.fc_on
        prev_fc_rem = state.fc_steps_remaining
        prev_el_on = state.el_on
        prev_el_rem = state.el_steps_remaining

        for year in years_order:
            result = dispatch_stage2b(
                load_kw=prepared[year]["load"],
                pv_kw=prepared[year]["pv"],
                config=cfg_ems,
                strategy="battery_first",
                initial_state=state,
                forecast_provider=causal_persistence_forecast,
            )
            yearly_results[year] = {
                "load": prepared[year]["load"],
                "pv": prepared[year]["pv"],
                "commands": result.commands,
                "trace": result.trace,
                "metrics": result.metrics,
            }
            state = result.final_state

        soc_delta = abs(state.soc - prev_soc)
        h2_frac = abs(state.h2_kg - prev_h2) / max(cfg_ems.h2_kg, 1e-9)
        if (soc_delta < 0.001 and h2_frac < 0.001
                and state.fc_on == prev_fc_on
                and state.fc_steps_remaining == prev_fc_rem
                and state.el_on == prev_el_on
                and state.el_steps_remaining == prev_el_rem):
            break

    return yearly_results, state, rep + 1


def compute_metrics(yearly_results, cfg_ems):
    """Compute LPSP, energy totals, and monthly breakdown."""
    dt_h = cfg_ems.dt_h
    total_load = 0
    total_uns = 0
    total_pv = 0
    total_fc = 0
    total_el = 0
    total_aux = 0
    total_cur = 0
    yearly = {}
    monthly_lpsp = {}

    for year in [2016, 2017, 2018]:
        yr = yearly_results[year]
        tr = yr["trace"]
        load = yr["load"]
        pv = yr["pv"]
        cmds = yr["commands"]
        n = len(load)

        p_bat = cmds[:, 0]
        p_fc = cmds[:, 1]
        p_el = cmds[:, 2]
        p_uns = tr["p_uns"]
        p_cur = tr["p_cur"]
        p_aux = tr["p_aux_total"]
        soc = tr["soc"]
        h2 = tr["h2_kg"]

        load_kwh = np.sum(load) * dt_h
        pv_kwh = np.sum(pv) * dt_h
        fc_kwh = np.sum(p_fc) * dt_h
        el_kwh = np.sum(p_el) * dt_h
        uns_kwh = np.sum(p_uns) * dt_h
        aux_kwh = np.sum(p_aux) * dt_h
        cur_kwh = np.sum(p_cur) * dt_h
        lpsp = uns_kwh / load_kwh if load_kwh > 0 else 0

        total_load += load_kwh
        total_uns += uns_kwh
        total_pv += pv_kwh
        total_fc += fc_kwh
        total_el += el_kwh
        total_aux += aux_kwh
        total_cur += cur_kwh

        yearly[year] = {
            "load_mwh": load_kwh / 1000,
            "pv_mwh": pv_kwh / 1000,
            "fc_mwh": fc_kwh / 1000,
            "el_mwh": el_kwh / 1000,
            "uns_mwh": uns_kwh / 1000,
            "aux_mwh": aux_kwh / 1000,
            "lpsp": lpsp,
            "soc_start": soc[0],
            "soc_end": soc[-1],
            "h2_start": h2[0],
            "h2_end": h2[-1],
        }

        # Monthly
        if year == 2016:
            days = [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
        else:
            days = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
        idx = 0
        worst_month_lpsp = 0
        worst_month = 0
        for m, d in enumerate(days):
            steps = d * 96
            end = min(idx + steps, n)
            m_load = np.sum(load[idx:end]) * dt_h
            m_uns = np.sum(p_uns[idx:end]) * dt_h
            m_lpsp = m_uns / m_load if m_load > 0 else 0
            if m_lpsp > worst_month_lpsp:
                worst_month_lpsp = m_lpsp
                worst_month = m + 1
            idx = end
        monthly_lpsp[year] = {"worst_month": worst_month, "worst_lpsp": worst_month_lpsp}

    three_year_lpsp = total_uns / total_load if total_load > 0 else 0
    return {
        "three_year_lpsp": three_year_lpsp,
        "total_load_mwh": total_load / 1000,
        "total_pv_mwh": total_pv / 1000,
        "total_fc_mwh": total_fc / 1000,
        "total_el_mwh": total_el / 1000,
        "total_aux_mwh": total_aux / 1000,
        "total_cur_mwh": total_cur / 1000,
        "yearly": yearly,
        "monthly_lpsp": monthly_lpsp,
    }


def main():
    # Define test grid
    pv_values = [350, 400, 450, 500, 600]
    bat_values = [1200, 1800, 2400]
    fc_values = [87, 150, 200]
    el_values = [237, 350, 450]
    h2_values = [1664, 3000, 5000]

    # Smart combinations: keep base ratio from best tier1, scale with PV
    # Base: PV=319, BAT=1135, FC=87, EL=237, H2=1664
    configs = []

    # Series 1: Scale everything proportionally with PV
    for pv in pv_values:
        ratio = pv / 319
        configs.append({
            "pv_kwp": pv,
            "bat_kwh": round(1200 * ratio),
            "pemfc_kw": round(100 * ratio),
            "pemel_kw": round(250 * ratio),
            "h2_kg": round(2000 * ratio),
            "label": f"scaled_{pv}kWp",
        })

    # Series 2: Fix PV, vary EL (to test if EL is the bottleneck)
    for pv in [400, 500]:
        for el in [300, 400, 500]:
            for h2 in [3000, 5000]:
                configs.append({
                    "pv_kwp": pv,
                    "bat_kwh": 1500,
                    "pemfc_kw": 150,
                    "pemel_kw": el,
                    "h2_kg": h2,
                    "label": f"pv{pv}_el{el}_h2{h2}",
                })

    # Series 3: Large system (generous sizing)
    configs.append({
        "pv_kwp": 500, "bat_kwh": 2400, "pemfc_kw": 200,
        "pemel_kw": 400, "h2_kg": 6000, "label": "generous_500",
    })
    configs.append({
        "pv_kwp": 600, "bat_kwh": 3000, "pemfc_kw": 250,
        "pemel_kw": 500, "h2_kg": 8000, "label": "generous_600",
    })

    print(f"Feasibility scan: {len(configs)} configurations")
    print("=" * 120)
    print(f"{'Config':<20} {'PV':>5} {'BAT':>6} {'FC':>5} {'EL':>5} {'H2':>6} "
          f"{'LPSP3yr':>8} {'LPSP2016':>9} {'LPSP2017':>9} {'LPSP2018':>9} "
          f"{'WorstMo':>8} {'WM-LPSP':>8} {'Reps':>5}")
    print("-" * 120)

    results = []
    for i, cfg in enumerate(configs):
        pv_kwp = cfg["pv_kwp"]
        prepared = load_scaled_data(pv_kwp)
        ems_cfg = Stage2BEMSConfig(
            pv_kwp=pv_kwp, bat_kwh=cfg["bat_kwh"],
            pemfc_kw=cfg["pemfc_kw"], pemel_kw=cfg["pemel_kw"],
            h2_kg=cfg["h2_kg"],
        )
        yearly_results, final_state, reps = run_three_year_cycle(prepared, ems_cfg)
        metrics = compute_metrics(yearly_results, ems_cfg)

        # Find worst month across all years
        worst_mo_lpsp = 0
        worst_mo_label = ""
        for year in [2016, 2017, 2018]:
            ml = metrics["monthly_lpsp"][year]
            if ml["worst_lpsp"] > worst_mo_lpsp:
                worst_mo_lpsp = ml["worst_lpsp"]
                worst_mo_label = f"{year}-{ml['worst_month']:02d}"

        line = (
            f"{cfg['label']:<20} {pv_kwp:>5} {cfg['bat_kwh']:>6} {cfg['pemfc_kw']:>5} "
            f"{cfg['pemel_kw']:>5} {cfg['h2_kg']:>6} "
            f"{metrics['three_year_lpsp']*100:>7.2f}% "
            f"{metrics['yearly'][2016]['lpsp']*100:>8.2f}% "
            f"{metrics['yearly'][2017]['lpsp']*100:>8.2f}% "
            f"{metrics['yearly'][2018]['lpsp']*100:>8.2f}% "
            f"{worst_mo_label:>8} {worst_mo_lpsp*100:>7.2f}% "
            f"{reps:>5}"
        )
        print(line)

        results.append({
            "config": cfg,
            "metrics": metrics,
            "worst_month_lpsp": worst_mo_lpsp,
            "worst_month_label": worst_mo_label,
            "reps": reps,
        })

    # Summary
    print("\n" + "=" * 120)
    print("SUMMARY: Configurations meeting LPSP thresholds")
    print("-" * 120)
    for threshold in [0.05, 0.03, 0.01]:
        passing = [r for r in results if r["metrics"]["three_year_lpsp"] <= threshold]
        print(f"\n  LPSP <= {threshold*100:.0f}%: {len(passing)} configurations")
        for r in passing[:10]:
            cfg = r["config"]
            m = r["metrics"]
            print(f"    {cfg['label']:<20} PV={cfg['pv_kwp']} BAT={cfg['bat_kwh']} "
                  f"FC={cfg['pemfc_kw']} EL={cfg['pemel_kw']} H2={cfg['h2_kg']} "
                  f"3yr={m['three_year_lpsp']*100:.2f}% "
                  f"worst={r['worst_month_label']}({r['worst_month_lpsp']*100:.1f}%)")

    # Detailed breakdown for the best configuration
    best = min(results, key=lambda r: r["metrics"]["three_year_lpsp"])
    print(f"\n\n{'='*120}")
    print(f"BEST CONFIG DETAIL: {best['config']['label']}")
    cfg = best["config"]
    print(f"  PV={cfg['pv_kwp']} kWp, BAT={cfg['bat_kwh']} kWh, "
          f"FC={cfg['pemfc_kw']} kW, EL={cfg['pemel_kw']} kW, H2={cfg['h2_kg']} kg")
    print(f"  3-year LPSP: {best['metrics']['three_year_lpsp']*100:.3f}%")
    for year in [2016, 2017, 2018]:
        y = best["metrics"]["yearly"][year]
        ml = best["metrics"]["monthly_lpsp"][year]
        print(f"\n  {year}:")
        print(f"    Load={y['load_mwh']:.1f} MWh, PV={y['pv_mwh']:.1f} MWh, "
              f"FC={y['fc_mwh']:.1f} MWh, EL={y['el_mwh']:.1f} MWh, "
              f"Aux={y['aux_mwh']:.1f} MWh")
        print(f"    LPSP={y['lpsp']*100:.2f}%, Unserved={y['uns_mwh']:.1f} MWh")
        print(f"    SOC: {y['soc_start']*100:.1f}% -> {y['soc_end']*100:.1f}%")
        print(f"    H2: {y['h2_start']:.1f} -> {y['h2_end']:.1f} kg")
        print(f"    Worst month: {ml['worst_month']} (LPSP={ml['worst_lpsp']*100:.1f}%)")


if __name__ == "__main__":
    main()
