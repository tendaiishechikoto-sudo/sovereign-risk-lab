"""
Shared configuration for the sovereign-risk-lab currency & inflation risk model.

Single source of truth for country codes, indicator codes, composite index
weights, and file paths, so ingestion/processing/tests never hardcode these
independently. See /methodology.md (project docs) and README.md for why each
value is what it is.
"""

from pathlib import Path

# --- Paths -------------------------------------------------------------

ROOT_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT_DIR / "raw"
RAW_MANUAL_DIR = RAW_DIR / "manual"
RAW_CACHE_DIR = RAW_DIR / "api_cache"  # raw API responses, saved for audit/reproducibility
PROCESSED_DIR = ROOT_DIR / "data" / "processed"

# --- Countries -----------------------------------------------------------

# ISO 3166-1 alpha-3 codes, used consistently across World Bank, IMF and
# internal data structures.
COUNTRIES = {
    "ZWE": "Zimbabwe",
    "KEN": "Kenya",
    "MWI": "Malawi",
}

# World Bank uses alpha-2 codes in some response fields (country.id); this
# maps ISO3 -> ISO2 for cross-referencing API output where needed.
ISO3_TO_ISO2 = {
    "ZWE": "ZW",
    "KEN": "KE",
    "MWI": "MW",
}

# --- Date range ------------------------------------------------------------

# World Bank / IMF DataMapper annual series pulled over this window.
# Zimbabwe's ZWL series (pre-April 2024) and ZiG series (post-April 2024)
# are NOT spliced into one continuous number — see process.py::flag_currency_regime.
START_YEAR = 2015
END_YEAR = 2025

# --- World Bank indicator codes ---------------------------------------------

WORLD_BANK_INDICATORS = {
    "inflation_cpi_pct": "FP.CPI.TOTL.ZG",          # Inflation, consumer prices (annual %)
    "reserves_usd": "FI.RES.TOTL.CD",                # Total reserves (includes gold, current US$)
    "reserves_months_imports": "FI.RES.TOTL.MO",     # Total reserves in months of imports
    "external_debt_usd": "DT.DOD.DECT.CD",           # External debt stocks, total (current US$)
    "gdp_usd": "NY.GDP.MKTP.CD",                     # GDP (current US$) - used to compute debt/GDP
}

WORLD_BANK_API_BASE = "https://api.worldbank.org/v2"

# --- IMF DataMapper indicator codes -----------------------------------------

# Cross-check series for inflation (WEO-based, differs methodologically from
# World Bank's CPI series - both are reported in the output, not merged into
# one number, so a reader can see where they diverge).
IMF_DATAMAPPER_INDICATORS = {
    "inflation_avg_cpi_pct": "PCPIPCH",   # Inflation, average consumer prices (annual % change)
}

IMF_DATAMAPPER_API_BASE = "https://www.imf.org/external/datamapper/api/v1"

# --- ZimRate (Zimbabwe official + parallel FX) ------------------------------

ZIMRATE_API_BASE = "https://zimrate.com/api"
ZIMRATE_LATEST_ENDPOINT = f"{ZIMRATE_API_BASE}/rates/latest"
ZIMRATE_HISTORY_ENDPOINT = f"{ZIMRATE_API_BASE}/rates/history"

# ZimRate aggregates multiple sources under one currencyPair label. These are
# the specific (source_type, currencyPair) combinations we treat as "official"
# vs "parallel" for the ZWE currency-spread calculation. This mapping is a
# judgment call documented here (and in README) because ZimRate's own
# "official_api" tag reflects a generic exchange-rate API's USD/ZWG quote,
# not a direct RBZ scrape - RBZ itself publishes no public API.
ZIMRATE_OFFICIAL_SOURCE_TYPE = "official_api"
ZIMRATE_OFFICIAL_PAIR = "USD/ZWG"
ZIMRATE_PARALLEL_SOURCE_TYPE = "parallel_market"
ZIMRATE_PARALLEL_PAIRS = ["USD/ZiG_InformalLow", "USD/ZiG_InformalHigh"]

# --- Manual data files -------------------------------------------------------

MANUAL_FILES = {
    "devaluation_events": RAW_MANUAL_DIR / "devaluation_events.csv",
    "policy_rate_decisions": RAW_MANUAL_DIR / "policy_rate_decisions.csv",
    "imf_program_status": RAW_MANUAL_DIR / "imf_program_status.csv",
}

# --- Composite Currency Stability & Sovereign Risk Index (CSRI) weights ----

# Approved methodology (see /methodology.md). Weights are a disclosed
# judgment call, not a derived statistical result - flagged as an assumption
# in the README. Must sum to 1.0 (checked by tests/test_process.py).
CSRI_WEIGHTS = {
    "inflation": 0.25,          # level & volatility of inflation
    "fx_spread": 0.20,          # official vs parallel exchange rate premium
    "reserve_adequacy": 0.20,   # months of import cover
    "external_debt": 0.15,      # external debt / GDP
    "policy_rate_volatility": 0.10,
    "imf_program_flag": 0.10,   # qualitative risk flag from IMF program status
}

assert abs(sum(CSRI_WEIGHTS.values()) - 1.0) < 1e-9, "CSRI_WEIGHTS must sum to 1.0"

# Rolling window (years) used for inflation volatility (coefficient of
# variation of annual YoY inflation). See methodology.md for why this is
# computed on the annual series rather than monthly IMF IFS/SDMX data.
INFLATION_VOLATILITY_WINDOW_YEARS = 5

REQUEST_TIMEOUT_SECONDS = 30
