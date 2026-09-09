"""
Shared HTTP helper. Every ingestion script goes through this so that a
network failure or an unexpected response shape raises loudly instead of
silently defaulting to zero/empty data (project non-negotiable: no silent
failure in data loading).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import requests

from config import REQUEST_TIMEOUT_SECONDS


class DataFetchError(RuntimeError):
    """Raised when a live data source cannot be reached or returns an
    unexpected shape. Callers must NOT catch this and substitute a default
    value - the whole point is that a gap in live data is visible, not
    silently filled with 0 or None."""


def get_json(url: str, *, params: dict[str, Any] | None = None,
             retries: int = 2, backoff_seconds: float = 1.5) -> Any:
    """GET a URL and parse JSON, raising DataFetchError with a clear message
    on any failure. Retries transient network errors a small number of times
    before giving up - it does not retry 4xx client errors."""
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS,
                                 headers={"User-Agent": "sovereign-risk-lab/1.0 (portfolio project)"})
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(backoff_seconds * (attempt + 1))
                continue
            raise DataFetchError(f"Network error fetching {url}: {exc}") from exc

        if resp.status_code >= 500 and attempt < retries:
            time.sleep(backoff_seconds * (attempt + 1))
            continue

        if resp.status_code != 200:
            raise DataFetchError(
                f"Unexpected HTTP {resp.status_code} fetching {url}. "
                f"Body (truncated): {resp.text[:500]}"
            )

        try:
            return resp.json()
        except json.JSONDecodeError as exc:
            raise DataFetchError(
                f"Response from {url} was not valid JSON: {exc}. "
                f"Body (truncated): {resp.text[:500]}"
            ) from exc

    # Should be unreachable, but keeps type-checkers happy and fails loudly.
    raise DataFetchError(f"Failed to fetch {url}: {last_exc}")


def cache_raw_response(cache_dir: Path, filename: str, payload: Any) -> None:
    """Persist the raw API response to raw/api_cache/ for audit trail and
    reproducibility - so a reviewer (or a hiring manager) can see exactly
    what the API returned on the date the pipeline last ran."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_path = cache_dir / filename
    out_path.write_text(json.dumps(payload, indent=2, default=str))
