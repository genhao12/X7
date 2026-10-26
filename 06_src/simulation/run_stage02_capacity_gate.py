"""Stage 0-2 PV/input/capacity gate orchestration with resumable checkpoints."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from typing import Dict, Iterable, Optional

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "06_src") not in sys.path:
    sys.path.insert(0, str(ROOT / "06_src"))

from components.annual_input_contract import (  # noqa: E402
    PVInputContract,
    detect_high_power_clipping,
    load_evaluation_years,
    scale_pv_available,
)
from ems.ems_bf_lite import EMSConfig, check_constraints, rule_based_dispatch  # noqa: E402
from simulation.capacity_gate import (  # noqa: E402
    CapacityCandidate,
    energy_screen,
    enumerate_domain,
    evaluate_admission,
    h2_lower_bound_diagnostics,
    solve_three_year_cycle,
)


CONFIG_PATH = ROOT / "05_config" / "rectification_v2_capacity_gate.json"
DATA_PATH = (
    ROOT
    / "02_data_processed"
    / "tradestreet_pipeline"
    / "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv"
)
RESULT_ROOT = ROOT / "07_results" / "rectification_v2"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _code_hash() -> str:
    paths = [
        ROOT / "06_src" / "components" / "annual_input_contract.py",
        ROOT / "06_src" / "ems" / "ems_bf_lite.py",
        ROOT / "06_src" / "simulation" / "capacity_gate.py",
        Path(__file__).resolve(),
    ]
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def deterministic_seed(candidate_id: str) -> int:
    return int(hashlib.sha256(candidate_id.encode("utf-8")).hexdigest()[:8], 16)


def atomic_write_json(path: Path, payload: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def checkpoint_is_current(
    path: Path, input_hash: str, code_hash: str, config_hash: Optional[str] = None
) -> bool:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    current = (
        payload.get("status") == "complete"
        and payload.get("input_hash") == input_hash
        and payload.get("code_hash") == code_hash
    )
    if config_hash is not None:
        current = current and payload.get("config_hash") == config_hash
    return current


def _domain_dir(domain: str) -> str:
    return {
        "main": "main_domain",
        "extended_pv": "extended_domain",
        "legacy_extended_diagnostic": "legacy_extended_diagnostic",
    }[domain]


def _candidate_config(candidate: CapacityCandidate) -> EMSConfig:
    return EMSConfig(
        pv_kwp=candidate.pv_kwp,
        bat_kwh=candidate.bat_kwh,
        pemfc_kw=candidate.pemfc_kw,
        pemel_kw=candidate.pemel_kw,
        h2_kg=candidate.h2_kg,
    )


def _year_metrics(year: int, frame, pv: np.ndarray, result, cfg: EMSConfig) -> dict:
    dt_h = cfg.dt_min / 60.0
    load = frame["load_kw"].to_numpy(dtype=float)
    raw = frame["pv_raw_kw"].to_numpy(dtype=float)
    commands = result.commands
    state = result.state
    report = check_constraints(commands, state, cfg)
    load_energy = float(load.sum() * dt_h)
    unserved = float(np.asarray(state["p_uns"]).sum() * dt_h)
    lpsp = unserved / max(load_energy, 1e-12)
    timestamps = frame["timestamp"].astype(str).to_numpy()
    h2_diag = h2_lower_bound_diagnostics(
        timestamps,
        state["h2_trace"],
        state["p_uns"],
        cfg.h2_kg * cfg.h2_min_fraction,
        dt_h,
    )
    return {
        "year": year,
        "load_energy_kwh": load_energy,
        "pv_raw_energy_kwh": float(raw.sum() * dt_h),
        "pv_scaled_energy_kwh": float(pv.sum() * dt_h),
        "pv_curtailed_energy_kwh": float(np.asarray(state["p_cur"]).sum() * dt_h),
        "battery_charge_energy_kwh": float(np.maximum(-commands[:, 0], 0).sum() * dt_h),
        "battery_discharge_energy_kwh": float(np.maximum(commands[:, 0], 0).sum() * dt_h),
        "electrolyzer_input_energy_kwh": float(commands[:, 2].sum() * dt_h),
        "h2_production_kg": float(np.asarray(state["h2_prod_trace"]).sum()),
        "fuel_cell_output_energy_kwh": float(commands[:, 1].sum() * dt_h),
        "auxiliary_energy_kwh": float(np.asarray(state["p_aux"]).sum() * dt_h),
        "unserved_energy_kwh": unserved,
        "lpsp": lpsp,
        "critical_satisfaction": 1.0 - lpsp,
        "start_soc": result.start_soc,
        "end_soc": result.end_soc,
        "soc_delta": result.end_soc - result.start_soc,
        "start_h2_kg": result.start_h2_kg,
        "end_h2_kg": result.end_h2_kg,
        "h2_delta_fraction": (result.end_h2_kg - result.start_h2_kg) / max(cfg.h2_kg, 1e-12),
        "power_residual_fraction": float(report["power_residual_ratio"]),
        "h2_residual_fraction": float(report["h2_residual_ratio"]),
        "bounds_pass": bool(report["soc_bounds_pass"] and report["h2_bounds_pass"]),
        "minimum_stable_power_pass": bool(report["min_stable_load_pass"]),
        "h2_lower_bound": h2_diag,
    }


_WORKER_YEARS = None
_WORKER_CONFIG = None
_WORKER_HASHES = None


def _worker_init(data_path: str, config: dict, hashes: dict) -> None:
    global _WORKER_YEARS, _WORKER_CONFIG, _WORKER_HASHES
    _WORKER_YEARS = load_evaluation_years(Path(data_path))
    _WORKER_CONFIG = config
    _WORKER_HASHES = hashes


def _coarse_rank(candidate: CapacityCandidate, pv_by_year: Dict[int, np.ndarray]):
    cfg = _candidate_config(candidate)
    frame = _WORKER_YEARS[2017]
    load = frame["load_kw"].to_numpy(dtype=float)
    alternatives = []
    for soc in _WORKER_CONFIG["screening"]["coarse_initial_soc"]:
        for h2_fraction in _WORKER_CONFIG["screening"]["coarse_initial_h2_fraction"]:
            h2 = candidate.h2_kg * h2_fraction
            commands, state = rule_based_dispatch(
                load, pv_by_year[2017], cfg, soc_initial=soc, h2_initial_kg=h2
            )
            lpsp = float(np.asarray(state["p_uns"]).sum() / max(load.sum(), 1e-12))
            alternatives.append(
                {
                    "soc": float(soc),
                    "h2_fraction": float(h2_fraction),
                    "lpsp": lpsp,
                    "terminal_soc": float(state["soc_trace"][-1]),
                    "terminal_h2_kg": float(state["h2_trace"][-1]),
                }
            )
    alternatives.sort(key=lambda row: (row["lpsp"], -row["terminal_h2_kg"], -row["terminal_soc"], row["soc"], row["h2_fraction"]))
    return alternatives[0], alternatives


def _evaluate_candidate(candidate_dict: dict) -> dict:
    candidate = CapacityCandidate(**candidate_dict)
    contract = PVInputContract(reference_kw=float(_WORKER_CONFIG["pv_source"]["reference_capacity_kw"]))
    pv_by_year = {
        year: scale_pv_available(
            frame["pv_raw_kw"].to_numpy(dtype=float), candidate.pv_kwp, contract
        )
        for year, frame in _WORKER_YEARS.items()
    }
    best, alternatives = _coarse_rank(candidate, pv_by_year)
    cfg = _candidate_config(candidate)
    years = {
        year: {
            "load": frame["load_kw"].to_numpy(dtype=float),
            "pv": pv_by_year[year],
        }
        for year, frame in _WORKER_YEARS.items()
    }
    cycle = solve_three_year_cycle(
        years,
        cfg,
        best["soc"],
        candidate.h2_kg * best["h2_fraction"],
        dispatch_fn=rule_based_dispatch,
        tolerance=float(_WORKER_CONFIG["cycle"]["state_tolerance"]),
        max_repetitions=int(_WORKER_CONFIG["cycle"]["max_repetitions"]),
    )
    yearly = {
        year: _year_metrics(year, _WORKER_YEARS[year], pv_by_year[year], result, cfg)
        for year, result in cycle.year_results.items()
    }
    load_total = sum(row["load_energy_kwh"] for row in yearly.values())
    uns_total = sum(row["unserved_energy_kwh"] for row in yearly.values())
    three_lpsp = uns_total / max(load_total, 1e-12)
    metrics = {
        "cycle_converged": cycle.converged,
        "cycle_repetitions": cycle.repetitions,
        "block_start_soc": cycle.block_start_soc,
        "block_end_soc": cycle.block_end_soc,
        "block_start_h2_kg": cycle.block_start_h2_kg,
        "block_end_h2_kg": cycle.block_end_h2_kg,
        "block_soc_delta": cycle.block_soc_delta,
        "block_h2_delta_fraction": cycle.block_h2_delta_fraction,
        "yearly": yearly,
        "three_year_lpsp": three_lpsp,
        "three_year_critical_satisfaction": 1.0 - three_lpsp,
        "max_power_residual_fraction": max(row["power_residual_fraction"] for row in yearly.values()),
        "h2_residual_fraction": max(row["h2_residual_fraction"] for row in yearly.values()),
        "bounds_pass": all(row["bounds_pass"] for row in yearly.values()),
        "minimum_stable_power_pass": all(row["minimum_stable_power_pass"] for row in yearly.values()),
    }
    admission = evaluate_admission(metrics)
    return {
        **candidate.to_dict(),
        "status": "complete",
        **_WORKER_HASHES,
        "config_hash": _WORKER_HASHES["config_hash"],
        "deterministic_seed": deterministic_seed(candidate.candidate_id),
        "upstream_gate": "necessary_energy_passed",
        "coarse_rank": {"best": best, "all_initial_states": alternatives},
        "metrics": metrics,
        "admission": asdict(admission),
        "paper_citable": False,
        "citation_status": "capacity_admission_evidence_only_synthetic_reconstructed_input",
    }


def _legacy_candidates(rows: Iterable[dict]):
    return [
        CapacityCandidate(
            candidate_id=f"legacy_extended_diagnostic__{row['name']}",
            domain="legacy_extended_diagnostic",
            pv_kwp=row["PV_kWp"],
            bat_kwh=row["BAT_kWh"],
            pemfc_kw=row["PEMFC_kW"],
            pemel_kw=row["PEMEL_kW"],
            h2_kg=row["H2_kg"],
        )
        for row in rows
    ]


def _all_candidates(config: dict) -> dict:
    return {
        "main": enumerate_domain("main", config["main_domain"]),
        "extended_pv": enumerate_domain("extended_pv", config["extended_pv_domain"]),
        "legacy_extended_diagnostic": _legacy_candidates(config["legacy_extended_diagnostic"]),
    }


def _screen_payload(candidate, ratios, failed, hashes):
    return {
        **candidate.to_dict(),
        "status": "complete",
        **hashes,
        "deterministic_seed": deterministic_seed(candidate.candidate_id),
        "upstream_gate": "necessary_energy_screen",
        "necessary_energy": {"passed": False, "ratios": ratios, "failed_years": failed},
        "admission": {"admissible": False, "checks": {"necessary_energy": False}, "failure_reasons": ["necessary_energy"]},
        "paper_citable": False,
    }


def _write_csv(path: Path, payloads: list) -> None:
    fields = [
        "candidate_id", "domain", "pv_kwp", "bat_kwh", "pemfc_kw", "pemel_kw", "h2_kg",
        "necessary_energy_pass", "admissible", "three_year_lpsp", "cycle_converged", "cycle_repetitions",
        "block_soc_delta", "block_h2_delta_fraction", "failure_reasons",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in sorted(payloads, key=lambda row: row["candidate_id"]):
            metrics = item.get("metrics", {})
            admission = item.get("admission", {})
            writer.writerow({
                "candidate_id": item["candidate_id"], "domain": item["domain"],
                "pv_kwp": item["pv_kwp"], "bat_kwh": item["bat_kwh"], "pemfc_kw": item["pemfc_kw"],
                "pemel_kw": item["pemel_kw"], "h2_kg": item["h2_kg"],
                "necessary_energy_pass": item.get("upstream_gate") == "necessary_energy_passed",
                "admissible": admission.get("admissible", False),
                "three_year_lpsp": metrics.get("three_year_lpsp"),
                "cycle_converged": metrics.get("cycle_converged"),
                "cycle_repetitions": metrics.get("cycle_repetitions"),
                "block_soc_delta": metrics.get("block_soc_delta"),
                "block_h2_delta_fraction": metrics.get("block_h2_delta_fraction"),
                "failure_reasons": "|".join(admission.get("failure_reasons", [])),
            })


def run_domain(domain: str, config: dict, requested_workers: int, resume: bool) -> dict:
    candidates = _all_candidates(config)[domain]
    frames = load_evaluation_years(DATA_PATH)
    contract = PVInputContract(reference_kw=float(config["pv_source"]["reference_capacity_kw"]))
    clipping = detect_high_power_clipping(np.concatenate([f["pv_raw_kw"].to_numpy() for f in frames.values()]), contract.reference_kw)
    if clipping.detected and max(c.pv_kwp for c in candidates) > contract.reference_kw:
        raise ValueError("PV amplification rejected: irradiance/capacity-factor reconstruction is required")
    input_hash = _sha256_file(DATA_PATH)
    code_hash = _code_hash()
    config_hash = _sha256_file(CONFIG_PATH)
    hashes = {
        "input_hash": input_hash,
        "code_hash": code_hash,
        "config_hash": config_hash,
        "python_version": platform.python_version(),
        "numpy_version": np.__version__,
    }
    pv_energy_cache = {}
    load_energy = {year: float(frame["load_kw"].sum() * 0.25) for year, frame in frames.items()}
    out_dir = RESULT_ROOT / _domain_dir(domain)
    checkpoint_dir = out_dir / "checkpoints"
    payloads = []
    survivors = []
    for candidate in candidates:
        checkpoint = checkpoint_dir / f"{candidate.candidate_id}.json"
        if resume and checkpoint_is_current(
            checkpoint, input_hash, code_hash, config_hash
        ):
            payloads.append(json.loads(checkpoint.read_text(encoding="utf-8")))
            continue
        if candidate.pv_kwp not in pv_energy_cache:
            pv_energy_cache[candidate.pv_kwp] = {
                year: float(scale_pv_available(frame["pv_raw_kw"].to_numpy(), candidate.pv_kwp, contract).sum() * 0.25)
                for year, frame in frames.items()
            }
        screen = energy_screen(candidate, pv_energy_cache[candidate.pv_kwp], load_energy, float(config["screening"]["necessary_pv_energy_fraction_of_load"]))
        if not screen.passed:
            payload = _screen_payload(candidate, screen.ratios, screen.failed_years, hashes)
            atomic_write_json(checkpoint, payload)
            payloads.append(payload)
        else:
            survivors.append(candidate)

    max_workers = min(requested_workers, int(config["parallel"]["max_workers"]), max(1, (os.cpu_count() or 2) - int(config["parallel"]["reserve_logical_cores"])))
    if survivors:
        with ProcessPoolExecutor(
            max_workers=max_workers,
            initializer=_worker_init,
            initargs=(str(DATA_PATH), config, hashes),
        ) as executor:
            futures = {executor.submit(_evaluate_candidate, item.to_dict()): item for item in survivors}
            for future in as_completed(futures):
                candidate = futures[future]
                payload = future.result()
                atomic_write_json(checkpoint_dir / f"{candidate.candidate_id}.json", payload)
                payloads.append(payload)
    _write_csv(out_dir / "capacity_gate_results.csv", payloads)
    summary = {
        "domain": domain,
        "candidate_count": len(candidates),
        "necessary_energy_pass_count": sum(item.get("upstream_gate") == "necessary_energy_passed" for item in payloads),
        "admissible_count": sum(item.get("admission", {}).get("admissible", False) for item in payloads),
        "admissible_candidates": [item["candidate_id"] for item in payloads if item.get("admission", {}).get("admissible", False)],
        "max_workers": max_workers,
        "input_hash": input_hash,
        "code_hash": code_hash,
        "config_hash": config_hash,
        "clipping_report": asdict(clipping),
    }
    atomic_write_json(out_dir / "capacity_gate_summary.json", summary)
    return summary


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--domain", choices=["main", "extended_pv", "legacy_extended_diagnostic", "all"], default="all")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    candidates = _all_candidates(config)
    if args.dry_run:
        report = {name: len(rows) for name, rows in candidates.items()}
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0
    domains = list(candidates) if args.domain == "all" else [args.domain]
    summaries = [run_domain(domain, config, args.workers, args.resume) for domain in domains]
    print(json.dumps(summaries, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
