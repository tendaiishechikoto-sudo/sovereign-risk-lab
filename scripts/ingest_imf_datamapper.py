"""
Pull IMF DataMapper indicators (WEO-based) as a cross-check series against
World Bank's CPI inflation numbers. Reported alongside, not merged into, the
World Bank series - the two differ methodologically (WEO uses IMF staff
estimates/projections in some years; World Bank uses national statistics
office submissions) and collapsing them into one number would hide that.

API docs: https://www.imf.org/external/datamapper/api/help
Response shape: {"values": {"<INDICATOR>": {"<ISO3>": {"<year>": value, ...}}}}
No authentication required.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

from config import (
    COUNTRIES, IMF_DATAMAPPER_INDICATORS, IMF_DATAMAPPER_API_BASE,
    START_YEAR, END_YEAR, RAW_CACHE_DIR,
)
from http_utils import get_json, cache_raw_response, DataFetchError


def parse_datamapper_response(payload: Any, indicator_code: str, country_iso3: str) -> dict[str, float]:
    """Pure parsing step, isolated for offline unit testing. Confirmed shape
    (via manual verification against the live API during methodology
    research): {"values": {"<INDICATOR>": {"<ISO3>": {"<year>": value}}}}."""
    try:
        series = payload["values"][indicator_code][country_iso3]
    except (KeyError, TypeError) as exc:
        raise DataFetchError(
            f"IMF DataMapper response for {indicator_code}/{country_iso3} did not "
            f"contain the expected values[{indicator_code}][{country_iso3}] path. "
            f"Got top-level keys: {list(payload.keys()) if isinstance(payload, dict) else type(payload)}"
        ) from exc
    return series


def fetch_indicator(indicator_code: str, country_iso3: str) -> dict[str, float]:
    url = f"{IMF_DATAMAPPER_API_BASE}/{indicator_code}/{country_iso3}"
    payload = get_json(url)
    series = parse_datamapper_response(payload, indicator_code, country_iso3)
    cache_raw_response(RAW_CACHE_DIR, f"imf_datamapper_{indicator_code}_{country_iso3}.json", payload)
    return series


def fetch_all_imf_indicators() -> pd.DataFrame:
    records: list[dict[str, Any]] = []

    for metric_name, indicator_code in IMF_DATAMAPPER_INDICATORS.items():
        for iso3, name in COUNTRIES.items():
            series = fetch_indicator(indicator_code, iso3)
            if not series:
                raise DataFetchError(
                    f"IMF DataMapper returned an empty series for {indicator_code}/{iso3}."
                )
            for year_str, value in series.items():
                try:
                    year = int(year_str)
                except ValueError:
                    continue  # DataMapper sometimes includes non-year keys; skip defensively
                if year < START_YEAR or year > END_YEAR:
                    continue
                records.append({
                    "country_iso3": iso3,
                    "country_name": name,
                    "indicator": metric_name,
                    "year": year,
                    "value": value,
                })

    df = pd.DataFrame.from_records(records)
    if df.empty:
        raise DataFetchError("IMF DataMapper ingestion produced an empty DataFrame.")
    return df


if __name__ == "__main__":
    out_df = fetch_all_imf_indicators()
    out_path = Path(__file__).resolve().parent.parent / "data" / "processed" / "_imf_datamapper_raw.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(out_path, index=False)
    print(f"Wrote {len(out_df)} rows to {out_path}")
