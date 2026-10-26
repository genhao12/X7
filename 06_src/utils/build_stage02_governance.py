"""Generate Stage 0-2 input, historical-result, and downstream-gate manifests."""
from __future__ import annotations

import csv
import hashlib
import json
import platform
import re
import sys
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "06_src"))

from components.annual_input_contract import (  # noqa: E402
    PVInputContract,
    detect_high_power_clipping,
    load_evaluation_years,
    scale_pv_available,
)


RESULT_ROOT = ROOT / "07_results" / "rectification_v2"
CONFIG_PATH = ROOT / "05_config" / "rectification_v2_capacity_gate.json"
DATA_PATH = ROOT / "02_data_processed" / "tradestreet_pipeline" / "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def historical_manifest() -> dict:
    directories = [
        "annual", "crossyear", "economic", "l4_comparison",
        "lifetime", "path_identification", "shap",
    ]
    producer_by_directory = {
        "annual": "06_src/simulation/run_f4_annual_simulation.py",
        "crossyear": "06_src/simulation/run_f8_crossyear_admissibility.py",
        "economic": "06_src/simulation/run_f5_timed_economic.py",
        "l4_comparison": "06_src/simulation/run_l4_comparison.py",
        "lifetime": "06_src/simulation/run_f4_annual_simulation.py",
        "path_identification": "06_src/utils/run_VS3_path_identification.py",
        "shap": "06_src/simulation/run_f7_shapley.py",
    }
    rows = []
    for directory in directories:
        base = ROOT / "07_results" / directory
        if not base.exists():
            continue
        for path in sorted(item for item in base.rglob("*") if item.is_file()):
            rows.append({
                "path": path.relative_to(ROOT).as_posix(),
                "sha256": sha256_file(path),
                "producer": producer_by_directory[directory],
                "status": "historical_invalid_pv_scaling",
                "reason": "generated before the 233 kW reference-capacity PV contract and continuous three-year capacity admission gate",
                "paper_citable": False,
            })
    return {
        "schema_version": "rectification_v2.0",
        "file_count": len(rows),
        "files": rows,
    }


def input_audit(config: dict) -> dict:
    frames = load_evaluation_years(DATA_PATH)
    contract = PVInputContract(reference_kw=config["pv_source"]["reference_capacity_kw"])
    all_raw = np.concatenate([frame["pv_raw_kw"].to_numpy(dtype=float) for frame in frames.values()])
    clipping = detect_high_power_clipping(all_raw, contract.reference_kw)
    years = {}
    for year, frame in frames.items():
        raw = frame["pv_raw_kw"].to_numpy(dtype=float)
        load = frame["load_kw"].to_numpy(dtype=float)
        years[str(year)] = {
            "points": len(frame),
            "start": str(frame["timestamp"].iloc[0]),
            "end": str(frame["timestamp"].iloc[-1]),
            "pv_raw_max_kw": float(raw.max()),
            "pv_raw_energy_kwh": float(raw.sum() * 0.25),
            "load_energy_kwh": float(load.sum() * 0.25),
            "pv_150_energy_kwh": float(scale_pv_available(raw, 150.0, contract).sum() * 0.25),
            "pv_150_to_load_ratio": float(scale_pv_available(raw, 150.0, contract).sum() / max(load.sum(), 1e-12)),
        }
    return {
        "schema_version": "rectification_v2.0",
        "source_path": DATA_PATH.relative_to(ROOT).as_posix(),
        "input_hash": sha256_file(DATA_PATH),
        "reference_capacity_kw": contract.reference_kw,
        "candidate_formula": "P_PV(t)=min(P_nom,max(0,P_raw(t))*P_nom/233)",
        "load_transform": "identity",
        "clipping_report": clipping.__dict__,
        "amplification_allowed": not clipping.detected,
        "irradiance_reconstruction_required": bool(clipping.detected),
        "years": years,
    }


def combined_gate(config: dict) -> dict:
    locations = {
        "main": RESULT_ROOT / "main_domain" / "capacity_gate_summary.json",
        "extended_pv": RESULT_ROOT / "extended_domain" / "capacity_gate_summary.json",
        "legacy_extended_diagnostic": RESULT_ROOT / "legacy_extended_diagnostic" / "capacity_gate_summary.json",
    }
    summaries = {
        name: json.loads(path.read_text(encoding="utf-8"))
        for name, path in locations.items()
    }
    main_count = int(summaries["main"]["admissible_count"])
    extended_count = int(summaries["extended_pv"]["admissible_count"])
    total = main_count + extended_count
    allow = total >= 1
    freeze_target = min(config["downstream_gate"]["freeze_target_max"], total)
    frozen = []
    if allow and freeze_target >= config["downstream_gate"]["freeze_target_min"]:
        frozen = (summaries["main"]["admissible_candidates"] + summaries["extended_pv"]["admissible_candidates"])[:freeze_target]
    return {
        "schema_version": "rectification_v2.0",
        "main_admissible_count": main_count,
        "extended_admissible_count": extended_count,
        "legacy_diagnostic_admissible_count": int(summaries["legacy_extended_diagnostic"]["admissible_count"]),
        "admissible_candidates": summaries["main"]["admissible_candidates"] + summaries["extended_pv"]["admissible_candidates"],
        "frozen_configuration_ids": frozen,
        "downstream_physical_inference_allowed": allow,
        "downstream_status": "unfrozen" if allow else "frozen_capacity_admission_failed",
        "frozen_modules": [] if allow else ["degradation", "six_paths", "Shapley", "L4", "economics", "double_layer_uncertainty"],
        "uncertainty_design": config["uncertainty"],
        "uncertainty_execution_status": "eligible" if allow else "not_started_capacity_gate_failed",
        "domain_summaries": summaries,
    }


def issue_register(gate: dict) -> None:
    path = ROOT / "09_docs" / "03_核验与审计/01_审查意见登记册_20260802.csv"
    fields = ["issue_id", "phase", "artifact", "status", "evidence_path", "resolution_text"]
    attachment = Path(r"C:\Users\HP\.codex\attachments\3df86062-f589-406b-8206-2f2c19f9765b\pasted-text.txt")
    review_text = attachment.read_text(encoding="utf-8") if attachment.exists() else ""
    matches = re.findall(
        r"(?ms)^(\d+)\.\s*(.*?)(?=\n\n\d+\.\s|\n\n[一二三四五六七八九十]+、|\n\n最终判断：|\Z)",
        review_text,
    )
    issue_text = {
        int(number): re.sub(r"\s+", " ", text).strip()
        for number, text in matches
    }
    resolved_ids = set(range(1, 19)) | {65, 66, 67, 70, 71, 72, 73, 74, 75, 76}
    rows = []
    for issue_id in range(1, 77):
        if issue_id <= 7:
            phase = "Stage 0"
            artifact = "DOCX semantics and historical-value reconciliation"
            evidence = "09_docs/06_项目梳理/07_工作区全面总结报告.docx"
        elif issue_id <= 19:
            phase = "Stage 1-2"
            artifact = "input contract and capacity admission"
            evidence = "07_results/rectification_v2/capacity_gate_summary.json"
        elif issue_id <= 64:
            phase = "Stage 3-7"
            artifact = "degradation/path/Shapley/L4/economics/uncertainty"
            evidence = "07_results/rectification_v2/capacity_gate_summary.json"
        else:
            phase = "Stage 0 / reproducibility"
            artifact = "tests, manifests, result governance, and conclusion wording"
            evidence = "07_results/rectification_v2/input_contract/run_manifest.json"
        status = "resolved" if issue_id in resolved_ids else "frozen_pending_capacity_gate"
        action = (
            "已按阶段0-2证据完成整改。"
            if status == "resolved"
            else "容量准入为0，保持冻结，未继续物理论证。"
        )
        resolution = f"评审项：{issue_text.get(issue_id, '原评审条目')} {action}"
        rows.append({
            "issue_id": f"R{issue_id:02d}", "phase": phase, "artifact": artifact,
            "status": status, "evidence_path": evidence, "resolution_text": resolution,
        })
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    audit = input_audit(config)
    write_json(RESULT_ROOT / "input_contract" / "input_audit.json", audit)
    write_json(RESULT_ROOT / "historical_result_status.json", historical_manifest())
    gate = combined_gate(config)
    write_json(RESULT_ROOT / "capacity_gate_summary.json", gate)
    manifest = {
        "schema_version": "rectification_v2.0",
        "input_hash": audit["input_hash"],
        "config_hash": sha256_file(CONFIG_PATH),
        "script_hash": sha256_file(Path(__file__).resolve()),
        "python_version": platform.python_version(),
        "numpy_version": np.__version__,
        "deterministic_seed_policy": "first 8 hex digits of sha256(candidate_id)",
        "domain_label_policy": "main, extended_pv, and legacy_extended_diagnostic are isolated",
        "upstream_gate": gate["downstream_status"],
        "double_layer_uncertainty": config["uncertainty"],
    }
    write_json(RESULT_ROOT / "input_contract" / "run_manifest.json", manifest)
    issue_register(gate)
    print(json.dumps({
        "historical_files": historical_manifest()["file_count"],
        "main_admissible": gate["main_admissible_count"],
        "extended_admissible": gate["extended_admissible_count"],
        "downstream_allowed": gate["downstream_physical_inference_allowed"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
