"""
Unit tests for the calculation logic in scripts/process.py.

These use small, deterministic, hand-built fixtures - NOT live API calls -
so they run offline and give a reproducible pass/fail signal. They test the
math (normalization, index calculation, exclusion handling), not whether any
particular external API is reachable right now.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from config import CSRI_WEIGHTS  # noqa: E402
from process import (  # noqa: E402
    build_annual_panel,
    min_max_normalize_higher_is_better,
    score_imf_program,
    build_latest_snapshot,
    run_sanity_checks,
    _write_json,
    _df_records_json_safe,
    _sanitize_nans,
)


def test_weights_sum_to_one():
    assert abs(sum(CSRI_WEIGHTS.values()) - 1.0) < 1e-9


def test_min_max_normalize_basic():
    values = {"ZWE": 10.0, "KEN": 30.0, "MWI": 20.0}
    result = min_max_normalize_higher_is_better(values)
    assert result["ZWE"] == pytest.approx(0.0)
    assert result["MWI"] == pytest.approx(50.0)
    assert result["KEN"] == pytest.approx(100.0)


def test_min_max_normalize_all_equal_returns_midpoint():
    values = {"ZWE": 5.0, "KEN": 5.0, "MWI": 5.0}
    result = min_max_normalize_higher_is_better(values)
    assert all(v == 50.0 for v in result.values())


def test_min_max_normalize_drops_missing_values():
    values = {"ZWE": None, "KEN": 30.0, "MWI": 20.0}
    result = min_max_normalize_higher_is_better(values)
    assert "ZWE" not in result
    assert set(result.keys()) == {"KEN", "MWI"}


def test_min_max_normalize_single_present_value_returns_midpoint():
    values = {"ZWE": None, "KEN": None, "MWI": 42.0}
    result = min_max_normalize_higher_is_better(values)
    assert result == {"MWI": 50.0}


@pytest.mark.parametrize("status,program_type,expected_min,expected_max", [
    ("active", "Extended Fund Facility (EFF)", 79, 81),
    ("active", "Staff-Monitored Program (SMP)", 54, 56),
    ("status uncertain - needs re-verification", "EFF", 39, 41),
    ("no active arrangement; talks ongoing", "ECF", 29, 31),
    ("no active arrangement", "ECF", 14, 16),
])
def test_score_imf_program(status, program_type, expected_min, expected_max):
    row = pd.Series({"status": status, "program_type": program_type})
    score = score_imf_program(row)
    assert expected_min <= score <= expected_max


def test_score_imf_program_unrecognized_status_returns_none():
    row = pd.Series({"status": "something entirely unexpected", "program_type": "??"})
    assert score_imf_program(row) is None


def test_build_annual_panel_computes_debt_to_gdp_and_volatility():
    # Synthetic World Bank-shaped long data: one country, rising inflation.
    rows = []
    for year, infl, reserves_mo, debt, gdp in [
        (2019, 10.0, 3.0, 100.0, 1000.0),
        (2020, 50.0, 2.5, 120.0, 900.0),
        (2021, 100.0, 2.0, 150.0, 800.0),
        (2022, 300.0, 1.0, 180.0, 700.0),
        (2023, 600.0, 0.5, 200.0, 600.0),
    ]:
        rows += [
            {"country_iso3": "ZWE", "country_name": "Zimbabwe", "indicator": "inflation_cpi_pct", "year": year, "value": infl},
            {"country_iso3": "ZWE", "country_name": "Zimbabwe", "indicator": "reserves_months_imports", "year": year, "value": reserves_mo},
            {"country_iso3": "ZWE", "country_name": "Zimbabwe", "indicator": "external_debt_usd", "year": year, "value": debt},
            {"country_iso3": "ZWE", "country_name": "Zimbabwe", "indicator": "gdp_usd", "year": year, "value": gdp},
        ]
    df = pd.DataFrame(rows)

    panel = build_annual_panel(df)
    last_row = panel[panel["year"] == 2023].iloc[0]

    assert last_row["external_debt_gdp_pct"] == pytest.approx(200.0 / 600.0 * 100.0)
    # Volatility should be a positive number given the sharply rising series,
    # and should NOT be null once >=2 obs are in the trailing window.
    assert last_row["inflation_volatility_cv"] > 0


def test_build_latest_snapshot_excludes_missing_components_without_zeroing():
    # Minimal 3-country panel, one observation each.
    panel = pd.DataFrame([
        {"country_iso3": "ZWE", "country_name": "Zimbabwe", "year": 2023,
         "inflation_cpi_pct": 500.0, "inflation_volatility_cv": 2.0,
         "reserves_months_imports": 0.5, "external_debt_gdp_pct": 40.0},
        {"country_iso3": "KEN", "country_name": "Kenya", "year": 2023,
         "inflation_cpi_pct": 7.5, "inflation_volatility_cv": 0.3,
         "reserves_months_imports": 3.5, "external_debt_gdp_pct": 68.0},
        {"country_iso3": "MWI", "country_name": "Malawi", "year": 2023,
         "inflation_cpi_pct": 28.0, "inflation_volatility_cv": 0.5,
         "reserves_months_imports": 1.0, "external_debt_gdp_pct": 45.0},
    ])
    zwe_fx = {"fx_spread_pct": 25.0, "official_rate_usd_zwg": 27.0,
              "parallel_rate_usd_zwg": 34.0}
    manual = {
        "policy_rate_decisions": pd.DataFrame([
            {"country_iso3": "ZWE", "policy_rate_pct": 35.0},
            {"country_iso3": "ZWE", "policy_rate_pct": 30.0},
            {"country_iso3": "KEN", "policy_rate_pct": 13.0},  # only 1 obs -> excluded
        ]),
        "imf_program_status": pd.DataFrame([
            {"country_iso3": "ZWE", "as_of_date": "2026-07-27", "status": "active", "program_type": "Staff-Monitored Program (SMP)"},
            {"country_iso3": "KEN", "as_of_date": "2023-07-17", "status": "status uncertain - needs re-verification", "program_type": "EFF"},
            # MWI has no row at all -> excluded
        ]),
    }

    snapshot = build_latest_snapshot(panel, zwe_fx, manual)

    # Kenya and Malawi should have fx_spread excluded (no live parallel-market source).
    assert "fx_spread" not in snapshot["components_used_per_country"]["KEN"]
    assert "fx_spread" not in snapshot["components_used_per_country"]["MWI"]
    assert "fx_spread" in snapshot["components_used_per_country"]["ZWE"]

    # Kenya's policy rate volatility excluded (only one decision on file).
    assert "policy_rate_volatility" not in snapshot["components_used_per_country"]["KEN"]

    # Malawi's IMF flag excluded (no row on file).
    assert "imf_program_flag" not in snapshot["components_used_per_country"]["MWI"]

    # All three composite scores should be present and finite (renormalized
    # over whatever components each country actually has).
    for iso3 in ("ZWE", "KEN", "MWI"):
        assert not pd.isna(snapshot["csri_composite_0_100"][iso3])

    # Given Zimbabwe's inputs are dramatically worse on every included axis,
    # it should score as the LEAST stable (lowest composite) of the three.
    scores = snapshot["csri_composite_0_100"]
    assert scores["ZWE"] == min(scores.values())


def test_build_latest_snapshot_handles_zimbabwe_fx_fetch_failure():
    # Regression test for the real GitHub Actions failure where ZimRate was
    # unreachable from the CI runner's network (see README "Known
    # simplifications"). zwe_fx=None simulates run_pipeline() catching a
    # DataFetchError from fetch_zimbabwe_fx_snapshot() - the pipeline must
    # degrade gracefully (null + reason, renormalized weights), never crash
    # or fabricate a value, exactly like the existing Kenya/Malawi gap.
    panel = pd.DataFrame([
        {"country_iso3": "ZWE", "country_name": "Zimbabwe", "year": 2023,
         "inflation_cpi_pct": 500.0, "inflation_volatility_cv": 2.0,
         "reserves_months_imports": 0.5, "external_debt_gdp_pct": 40.0},
        {"country_iso3": "KEN", "country_name": "Kenya", "year": 2023,
         "inflation_cpi_pct": 7.5, "inflation_volatility_cv": 0.3,
         "reserves_months_imports": 3.5, "external_debt_gdp_pct": 68.0},
        {"country_iso3": "MWI", "country_name": "Malawi", "year": 2023,
         "inflation_cpi_pct": 28.0, "inflation_volatility_cv": 0.5,
         "reserves_months_imports": 1.0, "external_debt_gdp_pct": 45.0},
    ])
    manual = {
        "policy_rate_decisions": pd.DataFrame([
            {"country_iso3": "ZWE", "policy_rate_pct": 35.0},
            {"country_iso3": "ZWE", "policy_rate_pct": 30.0},
        ]),
        "imf_program_status": pd.DataFrame([
            {"country_iso3": "ZWE", "as_of_date": "2026-07-27", "status": "active", "program_type": "Staff-Monitored Program (SMP)"},
        ]),
    }

    snapshot = build_latest_snapshot(panel, None, manual)

    # fx_spread must be excluded for ALL THREE countries now, not just KEN/MWI.
    for iso3 in ("ZWE", "KEN", "MWI"):
        assert "fx_spread" not in snapshot["components_used_per_country"][iso3]
        assert "fx_spread" in snapshot["components_excluded_reasons"][iso3]
    # Zimbabwe's reason must be distinguishable from Kenya/Malawi's ("no
    # programmatic source") - it's a failed fetch, not a permanent gap.
    assert "fetch failed" in snapshot["components_excluded_reasons"]["ZWE"]["fx_spread"].lower()

    # zwe_fx_detail must be a well-defined stub the frontend can branch on,
    # never a bare None that would crash detail.parallel_pairs_used.join(...).
    assert snapshot["zwe_fx_detail"]["available"] is False
    assert isinstance(snapshot["zwe_fx_detail"]["reason"], str)

    # The composite must still compute (renormalized over what's left) -
    # a missing FX spread must not zero out or blow up Zimbabwe's score.
    assert not pd.isna(snapshot["csri_composite_0_100"]["ZWE"])


def test_run_sanity_checks_flags_when_zimbabwe_is_not_least_stable():
    fake_snapshot = {
        "csri_composite_0_100": {"ZWE": 90.0, "KEN": 40.0, "MWI": 50.0},
        "components_excluded_reasons": {"ZWE": {}, "KEN": {}, "MWI": {}},
    }
    warnings = run_sanity_checks(fake_snapshot)
    assert any("REVIEW FLAG" in w for w in warnings)


def test_write_json_output_is_valid_strict_json_even_with_nan_source(tmp_path, monkeypatch):
    """Regression test: a DataFrame column with blank/NaN values (e.g. an
    empty magnitude_pct cell in a manual CSV) must never reach the written
    JSON file as a bare `NaN` token - that's invalid JSON and breaks
    JSON.parse in the browser, even though Python's own json.load tolerates
    it. This caught a real bug during development (see git history)."""
    import process as process_module

    monkeypatch.setattr(process_module, "PROCESSED_DIR", tmp_path)

    df = pd.DataFrame([
        {"country_iso3": "ZWE", "magnitude_pct": np.nan, "note": "no magnitude on file"},
        {"country_iso3": "KEN", "magnitude_pct": -20.0, "note": "has a value"},
    ])
    out_path = _write_json(_df_records_json_safe(df), "regression_sample.json")
    raw_text = out_path.read_text()

    assert "NaN" not in raw_text, f"bare NaN leaked into JSON output: {raw_text}"
    # Round-trip through Python's *strict* JSON decoder (reject_constant on
    # NaN) as a stand-in for a browser's JSON.parse, which has no NaN extension.
    reparsed = json.loads(raw_text, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
    assert reparsed[0]["magnitude_pct"] is None
    assert reparsed[1]["magnitude_pct"] == -20.0


def test_sanitize_nans_handles_nested_structures():
    nested = {"a": float("nan"), "b": [1.0, float("nan"), {"c": float("nan")}], "d": "ok"}
    result = _sanitize_nans(nested)
    assert result == {"a": None, "b": [1.0, None, {"c": None}], "d": "ok"}


def test_run_sanity_checks_silent_when_zimbabwe_is_least_stable():
    fake_snapshot = {
        "csri_composite_0_100": {"ZWE": 10.0, "KEN": 70.0, "MWI": 50.0},
        "components_excluded_reasons": {"ZWE": {}, "KEN": {}, "MWI": {}},
    }
    warnings = run_sanity_checks(fake_snapshot)
    assert not any("REVIEW FLAG" in w for w in warnings)
