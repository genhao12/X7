"""Stage2b hourly EMS bottleneck audit runner."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "06_src") not in sys.path:
    sys.path.insert(0, str(ROOT / "06_src"))

from components.annual_input_contract import (
    PVInputContract,
    load_evaluation_years,
    scale_pv_available,
)
from ems.ems_stage2b import (
    DispatchResult,
    EMSState,
    Stage2BEMSConfig,
    dispatch_stage2b,
)
from simulation.stage2b_capacity_gate import (
    aggregate_cycle_metrics,
    solve_stage2b_three_year_cycle,
)
from simulation.stage2b_legacy_governance import reproduce_legacy_defects


def build_stepwise_audit(
    load_kw: np.ndarray,
    pv_kw: np.ndarray,
    result: DispatchResult,
    config: Stage2BEMSConfig,
    timestamps: Optional[np.ndarray] = None,
) -> pd.DataFrame:
    """Convert a dispatch trace into explicit supply/absorption cause diagnostics."""
    load = np.asarray(load_kw, dtype=float)
    pv = np.asarray(pv_kw, dtype=float)
    trace = result.trace
    commands = result.commands
    n = len(load)
    if timestamps is None:
        timestamps = np.arange(n)
    h2 = np.asarray(trace["h2_kg"], dtype=float)
    soc = np.asarray(trace["soc"], dtype=float)
    h2_min = config.h2_kg * config.h2_min_fraction
    h2_max = config.h2_kg * config.h2_max_fraction
    fc_available = np.minimum(
        config.pemfc_kw,
        np.maximum(0.0, (h2 - h2_min) * config.eta_fc * 33.3 / config.dt_h),
    )
    el_available = np.minimum(
        config.pemel_kw,
        np.maximum(0.0, (h2_max - h2) * 33.3 / (config.eta_el * config.dt_h)),
    )
    auxiliary = np.asarray(trace["p_aux_total"], dtype=float)
    net = pv - load - auxiliary
    deficit = np.maximum(-net, 0.0)
    surplus = np.maximum(net, 0.0)
    battery_supply = np.asarray(trace["battery_supply_headroom_kw"], dtype=float)
    battery_absorption = np.asarray(
        trace["battery_absorption_headroom_kw"], dtype=float
    )
    p_uns = np.asarray(trace["p_uns"], dtype=float)
    p_cur = np.asarray(trace["p_cur"], dtype=float)

    deficit_cause = np.full(n, "none", dtype=object)
    uns = p_uns > 1e-9
    bat_empty = soc <= config.soc_min + 1e-9
    h2_empty = h2 <= h2_min + 1e-9
    rated_ceiling = deficit > config.bat_power_kw + config.pemfc_kw + 1e-9
    deficit_cause[uns & bat_empty & h2_empty] = "battery_and_h2_inventory_empty"
    deficit_cause[uns & bat_empty & ~h2_empty] = "battery_empty_fc_state_or_power_limit"
    deficit_cause[uns & ~bat_empty & h2_empty] = "h2_empty_battery_state_or_power_limit"
    deficit_cause[uns & rated_ceiling] = "bat_fc_rated_power_ceiling"
    deficit_cause[uns & (deficit_cause == "none")] = "minimum_stable_or_state_machine_limit"

    curtailment_cause = np.full(n, "none", dtype=object)
    curtailed = p_cur > 1e-9
    el_at_power = commands[:, 2] >= config.pemel_kw - 1e-9
    storage_full = (battery_absorption <= 1e-9) & (el_available <= 1e-9)
    curtailment_cause[curtailed & el_at_power] = "electrolyzer_rated_power_limit"
    curtailment_cause[curtailed & storage_full] = "battery_and_h2_capacity_full"
    curtailment_cause[curtailed & (curtailment_cause == "none")] = "combined_absorption_limit"

    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "load_kw": load,
            "pv_kw": pv,
            "p_aux_kw": auxiliary,
            "deficit_kw": deficit,
            "battery_available_supply_kw": battery_supply,
            "fc_available_supply_kw": fc_available,
            "surplus_kw": surplus,
            "battery_available_absorption_kw": battery_absorption,
            "el_available_absorption_kw": el_available,
            "soc": soc,
            "h2_kg": h2,
            "p_bat_kw": commands[:, 0],
            "p_fc_kw": commands[:, 1],
            "p_el_kw": commands[:, 2],
            "h2_production_kg": trace["h2_prod_kg"],
            "h2_consumption_kg": trace["h2_cons_kg"],
            "h2_leakage_kg": trace["h2_leak_kg"],
            "p_uns_kw": p_uns,
            "p_cur_kw": p_cur,
            "p_dump_kw": trace["p_dump"],
            "power_residual_kw": trace["power_residual_kw"],
            "h2_residual_kg": trace["h2_residual_kg"],
            "deficit_cause": deficit_cause,
            "curtailment_cause": curtailment_cause,
        }
    )


def summarize_audit_frames(
    frames, config: Stage2BEMSConfig
) -> dict:
    """Aggregate hourly causes into an auditable equipment-boundary trigger."""
    frame = pd.concat(list(frames), ignore_index=True)
    triggers = set()
    deficit_causes = set(frame.loc[frame["p_uns_kw"] > 1e-9, "deficit_cause"])
    curtail_causes = set(
        frame.loc[frame["p_cur_kw"] > 1e-9, "curtailment_cause"]
    )
    if any("battery" in cause for cause in deficit_causes):
        triggers.add("BAT_kWh")
    if any("h2" in cause for cause in deficit_causes):
        triggers.add("H2_kg")
    if any(
        cause in {"bat_fc_rated_power_ceiling", "minimum_stable_or_state_machine_limit"}
        or "fc_" in cause
        for cause in deficit_causes
    ):
        triggers.add("PEMFC_kW")
    if any("electrolyzer" in cause or "absorption" in cause for cause in curtail_causes):
        triggers.add("PEMEL_kW")
    if "battery_and_h2_capacity_full" in curtail_causes:
        triggers.update(("BAT_kWh", "H2_kg"))
    return {
        "unserved_energy_kwh": float(frame["p_uns_kw"].sum() * config.dt_h),
        "curtailed_energy_kwh": float(frame["p_cur_kw"].sum() * config.dt_h),
        "dumped_energy_kwh": float(frame["p_dump_kw"].sum() * config.dt_h),
        "equipment_upper_bound_trigger": sorted(triggers),
        "deficit_causes": sorted(deficit_causes),
        "curtailment_causes": sorted(curtail_causes),
        "power_residual_abs_max_kW": float(frame["power_residual_kw"].abs().max()),
        "h2_residual_abs_max_kg": float(frame["h2_residual_kg"].abs().max()),
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _code_hash() -> str:
    digest = hashlib.sha256()
    base_files = (
        "06_src/ems/ems_stage2b.py",
        "06_src/simulation/stage2b_capacity_gate.py",
        "06_src/simulation/run_stage2b_ems_audit.py",
        "06_src/simulation/run_stage2b_adaptive_search.py",
    )
    stage2c_files = (
        "06_src/simulation/run_stage2c_ems_audit.py",
        "06_src/simulation/run_stage2c_adaptive_search.py",
    )
    for relative in base_files:
        path = ROOT / relative
        digest.update(relative.encode("utf-8"))
        if path.exists():
            digest.update(path.read_bytes())
    for relative in stage2c_files:
        path = ROOT / relative
        if path.exists():
            digest.update(relative.encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _ems_config(anchor: dict, cfg: dict) -> Stage2BEMSConfig:
    ems = cfg["ems"]
    aux = cfg["auxiliary"]
    return Stage2BEMSConfig(
        pv_kwp=float(anchor["PV_kWp"]),
        bat_kwh=float(anchor["BAT_kWh"]),
        pemfc_kw=float(anchor["PEMFC_kW"]),
        pemel_kw=float(anchor["PEMEL_kW"]),
        h2_kg=float(anchor["H2_kg"]),
        soc_min=float(ems["soc_min"]),
        soc_max=float(ems["soc_max"]),
        dt_min=float(ems["dt_min"]),
        h2_min_fraction=float(ems["h2_min_fraction"]),
        h2_max_fraction=float(ems["h2_max_fraction"]),
        h2_leakage_per_day=float(ems["h2_leakage_per_day"]),
        min_stable_load_fc=float(ems["fc_min_stable_fraction"]),
        min_stable_load_el=float(ems["el_min_stable_fraction"]),
        minimum_run_steps=int(ems["minimum_run_steps"]),
        standby_kw=float(aux["standby_kW"]),
        pv_aux_fraction=float(aux["pv_fraction_rated_when_on"]),
        bat_aux_fraction=float(aux["bat_fraction_rated_when_on"]),
        fc_aux_fraction=float(aux["fc_fraction_rated_when_on"]),
        el_aux_fraction=float(aux["el_fraction_rated_when_on"]),
        eta_fc=float(ems["fc_efficiency_lhv"]),
        eta_el=float(ems["el_efficiency_lhv"]),
        bat_c_rate=float(ems["battery_c_rate"]),
    )


def run_audit(
    config_path: Path,
    data_path: Path,
    result_root: Path,
    *,
    years=None,
    anchors=None,
    strategies=None,
) -> dict:
    """Run all approved audit anchors and write traces before search is allowed."""
    config_path = Path(config_path)
    data_path = Path(data_path)
    result_root = Path(result_root)
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    frames = years if years is not None else load_evaluation_years(data_path)
    anchors = anchors if anchors is not None else cfg["audit"]["anchor_configurations"]
    strategies = strategies if strategies is not None else cfg["ems"]["strategies"]
    audit_dir = result_root / "audit"
    trace_dir = audit_dir / "traces"
    trace_dir.mkdir(parents=True, exist_ok=True)
    reproduce_legacy_defects(audit_dir / "legacy_invalidation.json")

    contract = PVInputContract(
        reference_kw=float(cfg["pv_source"]["reference_capacity_kw"])
    )
    cases = []
    combined_triggers = set()
    for anchor in anchors:
        ems_cfg = _ems_config(anchor, cfg)
        prepared = {}
        for year in (2016, 2017, 2018):
            frame = frames[year]
            raw = frame["pv_raw_kw"].to_numpy(dtype=float)
            prepared[year] = {
                "load": frame["load_kw"].to_numpy(dtype=float),
                "pv": scale_pv_available(raw, ems_cfg.pv_kwp, contract),
            }
        for strategy in strategies:
            initial = EMSState(0.50, ems_cfg.h2_kg * 0.50)
            cycle = solve_stage2b_three_year_cycle(
                prepared,
                ems_cfg,
                initial,
                dispatch_stage2b,
                tolerance=float(cfg["cycle"]["soc_h2_solver_tolerance"]),
                max_repetitions=int(cfg["cycle"]["max_repetitions"]),
                strategy=strategy,
            )
            metrics = aggregate_cycle_metrics(cycle, prepared, ems_cfg)
            audit_frames = []
            for year in (2016, 2017, 2018):
                source = frames[year]
                timestamp_column = "timestamp" if "timestamp" in source.columns else None
                timestamps = (
                    source[timestamp_column].to_numpy()
                    if timestamp_column is not None
                    else np.arange(len(source))
                )
                row = cycle.year_results[year]
                frame = build_stepwise_audit(
                    prepared[year]["load"],
                    prepared[year]["pv"],
                    row.dispatch,
                    ems_cfg,
                    timestamps,
                )
                frame.insert(0, "year", year)
                audit_frames.append(frame)
            case_summary = summarize_audit_frames(audit_frames, ems_cfg)
            combined_triggers.update(case_summary["equipment_upper_bound_trigger"])
            stem = f"{anchor['name']}__{strategy}"
            pd.concat(audit_frames, ignore_index=True).to_csv(
                trace_dir / f"{stem}.csv", index=False, encoding="utf-8-sig"
            )
            cases.append(
                {
                    "anchor": anchor,
                    "strategy": strategy,
                    "cycle_converged": cycle.converged,
                    "cycle_repetitions": cycle.repetitions,
                    "metrics": metrics,
                    "bottleneck": case_summary,
                    "trace": f"audit/traces/{stem}.csv",
                    "paper_citable": False,
                }
            )

    summary = {
        "schema_version": "rectification_v2_stage2b.audit.1",
        "status": "complete",
        "case_count": len(cases),
        "equipment_upper_bound_trigger": sorted(combined_triggers),
        "cases": cases,
        "paper_citable": False,
    }
    atomic_json(audit_dir / "bottleneck_summary.json", summary)
    manifest = {
        "schema_version": "rectification_v2_stage2b.audit_manifest.1",
        "status": "complete",
        "audit_case_count": len(cases),
        "input_hash": _sha256(data_path),
        "config_hash": _sha256(config_path),
        "code_hash": _code_hash(),
        "bottleneck_summary": "audit/bottleneck_summary.json",
        "legacy_invalidation": "audit/legacy_invalidation.json",
        "paper_citable": False,
    }
    atomic_json(audit_dir / "audit_manifest.json", manifest)
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--data", type=Path, default=ROOT / "02_data_processed" / "tradestreet_pipeline" / "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv")
    parser.add_argument("--result-root", type=Path, default=ROOT / "07_results" / "rectification_v2_stage2b")
    args = parser.parse_args(argv)
    run_audit(args.config, args.data, args.result_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "atomic_json", "build_stepwise_audit", "main", "run_audit",
    "summarize_audit_frames"
]
