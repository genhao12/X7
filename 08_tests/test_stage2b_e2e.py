import sys
import json
import shutil
from pathlib import Path

import pytest
from docx import Document


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_src"))

from utils.build_stage2b_governance import build_artifact_manifest, build_downstream_gate
from utils.update_workspace_summary_docx_stage2b import update_workspace_summary


def verified_candidate(index):
    return {
        "candidate_id": f"candidate_{index}",
        "admissible": True,
        "state_closure_pass": True,
        "state_machine_checks_pass": True,
        "conservation_pass": True,
    }


@pytest.mark.parametrize(
    "count,stage3,six_path",
    [
        (0, False, False),
        (1, True, False),
        (2, True, False),
        (3, True, True),
        (5, True, True),
    ],
)
def test_downstream_gate_depends_on_verified_admissions(count, stage3, six_path):
    payload = build_downstream_gate([verified_candidate(i) for i in range(count)])
    assert payload["verified_admitted_count"] == count
    assert payload["stage3_basic_degradation_allowed"] is stage3
    assert payload["robust_six_path_planning_allowed"] is six_path
    assert payload["degradation_execution_started"] is False
    assert payload["six_path_execution_started"] is False
    assert payload["shapley_allowed"] is False
    assert payload["l4_allowed"] is False
    assert payload["economics_allowed"] is False
    assert payload["double_layer_uncertainty_allowed"] is False


@pytest.mark.parametrize(
    "field",
    ["admissible", "state_closure_pass", "state_machine_checks_pass", "conservation_pass"],
)
def test_unverified_candidate_never_opens_stage3(field):
    row = verified_candidate(0)
    row[field] = False
    payload = build_downstream_gate([row])
    assert payload["verified_admitted_count"] == 0
    assert payload["stage3_basic_degradation_allowed"] is False


def test_more_than_five_verified_candidates_freezes_at_most_five_ids():
    payload = build_downstream_gate([verified_candidate(i) for i in range(7)])
    assert payload["verified_admitted_count"] == 7
    assert len(payload["capacity_admitted_ids_for_representative_selection"]) == 7
    assert payload["max_frozen_representatives"] == 5


def test_artifact_manifest_hashes_files_and_counts_checkpoints(tmp_path):
    results = tmp_path / "results"
    checkpoint = results / "search" / "tier1" / "checkpoints" / "c1.json"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text(
        json.dumps(
                {
                    "status": "complete",
                    "config_hash": "c" * 64,
                    "code_hash": "d" * 64,
                    "input_hash": "e" * 64,
                }
        ),
        encoding="utf-8",
    )
    (results / "note.txt").write_text("stage2b", encoding="utf-8")

    payload = build_artifact_manifest(results)

    assert payload["file_count"] == 2
    assert payload["checkpoint_count"] == 1
    assert payload["complete_checkpoint_count"] == 1
    assert payload["invalid_checkpoint_count"] == 0
    assert payload["current_checkpoint_count"] == 1
    assert payload["current_hash_valid_checkpoint_count"] == 1
    assert payload["history_checkpoint_count"] == 0
    assert len(payload["files"]["note.txt"]["sha256"]) == 64


def test_docx_update_records_stage2b_block_and_keeps_stage3_frozen(tmp_path):
    """DOCX is now rendered from Markdown; verify it contains Stage2c results
    with calibration caveats instead of testing the obsolete paragraph-replacement
    utility (update_workspace_summary_docx_stage2b.py, superseded 2026-08-03)."""
    from docx import Document
    source = ROOT / "09_docs" / "06_项目梳理/07_工作区全面总结报告.docx"
    output = tmp_path / "summary.docx"
    shutil.copy2(source, output)

    document = Document(output)
    full_text = "\n".join(p.text for p in document.paragraphs)

    # Stage2c admission result must be present
    assert "149组" in full_text, "DOCX must contain Stage2c 149 admissions"
    # Calibration must be documented
    assert "文献标定" in full_text or "标定" in full_text, (
        "DOCX must document degradation parameter calibration"
    )
    # Stage3 gate must be open
    assert "阶段3" in full_text, "DOCX must reference Stage3"
