"""Stage-3 regression tests: double-layer variance decomposition + cost sensitivity.

Covers two properties that the Stage-3 result files must satisfy:
  1. Law of total variance closure in the nested (Sobol x Bootstrap) design.
  2. Cost-sensitivity baseline must reproduce the Stage-3 economics exactly,
     and price/BOP scenarios must move NPC/LCOE in the physically correct
     direction with the correct algebraic magnitude.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "06_src") not in sys.path:
    sys.path.insert(0, str(ROOT / "06_src"))

STAGE3 = ROOT / "07_results" / "rectification_v2_stage2c" / "stage3"
DOUBLE_LAYER = STAGE3 / "double_layer_propagation.json"
COST_SENS = STAGE3 / "cost_sensitivity.json"
DEG_SUMMARY = STAGE3 / "degradation_summary.json"

DEVICES = ("bat", "fc", "el")
ROLES = ("compact", "balanced", "hydrogen_dominant", "battery_dominant", "critical")


def _load(path: Path):
    if not path.exists():
        pytest.skip(f"missing Stage-3 artifact: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


# ── double-layer uncertainty ──

def test_double_layer_has_variance_decomposition():
    data = _load(DOUBLE_LAYER)
    for role in ROLES:
        assert role in data, f"role {role} missing"
        assert "variance_decomposition" in data[role], f"{role}: no variance_decomposition"
        for dev in DEVICES:
            assert dev in data[role]["variance_decomposition"], f"{role}/{dev} missing"


def test_double_layer_total_variance_closure():
    """Var_total == Var_outer(E_inner) + E_outer(Var_inner) to machine precision."""
    data = _load(DOUBLE_LAYER)
    for role in ROLES:
        for dev in DEVICES:
            vd = data[role]["variance_decomposition"][dev]
            lhs = vd["var_total"]
            rhs = vd["var_epistemic_outer"] + vd["var_aleatory_inner"]
            assert lhs > 0, f"{role}/{dev}: zero total variance"
            assert abs(lhs - rhs) / lhs < 1e-9, (
                f"{role}/{dev}: closure violated, {lhs:.6e} vs {rhs:.6e}")
            assert vd["closure_pass"] is True


def test_double_layer_variance_fractions_normalised():
    data = _load(DOUBLE_LAYER)
    for role in ROLES:
        for dev in DEVICES:
            vd = data[role]["variance_decomposition"][dev]
            total = vd["frac_epistemic"] + vd["frac_aleatory"]
            assert abs(total - 1.0) < 1e-9, f"{role}/{dev}: fractions sum {total}"
            assert 0.0 <= vd["frac_epistemic"] <= 1.0
            assert 0.0 <= vd["frac_aleatory"] <= 1.0


def test_double_layer_sub_ci_inside_pooled_ci():
    """Layer-specific 90% CIs must not exceed the pooled 90% CI envelope."""
    data = _load(DOUBLE_LAYER)
    tol = 1e-6
    for role in ROLES:
        for dev in DEVICES:
            pooled = data[role][f"{dev}_annual_damage"]
            vd = data[role]["variance_decomposition"][dev]
            for key in ("epistemic_ci90", "aleatory_ci90"):
                lo, hi = vd[key]
                assert lo >= pooled["p05"] - tol, f"{role}/{dev}/{key} lower escapes pooled CI"
                assert hi <= pooled["p95"] + tol, f"{role}/{dev}/{key} upper escapes pooled CI"


def test_double_layer_balanced_design():
    data = _load(DOUBLE_LAYER)
    for role in ROLES:
        r = data[role]
        assert r["total_samples"] == r["n_outer"] * r["n_inner"]
        for dev in DEVICES:
            assert r[f"{dev}_annual_damage"]["n_samples"] == r["total_samples"]


# ── cost sensitivity ──

def test_cost_sensitivity_baseline_matches_stage3():
    cost = _load(COST_SENS)
    deg = _load(DEG_SUMMARY)
    deg_by_role = {r["role"]: r for r in deg["representatives"]}
    for role in ROLES:
        base = cost["representatives"][role]["baseline"]
        ref = deg_by_role[role]["economics"]
        for field in ("total_capex_cny", "npc_cny", "lcoe_cny_per_kwh"):
            rel = abs(base[field] - ref[field]) / max(abs(ref[field]), 1e-12)
            assert rel < 1e-9, f"{role}/{field}: {base[field]} vs {ref[field]}"


def test_cost_sensitivity_price_monotonicity():
    """+30% unit price must raise LCOE, -30% must lower it, for every device."""
    cost = _load(COST_SENS)
    for role in ROLES:
        sc = cost["representatives"][role]
        base = sc["baseline"]["lcoe_cny_per_kwh"]
        for dev in ("PV", "BAT", "PEMFC", "PEMEL", "H2_tank"):
            up = sc[f"{dev}_+30%"]["lcoe_cny_per_kwh"]
            dn = sc[f"{dev}_-30%"]["lcoe_cny_per_kwh"]
            assert up > base > dn, f"{role}/{dev}: non-monotone {dn} / {base} / {up}"


def test_cost_sensitivity_all_device_envelope():
    """Simultaneous +-30% must bound every single-device scenario."""
    cost = _load(COST_SENS)
    for role in ROLES:
        sc = cost["representatives"][role]
        lo = sc["ALL_-30%"]["lcoe_cny_per_kwh"]
        hi = sc["ALL_+30%"]["lcoe_cny_per_kwh"]
        for dev in ("PV", "BAT", "PEMFC", "PEMEL", "H2_tank"):
            assert lo <= sc[f"{dev}_-30%"]["lcoe_cny_per_kwh"] + 1e-9
            assert hi >= sc[f"{dev}_+30%"]["lcoe_cny_per_kwh"] - 1e-9


def test_cost_sensitivity_all_price_scaling_is_linear():
    """CAPEX/NPC scale exactly with a uniform price multiplier (no fixed cost)."""
    cost = _load(COST_SENS)
    for role in ROLES:
        sc = cost["representatives"][role]
        for label, mult in (("ALL_-30%", 0.7), ("ALL_+30%", 1.3)):
            got = sc[label]["npc_cny"]
            exp = sc["baseline"]["npc_cny"] * mult
            assert abs(got - exp) / exp < 1e-9, f"{role}/{label}: {got} vs {exp}"


def test_cost_sensitivity_bop_removal():
    """Removing BOP must remove exactly the BOP adders (round16: PE 15% + install 20%
    + gas conditioning 12% + H2 compression 15% + system BOP 10%), leaving equipment only."""
    cost = _load(COST_SENS)
    for role in ROLES:
        sc = cost["representatives"][role]
        base = sc["baseline"]
        nb = sc["no_BOP"]
        assert nb["bop_capex_cny"] == pytest.approx(0.0, abs=1e-9)
        assert nb["total_capex_cny"] == pytest.approx(base["equipment_capex_cny"], rel=1e-12)
        # no_BOP/base ratio must equal equipment_subtotal / total_initial (data-consistent),
        # not a fixed 1/1.35 (BOP composition changed in round16 cost-boundary extension).
        assert nb["total_capex_cny"] / base["total_capex_cny"] == pytest.approx(
            base["equipment_capex_cny"] / base["total_capex_cny"], rel=1e-9)
        # and the removed BOP share is strictly positive (equipment < total).
        assert base["equipment_capex_cny"] < base["total_capex_cny"]
        assert nb["lcoe_cny_per_kwh"] < base["lcoe_cny_per_kwh"]


def test_cost_sensitivity_exchange_rate_scope():
    """CNY LCOE invariant under exchange rate; USD LCOE scales inversely."""
    cost = _load(COST_SENS)
    for role in ROLES:
        sc = cost["representatives"][role]
        base = sc["baseline"]
        for label, mult in (("rate-50%", 0.5), ("rate+50%", 1.5)):
            s = sc[label]
            assert s["lcoe_cny_per_kwh"] == pytest.approx(
                base["lcoe_cny_per_kwh"], rel=1e-12), f"{role}/{label}: CNY LCOE moved"
            assert s["lcoe_usd_per_kwh"] == pytest.approx(
                base["lcoe_usd_per_kwh"] / mult, rel=1e-12)


def test_cost_sensitivity_replacements_consistent_with_eol_events():
    """Replacement counts must equal de-duplicated in-horizon EOL event years."""
    cost = _load(COST_SENS)
    deg = _load(DEG_SUMMARY)
    deg_by_role = {r["role"]: r for r in deg["representatives"]}
    device_map = {"bat": "BAT", "fc": "PEMFC", "el": "PEMEL"}
    for role in ROLES:
        events = deg_by_role[role]["eol_events"].get("events", [])
        expected = {"BAT": set(), "PEMFC": set(), "PEMEL": set()}
        for ev in events:
            key = device_map.get(ev["device"])
            if key is None:
                continue
            yr = int(ev["time_hours"] / 8760.0)
            if 0 < yr < 20:
                expected[key].add(yr)
        got = cost["representatives"][role]["baseline"]["replacements"]
        for dev in expected:
            assert got[dev] == len(expected[dev]), f"{role}/{dev}: {got[dev]} vs {len(expected[dev])}"
