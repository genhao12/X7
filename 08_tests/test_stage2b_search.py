"""PYTEST_DONT_REWRITE: SciPy 1.9 qmc hangs under pytest 7.1 assertion rewriting."""

import json
import hashlib
import sys
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_src"))

from simulation.stage2b_capacity_gate import (
    freeze_representatives,
    make_sobol_pool,
    map_sobol_capacities,
    select_adaptive_batch,
    should_stop,
)
from simulation.run_stage2b_adaptive_search import (
    assert_audit_precedes_search,
    evaluate_capacity_candidate,
    run_search,
    tier2_activation_allowed,
)
from simulation.run_stage2b_ems_audit import (
    build_stepwise_audit,
    run_audit,
    summarize_audit_frames,
)
from ems.ems_stage2b import EMSState, Stage2BEMSConfig, dispatch_stage2b


def load_cfg():
    return json.loads(
        (ROOT / "05_config" / "rectification_v2_stage2b.json").read_text(
            encoding="utf-8"
        )
    )


def test_sobol_pool_is_fixed_scrambled_nested_and_generated_once():
    cfg = load_cfg()
    first = make_sobol_pool("tier1", cfg)
    second = make_sobol_pool("tier1", cfg)
    assert first.shape == (2048, 5)
    assert np.array_equal(first, second)
    assert hashlib.sha256(first.tobytes()).hexdigest() == "85c9393d7c263b905e791e63281380afc33c23402c00d03d7b1ab8924c73885e"
    assert np.array_equal(first[:256], second[:256])


def test_tier2_contains_exactly_2048_increment_candidates():
    cfg = load_cfg()
    rows = map_sobol_capacities(make_sobol_pool("tier2", cfg), "tier2", cfg)
    assert len(rows) == 2048
    assert all(
        row.bat_kwh > 1200
        or row.pemfc_kw > 160
        or row.pemel_kw > 300
        or row.h2_kg > 3000
        for row in rows
    )
    assert len({row.candidate_id for row in rows}) == 2048


def _reference_coverage(pool, evaluated, candidates, count):
    chosen = []
    anchors = list(evaluated)
    remaining = set(candidates)
    for _ in range(count):
        anchor_points = pool[np.array(anchors, dtype=int)]
        best = min(
            remaining,
            key=lambda idx: (
                -float(np.min(np.linalg.norm(anchor_points - pool[idx], axis=1))),
                idx,
            ),
        )
        chosen.append(best)
        anchors.append(best)
        remaining.remove(best)
    return chosen


def test_adaptive_batch_has_102_exploit_and_26_sequential_maximin_points():
    pool = make_sobol_pool("tier1", load_cfg())
    evaluated = list(range(256))
    scores = {
        idx: np.array([idx / 256.0, 0.2, 0.0, 0.0], dtype=float)
        for idx in evaluated
    }
    batch = select_adaptive_batch(pool, evaluated, scores)
    exploit = [item.sobol_index for item in batch if item.selection_role == "exploit"]
    coverage = [
        item.sobol_index
        for item in batch
        if item.selection_role == "coverage_maximin"
    ]
    assert len(exploit) == 102
    assert len(coverage) == 26
    candidates = [
        idx
        for idx in range(len(pool))
        if idx not in set(evaluated) and idx not in set(exploit)
    ]
    assert coverage == _reference_coverage(pool, evaluated, candidates, 26)


def _history(improvements, conservation_improvements=None):
    conservation_improvements = conservation_improvements or improvements
    rows = []
    values = {"lpsp": 1.0, "closure": 1.0, "state_machine": 1.0, "conservation": 1.0}
    rows.append({"best": dict(values), "new_admissions": 0, "batch_complete": True})
    for i, improvement in enumerate(improvements):
        values = {
            "lpsp": values["lpsp"] - improvement,
            "closure": values["closure"] - improvement,
            "state_machine": values["state_machine"] - improvement,
            "conservation": values["conservation"] - conservation_improvements[i],
        }
        rows.append({"best": dict(values), "new_admissions": 0, "batch_complete": True})
    return rows


def test_stop_rule_uses_every_violation_class():
    improving_conservation = _history([0.01] * 4, [0.10] * 4)
    assert should_stop(improving_conservation, 0, 768, 2048) is False
    all_flat = _history([0.01] * 4)
    assert should_stop(all_flat, 0, 768, 2048) is True
    assert should_stop(all_flat, 5, 768, 2048) is True
    assert should_stop(all_flat, 0, 2048, 2048) is True


def _row(cid, pv, bat, fc, el, h2, margin):
    return {
        "candidate_id": cid,
        "pv_kwp": pv,
        "bat_kwh": bat,
        "pemfc_kw": fc,
        "pemel_kw": el,
        "h2_kg": h2,
        "admission_margin": margin,
    }


def test_representatives_use_global_bounds_and_deduplicate_with_fallback():
    rows = [
        _row("A", 170, 200, 50, 50, 100, 0.90),
        _row("B", 180, 260, 55, 55, 150, 0.80),
        _row("C", 220, 300, 160, 300, 5000, 0.70),
        _row("D", 230, 2200, 60, 80, 300, 0.60),
        _row("E", 320, 1200, 120, 200, 3000, 0.01),
        _row("F", 250, 1000, 100, 160, 2000, 0.20),
    ]
    chosen = freeze_representatives(rows, load_cfg())
    ids = [item["candidate_id"] for item in chosen]
    assert len(ids) == len(set(ids)) == 5
    assert chosen[0]["representative_role"] == "compact"
    assert chosen[0]["candidate_id"] == "A"
    assert chosen[1]["representative_role"] == "balanced"
    assert chosen[1]["candidate_id"] != "A"
    assert all(item["normalization_bounds"] == "stage2b_global" for item in chosen)


def test_stepwise_audit_contains_supply_absorption_inventory_and_cause_codes():
    cfg = Stage2BEMSConfig(100, 10, 20, 20, 20)
    load = np.array([50.0] * 5 + [0.0] * 5)
    pv = np.array([0.0] * 5 + [80.0] * 5)
    out = dispatch_stage2b(load, pv, cfg, initial_state=EMSState(cfg.soc_min, 10.0))
    frame = build_stepwise_audit(load, pv, out, cfg)
    required = {
        "deficit_kw",
        "battery_available_supply_kw",
        "fc_available_supply_kw",
        "surplus_kw",
        "battery_available_absorption_kw",
        "el_available_absorption_kw",
        "h2_production_kg",
        "h2_consumption_kg",
        "p_uns_kw",
        "p_cur_kw",
        "p_dump_kw",
        "deficit_cause",
        "curtailment_cause",
    }
    assert required.issubset(frame.columns)
    assert len(frame) == len(load)


def test_search_requires_matching_completed_audit_manifest(tmp_path):
    manifest = tmp_path / "audit_manifest.json"
    payload = {
        "status": "complete",
        "input_hash": "input",
        "config_hash": "config",
        "code_hash": "code",
    }
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    assert_audit_precedes_search(manifest, "input", "config", "code")
    payload["status"] = "running"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    try:
        assert_audit_precedes_search(manifest, "input", "config", "code")
    except RuntimeError as exc:
        assert "completed audit" in str(exc)
    else:
        raise AssertionError("incomplete audit must block search")


def test_tier2_requires_fewer_than_five_admissions_and_audited_boundary_trigger():
    trigger = {"equipment_upper_bound_trigger": ["BAT_kWh", "H2_kg"]}
    assert tier2_activation_allowed(4, trigger)
    assert not tier2_activation_allowed(5, trigger)
    assert not tier2_activation_allowed(0, {"equipment_upper_bound_trigger": []})


def test_audit_summary_converts_hourly_causes_into_boundary_triggers():
    cfg = Stage2BEMSConfig(320, 180, 45, 45, 10)
    out = dispatch_stage2b(
        np.full(8, 150.0),
        np.array([0.0] * 4 + [300.0] * 4),
        cfg,
        initial_state=EMSState(cfg.soc_min, cfg.h2_kg * cfg.h2_min_fraction),
    )
    frame = build_stepwise_audit(np.full(8, 150.0), np.array([0.0] * 4 + [300.0] * 4), out, cfg)
    summary = summarize_audit_frames([frame], cfg)
    assert summary["unserved_energy_kwh"] > 0.0
    assert set(summary["equipment_upper_bound_trigger"]).issubset(
        {"BAT_kWh", "PEMFC_kW", "PEMEL_kW", "H2_kg"}
    )
    assert summary["power_residual_abs_max_kW"] <= 1e-6


def test_audit_runner_writes_completed_manifest_before_search(tmp_path):
    frame = pd.DataFrame(
        {
            "timestamp": np.arange(8),
            "load_kw": np.full(8, 20.0),
            "pv_raw_kw": np.array([0.0] * 4 + [40.0] * 4),
        }
    )
    years = {year: frame.copy() for year in (2016, 2017, 2018)}
    anchor = {
        "name": "test_anchor",
        "PV_kWp": 320.0,
        "BAT_kWh": 180.0,
        "PEMFC_kW": 45.0,
        "PEMEL_kW": 45.0,
        "H2_kg": 10.0,
    }
    manifest = run_audit(
        ROOT / "05_config" / "rectification_v2_stage2b.json",
        ROOT / "02_data_processed" / "tradestreet_pipeline" / "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv",
        tmp_path,
        years=years,
        anchors=[anchor],
        strategies=["battery_first"],
    )
    assert manifest["status"] == "complete"
    assert manifest["audit_case_count"] == 1
    assert (tmp_path / "audit" / "audit_manifest.json").exists()
    assert list((tmp_path / "audit" / "traces").glob("*.csv"))


def test_candidate_evaluation_runs_energy_coarse_and_complete_cycle_layers():
    cfg = load_cfg()
    candidate = map_sobol_capacities(make_sobol_pool("tier1", cfg), "tier1", cfg)[0]
    years = {
        year: {
            "load": np.full(8, 20.0),
            "pv_raw": np.array([0.0] * 4 + [80.0] * 4),
        }
        for year in (2016, 2017, 2018)
    }
    result = evaluate_capacity_candidate(candidate, years, cfg)
    assert result["candidate_id"] == candidate.candidate_id
    assert "necessary_energy" in result
    assert set(result["coarse_strategies"]) == {
        "battery_first",
        "hydrogen_first",
        "balanced",
    }
    assert result["fine_strategy"] in result["coarse_strategies"]
    assert set(result["violation"]) == {
        "lpsp",
        "closure",
        "state_machine",
        "conservation",
        "maximum",
        "total",
    }
    assert result["paper_citable_scope"] == "synthetic_reconstructed_scenario_only"


def test_search_runner_writes_atomic_candidate_and_summary_after_audit(tmp_path):
    config_path = ROOT / "05_config" / "rectification_v2_stage2b.json"
    data_path = ROOT / "02_data_processed" / "tradestreet_pipeline" / "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv"
    frame = {
        "load": np.full(8, 20.0),
        "pv_raw": np.array([0.0] * 4 + [80.0] * 4),
    }
    years = {year: dict(frame) for year in (2016, 2017, 2018)}
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir(parents=True)
    from simulation.run_stage2b_adaptive_search import stage2b_code_hash
    manifest = {
        "status": "complete",
        "input_hash": __import__("hashlib").sha256(data_path.read_bytes()).hexdigest(),
        "config_hash": __import__("hashlib").sha256(config_path.read_bytes()).hexdigest(),
        "code_hash": stage2b_code_hash(),
    }
    (audit_dir / "audit_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    summary = run_search(
        config_path,
        data_path,
        tmp_path,
        years=years,
        candidate_limit=2,
        max_workers=1,
        allow_tier2=False,
    )
    assert summary["status"] == "complete"
    assert summary["tier_summaries"]["tier1"]["evaluated_count"] == 2
    assert len(list((tmp_path / "search" / "tier1" / "checkpoints").glob("*.json"))) == 2
    assert (tmp_path / "search" / "search_summary.json").exists()
