"""Build the Stage2b downstream gate without executing frozen analyses."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Iterable, Mapping


REQUIRED_VERIFICATION_FIELDS = (
    "admissible",
    "state_closure_pass",
    "state_machine_checks_pass",
    "conservation_pass",
)


def _verified(row: Mapping[str, object]) -> bool:
    return all(bool(row.get(field, False)) for field in REQUIRED_VERIFICATION_FIELDS)


def build_downstream_gate(candidates: Iterable[Mapping[str, object]]) -> dict:
    rows = list(candidates)
    verified = [row for row in rows if _verified(row)]
    count = len(verified)
    return {
        "schema_version": "rectification_v2_stage2b.downstream_gate.1",
        "verified_admitted_count": count,
        "capacity_admitted_ids_for_representative_selection": [
            str(row["candidate_id"]) for row in verified
        ],
        "max_frozen_representatives": 5,
        "stage3_basic_degradation_allowed": count >= 1,
        "robust_six_path_planning_allowed": count >= 3,
        "degradation_execution_started": False,
        "six_path_execution_started": False,
        "shapley_allowed": False,
        "l4_allowed": False,
        "economics_allowed": False,
        "double_layer_uncertainty_allowed": False,
        "legacy_results_paper_citable": False,
        "gate_reason": (
            "capacity_admission_passed_stage3_plan_only"
            if count >= 1
            else "capacity_admission_not_obtained_downstream_frozen"
        ),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_artifact_manifest(results: Path) -> dict:
    results = Path(results)
    manifest_path = results / "governance" / "artifact_manifest.json"
    summary_path = results / "search" / "search_summary.json"
    expected_hashes = None
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            expected_hashes = {
                field: summary[field]
                for field in ("config_hash", "code_hash", "input_hash")
            }
        except (OSError, KeyError, ValueError):
            expected_hashes = None
    files = {}
    checkpoints = []
    for path in sorted(results.rglob("*")):
        if not path.is_file() or path == manifest_path or path.suffix == ".tmp":
            continue
        relative = path.relative_to(results).as_posix()
        files[relative] = {"bytes": path.stat().st_size, "sha256": _sha256(path)}
        if "/checkpoints/" in f"/{relative}":
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                payload = {}
            valid = payload.get("status") == "complete" and all(
                isinstance(payload.get(field), str) and len(payload[field]) == 64
                for field in ("config_hash", "code_hash", "input_hash")
            )
            is_history = relative.startswith("history/")
            hash_valid = valid and (
                expected_hashes is None
                or all(payload.get(key) == value for key, value in expected_hashes.items())
            )
            checkpoints.append(
                {
                    "path": relative,
                    "valid": valid,
                    "scope": "history_invalid" if is_history else "current",
                    "current_hash_valid": bool(hash_valid and not is_history),
                }
            )
    valid_count = sum(row["valid"] for row in checkpoints)
    current = [row for row in checkpoints if row["scope"] == "current"]
    history = [row for row in checkpoints if row["scope"] == "history_invalid"]
    return {
        "schema_version": "rectification_v2_stage2b.artifact_manifest.1",
        "file_count": len(files),
        "checkpoint_count": len(checkpoints),
        "complete_checkpoint_count": valid_count,
        "invalid_checkpoint_count": len(checkpoints) - valid_count,
        "current_checkpoint_count": len(current),
        "current_hash_valid_checkpoint_count": sum(
            row["current_hash_valid"] for row in current
        ),
        "history_checkpoint_count": len(history),
        "history_paper_citable": False,
        "files": files,
    }


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    args = parser.parse_args(argv)
    admissions_path = args.results / "search" / "verified_admissions.json"
    if admissions_path.exists():
        payload = json.loads(admissions_path.read_text(encoding="utf-8"))
        candidates = payload.get("candidates", [])
    else:
        candidates = []
    gate = build_downstream_gate(candidates)
    _atomic_json(args.results / "governance" / "downstream_gate.json", gate)
    manifest = build_artifact_manifest(args.results)
    _atomic_json(args.results / "governance" / "artifact_manifest.json", manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_artifact_manifest", "build_downstream_gate", "main"]
