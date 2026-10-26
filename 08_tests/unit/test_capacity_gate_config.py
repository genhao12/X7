import json
from itertools import product
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_rectification_v2_capacity_domains_are_separate():
    path = ROOT / "05_config" / "rectification_v2_capacity_gate.json"
    cfg = json.loads(path.read_text(encoding="utf-8"))
    assert cfg["pv_source"]["reference_capacity_kw"] == 233.0
    assert cfg["main_domain"]["PV_kWp"] == [90, 100, 110, 120, 130, 140, 150]
    assert cfg["extended_pv_domain"]["PV_kWp"] == [160, 180, 200, 220, 240, 260, 280, 300, 320]
    assert len(list(product(*cfg["main_domain"].values()))) == 2240
    assert len(list(product(*cfg["extended_pv_domain"].values()))) == 2880
    assert cfg["cycle"]["years"] == [2016, 2017, 2018]
    assert cfg["cycle"]["state_tolerance"] == 0.001
    assert cfg["cycle"]["max_repetitions"] == 50
    assert cfg["admission"]["single_year_state_closure_required"] is False
    assert cfg["downstream_gate"]["freeze_until_capacity_admission"] is True
