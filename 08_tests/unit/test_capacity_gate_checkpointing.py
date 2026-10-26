import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "06_src"))

from simulation.run_stage02_capacity_gate import (
    atomic_write_json,
    checkpoint_is_current,
    deterministic_seed,
)


def test_atomic_checkpoint_can_be_resumed(tmp_path):
    path = tmp_path / "main__pv150.json"
    payload = {
        "candidate_id": "main__pv150",
        "input_hash": "a",
        "code_hash": "b",
        "status": "complete",
    }
    atomic_write_json(path, payload)
    assert json.loads(path.read_text(encoding="utf-8")) == payload
    assert checkpoint_is_current(path, "a", "b")
    assert not checkpoint_is_current(path, "changed", "b")


def test_candidate_seed_is_stable_and_candidate_specific():
    assert deterministic_seed("main__pv150") == deterministic_seed("main__pv150")
    assert deterministic_seed("main__pv150") != deterministic_seed("extended_pv__pv300")
