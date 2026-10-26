import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_src"))

from simulation.stage2b_legacy_governance import reproduce_legacy_defects


def test_legacy_defects_are_reproduced_and_invalidated(tmp_path):
    target = tmp_path / "legacy_defects.json"
    evidence = reproduce_legacy_defects(target)
    assert evidence["legacy_pv_aux_active_at_zero_pv"] is True
    assert evidence["legacy_bat_aux_active_when_idle"] is True
    assert evidence["legacy_fc_el_flags_do_not_follow_same_step_power"] is True
    assert evidence["legacy_has_run_shorter_than_5_steps"] is True
    assert evidence["paper_citable"] is False
    assert evidence["superseded_by"] == "rectification_v2_stage2b"
    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved == evidence


def test_reproduction_does_not_modify_legacy_kernel(tmp_path):
    legacy = ROOT / "06_src" / "ems" / "ems_bf_lite.py"
    before = legacy.read_bytes()
    reproduce_legacy_defects(tmp_path / "evidence.json")
    assert legacy.read_bytes() == before
