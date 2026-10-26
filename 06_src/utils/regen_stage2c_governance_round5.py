# -*- coding: utf-8 -*-
"""Round-5 regeneration of Stage2c governance files.

Adds upstream-gate split fields (main_domain_failed / stage2c_admission_passed),
records round-5 consistency verification (no magnitude-leading claims; L4 %
precision note; execution flags unchanged False), keeps all facts unchanged,
recomputes the three contract hashes (reproducible, unchanged).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(r"d:\matlab\C")
GOV = ROOT / "07_results" / "rectification_v2_stage2c" / "governance"
NOW = "2026-08-04T15:12:00"

INPUT_HASH = "86e12930b6b08cab63dd00e12d5e00ab5afd0a3d1cc6e1122c90b1b52d207dd3"
CONFIG_HASH = "11ed37be41737aaba1558b2eed366457d50724c73e9ac7094ea2f9afd249965f"
SCRIPT_HASH = "42c21ad0a5e6f8451956da552455bf580305709b3ccad85c62c44c00b0b7cb11"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_hashes() -> dict:
    got = {
        "input_hash": sha256_file(
            ROOT / "02_data_processed" / "tradestreet_pipeline"
            / "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv"
        ),
        "config_hash": sha256_file(ROOT / "05_config" / "rectification_v2_capacity_gate.json"),
        "script_hash": sha256_file(ROOT / "06_src" / "utils" / "build_stage02_governance.py"),
    }
    assert got["input_hash"] == INPUT_HASH, got
    assert got["config_hash"] == CONFIG_HASH, got
    assert got["script_hash"] == SCRIPT_HASH, got
    return got


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print("written:", path)


def main() -> int:
    hashes = verify_hashes()
    print("contract hashes recomputed & unchanged:", {k: v[:10] for k, v in hashes.items()})

    upstream_split = {
        "main_domain_failed": True,
        "stage2c_admission_passed": True,
        "note": (
            "main_domain = Stage0-2 rectification_v2 主域 PV 160-320 kWp: 0 admission "
            "(2016-09 seasonal storage gap); stage2c = 扩展域 350-500 kWp: 149 admitted / "
            "5 frozen representatives. These are two separate facts and must not be merged."
        ),
    }
    round5 = {
        "passed": True,
        "verified_at": NOW,
        "magnitude_leading_claims_removed": True,
        "l4_percent_precision_noted": True,
        "gate_status_unchanged": {
            "gate_passed": False,
            "stage3_basic_degradation_allowed_plan_only": True,
            "degradation_execution_started": False,
            "six_path_execution_started": False,
            "shapley_allowed": False,
            "l4_allowed": False,
            "economics_allowed": False,
            "double_layer_uncertainty_allowed": False,
            "legacy_results_paper_citable": False,
        },
        "allowed_now": "planning + TDD case authoring only; result recomputation NOT started",
    }

    # ---- downstream_gate.json (.4) ----
    gate = {
        "schema_version": "rectification_v2_stage2c.downstream_gate.4",
        "generated_at": NOW,
        "gate_passed": False,
        "gate_reason": (
            "capacity_admission_passed_stage3_plan_only; round5 single-truth-source gate "
            "PASS (upstream split, magnitude-leading claims removed, L4 % precision noted); "
            "all execution/paper-citable flags remain False"
        ),
        "verified_admitted_count": 149,
        "max_frozen_representatives": 5,
        "capacity_admitted_ids_for_representative_selection": None,
        "stage3_basic_degradation_allowed": True,
        "robust_six_path_planning_allowed": True,
        "degradation_execution_started": False,
        "six_path_execution_started": False,
        "shapley_allowed": False,
        "l4_allowed": False,
        "economics_allowed": False,
        "double_layer_uncertainty_allowed": False,
        "legacy_results_paper_citable": False,
        "flag_semantics": {
            "allowed_True": (
                "plan-level permission ONLY (stage3_basic_degradation_allowed/robust_six_path_"
                "planning_allowed True, corresponding execution_started False); NOT executed/"
                "NOT paper-citable"
            ),
            "execution_started_False": "computation has NOT been run to produce paper-citable results",
            "_allowed_False": "explicitly gated; method-layer/prototype; MUST NOT appear as paper conclusions",
        },
        "upstream_gate": upstream_split,
        "input_hash": INPUT_HASH,
        "config_hash": CONFIG_HASH,
        "script_hash": SCRIPT_HASH,
        "ledger_anchor": "09_docs/统一结果台账.docx (single truth source)",
        "round4_consistency_gate": {"passed": True, "verified_at": "2026-08-04T14:50:38"},
        "round5_consistency_gate": round5,
    }
    write_json(GOV / "downstream_gate.json", gate)

    # ---- run_manifest.json (.3) ----
    manifest = {
        "schema_version": "rectification_v2_stage2c.run_manifest.3",
        "generated_at": NOW,
        "input_hash": INPUT_HASH,
        "config_hash": CONFIG_HASH,
        "script_hash": SCRIPT_HASH,
        "upstream_gate": upstream_split,
        "representatives": 5,
        "cost_sensitivity_groups": 80,
        "tests": {
            "functions": 127,
            "cases": 136,
            "breakdown_functions": {"stage2b": 43, "unit": 32, "stage3": 12, "special": 40},
            "breakdown_cases": {"stage2b": 50, "unit": 34, "stage3": 12, "special": 40},
        },
        "data_points_per_year": {
            "2016": 35136, "2017": 35040, "2018": 35040,
            "note": "2016 leap year (366x96=35136); verified in input_audit.json",
        },
        "artifacts": [
            {"id": "verified_admissions",
             "script": "rectification_v2_stage2c adaptive search",
             "path": "07_results/rectification_v2_stage2c/search/verified_admissions.json",
             "hash_in": INPUT_HASH, "status": "verified_149_admitted_5_frozen"},
            {"id": "degradation_summary",
             "script": "run_stage3 (degradation)",
             "path": "07_results/rectification_v2_stage2c/stage3/degradation_summary.json",
             "hash_in": INPUT_HASH, "status": "landed (method-layer; degradation_execution_started=False)"},
            {"id": "uncertainty_propagation",
             "script": "run_degradation_uncertainty.py",
             "path": "07_results/rectification_v2_stage2c/stage3/uncertainty_propagation.json",
             "hash_in": INPUT_HASH, "status": "single_layer_LHS_128_method_pass"},
            {"id": "cost_sensitivity",
             "script": "run_cost_sensitivity.py",
             "path": "07_results/rectification_v2_stage2c/stage3/cost_sensitivity.json",
             "hash_in": INPUT_HASH, "status": "5x16=80 groups; economics_allowed=False"},
            {"id": "cross_device_paths",
             "script": "run_cross_device_paths.py",
             "path": "07_results/rectification_v2_stage2c/stage3/cross_device_paths.json",
             "hash_in": INPUT_HASH,
             "status": "6 paths all non-zero; units not unified; no magnitude-leading claim; shapley_allowed=False"},
            {"id": "l4_ablation",
             "script": "run_l4_ablation.py",
             "path": "07_results/rectification_v2_stage2c/stage3/l4_ablation.json",
             "hash_in": INPUT_HASH,
             "status": "negative result 4/5 configs; percentages from unrounded values; l4_allowed=False"},
            {"id": "double_layer_propagation",
             "script": "run_double_layer_propagation.py",
             "path": "07_results/rectification_v2_stage2c/stage3/double_layer_propagation.json",
             "hash_in": INPUT_HASH,
             "status": "PROTOTYPE single-year 2017; 32x25=800; min_inner_samples=4000 NOT met; double_layer_uncertainty_allowed=False"},
        ],
        "round4_consistency_gate": {"passed": True, "verified_at": "2026-08-04T14:50:38"},
        "round5_consistency_gate": round5,
    }
    write_json(GOV / "run_manifest.json", manifest)

    # ---- hash_record.json (.3) ----
    hash_rec = {
        "schema_version": "rectification_v2_stage2c.hash_record.3",
        "generated_at": NOW,
        "input_hash": INPUT_HASH,
        "config_hash": CONFIG_HASH,
        "script_hash": SCRIPT_HASH,
        "source": (
            "07_results/rectification_v2/input_contract/{input_audit,run_manifest}.json; "
            "hash inputs: input=64_TradeStreet CSV, config=rectification_v2_capacity_gate.json, "
            "script=build_stage02_governance.py"
        ),
        "note": "identical across input_contract, ledger Table0, downstream_gate.json, run_manifest.json",
        "round4_verification": {"recomputed_at": "2026-08-04T14:50:38", "all_three_hashes_reproducible": True},
        "round5_verification": {"recomputed_at": NOW, "all_three_hashes_reproducible": True, "unchanged": True},
    }
    write_json(GOV / "hash_record.json", hash_rec)

    # ---- artifact_manifest.json ----
    am_path = GOV / "artifact_manifest.json"
    if am_path.exists():
        am = json.loads(am_path.read_text(encoding="utf-8"))
        for rel in ("governance/downstream_gate.json", "governance/run_manifest.json", "governance/hash_record.json"):
            full = GOV.parent / rel
            am["files"][rel] = {"bytes": full.stat().st_size, "sha256": sha256_file(full)}
            print("artifact_manifest updated:", rel)
        am["file_count"] = len(am["files"])
        am["updated_at"] = NOW
        am["round5_consistency_gate"] = round5
        am_path.write_text(json.dumps(am, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        print("written:", am_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
