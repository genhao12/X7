#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Publication assets for the rectified Stage-2c / Stage-3 pipeline.

Produces citable figures (300 dpi) and tables (CSV) from the verified
Stage-2c admission search and the Stage-3 degradation / uncertainty /
economics artifacts. Legacy (pre-rectification) results are NOT touched:
everything is written under 07_results/rectification_v2_stage2c/.

Figures
  F1 admission_representatives : capacities + 3-year LPSP vs 1% limit
  F2 degradation_lifetime      : annual damage, lifetime, replacement counts
  F3 uncertainty_decomposition : epistemic/aleatory variance split + 90% CI,
                                 with a VS7 posterior-narrowing footnote (separate
                                 experiment; authoritative numbers in T6)
  F4 economics_sensitivity     : LCOE envelope + per-device price tornado
  F5 coupling_paths            : (a) Shapley mediation split, (b) alpha1 with the
                                 four-mechanism FC branch, (c) other cross-device paths
  F6 fc_model_sensitivity      : (a) six independent four-mech/legacy ratio estimates,
                                 (b) alpha1 sensitivity band at D_BAT = 0.7

Tables
  T1_representatives.csv
  T2_lifetime_replacements.csv
  T3_variance_decomposition.csv   -- 15 data rows + trailing __NOTE__ annotation
                                     rows (VS7 posterior narrowing, cross-ref to T6)
  T4_cost_sensitivity_lcoe.csv
  T5_coupling_paths.csv          -- two frameworks, explicitly separated
  T6_fc_model_sensitivity.csv    -- alpha1 band, branch isolation, VS7 narrowing

CALIBRE NOTE (2026-08-31)
  T5 carries two *non-interchangeable* frameworks in one file, distinguished by
  the `framework` column:
    Shapley_mediation    -- mediation split of BAT->FC / BAT->EL into capacity and
                            power-limit channels. Contribution attribution only;
                            must not be phrased as causal transmission (limitations L218).
    counterfactual_path  -- effect of toggling the source device's degradation
                            feedback in two otherwise identical dispatches. This is
                            the framework behind the headline alpha1 = +24.33%.
  T6 blocks are robustness evidence from *separate* experiments (four-mechanism
  replay, VS7 LHS) and are deliberately kept out of T3's numeric rows, whose
  variance budget comes from the Stage-3 nested double-layer design. T3 only
  *cross-references* the VS7 narrowing via trailing rows whose `role` field is
  the marker `__NOTE__`; those rows are annotations, not data, and must be
  filtered out before any numeric use of T3.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[2]
STAGE = ROOT / "07_results" / "rectification_v2_stage2c"
STAGE3 = STAGE / "stage3"
FIG_DIR = STAGE / "figures"
TAB_DIR = STAGE / "tables"

ROLES = ["compact", "balanced", "hydrogen_dominant", "battery_dominant", "critical"]
ROLE_LABEL = {
    "compact": "Compact",
    "balanced": "Balanced",
    "hydrogen_dominant": "H2-dominant",
    "battery_dominant": "BAT-dominant",
    "critical": "Critical",
}
DEVICES = ["bat", "fc", "el"]
DEV_LABEL = {"bat": "BAT", "fc": "PEMFC", "el": "PEMEL"}

C_BAT = "#2166AC"
C_FC = "#D6604D"
C_EL = "#4DAF4A"
C_DEV = {"bat": C_BAT, "fc": C_FC, "el": C_EL}
C_EPI = "#4A6FA5"
C_ALE = "#E8A33D"

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial", "DejaVu Sans"],
    "font.size": 9,
    "axes.titlesize": 10,
    "axes.labelsize": 9,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "legend.fontsize": 7.5,
    "figure.dpi": 300,
    "savefig.dpi": 300,
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.05,
    "axes.linewidth": 0.6,
    "axes.grid": True,
    "grid.alpha": 0.3,
    "grid.linewidth": 0.4,
})

WIDTH_2COL = 7.08


def load_json(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"missing artifact: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def write_csv(path: Path, header: list, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    print(f"  wrote {path.relative_to(ROOT)}  ({len(rows)} rows)")


# ── data access ──

def load_vs7_narrowing() -> dict:
    """Read the R08 posterior-narrowing block from the VS7 summary CSV.

    Returns {metric: {prior_width, post_width, narrow_pct}} or {} if absent.
    NOTE: VS7 is a *separate* verification experiment (168 h window, prescribed
    D_test=0.90, 8-parameter LHS). Its narrowing figures are NOT part of the
    Stage-3 double-layer uncertainty budget and must not be merged into T3.
    """
    path = ROOT / "07_results" / "annual" / "VS7_uncertainty_summary.csv"
    if not path.exists():
        return {}
    out: dict = {}
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    for i, r in enumerate(rows):
        if r and r[0].startswith("R08_Target"):
            hdr = r
            for row in rows[i + 1:]:
                if not row or not row[0].startswith("AE"):
                    break
                d = dict(zip(hdr, row))
                try:
                    out[d["R08_Target"]] = {
                        "prior_width": float(d["prior_width"]),
                        "post_width": float(d["post_width"]),
                        "narrow_pct": float(d["narrow_pct"]),
                    }
                except (KeyError, ValueError):
                    continue
            break
    return out


def load_all() -> dict:
    adm = load_json(STAGE / "search" / "verified_admissions.json")
    deg = load_json(STAGE3 / "degradation_summary.json")
    dbl = load_json(STAGE3 / "double_layer_propagation.json")
    cost = load_json(STAGE3 / "cost_sensitivity.json")
    cross = load_json(STAGE3 / "cross_device_paths.json")

    # Four-mechanism FC branch (counterfactual paths, independent artifact).
    four_path = STAGE3 / "cross_device_paths_four_mech.json"
    cross_four = load_json(four_path) if four_path.exists() else None

    return {
        "adm": {r["representative_role"]: r for r in adm["representatives"]},
        "deg": {r["role"]: r for r in deg["representatives"]},
        "dbl": dbl,
        "cost": cost["representatives"],
        "cross": cross,
        "cross_four": cross_four,
        "vs7": load_vs7_narrowing(),
    }


# ── tables ──

def table_representatives(d: dict) -> None:
    rows = []
    for role in ROLES:
        a = d["adm"][role]
        m = a["metrics"]
        lpsp_years = [m["yearly"][str(y)]["lpsp"] for y in (2016, 2017, 2018)]
        rows.append([
            ROLE_LABEL[role],
            f"{a['pv_kwp']:.1f}", f"{a['bat_kwh']:.1f}", f"{a['pemfc_kw']:.1f}",
            f"{a['pemel_kw']:.1f}", f"{a['h2_kg']:.1f}",
            f"{m['three_year_lpsp'] * 100:.4f}",
            *[f"{v * 100:.4f}" for v in lpsp_years],
            f"{m['block_soc_delta']:.5f}",
            f"{m['block_h2_delta_fraction'] * 100:.3f}",
            "PASS" if a.get("state_closure_pass") else "FAIL",
            "PASS" if a.get("conservation_pass") else "FAIL",
        ])
    write_csv(TAB_DIR / "T1_representatives.csv",
              ["role", "PV_kWp", "BAT_kWh", "PEMFC_kW", "PEMEL_kW", "H2_kg",
               "LPSP_3yr_%", "LPSP_2016_%", "LPSP_2017_%", "LPSP_2018_%",
               "block_SOC_delta", "block_H2_delta_%", "state_closure", "conservation"],
              rows)


def table_lifetime(d: dict) -> None:
    rows = []
    for role in ROLES:
        g = d["deg"][role]
        ev = g["eol_events"]
        rows.append([
            ROLE_LABEL[role],
            *[f"{g['avg_annual_damage'][dev]:.4f}" for dev in DEVICES],
            *[f"{ev['lifetime_years'][dev]:.2f}" for dev in DEVICES],
            ev["replacement_counts"]["bat"], ev["replacement_counts"]["fc"],
            ev["replacement_counts"]["el"],
            f"{g['economics']['total_capex_cny'] / 1e6:.3f}",
            f"{g['economics']['npc_cny'] / 1e6:.3f}",
            f"{g['economics']['lcoe_cny_per_kwh']:.3f}",
        ])
    write_csv(TAB_DIR / "T2_lifetime_replacements.csv",
              ["role", "dD_BAT_yr", "dD_PEMFC_yr", "dD_PEMEL_yr",
               "life_BAT_y", "life_PEMFC_y", "life_PEMEL_y",
               "repl_BAT", "repl_PEMFC", "repl_PEMEL",
               "CAPEX_MCNY", "NPC_MCNY", "LCOE_CNY_per_kWh"], rows)


def table_variance(d: dict) -> None:
    rows = []
    for role in ROLES:
        r = d["dbl"][role]
        for dev in DEVICES:
            s = r[f"{dev}_annual_damage"]
            v = r["variance_decomposition"][dev]
            rows.append([
                ROLE_LABEL[role], DEV_LABEL[dev],
                f"{s['mean']:.5f}", f"{s['std']:.5f}",
                f"{s['p05']:.5f}", f"{s['p95']:.5f}",
                f"{v['var_total']:.3e}", f"{v['var_epistemic_outer']:.3e}",
                f"{v['var_aleatory_inner']:.3e}",
                f"{v['frac_epistemic'] * 100:.2f}", f"{v['frac_aleatory'] * 100:.2f}",
                f"{v['closure_residual_rel']:.2e}",
                "PASS" if v["closure_pass"] else "FAIL",
            ])
    # ── Trailing annotation rows (NOT data) ────────────────────────────────
    # The VS7 posterior-narrowing result is a *separate* local-verification
    # experiment (VS7 window, AE metrics, prior-vs-posterior CI widths). It is
    # deliberately NOT folded into the 15 variance-decomposition rows above,
    # which are the Stage-3 nested double-layer budget. Cross-referenced here
    # so the reader sees it next to the uncertainty evidence; authoritative
    # numbers live in T6.
    narrowing = d.get("vs7") or {}
    if narrowing:
        def _note(tag, text):
            rows.append(["__NOTE__", tag] + [""] * 9 + ["", text])

        _note("readme",
              "Rows with role=__NOTE__ are annotations, not data; the 15 rows "
              "above are the Stage-3 nested double-layer variance budget.")
        def _pct(key):
            rec = narrowing.get(key)
            return None if rec is None else rec.get("narrow_pct")

        b = _pct("AE_BAT")
        t = _pct("AE_TOTAL")
        f = _pct("AE_FC")
        e = _pct("AE_EL")
        if b is not None and t is not None:
            def _w(v):
                # A zero narrowing is a zero, not a signed zero: the stored
                # value is the reduction, so "0.00" must not read "-0.00".
                return "0.00" if abs(v) < 5e-3 else f"{v:.2f}"

            _note("VS7_posterior_narrowing",
                  f"VS7 v2 (separate local-verification window, not the "
                  f"Stage-3 budget): BAT cycle coefficient a_d posterior "
                  f"N(5.085e-4, 2.789e-5) via inverse CDF into LHS -> 95% CI "
                  f"width reduction: AE_BAT {_w(b)}%; AE_TOTAL {_w(t)}%"
                  + (f"; AE_FC {_w(f)}%" if f is not None else "")
                  + (f"; AE_EL {_w(e)}%" if e is not None else "")
                  + ". Authoritative values: T6_fc_model_sensitivity.csv.")
            _note("VS7_narrowing_caveat",
                  "AE_TOTAL narrows far less than AE_BAT because BAT damage is "
                  "only ~2.4% of the total; AE_FC/AE_EL show 0.00% narrowing "
                  "because k_cyc_mult is structurally inert on AE_FC/AE_EL and "
                  "eta_degrade on all four metrics in the VS7 design "
                  "(see limitations L50). Do not read these as Stage-3 shares.")
    write_csv(TAB_DIR / "T3_variance_decomposition.csv",
              ["role", "device", "mean_dD_yr", "std", "p05", "p95",
               "var_total", "var_epistemic", "var_aleatory",
               "epistemic_%", "aleatory_%", "closure_residual", "closure"], rows)


def table_cost(d: dict) -> None:
    devs = ["PV", "BAT", "PEMFC", "PEMEL", "H2_tank"]
    rows = []
    for role in ROLES:
        sc = d["cost"][role]
        base = sc["baseline"]["lcoe_cny_per_kwh"]
        rows.append([
            ROLE_LABEL[role], f"{base:.4f}",
            f"{sc['ALL_-30%']['lcoe_cny_per_kwh']:.4f}",
            f"{sc['ALL_+30%']['lcoe_cny_per_kwh']:.4f}",
            f"{sc['no_BOP']['lcoe_cny_per_kwh']:.4f}",
            *[f"{(sc[f'{dev}_+30%']['lcoe_cny_per_kwh'] - base) / base * 100:+.2f}"
              for dev in devs],
        ])
    write_csv(TAB_DIR / "T4_cost_sensitivity_lcoe.csv",
              ["role", "LCOE_base", "LCOE_all-30%", "LCOE_all+30%", "LCOE_noBOP",
               *[f"d%_{dev}+30%" for dev in devs]], rows)


D_LEVELS = ("0.3", "0.5", "0.7")
# Counterfactual cross-device paths: source -> target device (target determines
# whether the four-mechanism FC branch can alter the value).
CROSS_PATHS = (("alpha1", "alpha1 (BAT->FC)"), ("beta1", "beta1 (BAT->EL)"),
               ("alpha2", "alpha2 (EL->FC)"), ("beta2", "beta2 (FC->EL)"),
               ("gamma1", "gamma1 (FC->BAT)"), ("gamma2", "gamma2 (EL->BAT)"))


def table_paths(d: dict) -> None:
    """Six coupling paths under TWO explicitly separated frameworks.

    Framework A `Shapley_mediation`  : closed-loop Shapley mediation split of the
        BAT->FC / BAT->EL effect into a capacity channel (phi_C) and a power-limit
        channel (phi_P), from degradation_summary.json. Contribution attribution
        only -- must NOT be phrased as causal transmission (see limitations L218).
    Framework B `counterfactual_path`: effect of switching the source device's
        degradation feedback on/off in two otherwise identical dispatches, from
        cross_device_paths.json. This is the framework behind the headline
        alpha1 = +24.33% (hydrogen_dominant, D=0.7).

    The two frameworks have different denominators and are NOT interchangeable;
    the `framework` column exists so they are never silently compared.
    """
    four = d.get("cross_four")
    rows = []
    for role in ROLES:
        # ── Framework A: Shapley mediation split (alpha1, beta1) ──
        for src in D_LEVELS:
            sh = d["deg"][role]["shapley"][src]["shapley"]
            for path, tgt in (("alpha1 (BAT->FC)", "fc"), ("beta1 (BAT->EL)", "el")):
                s = sh[tgt]
                base = d["deg"][role]["shapley"][src]["damages"]["00"][tgt]
                rows.append([
                    ROLE_LABEL[role], path, src, "Shapley_mediation",
                    f"{s['total_effect']:.6f}",
                    f"{s['total_effect'] / max(base, 1e-12) * 100:+.2f}",
                    "", "",  # four-mech branch not wired into the mediation split
                    f"{s['phi_C']:.6f}", f"{s['phi_P']:.6f}",
                    f"{s['closure_error']:.2e}", "activated",
                ])
        # ── Framework B: counterfactual cross-device paths (all six) ──
        cr = d["cross"][role]
        for src in D_LEVELS:
            for key, label in CROSS_PATHS:
                e = cr[key][src]
                pct = e["effect_pct"]
                status = "negligible" if abs(pct) < 3.0 else "activated"
                if four is not None:
                    pct_f = four[role][key][src]["effect_pct"]
                    ratio = (pct_f / pct) if abs(pct) > 1e-9 else float("nan")
                    s_four = f"{pct_f:+.2f}"
                    s_ratio = "n/a" if ratio != ratio else f"{ratio:.3f}"
                else:
                    s_four, s_ratio = "", ""
                rows.append([
                    ROLE_LABEL[role], label, src, "counterfactual_path",
                    f"{e['effect']:.6f}", f"{pct:+.2f}",
                    s_four, s_ratio,
                    "", "", "", status,
                ])
    write_csv(TAB_DIR / "T5_coupling_paths.csv",
              ["role", "path", "D_source", "framework", "effect_dD_yr", "effect_%",
               "effect_%_four_mech", "ratio_four_over_legacy",
               "phi_C", "phi_P", "closure_error", "status"], rows)


def table_fc_model_sensitivity(d: dict) -> None:
    """T6: FC model-form sensitivity + posterior narrowing.

    Both blocks are *robustness evidence*, reported separately from the Stage-3
    double-layer uncertainty budget (T3) because they come from different
    experiments with different windows, seeds and damage formulations.
    """
    four = d.get("cross_four")
    rows = []
    # Block 1 -- alpha1 sensitivity band from the counterfactual framework
    if four is not None:
        for role in ROLES:
            for src in D_LEVELS:
                lg = d["cross"][role]["alpha1"][src]["effect_pct"]
                fm = four[role]["alpha1"][src]["effect_pct"]
                rows.append([
                    "alpha1_sensitivity_band", ROLE_LABEL[role], src,
                    f"{lg:+.2f}", f"{fm:+.2f}",
                    f"{fm - lg:+.2f}", "",
                    "legacy = citable primary value; four-mech = sensitivity lower bound",
                ])
        # Branch isolation check: non-FC-target paths should be unchanged
        for key, label in (("beta1", "beta1 (BAT->EL)"), ("beta2", "beta2 (FC->EL)"),
                           ("gamma1", "gamma1 (FC->BAT)"), ("gamma2", "gamma2 (EL->BAT)")):
            deltas = []
            for role in ROLES:
                for src in D_LEVELS:
                    deltas.append(four[role][key][src]["effect_pct"]
                                  - d["cross"][role][key][src]["effect_pct"])
            rows.append([
                "branch_isolation_check", label, "all",
                "", "",
                f"{max(deltas):+.4f}", f"{min(deltas):+.4f}",
                "max/min delta vs legacy, pp (should be ~0: branch only touches FC damage)",
            ])
    # Block 2 -- posterior narrowing from VS7 (separate verification experiment)
    for metric in ("AE_BAT", "AE_TOTAL", "AE_FC", "AE_EL"):
        v = d.get("vs7", {}).get(metric)
        if not v:
            continue
        rows.append([
            "VS7_posterior_narrowing", metric, "n/a",
            f"{v['prior_width']:.6e}", f"{v['post_width']:.6e}",
            "", f"{v['narrow_pct']:.2f}",
            "LHS 95% CI width, prior -> posterior; VS7 window, not Stage-3 budget",
        ])
    write_csv(TAB_DIR / "T6_fc_model_sensitivity.csv",
              ["block", "item", "D_source", "legacy", "four_mech",
               "delta_pp_or_max", "min_or_narrow_pct", "note"], rows)


# ── figures ──

def fig1_admission(d: dict) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(WIDTH_2COL, 2.7))
    x = np.arange(len(ROLES))

    ax = axes[0]
    keys = [("pv_kwp", "PV (kWp)", "#F0A202"), ("bat_kwh", "BAT (kWh)", C_BAT),
            ("pemfc_kw", "PEMFC (kW)", C_FC), ("pemel_kw", "PEMEL (kW)", C_EL),
            ("h2_kg", "H2 (kg)", "#7B68A6")]
    w = 0.16
    for i, (k, lab, c) in enumerate(keys):
        vals = [d["adm"][r][k] for r in ROLES]
        ax.bar(x + (i - 2) * w, vals, w, label=lab, color=c, edgecolor="k", linewidth=0.3)
    ax.set_yscale("log")
    ax.set_xticks(x)
    ax.set_xticklabels([ROLE_LABEL[r] for r in ROLES], rotation=20, ha="right")
    ax.set_ylabel("Capacity (log scale)")
    ax.set_title("(a) Frozen representative configurations")
    ax.legend(ncol=2, framealpha=0.9)

    ax = axes[1]
    w = 0.25
    for i, year in enumerate((2016, 2017, 2018)):
        vals = [d["adm"][r]["metrics"]["yearly"][str(year)]["lpsp"] * 100 for r in ROLES]
        ax.bar(x + (i - 1) * w, np.maximum(vals, 1e-6), w, label=str(year),
               edgecolor="k", linewidth=0.3)
    ax.axhline(1.0, color="crimson", ls="--", lw=1.0, label="LPSP limit 1%")
    ax.set_yscale("log")
    ax.set_xticks(x)
    ax.set_xticklabels([ROLE_LABEL[r] for r in ROLES], rotation=20, ha="right")
    ax.set_ylabel("LPSP (%)")
    ax.set_title("(b) Annual LPSP vs admission limit")
    ax.legend(ncol=2, framealpha=0.9)

    out = FIG_DIR / "F1_admission_representatives.png"
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    print(f"  wrote {out.relative_to(ROOT)}")


def fig2_degradation(d: dict) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(WIDTH_2COL, 2.7))
    x = np.arange(len(ROLES))
    w = 0.26

    ax = axes[0]
    for i, dev in enumerate(DEVICES):
        vals = [d["deg"][r]["avg_annual_damage"][dev] for r in ROLES]
        ax.bar(x + (i - 1) * w, vals, w, label=DEV_LABEL[dev], color=C_DEV[dev],
               edgecolor="k", linewidth=0.3)
    ax.set_xticks(x)
    ax.set_xticklabels([ROLE_LABEL[r] for r in ROLES], rotation=25, ha="right")
    ax.set_ylabel(r"Annual damage $\Delta D$ / yr")
    ax.set_title("(a) Mean annual damage")
    ax.legend()

    ax = axes[1]
    for i, dev in enumerate(DEVICES):
        vals = [d["deg"][r]["eol_events"]["lifetime_years"][dev] for r in ROLES]
        ax.bar(x + (i - 1) * w, vals, w, label=DEV_LABEL[dev], color=C_DEV[dev],
               edgecolor="k", linewidth=0.3)
    ax.axhline(20, color="k", ls=":", lw=0.9, label="20-yr horizon")
    ax.set_xticks(x)
    ax.set_xticklabels([ROLE_LABEL[r] for r in ROLES], rotation=25, ha="right")
    ax.set_ylabel("Time to EOL (years)")
    ax.set_title("(b) Lifetime (EOL event model)")
    ax.legend()

    ax = axes[2]
    for i, dev in enumerate(DEVICES):
        vals = [d["deg"][r]["eol_events"]["replacement_counts"][dev] for r in ROLES]
        ax.bar(x + (i - 1) * w, vals, w, label=DEV_LABEL[dev], color=C_DEV[dev],
               edgecolor="k", linewidth=0.3)
    ax.set_xticks(x)
    ax.set_xticklabels([ROLE_LABEL[r] for r in ROLES], rotation=25, ha="right")
    ax.set_ylabel("Replacements within 20 yr")
    ax.set_yticks([0, 1, 2, 3])
    ax.set_title("(c) Replacement events")
    ax.legend()

    out = FIG_DIR / "F2_degradation_lifetime.png"
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    print(f"  wrote {out.relative_to(ROOT)}")


def fig3_uncertainty(d: dict) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(WIDTH_2COL, 2.9))

    labels, epi, ale = [], [], []
    for role in ROLES:
        for dev in DEVICES:
            v = d["dbl"][role]["variance_decomposition"][dev]
            labels.append(f"{ROLE_LABEL[role]}\n{DEV_LABEL[dev]}")
            epi.append(v["frac_epistemic"] * 100)
            ale.append(v["frac_aleatory"] * 100)
    x = np.arange(len(labels))

    ax = axes[0]
    ax.bar(x, epi, 0.75, label="Epistemic (degradation params)", color=C_EPI,
           edgecolor="k", linewidth=0.3)
    ax.bar(x, ale, 0.75, bottom=epi, label="Aleatory (load/PV resampling)", color=C_ALE,
           edgecolor="k", linewidth=0.3)
    ax.set_xticks(x)
    ax.set_xticklabels([lab.split("\n")[1] for lab in labels], fontsize=6.5)
    for i in range(0, len(labels), 3):
        ax.text(i + 1, -16, ROLE_LABEL[ROLES[i // 3]], ha="center", fontsize=6.5)
    for i in range(3, len(labels), 3):
        ax.axvline(i - 0.5, color="k", lw=0.4, alpha=0.4)
    ax.set_ylim(0, 100)
    ax.set_ylabel("Variance share (%)")
    ax.set_title("(a) Total-variance decomposition (32x25 nested)")
    ax.legend(loc="lower center", ncol=1, framealpha=0.9)

    ax = axes[1]
    xr = np.arange(len(ROLES))
    w = 0.26
    for i, dev in enumerate(DEVICES):
        mean = np.array([d["dbl"][r][f"{dev}_annual_damage"]["mean"] for r in ROLES])
        lo = np.array([d["dbl"][r][f"{dev}_annual_damage"]["p05"] for r in ROLES])
        hi = np.array([d["dbl"][r][f"{dev}_annual_damage"]["p95"] for r in ROLES])
        ax.errorbar(xr + (i - 1) * w, mean, yerr=[mean - lo, hi - mean], fmt="o",
                    color=C_DEV[dev], ecolor=C_DEV[dev], elinewidth=1.1, capsize=3,
                    markersize=4, label=DEV_LABEL[dev])
    ax.set_xticks(xr)
    ax.set_xticklabels([ROLE_LABEL[r] for r in ROLES], rotation=25, ha="right")
    ax.set_ylabel(r"Annual damage $\Delta D$ / yr")
    ax.set_title("(b) Pooled 90% CI (800 samples each)")
    ax.legend()

    # Footnote: posterior narrowing is a separate VS7 experiment, reported here
    # only as a cross-reference (authoritative numbers in T6).
    nw = d.get("vs7") or {}
    if nw.get("AE_BAT") and nw.get("AE_TOTAL"):
            fig.text(
                0.5, 0.015,
                "VS7 posterior (separate local-verification window, not the Stage-3 "
                "budget above): AE$_{BAT}$ 95% CI width "
                f"\u2212{nw['AE_BAT']['narrow_pct']:.2f}%, "
                f"AE$_{{TOTAL}}$ \u2212{nw['AE_TOTAL']['narrow_pct']:.2f}%; "
                "AE$_{FC}$/AE$_{EL}$ 0.00% (structural inertness, L50). See T6.",
                ha="center", fontsize=6.4, color="0.25",
            )

    out = FIG_DIR / "F3_uncertainty_decomposition.png"
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    print(f"  wrote {out.relative_to(ROOT)}")


def fig4_economics(d: dict) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(WIDTH_2COL, 2.9))
    x = np.arange(len(ROLES))

    ax = axes[0]
    base = np.array([d["cost"][r]["baseline"]["lcoe_cny_per_kwh"] for r in ROLES])
    lo = np.array([d["cost"][r]["ALL_-30%"]["lcoe_cny_per_kwh"] for r in ROLES])
    hi = np.array([d["cost"][r]["ALL_+30%"]["lcoe_cny_per_kwh"] for r in ROLES])
    nobop = np.array([d["cost"][r]["no_BOP"]["lcoe_cny_per_kwh"] for r in ROLES])
    ax.bar(x, base, 0.5, yerr=[base - lo, hi - base], capsize=4, color="#6B8FB4",
           edgecolor="k", linewidth=0.4, label="Baseline (+-30% all prices)")
    ax.plot(x, nobop, "D", color="#C0504D", markersize=5, label="Excl. BOP")
    ax.set_xticks(x)
    ax.set_xticklabels([ROLE_LABEL[r] for r in ROLES], rotation=25, ha="right")
    ax.set_ylabel("LCOE (CNY/kWh)")
    ax.set_title("(a) LCOE and cost envelope")
    ax.legend(loc="upper left", framealpha=0.9)

    ax = axes[1]
    devs = ["PV", "BAT", "PEMFC", "PEMEL", "H2_tank"]
    colors = ["#F0A202", C_BAT, C_FC, C_EL, "#7B68A6"]
    w = 0.16
    for i, (dev, c) in enumerate(zip(devs, colors)):
        vals = []
        for r in ROLES:
            b = d["cost"][r]["baseline"]["lcoe_cny_per_kwh"]
            up = d["cost"][r][f"{dev}_+30%"]["lcoe_cny_per_kwh"]
            vals.append((up - b) / b * 100)
        ax.bar(x + (i - 2) * w, vals, w, label=dev, color=c, edgecolor="k", linewidth=0.3)
    ax.set_xticks(x)
    ax.set_xticklabels([ROLE_LABEL[r] for r in ROLES], rotation=25, ha="right")
    ax.set_ylabel("LCOE change (%) per +30% unit price")
    ax.set_title("(b) Unit-price sensitivity")
    ax.legend(ncol=2, framealpha=0.9)

    out = FIG_DIR / "F4_economics_sensitivity.png"
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    print(f"  wrote {out.relative_to(ROOT)}")


def _cross_validation_ratios(d: dict) -> list:
    """Six independent estimates of the four-mechanism / legacy FC-damage ratio.

    Four of the six are recomputed here from on-disk artifacts; the remaining two
    (module self-check, literature prior) cannot be recomputed and are carried as
    explicit constants with their provenance recorded in the label.
    """
    out = []

    # (1) VS7 LHS branch effect on AE_FC
    try:
        with (ROOT / "07_results" / "annual" / "VS7_uncertainty_summary.csv").open(
                encoding="utf-8", newline="") as f:
            rows = list(csv.reader(f))
        for i, r in enumerate(rows):
            if r and r[0].startswith("Branch_effect"):
                hdr = r
                for row in rows[i + 1:]:
                    if not row or not row[0].startswith("AE"):
                        break
                    rec = dict(zip(hdr, row))
                    if rec.get("Branch_effect") == "AE_FC":
                        out.append(("VS7 LHS", float(rec["ratio"]), None))
                break
    except (OSError, KeyError, ValueError):
        pass

    # (2) B2: 999 h dynamic-duty replay
    try:
        b2 = load_json(ROOT / "07_results" / "gap_closure" / "s4_fc_four_mechanism_check.json")
        out.append(("B2 999h replay",
                    b2["main_j_rated_1.0"]["ratio_four_over_legacy"], None))
    except (OSError, KeyError):
        pass

    # (3) Counterfactual six-path absolute effects (min--max across roles/levels)
    four = d.get("cross_four")
    if four is not None:
        rs = []
        for role in ROLES:
            for src in D_LEVELS:
                a = d["cross"][role]["alpha1"][src]["effect"]
                b = four[role]["alpha1"][src]["effect"]
                if abs(a) > 1e-12:
                    rs.append(b / a)
        if rs:
            out.append(("Six-path effect", float(np.mean(rs)),
                        (float(np.min(rs)), float(np.max(rs)))))

    # (4) F7 path-aligned Shapley (C5 config, W1 window)
    try:
        fa = load_json(ROOT / "07_results" / "path_identification"
                       / "f7_path_aligned_shapley.json")
        fb = load_json(ROOT / "07_results" / "path_identification"
                       / "f7_path_aligned_shapley_four_mech.json")
        ea, eb = fa["paths"]["alpha1"]["path_effect"], fb["paths"]["alpha1"]["path_effect"]
        if abs(ea) > 1e-12:
            out.append(("F7 Shapley", eb / ea, None))
    except (OSError, KeyError, ZeroDivisionError):
        pass

    # (5)-(6) not recomputable here -- provenance recorded explicitly
    out.append(("Module self-check*", 2.9758, None))
    out.append(("Literature prior*", 2.57, None))
    return out


def fig6_fc_model_sensitivity(d: dict) -> None:
    """F6: (a) six independent ratio estimates; (b) alpha1 sensitivity band."""
    ratios = _cross_validation_ratios(d)
    four = d.get("cross_four")
    fig, axes = plt.subplots(1, 2, figsize=(WIDTH_2COL, 2.9))

    ax = axes[0]
    names = [r[0] for r in ratios]
    vals = [r[1] for r in ratios]
    ypos = np.arange(len(names))
    err = None
    if any(r[2] is not None for r in ratios):
        lo = np.array([r[1] - (r[2][0] if r[2] else r[1]) for r in ratios])
        hi = np.array([(r[2][1] if r[2] else r[1]) - r[1] for r in ratios])
        err = ax.barh(ypos, vals, 0.62, xerr=[lo, hi], capsize=3,
                      color="#8C6BB1", edgecolor="k", linewidth=0.3)
    else:
        ax.barh(ypos, vals, 0.62, color="#8C6BB1", edgecolor="k", linewidth=0.3)
    ax.axvline(1.0, color="crimson", ls="--", lw=0.9, label="legacy = 1.0")
    ax.set_yticks(ypos)
    ax.set_yticklabels(names, fontsize=7)
    ax.set_xlabel(r"Four-mechanism / legacy FC damage ratio (--)")
    ax.set_title("(a) Six independent estimates")
    ax.legend(loc="lower right", framealpha=0.9)
    ax.text(0.02, -0.20, "* not recomputed here; value carried from the "
            "module self-check / literature prior",
            transform=ax.transAxes, fontsize=6, color="dimgrey")

    ax = axes[1]
    if four is not None:
        x = np.arange(len(ROLES))
        w = 0.38
        lg = [d["cross"][r]["alpha1"]["0.7"]["effect_pct"] for r in ROLES]
        fm = [four[r]["alpha1"]["0.7"]["effect_pct"] for r in ROLES]
        ax.bar(x - w / 2, lg, w, label="legacy FC model (primary)",
               color=C_FC, edgecolor="k", linewidth=0.3)
        ax.bar(x + w / 2, fm, w, label="four-mechanism FC model (sensitivity)",
               color="#8C6BB1", edgecolor="k", linewidth=0.3)
        ax.set_xticks(x)
        ax.set_xticklabels([ROLE_LABEL[r] for r in ROLES], rotation=25, ha="right")
        ax.set_ylabel(r"$\alpha_1$ effect on PEMFC damage (%)")
        ax.set_title(r"(b) $\alpha_1$ sensitivity band at $D_{BAT}=0.7$")
        ax.legend(fontsize=6.5, framealpha=0.9)
    else:
        ax.text(0.5, 0.5, "cross_device_paths_four_mech.json\nnot available",
                ha="center", va="center", fontsize=8, color="dimgrey")
        ax.axis("off")

    out_p = FIG_DIR / "F6_fc_model_sensitivity.png"
    fig.tight_layout()
    fig.savefig(out_p)
    plt.close(fig)
    print(f"  wrote {out_p.relative_to(ROOT)}")


def fig5_paths(d: dict) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(WIDTH_2COL, 2.75))
    x = np.arange(len(ROLES))

    # (a) Framework A -- Shapley mediation split (contribution attribution only)
    ax = axes[0]
    w = 0.35
    for i, (tgt, lab, c) in enumerate((("fc", r"$\alpha_1$ BAT$\to$FC", C_FC),
                                       ("el", r"$\beta_1$ BAT$\to$EL", C_EL))):
        phic, phip = [], []
        for r in ROLES:
            s = d["deg"][r]["shapley"]["0.5"]["shapley"][tgt]
            phic.append(s["phi_C"])
            phip.append(s["phi_P"])
        pos = x + (i - 0.5) * w
        ax.bar(pos, phic, w * 0.9, color=c, edgecolor="k", linewidth=0.3,
               label=f"{lab}: $\\phi_C$")
        ax.bar(pos, phip, w * 0.9, bottom=phic, color=c, alpha=0.45,
               edgecolor="k", linewidth=0.3, label=f"{lab}: $\\phi_P$")
    ax.axhline(0, color="k", lw=0.6)
    ax.set_xticks(x)
    ax.set_xticklabels([ROLE_LABEL[r] for r in ROLES], rotation=30, ha="right")
    ax.set_ylabel(r"Shapley contribution ($\Delta D$/yr)")
    ax.set_title("(a) Mediation split, $D_{BAT}=0.5$")
    ax.legend(fontsize=6, ncol=2, framealpha=0.9)

    # (b) Framework B -- counterfactual alpha1, legacy vs four-mechanism
    ax = axes[1]
    four = d.get("cross_four")
    w2 = 0.26
    for i, src in enumerate(D_LEVELS):
        lg = [d["cross"][r]["alpha1"][src]["effect_pct"] for r in ROLES]
        ax.bar(x + (i - 1) * w2, lg, w2, edgecolor="k", linewidth=0.3,
               label=f"$D_{{BAT}}$={src} legacy")
        if four is not None:
            fm = [four[r]["alpha1"][src]["effect_pct"] for r in ROLES]
            ax.bar(x + (i - 1) * w2, fm, w2 * 0.45, edgecolor="k", linewidth=0.3,
                   color="#8C6BB1",
                   label="four-mechanism (all $D_{BAT}$)" if i == 0 else "")
    ax.axhline(0, color="k", lw=0.6)
    ax.set_xticks(x)
    ax.set_xticklabels([ROLE_LABEL[r] for r in ROLES], rotation=30, ha="right")
    ax.set_ylabel(r"$\alpha_1$ effect on PEMFC damage (%)")
    ax.set_title(r"(b) $\alpha_1$, counterfactual framework")
    ax.legend(fontsize=5.6, ncol=1, framealpha=0.9)

    # (c) Framework B -- remaining cross-device paths
    ax = axes[2]
    paths = [("alpha2", r"$\alpha_2$ EL$\to$FC"), ("beta2", r"$\beta_2$ FC$\to$EL"),
             ("gamma1", r"$\gamma_1$ FC$\to$BAT"), ("gamma2", r"$\gamma_2$ EL$\to$BAT")]
    w = 0.2
    for i, (key, lab) in enumerate(paths):
        vals = [d["cross"][r][key]["0.5"]["effect_pct"] for r in ROLES]
        ax.bar(x + (i - 1.5) * w, vals, w, label=lab, edgecolor="k", linewidth=0.3)
    ax.axhline(0, color="k", lw=0.6)
    ax.axhspan(-3, 3, color="grey", alpha=0.15, zorder=0)
    ax.text(0.02, 0.04, "shaded: |effect| < 3%", transform=ax.transAxes,
            fontsize=6, color="dimgrey")
    ax.set_xticks(x)
    ax.set_xticklabels([ROLE_LABEL[r] for r in ROLES], rotation=30, ha="right")
    ax.set_ylabel("Target damage change (%)")
    ax.set_title(r"(c) Other paths, $D_{source}=0.5$")
    ax.legend(fontsize=6, ncol=2, framealpha=0.9)

    out = FIG_DIR / "F5_coupling_paths.png"
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    print(f"  wrote {out.relative_to(ROOT)}")


def main() -> None:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    TAB_DIR.mkdir(parents=True, exist_ok=True)
    d = load_all()

    print("Tables:")
    table_representatives(d)
    table_lifetime(d)
    table_variance(d)
    table_cost(d)
    table_paths(d)
    table_fc_model_sensitivity(d)

    print("Figures:")
    fig1_admission(d)
    fig2_degradation(d)
    fig3_uncertainty(d)
    fig4_economics(d)
    fig5_paths(d)
    fig6_fc_model_sensitivity(d)

    print("\nAll Stage-3 paper assets regenerated under "
          f"{STAGE.relative_to(ROOT)}/(figures|tables)")


if __name__ == "__main__":
    sys.exit(main())
