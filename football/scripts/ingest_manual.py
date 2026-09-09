"""
Load and validate the manually-sourced CSVs in raw/manual/. Every one of
these covers data no live API exposes (confirmed during methodology
research - see /methodology.md "Source verification findings").

Validation is strict: every row must have a non-empty source_url and
date_retrieved, or the loader raises. Same rule as phase one's
ingest_manual.py - an unsourced row is worse than no row at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

from config import MANUAL_FILES


class ManualDataError(RuntimeError):
    pass


REQUIRED_COLUMNS = {
    "cies_expatriate_totals": {"country_iso3", "total_expatriates_2020_2025", "source_url", "date_retrieved"},
    "cies_nigeria_trend": {"country_iso3", "year", "expatriate_count", "source_url", "date_retrieved"},
    "cies_nigeria_destinations": {"country_iso3", "destination_country", "expatriate_count", "source_url", "date_retrieved"},
    "case_study_transfers": {"country_iso3", "player_name", "source_url", "date_retrieved"},
    "value_capture_context": {"metric", "scope", "source_url", "date_retrieved"},
}


def _validate(df: pd.DataFrame, name: str) -> None:
    required = REQUIRED_COLUMNS[name]
    missing_cols = required - set(df.columns)
    if missing_cols:
        raise ManualDataError(f"{name}.csv is missing required columns: {missing_cols}")

    unsourced = df[df["source_url"].isna() | (df["source_url"].str.strip() == "")]
    if not unsourced.empty:
        raise ManualDataError(
            f"{name}.csv has {len(unsourced)} row(s) with no source_url. "
            f"Every manually-entered figure must be citable. Offending rows:\n{unsourced}"
        )

    undated = df[df["date_retrieved"].isna() | (df["date_retrieved"].str.strip() == "")]
    if not undated.empty:
        raise ManualDataError(
            f"{name}.csv has {len(undated)} row(s) with no date_retrieved. "
            f"Offending rows:\n{undated}"
        )


def load_manual_table(name: str) -> pd.DataFrame:
    if name not in MANUAL_FILES:
        raise ManualDataError(f"Unknown manual table '{name}'. Known: {list(MANUAL_FILES)}")
    path = MANUAL_FILES[name]
    if not path.exists():
        raise ManualDataError(f"Manual data file not found: {path}")
    df = pd.read_csv(path)
    _validate(df, name)
    return df


def load_all_manual_tables() -> dict[str, pd.DataFrame]:
    return {name: load_manual_table(name) for name in MANUAL_FILES}


if __name__ == "__main__":
    tables = load_all_manual_tables()
    for name, df in tables.items():
        print(f"{name}: {len(df)} rows, all sourced and dated. OK.")
