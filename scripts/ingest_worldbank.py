"""
Pull World Bank World Development Indicators for Zimbabwe, Kenya, Malawi:
CPI inflation, FX reserves (USD and months-of-import-cover), external debt,
and GDP (used to derive external debt / GDP).

API docs: https://datahelpdesk.worldbank.org/knowledgebase/articles/898599
No authentication required. Response shape is a 2-element JSON array:
[ {page, pages, per_page, total, ...}, [ {indicator, country, countryiso3code,
date, value, unit, obs_status, decimal}, ... ] ]
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

from config import (
    COUNTRIES, WORLD_BANK_INDICATORS, WORLD_BANK_API_BASE,
    START_YEAR, END_YEAR, RAW_CACHE_DIR,
)
from http_utils import get_json, cache_raw_response, DataFetchError


def parse_worldbank_page(payload: Any, indicator_code: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Pure parsing step, isolated so it can be unit-tested against a fixture
    without a network call. Validates the [metadata, data] shape World Bank
    has used for this endpoint for years and raises DataFetchError (not a
    generic exception) on anything unexpected."""
    if not isinstance(payload, list) or len(payload) != 2:
        raise DataFetchError(
            f"World Bank response for {indicator_code} was not the expected "
            f"[metadata, data] shape: got {type(payload)} with "
            f"{len(payload) if isinstance(payload, list) else 'n/a'} elements."
        )

    meta, rows = payload
    if rows is None:
        raise DataFetchError(
            f"World Bank returned no data array for indicator {indicator_code}. "
            f"Metadata: {meta}"
        )
    return meta, rows


def fetch_indicator(country_iso3_list: list[str], indicator_code: str) -> list[dict[str, Any]]:
    """Fetch one indicator for a list of ISO3 country codes across
    START_YEAR-END_YEAR. Handles World Bank's pagination."""
    countries_path = ";".join(country_iso3_list)
    url = f"{WORLD_BANK_API_BASE}/country/{countries_path}/indicator/{indicator_code}"

    all_rows: list[dict[str, Any]] = []
    page = 1
    while True:
        payload = get_json(url, params={
            "format": "json",
            "per_page": 1000,
            "date": f"{START_YEAR}:{END_YEAR}",
            "page": page,
        })

        meta, rows = parse_worldbank_page(payload, indicator_code)
        all_rows.extend(rows)

        total_pages = meta.get("pages", 1)
        if page >= total_pages:
            break
        page += 1

    cache_raw_response(RAW_CACHE_DIR, f"worldbank_{indicator_code}.json", all_rows)
    return all_rows


def fetch_all_worldbank_indicators() -> pd.DataFrame:
    """Returns a tidy long-format DataFrame:
    columns = [country_iso3, country_name, indicator, year, value]
    Missing observations are kept as NaN (World Bank returns value: null for
    gaps) rather than dropped or coerced to zero - the processing stage must
    decide how to handle NaNs explicitly, never silently.
    """
    country_codes = list(COUNTRIES.keys())
    records: list[dict[str, Any]] = []

    for metric_name, indicator_code in WORLD_BANK_INDICATORS.items():
        rows = fetch_indicator(country_codes, indicator_code)
        if not rows:
            raise DataFetchError(
                f"World Bank returned zero rows for indicator {indicator_code} "
                f"({metric_name}) across {country_codes}. Refusing to proceed "
                f"with an empty series."
            )
        for row in rows:
            iso3 = row.get("countryiso3code")
            if iso3 not in COUNTRIES:
                continue  # World Bank sometimes echoes aggregate/region rows; skip them
            records.append({
                "country_iso3": iso3,
                "country_name": COUNTRIES[iso3],
                "indicator": metric_name,
                "year": int(row["date"]),
                "value": row["value"],  # may be None - preserved as NaN downstream
            })

    df = pd.DataFrame.from_records(records)
    if df.empty:
        raise DataFetchError("World Bank ingestion produced an empty DataFrame.")
    return df


if __name__ == "__main__":
    out_df = fetch_all_worldbank_indicators()
    out_path = Path(__file__).resolve().parent.parent / "data" / "processed" / "_worldbank_raw.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(out_path, index=False)
    print(f"Wrote {len(out_df)} rows to {out_path}")
    print(out_df.groupby(["indicator", "country_iso3"])["value"].apply(lambda s: s.notna().sum()))
