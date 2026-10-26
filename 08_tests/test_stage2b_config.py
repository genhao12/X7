import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "05_config" / "rectification_v2_stage2b.json"


def test_stage2b_contract_is_fully_frozen():
    cfg = json.loads(PATH.read_text(encoding="utf-8"))
    assert cfg["schema_version"] == "rectification_v2_stage2b.1"
    assert cfg["status"] == "STAGE_2B_ONLY"
    assert cfg["pv_source"]["reference_capacity_kw"] == 233.0
    assert cfg["pv_domain_kWp"] == [160.0, 320.0]
    assert cfg["main_domain_status"] == "frozen_90_150_kWp"
    assert cfg["ems"]["minimum_run_steps"] == 5
    assert cfg["ems"]["count_start_step"] is True
    assert cfg["ems"]["decrement_after_dispatch"] is True
    assert cfg["forecast"]["primary_mode"] == "causal_persistence"
    assert cfg["forecast"]["oracle_mode"] == "perfect_forecast_upper_bound"
    assert cfg["search"]["sobol"] == {
        "d": 5,
        "scramble": True,
        "seed": 20260803,
        "pool_size_per_tier": 2048,
        "generate_once": True,
    }
    assert cfg["search"]["batch_size"] == 128
    assert cfg["search"]["exploit_count"] == 102
    assert cfg["search"]["coverage_count"] == 26
    assert cfg["result_root"] == "07_results/rectification_v2_stage2b"
    assert cfg["downstream_gate"]["stage3_min_admitted"] == 1
    assert cfg["downstream_gate"]["robust_six_path_min_admitted"] == 3
    assert cfg["downstream_gate"]["max_frozen_representatives"] == 5


def test_tier2_is_a_strict_increment_region():
    cfg = json.loads(PATH.read_text(encoding="utf-8"))
    limits = cfg["capacity_domains"]
    assert limits["tier1"] == {
        "BAT_kWh": [180.0, 1200.0],
        "PEMFC_kW": [45.0, 160.0],
        "PEMEL_kW": [45.0, 300.0],
        "H2_kg": [40.0, 3000.0],
    }
    assert limits["tier2_total"] == {
        "BAT_kWh": [180.0, 2400.0],
        "PEMFC_kW": [45.0, 180.0],
        "PEMEL_kW": [45.0, 320.0],
        "H2_kg": [40.0, 6000.0],
    }
    assert (
        limits["tier2_increment_rule"]
        == "BAT_kWh>1200 or PEMFC_kW>160 or PEMEL_kW>300 or H2_kg>3000"
    )


def test_auxiliary_cycle_and_admission_thresholds_are_explicit():
    cfg = json.loads(PATH.read_text(encoding="utf-8"))
    assert cfg["auxiliary"]["sensitivity_multipliers"] == [0.8, 1.0, 1.2]
    assert cfg["cycle"]["single_year_closure_required"] is False
    assert cfg["cycle"]["carry_machine_state_across_years"] is True
    assert cfg["admission"]["power_residual_abs_max_kW"] == 1e-6
    assert cfg["admission"]["h2_residual_abs_max_kg"] == 1e-6
    assert cfg["admission"]["critical_load_definition"] == "all_load_until_separate_series_exists"


def test_audit_anchors_and_parallel_execution_are_frozen():
    cfg = json.loads(PATH.read_text(encoding="utf-8"))
    assert [row["name"] for row in cfg["audit"]["anchor_configurations"]] == [
        "stage02_best_failed",
        "legacy_c5_500_failed",
        "stage2b_global_upper_anchor",
    ]
    assert cfg["parallel"]["max_workers"] == 8
    assert cfg["parallel"]["atomic_checkpoint_each_candidate"] is True
