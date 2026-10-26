# -*- coding: utf-8 -*-
"""Round-4 regeneration of Stage2c governance files.

Regenerates downstream_gate.json / run_manifest.json / hash_record.json with
the verified facts unchanged (149 admitted, 5 frozen reps, 80 cost groups,
2016=35136 leap-year points, 127 functions -> 136 cases, Stage2b 43 fn/50 cases,
six paths non-zero & units not unified), records the round-4 single-truth-source
consistency verification, and recomputes the three contract hashes (which are
reproducible and unchanged: input=64-CSV, config=rectification_v2_capacity_gate.json,
script=build_stage02_governance.py).
Then updates artifact_manifest.json entries for the three regenerated files.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(r"d:\matlab\C")
GOV = ROOT / "07_results" / "rectification_v2_stage2c" / "governance"
NOW = "2026-08-04T14:50:38"

INPUT_HASH = "86e12930b6b08cab63dd00e12d5e00ab5afd0a3d1cc6e1122c90b1b52d207dd3"
CONFIG_HASH = "11ed37be41737aaba1558b2eed366457d50724c73e9ac7094ea2f9afd249965f"
SCRIPT_HASH = "42c21ad0a5e6f8451956da552455bf580305709b3ccad85c62c44c00b0b7cb11"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_hashes() -> dict:
    """Recompute contract hashes from their source files; must equal recorded ones."""
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
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print("written:", path)


def main() -> int:
    hashes = verify_hashes()
    print("contract hashes recomputed & unchanged:", {k: v[:10] for k, v in hashes.items()})

    # ---- downstream_gate.json ----
    gate = {
        "schema_version": "rectification_v2_stage2c.downstream_gate.3",
        "generated_at": NOW,
        "gate_passed": False,
        "gate_reason": (
            "capacity_admission_passed_stage3_plan_only; "
            "round4 single-truth-source consistency gate PASS "
            "(ledger/summary/main-report aligned: 2016=35136 leap-year, 5x16=80 cost groups, "
            "127 functions->136 cases, Stage2b 43 fn/50 cases, six paths non-zero & units "
            "not unified, 'negligible'/'2-active' claims removed); "
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
                "planning_allowed are True while the corresponding execution_started flags are "
                "False); does NOT mean executed or paper-citable"
            ),
            "execution_started_False": (
                "the corresponding computation has NOT been run to produce paper-citable results"
            ),
            "_allowed_False": (
                "explicitly gated; results are method-layer/prototype and MUST NOT appear as "
                "paper conclusions"
            ),
        },
        "input_hash": INPUT_HASH,
        "config_hash": CONFIG_HASH,
        "script_hash": SCRIPT_HASH,
        "ledger_anchor": "09_docs/统一结果台账.docx (single truth source)",
        "round4_consistency_gate": {
            "passed": True,
            "verified_at": NOW,
            "documents_aligned": [
                "09_docs/统一结果台账.docx",
                "09_docs/工作区运行结果汇总.docx",
                "09_docs/工作区全面总结报告.docx",
            ],
            "data_points_per_year": {"2016": 35136, "2017": 35040, "2018": 35040},
            "cost_sensitivity_groups": 80,
            "tests": {
                "functions": 127,
                "cases": 136,
                "breakdown_functions": {"stage2b": 43, "unit": 32, "stage3": 12, "special": 40},
                "breakdown_cases": {"stage2b": 50, "unit": 34, "stage3": 12, "special": 40},
            },
            "six_path_claim": (
                "all six paths non-zero; units not unified (alpha1/beta1 in DeltaD/yr, "
                "cross-device in % contribution); only D=0.5 shown; 'negligible'/'only 2 "
                "active' claims removed from all three documents"
            ),
            "hash_notes": {
                "input_hash_src": "02_data_processed/tradestreet_pipeline/64_TradeStreet_..._evaluation_years.csv",
                "config_hash_src": "05_config/rectification_v2_capacity_gate.json",
                "script_hash_src": "06_src/utils/build_stage02_governance.py",
            },
        },
    }
    gate_path = GOV / "downstream_gate.json"
    write_json(gate_path, gate)

    # ---- run_manifest.json ----
    manifest = {
        "schema_version": "rectification_v2_stage2c.run_manifest.2",
        "generated_at": NOW,
        "input_hash": INPUT_HASH,
        "config_hash": CONFIG_HASH,
        "script_hash": SCRIPT_HASH,
        "representatives": 5,
        "cost_sensitivity_groups": 80,
        "tests": {
            "functions": 127,
            "cases": 136,
            "breakdown_functions": {"stage2b": 43, "unit": 32, "stage3": 12, "special": 40},
            "breakdown_cases": {"stage2b": 50, "unit": 34, "stage3": 12, "special": 40},
        },
        "data_points_per_year": {
            "2016": 35136,
            "2017": 35040,
            "2018": 35040,
            "note": "2016 leap year (366x96=35136); verified in input_audit.json",
        },
        "artifacts": [
            {
                "id": "verified_admissions",
                "script": "rectification_v2_stage2c adaptive search",
                "path": "07_results/rectification_v2_stage2c/search/verified_admissions.json",
                "hash_in": INPUT_HASH,
                "status": "verified_149_admitted_5_frozen",
            },
            {
                "id": "degradation_summary",
                "script": "run_stage3 (degradation)",
                "path": "07_results/rectification_v2_stage2c/stage3/degradation_summary.json",
                "hash_in": INPUT_HASH,
                "status": "landed (method-layer; degradation_execution_started=False)",
            },
            {
                "id": "uncertainty_propagation",
                "script": "run_degradation_uncertainty.py",
                "path": "07_results/rectification_v2_stage2c/stage3/uncertainty_propagation.json",
                "hash_in": INPUT_HASH,
                "status": "single_layer_LHS_128_method_pass",
            },
            {
                "id": "cost_sensitivity",
                "script": "run_cost_sensitivity.py",
                "path": "07_results/rectification_v2_stage2c/stage3/cost_sensitivity.json",
                "hash_in": INPUT_HASH,
                "status": "5x16=80 groups; economics_allowed=False (not paper-citable)",
            },
            {
                "id": "cross_device_paths",
                "script": "run_cross_device_paths.py",
                "path": "07_results/rectification_v2_stage2c/stage3/cross_device_paths.json",
                "hash_in": INPUT_HASH,
                "status": "6 paths all non-zero; units not unified; shapley_allowed=False",
            },
            {
                "id": "l4_ablation",
                "script": "run_l4_ablation.py",
                "path": "07_results/rectification_v2_stage2c/stage3/l4_ablation.json",
                "hash_in": INPUT_HASH,
                "status": "negative result 4/5 configs; l4_allowed=False",
            },
            {
                "id": "double_layer_propagation",
                "script": "run_double_layer_propagation.py",
                "path": "07_results/rectification_v2_stage2c/stage3/double_layer_propagation.json",
                "hash_in": INPUT_HASH,
                "status": (
                    "PROTOTYPE single-year 2017; 32x25=800; min_inner_samples=4000 NOT met; "
                    "double_layer_uncertainty_allowed=False"
                ),
            },
        ],
        "round4_consistency_gate": {"passed": True, "verified_at": NOW},
    }
    manifest_path = GOV / "run_manifest.json"
    write_json(manifest_path, manifest)

    # ---- hash_record.json ----
    hash_rec = {
        "schema_version": "rectification_v2_stage2c.hash_record.2",
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
        "round4_verification": {
            "recomputed_at": NOW,
            "all_three_hashes_reproducible": True,
            "unchanged": True,
        },
    }
    write_json(GOV / "hash_record.json", hash_rec)

    # ---- update artifact_manifest.json entries for regenerated files ----
    am_path = GOV / "artifact_manifest.json"
    if am_path.exists():
        am = json.loads(am_path.read_text(encoding="utf-8"))
        for rel in ("governance/downstream_gate.json", "governance/run_manifest.json", "governance/hash_record.json"):
            full = GOV.parent / rel
            if rel in am["files"]:
                am["files"][rel] = {"bytes": full.stat().st_size, "sha256": sha256_file(full)}
                print("artifact_manifest updated:", rel)
        am["updated_at"] = NOW
        am["round4_consistency_gate"] = {"passed": True, "verified_at": NOW}
        am_path.write_text(
            json.dumps(am, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print("written:", am_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
