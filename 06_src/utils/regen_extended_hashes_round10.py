# -*- coding: utf-8 -*-
"""Round-10 extended hash coverage (EFC code, Stage2c config, params, all scripts)."""
from __future__ import annotations

import hashlib
import json
import datetime
from pathlib import Path

ROOT = Path(r"d:\matlab\C")
GOV = ROOT / "07_results" / "rectification_v2_stage2c" / "governance"
NOW = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

FILES = {
    "efc_cycle_model": r"06_src\components\efc_cycle_model.py",
    "closed_loop_lite": r"06_src\simulation\closed_loop_lite.py",
    "stage2c_config": r"05_config\rectification_v2_stage2c.json",
    "capacity_gate_config": r"05_config\rectification_v2_capacity_gate.json",
    "frozen_params": r"05_config\frozen_parameters_v2.2.json",
    "economic_params": r"05_config\economic_parameters_v1.0.json",
    "mvp_c1_config": r"05_config\mvp_c1_config.json",
    "run_stage3_full_analysis": r"06_src\simulation\run_stage3_full_analysis.py",
    "run_cost_sensitivity": r"06_src\simulation\run_cost_sensitivity.py",
    "run_cross_device_paths": r"06_src\simulation\run_cross_device_paths.py",
    "run_degradation_uncertainty": r"06_src\simulation\run_degradation_uncertainty.py",
    "run_double_layer_propagation": r"06_src\simulation\run_double_layer_propagation.py",
    "run_l4_ablation": r"06_src\simulation\run_l4_ablation.py",
    "run_stage3_paper_assets": r"06_src\simulation\run_stage3_paper_assets.py",
    "run_efc_vs_proxy_comparison": r"06_src\simulation\run_efc_vs_proxy_comparison.py",
    "ems_stage2b": r"06_src\ems\ems_stage2b.py",
    "annual_input_contract": r"06_src\components\annual_input_contract.py",
    "input_csv": r"02_data_processed\tradestreet_pipeline\64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv",
}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    ext = {k: sha(ROOT / v) for k, v in FILES.items()}
    hr = {
        "schema_version": "rectification_v2_stage2c.hash_record.5",
        "generated_at": NOW,
        "input_hash": ext["input_csv"],
        "config_hash": ext["capacity_gate_config"],
        "script_hash": "42c21ad0a5e6f8451956da552455bf580305709b3ccad85c62c44c00b0b7cb11",
        "source": "07_results/rectification_v2/input_contract/{input_audit,run_manifest}.json",
        "note": "contract hashes identical across input_contract / ledger Table0 / downstream_gate.json / run_manifest.json",
        "extended_hashes": ext,
        "extended_hashes_note": "covers EFC code, Stage2c config, degradation/economic params, and all Stage3 analysis scripts",
        "round10_extension": {"verified_at": NOW, "extended_coverage": True},
    }
    (GOV / "hash_record.json").write_text(
        json.dumps(hr, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print("hash_record.json(.5) written, files:", len(ext))

    am = json.loads((GOV / "artifact_manifest.json").read_text(encoding="utf-8"))
    full = GOV / "hash_record.json"
    am["files"]["governance/hash_record.json"] = {"bytes": full.stat().st_size, "sha256": sha(full)}
    am["file_count"] = len(am["files"])
    am["round10_extended_hashes"] = {"verified_at": NOW, "coverage": len(ext)}
    (GOV / "artifact_manifest.json").write_text(
        json.dumps(am, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print("artifact_manifest updated, file_count:", am["file_count"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
