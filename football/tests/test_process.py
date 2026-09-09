"""
Unit tests for scripts/process.py. Deterministic, hand-built fixtures - no
live API calls - so they run offline and give a reproducible pass/fail
signal, same convention as phase one's tests/test_process.py.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from config import FEFI_WEIGHTS, FEFI_COUNTRIES, COMPARATOR_COUNTRIES  # noqa: E402
from process import (  # noqa: E402
    min_max_normalize_higher_is_better,
    build_fefi,
    build_nigeria_case_study,
    build_case_study_transfers,
    build_value_capture_context,
    build_comparator_context,
    run_sanity_checks,
    _write_json,
)
from http_utils import DataFetchError  # noqa: E402


def test_fefi_weights_sum_to_one():
    assert abs(sum(FEFI_WEIGHTS.values()) - 1.0) < 1e-9


def test_min_max_normalize_basic():
    values = {"NGA": 900.0, "GHA": 700.0, "SEN": 500.0}
    result = min_max_normalize_higher_is_better(values)
    assert result["SEN"] == pytest.approx(0.0)
    assert result["GHA"] == pytest.approx(50.0)
    assert result["NGA"] == pytest.approx(100.0)


def test_build_fefi_ranks_by_expatriate_volume_and_excludes_comparators():
    totals = pd.DataFrame([
        {"country_iso3": "NGA", "total_expatriates_2020_2025": 926},
        {"country_iso3": "GHA", "total_expatriates_2020_2025": 677},
        {"country_iso3": "SEN", "total_expatriates_2020_2025": 511},
        {"country_iso3": "CIV", "total_expatriates_2020_2025": 484},
        {"country_iso3": "CMR", "total_expatriates_2020_2025": 327},
    ])
    fefi = build_fefi(totals)

    # Nigeria has the highest sourced total, so must rank first.
    assert fefi["ranking_highest_footprint_first"][0] == "NGA"
    assert fefi["ranking_highest_footprint_first"][-1] == "CMR"
    assert fefi["fefi_composite_0_100"]["NGA"] == pytest.approx(100.0)
    assert fefi["fefi_composite_0_100"]["CMR"] == pytest.approx(0.0)

    # Zimbabwe/Kenya/Malawi must not appear in FEFI output at all - not as a
    # null, not as a zero. Their absence means "not tracked", not "score 0".
    for iso3 in COMPARATOR_COUNTRIES:
        assert iso3 not in fefi["fefi_composite_0_100"]
        assert iso3 not in fefi["raw_expatriate_totals_2020_2025"]


def test_build_fefi_raises_if_a_fefi_country_is_missing_from_the_csv():
    # Cameroon missing entirely - this must fail loudly, not silently score
    # the remaining four and pretend Cameroon doesn't exist.
    totals = pd.DataFrame([
        {"country_iso3": "NGA", "total_expatriates_2020_2025": 926},
        {"country_iso3": "GHA", "total_expatriates_2020_2025": 677},
        {"country_iso3": "SEN", "total_expatriates_2020_2025": 511},
        {"country_iso3": "CIV", "total_expatriates_2020_2025": 484},
    ])
    with pytest.raises(DataFetchError, match="CMR"):
        build_fefi(totals)


def test_build_nigeria_case_study_flags_no_big5_destinations():
    trend = pd.DataFrame([
        {"year": 2020, "expatriate_count": 319},
        {"year": 2025, "expatriate_count": 500},
    ])
    destinations = pd.DataFrame([
        {"destination_country": "Czech Republic", "expatriate_count": 65, "is_big5_league_country": False},
        {"destination_country": "Turkey", "expatriate_count": 59, "is_big5_league_country": False},
    ])
    result = build_nigeria_case_study(trend, destinations)
    assert result["top_5_destinations_big5_league_share_pct"] == 0.0
    assert result["yearly_trend_2020_2025"][0]["year"] == 2020
    assert result["yearly_trend_2020_2025"][-1]["expatriate_count"] == 500


def test_build_case_study_transfers_counts_confirmed_fees_and_nulls_the_rest():
    df = pd.DataFrame([
        {"country_iso3": "KEN", "player_name": "Collins Sichenje", "position": "Centre-back",
         "current_club": "Charlton Athletic", "current_league": "England (EFL Championship)",
         "previous_club": "FK Vojvodina", "transfer_fee_eur": 1900000, "fee_confirmed": True,
         "source_url": "https://example.com", "notes": None},
        {"country_iso3": "KEN", "player_name": "Job Ochieng", "position": "Forward",
         "current_club": "Real Sociedad", "current_league": "Spain (La Liga)",
         "previous_club": None, "transfer_fee_eur": None, "fee_confirmed": False,
         "source_url": "https://example.com", "notes": None},
        {"country_iso3": "ZWE", "player_name": "Marvelous Nakamba", "position": "Midfielder",
         "current_club": "Luton Town", "current_league": "England (EFL Championship)",
         "previous_club": "Aston Villa", "transfer_fee_eur": None, "fee_confirmed": False,
         "source_url": "https://example.com", "notes": None},
    ])
    result = build_case_study_transfers(df)
    assert result["player_counts"]["KEN"] == 2
    assert result["player_counts"]["ZWE"] == 1
    assert result["player_counts"]["MWI"] == 0
    assert result["confirmed_fee_count"] == 1

    sichenje = next(p for p in result["players_by_country"]["KEN"] if p["player_name"] == "Collins Sichenje")
    assert sichenje["transfer_fee_eur"] == 1900000.0
    ochieng = next(p for p in result["players_by_country"]["KEN"] if p["player_name"] == "Job Ochieng")
    assert ochieng["transfer_fee_eur"] is None  # null, never estimated


def test_build_value_capture_context_is_never_folded_into_a_score():
    df = pd.DataFrame([
        {"metric": "Agent fees - CAF share", "scope": "CAF", "value": 0.0703,
         "unit": "USD million", "year": 2025, "source": "FIFA", "source_url": "https://example.com",
         "date_retrieved": "2026-09-09", "caveat": "wrong side of the transaction"},
    ])
    result = build_value_capture_context(df)
    assert len(result["entries"]) == 1
    assert "not fed" in result["not_an_index"] or "None of these figures feed" in result["not_an_index"]


def test_build_comparator_context_handles_missing_year_gracefully():
    wb_df = pd.DataFrame([
        {"country_iso3": "ZWE", "country_name": "Zimbabwe", "indicator": "gdp_usd", "year": 2024, "value": 41521975830.24},
        {"country_iso3": "ZWE", "country_name": "Zimbabwe", "indicator": "remittances_usd", "year": 2024, "value": None},
        {"country_iso3": "KEN", "country_name": "Kenya", "indicator": "gdp_usd", "year": 2024, "value": 120397537849.83},
        {"country_iso3": "KEN", "country_name": "Kenya", "indicator": "remittances_usd", "year": 2024, "value": 5000000000.0},
        # Malawi has no rows at all this run.
    ])
    result = build_comparator_context(wb_df)
    assert result["latest_available"]["ZWE"]["gdp_usd"] == pytest.approx(41521975830.24)
    assert result["latest_available"]["ZWE"]["remittances_usd"] is None
    assert result["latest_available"]["KEN"]["remittances_usd"] == pytest.approx(5000000000.0)
    assert result["latest_available"]["MWI"]["gdp_usd"] is None


def test_run_sanity_checks_flags_nigeria_not_first():
    fake_fefi = {
        "ranking_highest_footprint_first": ["GHA", "NGA", "SEN", "CIV", "CMR"],
        "raw_expatriate_totals_2020_2025": {"NGA": 926.0, "GHA": 927.0, "SEN": 511.0, "CIV": 484.0, "CMR": 327.0},
    }
    warnings = run_sanity_checks(fake_fefi)
    assert any("REVIEW FLAG" in w for w in warnings)
    assert any("single-component index" in w for w in warnings)
    for iso3 in COMPARATOR_COUNTRIES:
        assert any(iso3 in w and "excluded from FEFI" in w for w in warnings)


def test_write_json_rejects_bare_nan(tmp_path, monkeypatch):
    import process as process_module
    monkeypatch.setattr(process_module, "PROCESSED_DIR", tmp_path)

    with pytest.raises(DataFetchError):
        _write_json({"value": float("nan")}, "bad.json")

    # A clean payload must round-trip fine.
    out_path = _write_json({"value": 1.0, "missing": None}, "good.json")
    assert out_path.read_text().count("NaN") == 0
