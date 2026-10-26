"""Cost sensitivity analysis for the 5 Stage-2c admissible representatives.

Scenarios
  1. Equipment unit price: each device x0.7 / x1.3 (+-30%)
  2. Exchange rate: 7.25 x0.5 / x1.5 (+-50%)
  3. BOP (power electronics + installation): included / excluded

Design notes
  - CAPEX is computed through economics.npc_lite.compute_equipment_capex so
    that this script and run_stage3_full_analysis.compute_economics share one
    single source of truth. Price multipliers are applied by replacing unit
    cost fields on EconomicConfig, never by re-hardcoding prices here.
  - Replacement schedule is taken from the Stage-3 EOL event model with the
    same year rounding rule as compute_economics (int(t/8760), de-duplicated),
    so the baseline scenario must reproduce the Stage-3 economics exactly.
    A consistency assertion enforces this.
  - Exchange rate: all unit costs are CNY-denominated, therefore NPC and the
    CNY LCOE are invariant under exchange rate by construction; the rate only
    rescales the USD-denominated reporting. This is stated explicitly in the
    output rather than being presented as a physical cost sensitivity.
"""
import sys
import json
from dataclasses import replace
from pathlib import Path
from typing import Dict

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "06_src") not in sys.path:
    sys.path.insert(0, str(ROOT / "06_src"))

from economics.npc_lite import EconomicConfig, compute_equipment_capex

# device key -> EconomicConfig unit-cost field
UNIT_COST_FIELD = {
    "PV": "pv_unit_cost",
    "BAT": "bat_unit_cost",
    "PEMFC": "pemfc_unit_cost",
    "PEMEL": "pemel_unit_cost",
    "H2_tank": "h2_tank_unit_cost",
}

DEVICE_MAP = {"bat": "BAT", "fc": "PEMFC", "el": "PEMEL"}


def extract_replacement_years(eol_result: dict, horizon: int) -> Dict[str, list]:
    """Replacement years from EOL events, identical rule to compute_economics."""
    replacements = {"BAT": [], "PEMFC": [], "PEMEL": []}
    for event in eol_result.get("events", []):
        dev_key = DEVICE_MAP.get(event["device"])
        if dev_key is None:
            continue
        yr = int(event["time_hours"] / 8760.0)
        if 0 < yr < horizon and yr not in replacements[dev_key]:
            replacements[dev_key].append(yr)
    for key in replacements:
        replacements[key].sort()
    return replacements


def compute_npc_lcoe(
    rep: dict,
    eol_result: dict,
    price_multipliers: Dict[str, float] = None,
    exchange_rate: float = 7.25,
    include_bop: bool = True,
    horizon_years: int = 20,
    discount: float = 0.06,
) -> dict:
    """NPC / LCOE under one cost scenario."""
    price_multipliers = price_multipliers or {}

    cfg = EconomicConfig(
        pv_kwp=float(rep["pv_kwp"]),
        bat_kwh=float(rep["bat_kwh"]),
        pemfc_kw=float(rep["pemfc_kw"]),
        pemel_kw=float(rep["pemel_kw"]),
        h2_kg=float(rep["h2_kg"]),
        horizon_years=horizon_years,
        discount_rate=discount,
        exchange_rate=exchange_rate,
    )

    # unit price multipliers
    overrides = {}
    for dev, mult in price_multipliers.items():
        field = UNIT_COST_FIELD.get(dev)
        if field is None:
            raise KeyError(f"unknown device for price multiplier: {dev}")
        overrides[field] = getattr(cfg, field) * float(mult)
    # BOP switch
    if not include_bop:
        overrides["power_electronics_fraction"] = 0.0
        overrides["installation_fraction"] = 0.0
        # round16: 成本边界项（气体处理/压缩/系统辅机）亦属 BOP，剥离时一并关闭
        overrides["gas_conditioning_fraction"] = 0.0
        overrides["h2_compression_fraction"] = 0.0
        overrides["system_bop_fraction"] = 0.0
    if overrides:
        cfg = replace(cfg, **overrides)

    capex = compute_equipment_capex(cfg)
    total_capex = capex["total_initial"]

    # served energy: 3-year mean from Stage-2c verified metrics
    yearly = rep.get("metrics", {}).get("yearly", {})
    annual_load_kwh = float(np.mean([
        yearly[str(y)].get("load_energy_kwh", 500000.0) for y in (2016, 2017, 2018)
    ]))
    annual_unserved_kwh = float(np.mean([
        yearly[str(y)].get("unserved_energy_kwh", 0.0) for y in (2016, 2017, 2018)
    ]))
    annual_served_kwh = annual_load_kwh - annual_unserved_kwh

    replacements = extract_replacement_years(eol_result, cfg.horizon_years)
    repl_fraction = {
        "BAT": cfg.bat_replacement_fraction,
        "PEMFC": cfg.pemfc_replacement_fraction,
        "PEMEL": cfg.pemel_replacement_fraction,
    }

    npc = total_capex
    annual_opex = total_capex * cfg.general_opex_fraction
    for y in range(1, cfg.horizon_years + 1):
        pv_factor = 1.0 / (1.0 + cfg.discount_rate) ** y
        npc += annual_opex * pv_factor
        for dev in ("BAT", "PEMFC", "PEMEL"):
            if y in replacements[dev]:
                npc += capex[dev] * repl_fraction[dev] * pv_factor

    d = cfg.discount_rate
    n = cfg.horizon_years
    crf = d * (1 + d) ** n / ((1 + d) ** n - 1) if d > 0 else 1.0 / n
    lcoe_cny = npc * crf / max(annual_served_kwh, 1e-6)

    return {
        "total_capex_cny": total_capex,
        "equipment_capex_cny": capex["equipment_subtotal"],
        "bop_capex_cny": capex["power_electronics"] + capex["installation"],
        "npc_cny": npc,
        "annual_opex_cny": annual_opex,
        "lcoe_cny_per_kwh": lcoe_cny,
        "lcoe_usd_per_kwh": lcoe_cny / cfg.exchange_rate,
        "annual_served_kwh": annual_served_kwh,
        "exchange_rate": cfg.exchange_rate,
        "include_bop": include_bop,
        "replacements": {dev: len(yrs) for dev, yrs in replacements.items()},
    }


def run_cost_sensitivity(admissions_path: Path,
                         degradation_summary_path: Path) -> dict:
    """Full cost sensitivity for all representatives, with baseline check."""
    admissions = json.loads(admissions_path.read_text(encoding="utf-8"))
    deg_summary = json.loads(degradation_summary_path.read_text(encoding="utf-8"))
    deg_by_role = {r["role"]: r for r in deg_summary["representatives"]}

    all_results = {"schema_version": "stage3_cost_sensitivity.1",
                   "notes": {
                       "capex_source": "economics.npc_lite.compute_equipment_capex",
                       "replacement_source": "stage3 EOL event model (int-year rounding, de-duplicated)",
                       "exchange_rate_scope": "USD reporting only; all unit costs are CNY-denominated, "
                                              "so NPC and CNY LCOE are invariant under exchange rate",
                   },
                   "representatives": {}}

    for rep in admissions["representatives"]:
        role = rep["representative_role"]
        deg = deg_by_role.get(role)
        if deg is None:
            raise KeyError(f"role {role} missing in degradation_summary")
        eol_result = deg.get("eol_events", {"events": []})

        scenarios = {"baseline": compute_npc_lcoe(rep, eol_result)}

        # baseline consistency vs Stage-3 economics
        ref = deg.get("economics", {})
        if ref:
            for field in ("total_capex_cny", "npc_cny", "lcoe_cny_per_kwh"):
                got = scenarios["baseline"][field]
                exp = ref[field]
                rel = abs(got - exp) / max(abs(exp), 1e-12)
                if rel > 1e-9:
                    raise AssertionError(
                        f"[{role}] baseline {field} deviates from Stage-3: "
                        f"{got:.6f} vs {exp:.6f} (rel={rel:.3e})")

        for dev in ("PV", "BAT", "PEMFC", "PEMEL", "H2_tank"):
            for mult, label in ((0.7, "-30%"), (1.3, "+30%")):
                scenarios[f"{dev}_{label}"] = compute_npc_lcoe(
                    rep, eol_result, price_multipliers={dev: mult})

        # all devices simultaneously (envelope case)
        for mult, label in ((0.7, "-30%"), (1.3, "+30%")):
            scenarios[f"ALL_{label}"] = compute_npc_lcoe(
                rep, eol_result,
                price_multipliers={d: mult for d in UNIT_COST_FIELD})

        for rate, label in ((7.25 * 0.5, "rate-50%"), (7.25 * 1.5, "rate+50%")):
            scenarios[label] = compute_npc_lcoe(rep, eol_result, exchange_rate=rate)

        scenarios["no_BOP"] = compute_npc_lcoe(rep, eol_result, include_bop=False)

        all_results["representatives"][role] = scenarios

    return all_results


def print_cost_sensitivity_report(all_results: dict):
    print(f"\n{'=' * 92}")
    print("Cost Sensitivity Analysis (Stage-3, 5 admissible representatives)")
    print(f"{'=' * 92}")

    for role, scenarios in all_results["representatives"].items():
        base = scenarios["baseline"]
        print(f"\n  {role}:")
        print(f"    Baseline: CAPEX={base['total_capex_cny'] / 1000:.0f} kCNY  "
              f"NPC={base['npc_cny'] / 1000:.0f} kCNY  "
              f"LCOE={base['lcoe_cny_per_kwh']:.3f} CNY/kWh "
              f"({base['lcoe_usd_per_kwh']:.3f} USD/kWh)  "
              f"repl B/F/E={base['replacements']['BAT']}/"
              f"{base['replacements']['PEMFC']}/{base['replacements']['PEMEL']}")

        cny = {k: v["lcoe_cny_per_kwh"] for k, v in scenarios.items()}
        lo, hi = min(cny, key=cny.get), max(cny, key=cny.get)
        print(f"    LCOE(CNY) envelope: [{cny[lo]:.3f} @{lo}, {cny[hi]:.3f} @{hi}]")

        # per-device elasticity: d(LCOE)/LCOE per +30% price
        elas = []
        for dev in ("PV", "BAT", "PEMFC", "PEMEL", "H2_tank"):
            up = scenarios[f"{dev}_+30%"]["lcoe_cny_per_kwh"]
            elas.append((dev, (up - base["lcoe_cny_per_kwh"]) / base["lcoe_cny_per_kwh"] * 100))
        elas.sort(key=lambda x: -abs(x[1]))
        print("    LCOE sensitivity to +30% unit price: " +
              ", ".join(f"{d}={v:+.2f}%" for d, v in elas))

        nb = scenarios["no_BOP"]
        print(f"    no_BOP: LCOE={nb['lcoe_cny_per_kwh']:.3f} "
              f"({(nb['lcoe_cny_per_kwh'] - base['lcoe_cny_per_kwh']) / base['lcoe_cny_per_kwh'] * 100:+.1f}%)")
        r_lo = scenarios["rate-50%"]
        r_hi = scenarios["rate+50%"]
        print(f"    exchange rate +-50%: LCOE(CNY) invariant "
              f"({r_lo['lcoe_cny_per_kwh']:.3f} = {r_hi['lcoe_cny_per_kwh']:.3f}); "
              f"LCOE(USD) {r_hi['lcoe_usd_per_kwh']:.3f} .. {r_lo['lcoe_usd_per_kwh']:.3f}")

    print(f"\n{'=' * 92}")
    print("Cross-config summary (CNY/kWh)")
    print(f"{'=' * 92}")
    print(f"  {'role':<12}{'baseline':>10}{'ALL-30%':>10}{'ALL+30%':>10}{'no_BOP':>10}{'dominant':>12}")
    for role, scenarios in all_results["representatives"].items():
        base = scenarios["baseline"]["lcoe_cny_per_kwh"]
        elas = [(dev, scenarios[f"{dev}_+30%"]["lcoe_cny_per_kwh"] - base)
                for dev in ("PV", "BAT", "PEMFC", "PEMEL", "H2_tank")]
        dom = max(elas, key=lambda x: abs(x[1]))[0]
        print(f"  {role:<12}{base:>10.3f}"
              f"{scenarios['ALL_-30%']['lcoe_cny_per_kwh']:>10.3f}"
              f"{scenarios['ALL_+30%']['lcoe_cny_per_kwh']:>10.3f}"
              f"{scenarios['no_BOP']['lcoe_cny_per_kwh']:>10.3f}"
              f"{dom:>12}")


if __name__ == "__main__":
    admissions_path = ROOT / "07_results" / "rectification_v2_stage2c" / "search" / "verified_admissions.json"
    degradation_summary_path = ROOT / "07_results" / "rectification_v2_stage2c" / "stage3" / "degradation_summary.json"

    all_results = run_cost_sensitivity(admissions_path, degradation_summary_path)
    print_cost_sensitivity_report(all_results)

    output_path = ROOT / "07_results" / "rectification_v2_stage2c" / "stage3" / "cost_sensitivity.json"
    with open(output_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\nResults written to: {output_path}")
