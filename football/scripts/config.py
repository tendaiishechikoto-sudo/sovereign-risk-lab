"""
Shared configuration for the football transfer economy model (phase two).

Single source of truth for country lists, file paths, and the Football
Export Footprint Index (FEFI) weights, so ingestion/processing/tests never
hardcode these independently. See /methodology.md (project docs) for why
each value is what it is, including the "Source verification findings"
section explaining why several originally-planned components aren't here.
"""

from pathlib import Path

# --- Paths -------------------------------------------------------------

ROOT_DIR = Path(__file__).resolve().parent.parent  # .../sovereign-risk-lab/football
RAW_DIR = ROOT_DIR / "raw"
RAW_MANUAL_DIR = RAW_DIR / "manual"
RAW_CACHE_DIR = RAW_DIR / "api_cache"
PROCESSED_DIR = ROOT_DIR / "data" / "processed"

# --- Countries -----------------------------------------------------------

# Countries with a verified, sourced expatriate count in CIES Football
# Observatory Monthly Report 100 (2020-2025). This is the ONLY basis for the
# FEFI index below - it is not "the top African exporters" in general, it is
# "the African nations this specific report gives a real figure for."
FEFI_COUNTRIES = {
    "NGA": "Nigeria",
    "GHA": "Ghana",
    "SEN": "Senegal",
    "CIV": "Cote d'Ivoire",
    "CMR": "Cameroon",
}

# Zimbabwe, Kenya, Malawi - the lived-experience comparator countries from
# phase one. They do NOT appear in CIES's expatriate tracking at all, so they
# are deliberately excluded from FEFI (see test_process.py) and covered only
# through named case-study transfers and World Bank macro context.
COMPARATOR_COUNTRIES = {
    "ZWE": "Zimbabwe",
    "KEN": "Kenya",
    "MWI": "Malawi",
}

# --- World Bank indicators (comparator countries only - narrative context,
#     not an index input; see build_comparator_context in process.py) -------

WORLD_BANK_INDICATORS = {
    "gdp_usd": "NY.GDP.MKTP.CD",                 # GDP (current US$)
    "remittances_usd": "BX.TRF.PWKR.CD.DT",      # Personal remittances, received (current US$)
}

WORLD_BANK_API_BASE = "https://api.worldbank.org/v2"
START_YEAR = 2015
END_YEAR = 2025

# --- Manual data files -------------------------------------------------------

MANUAL_FILES = {
    "cies_expatriate_totals": RAW_MANUAL_DIR / "cies_expatriate_totals.csv",
    "cies_nigeria_trend": RAW_MANUAL_DIR / "cies_nigeria_trend.csv",
    "cies_nigeria_destinations": RAW_MANUAL_DIR / "cies_nigeria_destinations.csv",
    "case_study_transfers": RAW_MANUAL_DIR / "case_study_transfers.csv",
    "value_capture_context": RAW_MANUAL_DIR / "value_capture_context.csv",
}

# --- Football Export Footprint Index (FEFI) weights -------------------------

# Approved methodology, revised 2026-09-09 after direct source verification
# (see /methodology.md "Source verification findings"). The originally
# approved index had a second component - destination concentration in
# Europe's "Big 5" leagues - but no source found gives that breakdown for
# more than one country (Nigeria), so it cannot be computed comparably across
# the FEFI_COUNTRIES set. Rather than approximate it from a single country's
# partial data, FEFI is a single-component index until a broader source is
# found. Kept as a dict (not a bare constant) so a second component can be
# added later without changing every call site - see test_weights_sum_to_one.
FEFI_WEIGHTS = {
    "expatriate_volume": 1.0,
}

assert abs(sum(FEFI_WEIGHTS.values()) - 1.0) < 1e-9, "FEFI_WEIGHTS must sum to 1.0"

REQUEST_TIMEOUT_SECONDS = 30
