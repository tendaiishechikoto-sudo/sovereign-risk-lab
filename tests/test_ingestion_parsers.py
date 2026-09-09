"""
Offline tests for the ingestion scripts' pure parsing functions, using
fixture JSON. The ZimRate fixture is a trimmed real payload captured while
verifying the API during methodology research (see raw/manual/README.md and
README.md "Data provenance"); the World Bank and IMF DataMapper fixtures are
hand-built to match each API's documented/observed response schema.

These tests catch a broken parser (wrong key path, wrong assumed shape)
without needing network access - live reachability is a separate, environment
-dependent concern (see README "Running this in a network-restricted
environment").
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from ingest_zimrate import extract_official_and_parallel  # noqa: E402
from ingest_worldbank import parse_worldbank_page  # noqa: E402
from ingest_imf_datamapper import parse_datamapper_response  # noqa: E402
from http_utils import DataFetchError  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _load_fixture(name: str):
    return json.loads((FIXTURES_DIR / name).read_text())


def test_zimrate_extract_official_and_parallel():
    rates = _load_fixture("zimrate_latest_sample.json")
    result = extract_official_and_parallel(rates)

    assert result["official_rate_usd_zwg"] == 26.85
    assert result["official_source_name"] == "Exchange Rate API"
    # parallel = mean of InformalHigh (30.0) and InformalLow (33.0) = 31.5
    assert result["parallel_rate_usd_zwg"] == 31.5
    expected_spread = (31.5 - 26.85) / 26.85 * 100.0
    assert abs(result["fx_spread_pct"] - expected_spread) < 1e-9
    # Spread should be positive: parallel market trades ZWG weaker than official.
    assert result["fx_spread_pct"] > 0


def test_zimrate_raises_when_official_source_missing():
    rates = [r for r in _load_fixture("zimrate_latest_sample.json")
             if r["source"]["type"] != "official_api"]
    try:
        extract_official_and_parallel(rates)
        assert False, "expected DataFetchError"
    except DataFetchError as exc:
        assert "official" in str(exc).lower()


def test_worldbank_parse_page_extracts_rows_and_handles_nulls():
    payload = _load_fixture("worldbank_page_sample.json")
    meta, rows = parse_worldbank_page(payload, "FP.CPI.TOTL.ZG")

    assert meta["pages"] == 1
    assert len(rows) == 3
    zwe_row = next(r for r in rows if r["countryiso3code"] == "ZWE")
    assert zwe_row["value"] == 667.36
    mwi_row = next(r for r in rows if r["countryiso3code"] == "MWI")
    assert mwi_row["value"] is None  # preserved as null, not coerced to 0


def test_worldbank_parse_page_rejects_wrong_shape():
    try:
        parse_worldbank_page({"not": "a list"}, "FP.CPI.TOTL.ZG")
        assert False, "expected DataFetchError"
    except DataFetchError:
        pass


def test_imf_datamapper_parse_extracts_series():
    payload = _load_fixture("imf_datamapper_sample.json")
    series = parse_datamapper_response(payload, "PCPIPCH", "ZWE")
    assert series["2023"] == 667.4
    assert len(series) == 5


def test_imf_datamapper_parse_rejects_missing_country():
    payload = _load_fixture("imf_datamapper_sample.json")
    try:
        parse_datamapper_response(payload, "PCPIPCH", "KEN")  # not in fixture
        assert False, "expected DataFetchError"
    except DataFetchError:
        pass
