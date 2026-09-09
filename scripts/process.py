"""
Processing pipeline: takes the raw ingested data (World Bank, IMF DataMapper,
ZimRate, manual CSVs) and produces:

  data/processed/panel.json              - multi-year annual trend series
  data/processed/latest_snapshot.json    - composite CSRI + component detail
  data/processed/devaluation_events.json - manual event log, passthrough
  data/processed/policy_rate_decisions.json
  data/processed/imf_program_status.json
  data/processed/meta.json               - run metadata, sources, sanity checks

Every number in these files traces back to either a live API pull (cached in
raw/api_cache/ for audit) or a manually-sourced, cited CSV row. Nothing here
is invented. Where a component genuinely cannot be computed for a country
(e.g. no parallel-market data for Kenya/Malawi), it is recorded as null with
an explicit reason - never silently defaulted to 0.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pandas as pd

from config import (
    COUNTRIES, PROCESSED_DIR, CSRI_WEIGHTS, INFLATION_VOLATILITY_WINDOW_YEARS,
    START_YEAR, END_YEAR,
)
from ingest_worldbank import fetch_all_worldbank_indicators
from ingest_imf_datamapper import fetch_all_imf_indicators
from ingest_zimrate import fetch_zimbabwe_fx_snapshot
from ingest_manual import load_all_manual_tables
from http_utils import DataFetchError


# --------------------------------------------------------------------------
# Annual panel: inflation, reserves, external debt/GDP over time
# --------------------------------------------------------------------------

def build_annual_panel(wb_df: pd.DataFrame) -> pd.DataFrame:
    """Pivot World Bank long-format data to one row per (country, year) and
    derive external_debt_gdp_pct and inflation_volatility."""
    wide = wb_df.pivot_table(
        index=["country_iso3", "country_name", "year"],
        columns="indicator", values="value", aggfunc="first",
    ).reset_index()
    wide.columns.name = None

    if "external_debt_usd" in wide.columns and "gdp_usd" in wide.columns:
        wide["external_debt_gdp_pct"] = (
            wide["external_debt_usd"] / wide["gdp_usd"] * 100.0
        )
    else:
        wide["external_debt_gdp_pct"] = np.nan

    wide = wide.sort_values(["country_iso3", "year"]).reset_index(drop=True)

    # Inflation volatility = coefficient of variation (std/|mean|) of the
    # trailing INFLATION_VOLATILITY_WINDOW_YEARS of annual inflation,
    # computed per country. Requires at least 2 non-null observations in the
    # window; otherwise NaN (never silently 0).
    if "inflation_cpi_pct" in wide.columns:
        grouped = wide.groupby("country_iso3")["inflation_cpi_pct"]
        roll_std = grouped.rolling(
            window=INFLATION_VOLATILITY_WINDOW_YEARS, min_periods=2
        ).std().reset_index(level=0, drop=True)
        roll_mean = grouped.rolling(
            window=INFLATION_VOLATILITY_WINDOW_YEARS, min_periods=2
        ).mean().reset_index(level=0, drop=True)
        with np.errstate(divide="ignore", invalid="ignore"):
            cv = (roll_std / roll_mean.abs()).replace([np.inf, -np.inf], np.nan)
        wide["inflation_volatility_cv"] = cv
    else:
        wide["inflation_volatility_cv"] = np.nan

    return wide


# --------------------------------------------------------------------------
# Normalization + composite index
# --------------------------------------------------------------------------

@dataclass
class ComponentResult:
    raw_value: float | None
    normalized_0_100: float | None
    included_in_index: bool
    reason_excluded: str | None = None


def min_max_normalize_higher_is_better(values: dict[str, float]) -> dict[str, float]:
    """values: {country_iso3: raw_value}, already oriented so that a HIGHER
    raw value means MORE stable. Returns {country_iso3: 0-100}. If all
    values are equal (or fewer than 2 present), returns 50 for every present
    country - there is no basis to differentiate them."""
    present = {k: v for k, v in values.items() if v is not None and not pd.isna(v)}
    if len(present) < 2:
        return {k: 50.0 for k in present}
    lo, hi = min(present.values()), max(present.values())
    if hi == lo:
        return {k: 50.0 for k in present}
    return {k: (v - lo) / (hi - lo) * 100.0 for k, v in present.items()}


def score_imf_program(status_row: pd.Series | None) -> float | None:
    """Maps a qualitative IMF program status to a 0-100 'more stable' score.
    This is a documented judgment call (see README limitations), not a
    market-derived number:
      - active financing arrangement (EFF/ECF/RSF), confirmed current: 80
      - active non-financing monitoring (SMP): 55
      - status uncertain / needs re-verification: 40
      - no active arrangement, in talks: 30
      - no active arrangement, no talks reported: 15
    """
    if status_row is None:
        return None
    status = str(status_row.get("status", "")).lower()
    if "uncertain" in status or "needs re-verification" in status:
        return 40.0
    if "no active arrangement" in status and "talks" in status:
        return 30.0
    if "no active arrangement" in status:
        return 15.0
    if "active" in status:
        program_type = str(status_row.get("program_type", "")).lower()
        if "smp" in program_type or "staff-monitored" in program_type:
            return 55.0
        return 80.0
    return None  # unrecognized status text - excluded rather than guessed


def build_latest_snapshot(
    panel: pd.DataFrame,
    zwe_fx_snapshot: dict[str, Any] | None,
    manual: dict[str, pd.DataFrame],
) -> dict[str, Any]:
    """Builds the composite CSRI for the most recent year with usable data
    per country, plus full component-level transparency."""

    latest_year_per_country: dict[str, int] = {}
    raw_components: dict[str, dict[str, float | None]] = {c: {} for c in COUNTRIES}
    exclusion_reasons: dict[str, dict[str, str]] = {c: {} for c in COUNTRIES}

    for iso3 in COUNTRIES:
        country_panel = panel[panel["country_iso3"] == iso3].sort_values("year")
        non_null_years = country_panel.dropna(subset=["inflation_cpi_pct"], how="all")
        if non_null_years.empty:
            raise DataFetchError(f"No inflation data available at all for {iso3}.")
        latest_row = non_null_years.iloc[-1]
        latest_year_per_country[iso3] = int(latest_row["year"])

        raw_components[iso3]["inflation_cpi_pct"] = latest_row.get("inflation_cpi_pct")
        raw_components[iso3]["inflation_volatility_cv"] = latest_row.get("inflation_volatility_cv")
        raw_components[iso3]["reserves_months_imports"] = latest_row.get("reserves_months_imports")
        raw_components[iso3]["external_debt_gdp_pct"] = latest_row.get("external_debt_gdp_pct")

    # --- FX spread: live for Zimbabwe, N/A for Kenya/Malawi (documented) ---
    # zwe_fx_snapshot is None when the live ZimRate fetch failed this run
    # (see run_pipeline) - treated exactly like the Kenya/Malawi "no
    # programmatic source" gap: null with an explicit reason, never a
    # fabricated or stale-cached number.
    if zwe_fx_snapshot is not None:
        raw_components["ZWE"]["fx_spread_pct"] = abs(zwe_fx_snapshot["fx_spread_pct"])
    else:
        raw_components["ZWE"]["fx_spread_pct"] = None
        exclusion_reasons["ZWE"]["fx_spread"] = (
            "Live ZimRate fetch failed this run - see meta.json sanity_check_warnings "
            "for the reason. Not fabricated or backfilled with a stale value."
        )
    for iso3 in ("KEN", "MWI"):
        raw_components[iso3]["fx_spread_pct"] = None
        exclusion_reasons[iso3]["fx_spread"] = (
            "No programmatic parallel-market FX source identified for this country "
            "during methodology research; not fabricated."
        )

    # --- Policy rate volatility: std dev across curated manual decisions ---
    policy_df = manual["policy_rate_decisions"]
    for iso3 in COUNTRIES:
        rates = policy_df[policy_df["country_iso3"] == iso3]["policy_rate_pct"]
        if len(rates) >= 2:
            raw_components[iso3]["policy_rate_volatility"] = float(rates.std())
        else:
            raw_components[iso3]["policy_rate_volatility"] = None
            exclusion_reasons[iso3]["policy_rate_volatility"] = (
                f"Only {len(rates)} curated policy rate decision(s) on file for this "
                f"country - not enough to compute a meaningful volatility figure. "
                f"See raw/manual/policy_rate_decisions.csv."
            )

    # --- IMF program qualitative flag ---
    imf_df = manual["imf_program_status"]
    for iso3 in COUNTRIES:
        rows = imf_df[imf_df["country_iso3"] == iso3]
        if rows.empty:
            raw_components[iso3]["imf_program_flag"] = None
            exclusion_reasons[iso3]["imf_program_flag"] = "No IMF program status row on file."
            continue
        latest_status = rows.sort_values("as_of_date").iloc[-1]
        score = score_imf_program(latest_status)
        raw_components[iso3]["imf_program_flag"] = score
        if score is None:
            exclusion_reasons[iso3]["imf_program_flag"] = (
                f"Status text '{latest_status['status']}' did not match any scoring rule."
            )

    # --- Orient raw values so "higher = more stable", then normalize ------
    # inflation: combine level + volatility into one "instability" score
    # (simple average of the two, since both matter and neither dominates
    # by construction), then invert for normalization.
    oriented: dict[str, dict[str, float | None]] = {c: {} for c in COUNTRIES}
    for iso3 in COUNTRIES:
        infl = raw_components[iso3]["inflation_cpi_pct"]
        vol = raw_components[iso3]["inflation_volatility_cv"]
        if infl is not None and not pd.isna(infl):
            # volatility (a ratio, typically 0-2+) is scaled by 100 to put it
            # on a roughly comparable footing with inflation (a percentage)
            # before combining - documented simplification, not a standard.
            instability = infl + (vol * 100.0 if vol is not None and not pd.isna(vol) else 0.0)
            oriented[iso3]["inflation"] = -instability  # higher (less negative) = more stable
        else:
            oriented[iso3]["inflation"] = None
            exclusion_reasons[iso3]["inflation"] = "No inflation observation available."

        fx = raw_components[iso3]["fx_spread_pct"]
        oriented[iso3]["fx_spread"] = -fx if fx is not None else None

        oriented[iso3]["reserve_adequacy"] = raw_components[iso3]["reserves_months_imports"]

        debt = raw_components[iso3]["external_debt_gdp_pct"]
        oriented[iso3]["external_debt"] = -debt if debt is not None and not pd.isna(debt) else None

        prv = raw_components[iso3]["policy_rate_volatility"]
        oriented[iso3]["policy_rate_volatility"] = -prv if prv is not None else None

        oriented[iso3]["imf_program_flag"] = raw_components[iso3]["imf_program_flag"]

    normalized: dict[str, dict[str, float]] = {c: {} for c in COUNTRIES}
    for component in CSRI_WEIGHTS:
        values = {c: oriented[c].get(component) for c in COUNTRIES}
        norm = min_max_normalize_higher_is_better(values)
        for c in COUNTRIES:
            if c in norm:
                normalized[c][component] = norm[c]

    # --- Weighted composite, renormalized over components actually present
    composite: dict[str, float] = {}
    components_used: dict[str, list[str]] = {}
    for iso3 in COUNTRIES:
        present = {k: v for k, v in normalized[iso3].items() if not pd.isna(v)}
        weight_sum = sum(CSRI_WEIGHTS[k] for k in present)
        if weight_sum == 0:
            composite[iso3] = float("nan")
            components_used[iso3] = []
            continue
        score = sum(CSRI_WEIGHTS[k] * present[k] for k in present) / weight_sum
        composite[iso3] = round(score, 2)
        components_used[iso3] = sorted(present.keys())

    return {
        "latest_year_per_country": latest_year_per_country,
        "raw_components": raw_components,
        "normalized_components_0_100": normalized,
        "csri_composite_0_100": composite,
        "components_used_per_country": components_used,
        "components_excluded_reasons": exclusion_reasons,
        "weights": CSRI_WEIGHTS,
        "zwe_fx_detail": (
            zwe_fx_snapshot
            if zwe_fx_snapshot is not None
            else {
                "available": False,
                "reason": exclusion_reasons.get("ZWE", {}).get(
                    "fx_spread",
                    "Live ZimRate fetch failed this run.",
                ),
            }
        ),
    }


# --------------------------------------------------------------------------
# Sanity checks (soft - logged into meta.json, never silently swallowed)
# --------------------------------------------------------------------------

def run_sanity_checks(snapshot: dict[str, Any]) -> list[str]:
    warnings: list[str] = []

    weight_total = sum(CSRI_WEIGHTS.values())
    if abs(weight_total - 1.0) > 1e-6:
        warnings.append(f"CSRI_WEIGHTS sum to {weight_total}, expected 1.0.")

    scores = snapshot["csri_composite_0_100"]
    valid_scores = {k: v for k, v in scores.items() if not pd.isna(v)}
    if len(valid_scores) >= 2:
        most_stable = max(valid_scores, key=valid_scores.get)
        least_stable = min(valid_scores, key=valid_scores.get)
        warnings.append(
            f"Ranking this run: most-stable={most_stable} ({valid_scores[most_stable]}), "
            f"least-stable={least_stable} ({valid_scores[least_stable]})."
        )
        if least_stable != "ZWE" and "ZWE" in valid_scores:
            warnings.append(
                "REVIEW FLAG: Zimbabwe did not score as the least-stable country. "
                "Check raw_components and components_used_per_country before publishing."
            )

    for iso3, reasons in snapshot["components_excluded_reasons"].items():
        for component, reason in reasons.items():
            warnings.append(f"{iso3}: '{component}' excluded from composite - {reason}")

    return warnings


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def _write_json(obj: Any, filename: str) -> Path:
    """Writes strict, browser-parseable JSON. allow_nan=False is deliberate:
    Python's json module accepts bare NaN/Infinity by default (a non-standard
    extension), but JavaScript's JSON.parse does not - a NaN slipping through
    silently produces valid-looking output here that then hard-fails in the
    browser. Better to fail loudly at pipeline run time than ship broken JSON."""
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    path = PROCESSED_DIR / filename
    try:
        text = json.dumps(obj, indent=2, default=str, allow_nan=False)
    except ValueError as exc:
        raise DataFetchError(
            f"Refusing to write {filename}: payload contains NaN/Infinity, which "
            f"is not valid JSON. Sanitize with df.replace({{np.nan: None}}) "
            f"before calling _write_json. Original error: {exc}"
        ) from exc
    path.write_text(text)
    return path


def _df_records_json_safe(df: pd.DataFrame) -> list[dict[str, Any]]:
    """DataFrame -> list-of-dicts with NaN/NaT replaced by None, so the result
    is always valid JSON (see _write_json)."""
    return df.replace({np.nan: None}).to_dict(orient="records")


def _sanitize_nans(obj: Any) -> Any:
    """Recursively replaces float NaN with None in nested dicts/lists/tuples,
    for structures (like the composite-index snapshot dict) that are built by
    hand rather than via a DataFrame and so don't pass through
    _df_records_json_safe."""
    if isinstance(obj, float) and np.isnan(obj):
        return None
    if isinstance(obj, dict):
        return {k: _sanitize_nans(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_nans(v) for v in obj]
    return obj


def run_pipeline() -> None:
    run_started_at = datetime.now(timezone.utc).isoformat()

    print("Fetching World Bank indicators...")
    wb_df = fetch_all_worldbank_indicators()

    print("Fetching IMF DataMapper cross-check series...")
    # This series is explicitly a cross-check, reported alongside the World
    # Bank inflation figures but never merged into panel.json or
    # latest_snapshot.json (see ingest_imf_datamapper.py docstring) - so a
    # failure here must not take down the whole run. IMF's site is known to
    # block requests from cloud/CI IP ranges (e.g. GitHub Actions runners)
    # at the network edge, which is an access-control issue on their end,
    # not a shape/parsing problem with our code. We still refuse to hide
    # the gap: it's recorded as an explicit null-with-reason (never a
    # fabricated value) in imf_datamapper_crosscheck.json and surfaced in
    # meta.json's sanity_check_warnings, which the dashboard renders.
    imf_df = None
    imf_fetch_error: str | None = None
    try:
        imf_df = fetch_all_imf_indicators()
    except DataFetchError as exc:
        imf_fetch_error = str(exc)
        print(f"  [warning] IMF DataMapper cross-check unavailable this run: {imf_fetch_error}")

    print("Fetching live Zimbabwe FX snapshot from ZimRate...")
    # Unlike the IMF cross-check, this feeds a real CSRI component (fx_spread,
    # 20% weight) and a dedicated dashboard panel - but the same cloud-CI
    # network-edge blocking risk applies (ZimRate has been observed reachable
    # from a normal network path but not from GitHub Actions runner IPs). We
    # handle it with the exact pattern already established for Kenya/Malawi's
    # missing parallel-market data: null the value and record an explicit,
    # human-readable exclusion reason - never fabricate or reuse a stale
    # cached rate.
    zwe_fx: dict[str, Any] | None = None
    zwe_fx_error: str | None = None
    try:
        zwe_fx = fetch_zimbabwe_fx_snapshot()
    except DataFetchError as exc:
        zwe_fx_error = str(exc)
        print(f"  [warning] Zimbabwe FX snapshot unavailable this run: {zwe_fx_error}")

    print("Loading and validating manual data...")
    manual = load_all_manual_tables()

    print("Building annual panel...")
    panel = build_annual_panel(wb_df)

    print("Building latest snapshot and composite index...")
    snapshot = build_latest_snapshot(panel, zwe_fx, manual)

    print("Running sanity checks...")
    warnings = run_sanity_checks(snapshot)
    if imf_fetch_error:
        warnings.append(
            "IMF DataMapper cross-check unavailable this run "
            f"(supplementary series only, not used in the CSRI or panel data): {imf_fetch_error}"
        )
    if zwe_fx_error:
        warnings.append(
            "Zimbabwe FX snapshot (ZimRate) unavailable this run - fx_spread excluded "
            f"from the CSRI for Zimbabwe and renormalized, not fabricated: {zwe_fx_error}"
        )
    for w in warnings:
        print(f"  [sanity] {w}")

    _write_json(_df_records_json_safe(panel), "panel.json")
    _write_json(_sanitize_nans(snapshot), "latest_snapshot.json")
    if imf_df is not None:
        _write_json(_df_records_json_safe(imf_df), "imf_datamapper_crosscheck.json")
    else:
        _write_json(
            {
                "available": False,
                "reason": imf_fetch_error,
                "note": (
                    "This cross-check series is supplementary - it is reported "
                    "alongside, but never merged into, the World Bank-sourced "
                    "inflation figures used in the CSRI and panel charts. Its "
                    "absence does not affect any number displayed elsewhere on "
                    "this dashboard."
                ),
            },
            "imf_datamapper_crosscheck.json",
        )
    _write_json(_df_records_json_safe(manual["devaluation_events"]), "devaluation_events.json")
    _write_json(_df_records_json_safe(manual["policy_rate_decisions"]), "policy_rate_decisions.json")
    _write_json(_df_records_json_safe(manual["imf_program_status"]), "imf_program_status.json")

    meta = {
        "run_started_at_utc": run_started_at,
        "run_finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "countries": COUNTRIES,
        "year_range": [START_YEAR, END_YEAR],
        "csri_weights": CSRI_WEIGHTS,
        "sanity_check_warnings": warnings,
        "sources": {
            "world_bank": "https://api.worldbank.org/v2 - World Development Indicators",
            "imf_datamapper": "https://www.imf.org/external/datamapper/api/v1",
            "zimrate": "https://zimrate.com/api-docs - third-party aggregator, not RBZ directly",
            "manual_csvs": "raw/manual/*.csv - see raw/manual/README.md",
        },
    }
    _write_json(meta, "meta.json")

    print(f"\nDone. Processed outputs written to {PROCESSED_DIR}")


if __name__ == "__main__":
    run_pipeline()
