"""Stage2b energy balance diagnostic for the best candidate configuration.

Runs a single candidate through the 3-year cycle and produces:
- Annual energy budget (PV, load, FC, EL, BAT, aux, unserved, curtailed)
- Monthly breakdown for seasonality analysis
- Equipment utilization hours
- Hourly LPSP distribution
"""
import sys
import json
import numpy as np
import pandas as pd
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


def load_data(pv_kwp):
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    ref_kw = cfg["pv_source"]["reference_capacity_kw"]  # 233.0
    loaded = load_evaluation_years(DATA_PATH)
    prepared = {}
    for year, frame in loaded.items():
        pv_raw = frame["pv_raw_kw"].to_numpy(dtype=float)
        pv_scaled = scale_pv_available(pv_raw, nominal_kw=pv_kwp)
        prepared[year] = {
            "load": frame["load_kw"].to_numpy(dtype=float),
            "pv": pv_scaled,
        }
    return prepared, cfg


def run_candidate(prepared, pv_kwp, bat_kwh, pemfc_kw, pemel_kw, h2_kg, strategy="battery_first"):
    cfg_ems = Stage2BEMSConfig(
        pv_kwp=pv_kwp, bat_kwh=bat_kwh, pemfc_kw=pemfc_kw,
        pemel_kw=pemel_kw, h2_kg=h2_kg,
    )
    dt_h = cfg_ems.dt_h

    years_order = [2016, 2017, 2018]
    state = EMSState(soc=0.5, h2_kg=h2_kg * 0.5)
    all_traces = {}

    max_reps = 50
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
                strategy=strategy,
                initial_state=state,
                forecast_provider=causal_persistence_forecast,
            )
            # commands: (n,3) -> col0=P_bat(+dis/-chg), col1=P_fc, col2=P_el
            cmds = result.commands
            all_traces[year] = {
                "load": prepared[year]["load"],
                "pv": prepared[year]["pv"],
                "p_bat": cmds[:, 0],
                "p_fc": cmds[:, 1],
                "p_el": cmds[:, 2],
                "trace": result.trace,
                "metrics": result.metrics,
            }
            state = result.final_state

        soc_delta = abs(state.soc - prev_soc)
        h2_frac = abs(state.h2_kg - prev_h2) / max(h2_kg, 1e-9)
        if (soc_delta < 0.001 and h2_frac < 0.001
                and state.fc_on == prev_fc_on
                and state.fc_steps_remaining == prev_fc_rem
                and state.el_on == prev_el_on
                and state.el_steps_remaining == prev_el_rem):
            break

    return all_traces, state, rep + 1


def analyze_energy_balance(all_traces, cfg_ems_config):
    """Produce comprehensive energy balance tables."""
    dt_h = cfg_ems_config.dt_h

    print("=" * 90)
    print("STAGE 2B ENERGY BALANCE DIAGNOSTIC")
    print(f"Configuration: PV={cfg_ems_config.pv_kwp} kWp, BAT={cfg_ems_config.bat_kwh} kWh, "
          f"FC={cfg_ems_config.pemfc_kw} kW, EL={cfg_ems_config.pemel_kw} kW, H2={cfg_ems_config.h2_kg} kg")
    print(f"BAT power: {cfg_ems_config.bat_power_kw:.1f} kW (0.5C)")
    print(f"Aux: standby={cfg_ems_config.standby_kw} kW, PV={cfg_ems_config.pv_aux_fraction}, "
          f"BAT={cfg_ems_config.bat_aux_fraction}, FC={cfg_ems_config.fc_aux_fraction}, EL={cfg_ems_config.el_aux_fraction}")
    print("=" * 90)

    for year in [2016, 2017, 2018]:
        t = all_traces[year]
        tr = t["trace"]
        load = t["load"]
        pv = t["pv"]
        p_bat = t["p_bat"]
        p_fc = t["p_fc"]
        p_el = t["p_el"]
        n = len(load)

        # Extract trace arrays
        p_uns = tr.get("p_uns", np.zeros(n))
        p_cur = tr.get("p_cur", np.zeros(n))
        p_dump = tr.get("p_dump", np.zeros(n))
        p_aux = tr.get("p_aux_total", np.zeros(n))
        soc = tr.get("soc", np.full(n, 0.5))
        h2 = tr.get("h2_kg", np.full(n, 100))
        fc_on = tr.get("fc_on", np.zeros(n))
        el_on = tr.get("el_on", np.zeros(n))

        # Annual totals (kWh)
        load_kwh = np.sum(load) * dt_h
        pv_kwh = np.sum(pv) * dt_h
        bat_kwh = np.sum(p_bat) * dt_h  # positive=discharge, negative=charge
        fc_kwh = np.sum(p_fc) * dt_h
        el_kwh = np.sum(p_el) * dt_h
        uns_kwh = np.sum(p_uns) * dt_h
        cur_kwh = np.sum(p_cur) * dt_h
        dump_kwh = np.sum(p_dump) * dt_h
        aux_kwh = np.sum(p_aux) * dt_h

        lpsp = uns_kwh / load_kwh if load_kwh > 0 else 0
        curtailment_rate = cur_kwh / pv_kwh if pv_kwh > 0 else 0

        # Equipment utilization
        fc_hours = np.sum(fc_on > 0) * dt_h
        el_hours = np.sum(el_on > 0) * dt_h
        bat_active_hours = np.sum(np.abs(p_bat) > 1e-6) * dt_h

        # FC hydrogen consumption (via FC efficiency: P_fc = eta_fc * m_dot * LHV)
        LHV = 33.3  # kWh/kg
        fc_h2_consumed = np.sum(p_fc * dt_h / (cfg_ems_config.eta_fc * LHV)) if cfg_ems_config.eta_fc > 0 else 0
        el_h2_produced = np.sum(p_el * dt_h * cfg_ems_config.eta_el / LHV) if LHV > 0 else 0

        print(f"\n--- {year} ---")
        print(f"  Steps: {n}, Duration: {n*dt_h:.0f} h ({n*dt_h/24:.0f} days)")
        print(f"  Load demand:      {load_kwh:12,.1f} kWh")
        print(f"  PV available:     {pv_kwh:12,.1f} kWh  (CF={pv_kwh/(cfg_ems_config.pv_kwp*8760)*100:.1f}%)")
        print(f"  PV surplus:       {np.sum(np.maximum(0, pv - load)) * dt_h:12,.1f} kWh")
        print(f"  --- Generation ---")
        print(f"  FC output:        {fc_kwh:12,.1f} kWh  ({fc_hours:.0f} h active, EFLH={fc_kwh/cfg_ems_config.pemfc_kw:.0f})")
        print(f"  BAT discharge:    {np.sum(np.maximum(0, p_bat)) * dt_h:12,.1f} kWh  ({bat_active_hours:.0f} h active)")
        print(f"  BAT charge:       {np.sum(np.minimum(0, p_bat)) * dt_h:12,.1f} kWh")
        print(f"  --- Consumption ---")
        print(f"  EL input:         {el_kwh:12,.1f} kWh  ({el_hours:.0f} h active, EFLH={el_kwh/cfg_ems_config.pemel_kw:.0f})")
        print(f"  Auxiliary total:  {aux_kwh:12,.1f} kWh  ({aux_kwh/load_kwh*100:.1f}% of load)")
        print(f"  --- Hydrogen ---")
        print(f"  H2 produced (EL): {el_h2_produced:12,.1f} kg")
        print(f"  H2 consumed (FC): {fc_h2_consumed:12,.1f} kg")
        print(f"  H2 net:           {el_h2_produced - fc_h2_consumed:12,.1f} kg")
        print(f"  H2 start: {h2[0]:.2f} kg, end: {h2[-1]:.2f} kg, delta: {h2[-1]-h2[0]:.2f} kg")
        print(f"  --- Reliability ---")
        print(f"  Unserved energy:  {uns_kwh:12,.1f} kWh  LPSP={lpsp*100:.2f}%")
        print(f"  PV curtailed:     {cur_kwh:12,.1f} kWh  ({curtailment_rate*100:.1f}%)")
        print(f"  FC dump:          {dump_kwh:12,.1f} kWh")
        print(f"  --- SOC ---")
        print(f"  SOC start: {soc[0]*100:.1f}%, end: {soc[-1]*100:.1f}%, min: {soc.min()*100:.1f}%, max: {soc.max()*100:.1f}%")

        # Monthly breakdown
        # Parse timestamps from trace or use step indices
        # Assume 15-min steps, 35040 steps/year (2016 is leap, others 35040)
        steps_per_month = []
        if year == 2016:
            days = [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
        else:
            days = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
        idx = 0
        monthly_data = []
        for m, d in enumerate(days):
            steps = d * 96  # 96 steps per day at 15-min
            end = min(idx + steps, n)
            monthly_data.append((idx, end, m + 1))
            idx = end

        print(f"\n  Monthly breakdown:")
        print(f"  {'Month':>5} {'Load':>10} {'PV':>10} {'FC':>10} {'EL':>10} {'Uns':>10} {'LPSP':>7} {'Cur':>10}")
        print(f"  {'':>5} {'(MWh)':>10} {'(MWh)':>10} {'(MWh)':>10} {'(MWh)':>10} {'(MWh)':>10} {'(%)':>7} {'(MWh)':>10}")
        for start_i, end_i, month in monthly_data:
            m_load = np.sum(load[start_i:end_i]) * dt_h / 1000
            m_pv = np.sum(pv[start_i:end_i]) * dt_h / 1000
            m_fc = np.sum(p_fc[start_i:end_i]) * dt_h / 1000
            m_el = np.sum(p_el[start_i:end_i]) * dt_h / 1000
            m_uns = np.sum(p_uns[start_i:end_i]) * dt_h / 1000
            m_cur = np.sum(p_cur[start_i:end_i]) * dt_h / 1000
            m_lpsp = m_uns / m_load * 100 if m_load > 0 else 0
            print(f"  {month:>5} {m_load:10.1f} {m_pv:10.1f} {m_fc:10.1f} {m_el:10.1f} {m_uns:10.1f} {m_lpsp:7.1f} {m_cur:10.1f}")


if __name__ == "__main__":
    # First load raw data for summary at reference capacity
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    ref_kw = cfg["pv_source"]["reference_capacity_kw"]
    loaded_raw = load_evaluation_years(DATA_PATH)
    print("\n=== RAW DATA SUMMARY (reference 233 kWp) ===")
    for year in [2016, 2017, 2018]:
        frame = loaded_raw[year]
        pv_raw = frame["pv_raw_kw"].to_numpy(dtype=float)
        load_raw = frame["load_kw"].to_numpy(dtype=float)
        dt_h = 0.25
        print(f"  {year}: Load={np.sum(load_raw)*dt_h/1000:.1f} MWh, PV_raw(233kWp)={np.sum(pv_raw)*dt_h/1000:.1f} MWh, "
              f"PV/Load={np.sum(pv_raw)/np.sum(load_raw):.2f}, "
              f"Peak load={np.max(load_raw):.1f} kW, Peak PV={np.max(pv_raw):.1f} kW")

    # Best tier1 candidate
    pv_kwp = 319
    prepared, cfg = load_data(pv_kwp)

    print(f"\n\n=== SCALED DATA (PV {pv_kwp} kWp) ===")
    for year in [2016, 2017, 2018]:
        load = prepared[year]["load"]
        pv = prepared[year]["pv"]
        dt_h = 0.25
        print(f"  {year}: Load={np.sum(load)*dt_h/1000:.1f} MWh, PV={np.sum(pv)*dt_h/1000:.1f} MWh, "
              f"PV/Load={np.sum(pv)/np.sum(load):.2f}, "
              f"Peak load={np.max(load):.1f} kW, Peak PV={np.max(pv):.1f} kW")

    print("\n\n=== BEST TIER1 CANDIDATE (319/1135/87/237/1664) ===")
    traces, final_state, reps = run_candidate(
        prepared, pv_kwp=319, bat_kwh=1135, pemfc_kw=87, pemel_kw=237, h2_kg=1664,
        strategy="battery_first"
    )
    cfg_ems = Stage2BEMSConfig(pv_kwp=319, bat_kwh=1135, pemfc_kw=87, pemel_kw=237, h2_kg=1664)
    print(f"Cycle converged in {reps} repetitions")
    print(f"Final state: SOC={final_state.soc:.4f}, H2={final_state.h2_kg:.2f} kg, "
          f"FC_on={final_state.fc_on}, EL_on={final_state.el_on}")
    analyze_energy_balance(traces, cfg_ems)
