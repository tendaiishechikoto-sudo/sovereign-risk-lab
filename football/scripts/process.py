"""
Processing pipeline for the African football transfer economy model (phase
two). Takes the raw manual CSVs and a World Bank pull and produces:

  data/processed/fefi.json              - Football Export Footprint Index
  data/processed/nigeria_case_study.json - Nigeria's trend + destinations
  data/processed/case_study_transfers.json - named ZWE/KEN/MWI transfers
  data/processed/value_capture_context.json - agent-fee/solidarity context
  data/processed/comparator_context.json - GDP/remittances for ZWE/KEN/MWI
  data/processed/meta.json              - run metadata, sources, sanity checks

Every number in these files traces back to either a live World Bank API pull
(cached in raw/api_cache/) or a manually-sourced, cited CSV row. Nothing here
is invented. Where a metric genuinely cannot be computed for a country (e.g.
FEFI for Zimbabwe/Kenya/Malawi, which CIES does not track at all), it is
recorded as absent with an explicit reason - never silently defaulted to 0 or
folded into an average that would mask its absence. See /methodology.md
"Source verification findings" for the research trail behind every decision
in this file.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pandas as pd

from config import (
    FEFI_COUNTRIES, COMPARATOR_COUNTRIES, FEFI_WEIGHTS, PROCESSED_DIR,
)
from ingest_worldbank import fetch_all_worldbank_indicators
from ingest_manual import load_all_manual_tables
from http_utils import DataFetchError


# --------------------------------------------------------------------------
# Normalization (same convention as phase one's process.py)
# --------------------------------------------------------------------------

def min_max_normalize_higher_is_better(values: dict[str, float]) -> dict[str, float]:
    """values: {country_iso3: raw_value}, already oriented so a HIGHER raw
    value means a bigger export footprint. Returns {country_iso3: 0-100}. If
    all values are equal (or fewer than 2 present), returns 50 for every
    present country - there is no basis to differentiate them."""
    present = {k: v for k, v in values.items() if v is not None and not pd.isna(v)}
    if len(present) < 2:
        return {k: 50.0 for k in present}
    lo, hi = min(present.values()), max(present.values())
    if hi == lo:
        return {k: 50.0 for k in present}
    return {k: (v - lo) / (hi - lo) * 100.0 for k, v in present.items()}


# --------------------------------------------------------------------------
# Football Export Footprint Index (FEFI)
# --------------------------------------------------------------------------

def build_fefi(expatriate_totals: pd.DataFrame) -> dict[str, Any]:
    """Single-component index (see FEFI_WEIGHTS docstring in config.py for
    why): expatriate_volume, the 2020-2025 aggregate count from CIES Monthly
    Report 100, normalized 0-100 across FEFI_COUNTRIES only. Zimbabwe, Kenya,
    and Malawi are NOT in this output at all - they are not a "0" or a
    missing-with-reason entry inside FEFI, because FEFI's scope is defined as
    "countries CIES tracks"; their absence is handled separately via
    comparator_context.json, so a reader doesn't misread a missing bar chart
    entry as a score of zero.
    """
    raw_values: dict[str, float] = {}
    for _, row in expatriate_totals.iterrows():
        iso3 = row["country_iso3"]
        if iso3 not in FEFI_COUNTRIES:
            continue
        raw_values[iso3] = float(row["total_expatriates_2020_2025"])

    missing = set(FEFI_COUNTRIES) - set(raw_values)
    if missing:
        raise DataFetchError(
            f"cies_expatriate_totals.csv is missing expected FEFI_COUNTRIES: {missing}. "
            f"Every country in FEFI_COUNTRIES must have a sourced row."
        )

    normalized = min_max_normalize_higher_is_better(raw_values)

    composite: dict[str, float] = {}
    components_used: dict[str, list[str]] = {}
    for iso3 in FEFI_COUNTRIES:
        present = {"expatriate_volume": normalized[iso3]}
        weight_sum = sum(FEFI_WEIGHTS[k] for k in present)
        score = sum(FEFI_WEIGHTS[k] * present[k] for k in present) / weight_sum
        composite[iso3] = round(score, 2)
        components_used[iso3] = sorted(present.keys())

    ranking = sorted(composite, key=lambda k: composite[k], reverse=True)

    return {
        "countries": FEFI_COUNTRIES,
        "raw_expatriate_totals_2020_2025": raw_values,
        "normalized_components_0_100": {c: {"expatriate_volume": normalized[c]} for c in FEFI_COUNTRIES},
        "fefi_composite_0_100": composite,
        "components_used_per_country": components_used,
        "weights": FEFI_WEIGHTS,
        "ranking_highest_footprint_first": ranking,
        "scope_note": (
            "Covers only the five African nations with a verified, sourced expatriate "
            "count in CIES Football Observatory Monthly Report 100 (2020-2025). Zimbabwe, "
            "Kenya, and Malawi are not tracked by CIES at all and are deliberately excluded "
            "from this index rather than scored as zero - see comparator_context.json and "
            "case_study_transfers.json for how they are covered instead."
        ),
    }


# --------------------------------------------------------------------------
# Nigeria case study (trend + destinations - the one country with this detail)
# --------------------------------------------------------------------------

def build_nigeria_case_study(trend_df: pd.DataFrame, destinations_df: pd.DataFrame) -> dict[str, Any]:
    trend = trend_df.sort_values("year")[["year", "expatriate_count"]].to_dict(orient="records")
    destinations = destinations_df.sort_values(
        "expatriate_count", ascending=False
    )[["destination_country", "expatriate_count", "is_big5_league_country"]].to_dict(orient="records")

    big5_count = sum(d["expatriate_count"] for d in destinations if d["is_big5_league_country"])
    total_in_top5 = sum(d["expatriate_count"] for d in destinations)

    return {
        "country_iso3": "NGA",
        "yearly_trend_2020_2025": trend,
        "top_5_destinations_2020_2025": destinations,
        "top_5_destinations_big5_league_share_pct": (
            round(big5_count / total_in_top5 * 100.0, 1) if total_in_top5 else None
        ),
        "note": (
            "Nigeria is the only African nation CIES Monthly Report 100 gives this level "
            "of detail for. None of Nigeria's top 5 listed destinations are in a 'Big 5' "
            "European league - shown here as a case-study finding, not generalized to "
            "other countries, since no comparable destination data exists for them."
        ),
    }


# --------------------------------------------------------------------------
# Case-study transfers (Zimbabwe / Kenya / Malawi named players)
# --------------------------------------------------------------------------

def build_case_study_transfers(transfers_df: pd.DataFrame) -> dict[str, Any]:
    by_country: dict[str, list[dict[str, Any]]] = {c: [] for c in COMPARATOR_COUNTRIES}
    for _, row in transfers_df.iterrows():
        iso3 = row["country_iso3"]
        if iso3 not in by_country:
            continue
        by_country[iso3].append({
            "player_name": row["player_name"],
            "position": row.get("position") if pd.notna(row.get("position")) else None,
            "current_club": row.get("current_club") if pd.notna(row.get("current_club")) else None,
            "current_league": row.get("current_league") if pd.notna(row.get("current_league")) else None,
            "previous_club": row.get("previous_club") if pd.notna(row.get("previous_club")) else None,
            "transfer_fee_eur": (
                float(row["transfer_fee_eur"])
                if pd.notna(row.get("transfer_fee_eur")) else None
            ),
            "fee_confirmed": bool(row.get("fee_confirmed", False)),
            "source_url": row["source_url"],
            "notes": row.get("notes") if pd.notna(row.get("notes")) else None,
        })

    confirmed_fee_count = sum(
        1 for players in by_country.values() for p in players if p["fee_confirmed"]
    )

    return {
        "countries": COMPARATOR_COUNTRIES,
        "players_by_country": by_country,
        "player_counts": {c: len(v) for c, v in by_country.items()},
        "confirmed_fee_count": confirmed_fee_count,
        "generalization_warning": (
            "These are named individual players, illustrative only - not a sample large "
            "enough to generalize about Zimbabwe, Kenya, or Malawi's football export "
            "economy. None of the three appear in CIES's aggregate expatriate tracking. "
            f"Only {confirmed_fee_count} of {sum(len(v) for v in by_country.values())} "
            "listed transfers has a publicly reported fee; every other fee field is null, "
            "not estimated."
        ),
    }


# --------------------------------------------------------------------------
# Value capture context (agent fees / solidarity mechanism - narrative only)
# --------------------------------------------------------------------------

def build_value_capture_context(context_df: pd.DataFrame) -> dict[str, Any]:
    records = context_df.replace({np.nan: None}).to_dict(orient="records")
    return {
        "entries": records,
        "not_an_index": (
            "None of these figures feed the FEFI composite. The originally-approved "
            "methodology planned to score 'value captured vs. leaked' quantitatively "
            "(agent commissions, solidarity/training compensation), but no current, "
            "country-level data exists to do that honestly - see /methodology.md "
            "'Source verification findings'. These entries are the real numbers found, "
            "kept as cited, dated context instead of being forced into a score."
        ),
    }


# --------------------------------------------------------------------------
# Comparator context (GDP / remittances for Zimbabwe, Kenya, Malawi)
# --------------------------------------------------------------------------

def build_comparator_context(wb_df: pd.DataFrame) -> dict[str, Any]:
    wide = wb_df.pivot_table(
        index=["country_iso3", "country_name", "year"],
        columns="indicator", values="value", aggfunc="first",
    ).reset_index()
    wide.columns.name = None
    wide = wide.sort_values(["country_iso3", "year"])

    latest: dict[str, dict[str, Any]] = {}
    for iso3 in COMPARATOR_COUNTRIES:
        country_rows = wide[wide["country_iso3"] == iso3].dropna(
            subset=["gdp_usd", "remittances_usd"], how="all"
        )
        if country_rows.empty:
            latest[iso3] = {"gdp_usd": None, "remittances_usd": None, "year": None}
            continue
        last_row = country_rows.iloc[-1]
        latest[iso3] = {
            "gdp_usd": (
                float(last_row["gdp_usd"]) if pd.notna(last_row.get("gdp_usd")) else None
            ),
            "remittances_usd": (
                float(last_row["remittances_usd"]) if pd.notna(last_row.get("remittances_usd")) else None
            ),
            "year": int(last_row["year"]),
        }

    return {
        "countries": COMPARATOR_COUNTRIES,
        "latest_available": latest,
        "note": (
            "GDP and remittances are World Bank figures, shown for economic scale only. "
            "No aggregate, country-of-origin football transfer income series exists in any "
            "source found for these three countries (or, for that matter, for most African "
            "exporting nations) - so this is NOT a computed 'transfer income as % of GDP' "
            "ratio. Comparing named case-study transfer fees (case_study_transfers.json) "
            "against these figures is left to the reader, deliberately, rather than turned "
            "into a single invented ratio."
        ),
    }


# --------------------------------------------------------------------------
# Sanity checks (soft - logged into meta.json, never silently swallowed)
# --------------------------------------------------------------------------

def run_sanity_checks(fefi: dict[str, Any]) -> list[str]:
    warnings: list[str] = []

    weight_total = sum(FEFI_WEIGHTS.values())
    if abs(weight_total - 1.0) > 1e-6:
        warnings.append(f"FEFI_WEIGHTS sum to {weight_total}, expected 1.0.")

    if len(FEFI_WEIGHTS) == 1:
        warnings.append(
            "FEFI is currently a single-component index (expatriate_volume only). "
            "The originally-approved 'destination concentration' component was dropped "
            "after source verification found it computable for only one country "
            "(Nigeria) - see /methodology.md. Treat FEFI as an export-volume measure, "
            "not a rounded 'export footprint' score, until a broader source is found."
        )

    ranking = fefi["ranking_highest_footprint_first"]
    raw = fefi["raw_expatriate_totals_2020_2025"]
    warnings.append(
        "FEFI ranking this run (highest footprint first): "
        + ", ".join(f"{c} ({raw[c]:.0f})" for c in ranking)
    )
    if ranking[0] != "NGA":
        warnings.append(
            "REVIEW FLAG: Nigeria is not ranked first by expatriate volume. Given "
            "Nigeria has the highest sourced total (926) of the five FEFI_COUNTRIES, "
            "check cies_expatriate_totals.csv for a data-entry error before publishing."
        )

    for iso3 in COMPARATOR_COUNTRIES:
        warnings.append(
            f"{iso3}: excluded from FEFI entirely - not tracked by CIES Monthly Report "
            f"100. Covered instead via case_study_transfers.json and comparator_context.json."
        )

    return warnings


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def _write_json(obj: Any, filename: str) -> Path:
    """Writes strict, browser-parseable JSON. allow_nan=False is deliberate -
    see phase one's process.py for why."""
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    path = PROCESSED_DIR / filename
    try:
        text = json.dumps(obj, indent=2, default=str, allow_nan=False)
    except ValueError as exc:
        raise DataFetchError(
            f"Refusing to write {filename}: payload contains NaN/Infinity, which "
            f"is not valid JSON. Original error: {exc}"
        ) from exc
    path.write_text(text)
    return path


def run_pipeline() -> None:
    run_started_at = datetime.now(timezone.utc).isoformat()

    print("Loading and validating manual data...")
    manual = load_all_manual_tables()

    print("Fetching World Bank comparator context (GDP, remittances for ZWE/KEN/MWI)...")
    wb_fetch_error: str | None = None
    try:
        wb_df = fetch_all_worldbank_indicators()
        comparator_context = build_comparator_context(wb_df)
    except DataFetchError as exc:
        wb_fetch_error = str(exc)
        print(f"  [warning] World Bank comparator context unavailable this run: {wb_fetch_error}")
        comparator_context = {
            "countries": COMPARATOR_COUNTRIES,
            "latest_available": {c: {"gdp_usd": None, "remittances_usd": None, "year": None} for c in COMPARATOR_COUNTRIES},
            "note": f"World Bank fetch failed this run - not fabricated. Reason: {wb_fetch_error}",
        }

    print("Building Football Export Footprint Index (FEFI)...")
    fefi = build_fefi(manual["cies_expatriate_totals"])

    print("Building Nigeria case study (trend + destinations)...")
    nigeria_case_study = build_nigeria_case_study(
        manual["cies_nigeria_trend"], manual["cies_nigeria_destinations"]
    )

    print("Building case-study transfers (Zimbabwe/Kenya/Malawi)...")
    case_study_transfers = build_case_study_transfers(manual["case_study_transfers"])

    print("Building value-capture context (agent fees / solidarity mechanism)...")
    value_capture_context = build_value_capture_context(manual["value_capture_context"])

    print("Running sanity checks...")
    warnings = run_sanity_checks(fefi)
    if wb_fetch_error:
        warnings.append(f"World Bank comparator context unavailable this run: {wb_fetch_error}")
    for w in warnings:
        print(f"  [sanity] {w}")

    _write_json(fefi, "fefi.json")
    _write_json(nigeria_case_study, "nigeria_case_study.json")
    _write_json(case_study_transfers, "case_study_transfers.json")
    _write_json(value_capture_context, "value_capture_context.json")
    _write_json(comparator_context, "comparator_context.json")

    meta = {
        "run_started_at_utc": run_started_at,
        "run_finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "fefi_countries": FEFI_COUNTRIES,
        "comparator_countries": COMPARATOR_COUNTRIES,
        "fefi_weights": FEFI_WEIGHTS,
        "sanity_check_warnings": warnings,
        "sources": {
            "cies_football_observatory": "https://football-observatory.hflip.co/MonthlyReport100 - Monthly Report 100, Football Expatriates 2020-2025",
            "world_bank": "https://api.worldbank.org/v2 - World Development Indicators (GDP, remittances)",
            "fifa_football_agents_report_2025": "https://digitalhub.fifa.com/m/1229fa2915af0144/original/Football-Agents-Report-2025.pdf",
            "asser_sports_law_blog": "https://www.asser.nl/SportsLaw/Blog/post/revisiting-fifa-s-training-compensation-and-solidarity-mechanism-part-2-the-african-reality-by-rhys-lenarduzzi",
            "case_study_press_coverage": "see raw/manual/case_study_transfers.csv for per-player sources",
        },
    }
    _write_json(meta, "meta.json")

    print(f"\nDone. Processed outputs written to {PROCESSED_DIR}")


if __name__ == "__main__":
    run_pipeline()
