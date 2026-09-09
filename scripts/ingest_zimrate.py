"""
Pull live Zimbabwe official and parallel-market USD exchange rates from the
ZimRate API (https://zimrate.com) - a free, no-auth, third-party aggregator.

IMPORTANT provenance note (see README "Data provenance" section and
config.py comments): ZimRate's "official_api" tag reflects a generic
exchange-rate API's USD/ZWG quote, not a direct scrape of RBZ's own
published rate (RBZ has no public API). Its "parallel_market" tag aggregates
informal-market price-check sources. Both are labeled explicitly as such
everywhere they surface - in this script's output columns, in the processed
JSON, and in the frontend UI (never presented as if they were an official
RBZ figure).

Kenya and Malawi are NOT covered here - no comparable programmatic parallel-
market source was found for either during methodology research (see
methodology.md). Their FX spread field is populated from manual data only
when a documented event exists, otherwise left as null with a stated reason.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

from config import (
    ZIMRATE_LATEST_ENDPOINT, ZIMRATE_OFFICIAL_SOURCE_TYPE, ZIMRATE_OFFICIAL_PAIR,
    ZIMRATE_PARALLEL_SOURCE_TYPE, ZIMRATE_PARALLEL_PAIRS, RAW_CACHE_DIR,
)
from http_utils import get_json, cache_raw_response, DataFetchError


def fetch_latest_rates() -> list[dict[str, Any]]:
    payload = get_json(ZIMRATE_LATEST_ENDPOINT)
    if not isinstance(payload, list) or not payload:
        raise DataFetchError(
            f"ZimRate /api/rates/latest returned an unexpected/empty payload: "
            f"{type(payload)}"
        )
    cache_raw_response(RAW_CACHE_DIR, "zimrate_latest.json", payload)
    return payload


def extract_official_and_parallel(rates: list[dict[str, Any]]) -> dict[str, Any]:
    """Returns a dict with the official rate, the parallel-market midpoint,
    and the computed spread. Raises DataFetchError if either side is
    missing - we do not silently fall back to a stale or default number."""
    official_matches = [
        r for r in rates
        if r.get("currencyPair") == ZIMRATE_OFFICIAL_PAIR
        and r.get("source", {}).get("type") == ZIMRATE_OFFICIAL_SOURCE_TYPE
    ]
    parallel_matches = [
        r for r in rates
        if r.get("currencyPair") in ZIMRATE_PARALLEL_PAIRS
        and r.get("source", {}).get("type") == ZIMRATE_PARALLEL_SOURCE_TYPE
    ]

    if not official_matches:
        raise DataFetchError(
            f"No ZimRate record matched official pair={ZIMRATE_OFFICIAL_PAIR} "
            f"source_type={ZIMRATE_OFFICIAL_SOURCE_TYPE}. ZimRate's source list "
            f"may have changed - update config.ZIMRATE_OFFICIAL_* to match."
        )
    if not parallel_matches:
        raise DataFetchError(
            f"No ZimRate record matched parallel pairs={ZIMRATE_PARALLEL_PAIRS} "
            f"source_type={ZIMRATE_PARALLEL_SOURCE_TYPE}. ZimRate's source list "
            f"may have changed - update config.ZIMRATE_PARALLEL_* to match."
        )

    official_rate = float(official_matches[0]["buyRate"])
    parallel_rate = sum(float(r["buyRate"]) for r in parallel_matches) / len(parallel_matches)
    spread_pct = (parallel_rate - official_rate) / official_rate * 100.0

    return {
        "official_rate_usd_zwg": official_rate,
        "official_source_name": official_matches[0]["source"]["name"],
        "official_scraped_at": official_matches[0]["scrapedAt"],
        "parallel_rate_usd_zwg": parallel_rate,
        "parallel_pairs_used": [r["currencyPair"] for r in parallel_matches],
        "parallel_source_name": parallel_matches[0]["source"]["name"],
        "fx_spread_pct": spread_pct,
    }


def fetch_zimbabwe_fx_snapshot() -> dict[str, Any]:
    rates = fetch_latest_rates()
    return extract_official_and_parallel(rates)


if __name__ == "__main__":
    snapshot = fetch_zimbabwe_fx_snapshot()
    out_path = Path(__file__).resolve().parent.parent / "data" / "processed" / "_zimrate_snapshot.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([snapshot]).to_csv(out_path, index=False)
    print(f"Wrote Zimbabwe FX snapshot to {out_path}")
    print(snapshot)
