# Sovereign Risk Lab — Currency & Inflation Risk Model

A comparative currency stability and sovereign risk model for **Zimbabwe, Kenya, and Malawi** . Three countries I've lived in, built on live IMF and World Bank data, plus cited manual research where no live API exists. First model in a planned two-part portfolio project (a companion African football transfer-economy model is phase two).

Built to demonstrate the intersection of political-risk analysis and financial data science for grad/internship applications in finance and data analysis.

## What this is, in one sentence

A Python pipeline pulls live inflation, FX reserve, and external-debt data from the World Bank and IMF, pulls a live Zimbabwe official-vs-parallel FX rate from a third-party aggregator, combines it with a small, explicitly-cited set of manually-researched figures (central bank policy decisions, devaluation events, IMF program status, none of which is exposed by any public API), computes a disclosed-weight composite risk index, and renders all of it in a dependency-light, hand-built dashboard.

## Live dashboard

GitHub Pages' classic "deploy from a branch" option only serves the repo root or a `/docs` folder — it can't point at `/frontend` directly, so this repo deploys via the Actions-based method instead (`.github/workflows/pages.yml`), which publishes the whole repo as-is.

**One-time setup:** in the repo on GitHub, go to Settings → Pages → Source → **GitHub Actions** (not "Deploy from a branch"). After that, every push to `main` redeploys automatically. The dashboard will be at:

```
https://tendaiishechikoto-sudo.github.io/sovereign-risk-lab/frontend/
```

(Note the `/frontend/` at the end — that's where `index.html` actually lives.)

The dashboard reads `data/processed/*.json`. That directory ships **empty** (see below) — it is populated either by running the pipeline yourself or by the scheduled GitHub Action, and `pages.yml` also redeploys automatically right after `refresh-data.yml` commits new data.

## Why `data/processed/` is empty in this repo

This project's non-negotiable is *never present fabricated or placeholder data as if it were real*. `data/processed/*.json` is generated output, not source material, and it depends on live network calls to the World Bank, IMF, and ZimRate at the moment the pipeline runs — so it isn't checked in as a static snapshot pretending to be current.

Two ways to populate it:

1. **Run it yourself:** `python scripts/process.py` (needs outbound network access to `api.worldbank.org`, `imf.org`, and `zimrate.com`).
2. **Let CI do it:** `.github/workflows/refresh-data.yml` runs the pipeline weekly and commits the refreshed JSON automatically, so the published dashboard stays current without manual upkeep.

Until either has run at least once, opening `frontend/index.html` shows an explicit "no processed data found" banner instead of blank charts or invented numbers.

## Architecture

```
raw/manual/*.csv  ──┐
                     ├──►  scripts/process.py  ──►  data/processed/*.json  ──►  frontend/ (reads JSON only)
World Bank API    ──┤        (pandas/numpy)
IMF DataMapper API ─┤
ZimRate API        ─┘
```

- `scripts/config.py` — single source of truth for country codes, indicator codes, index weights, file paths.
- `scripts/ingest_worldbank.py`, `ingest_imf_datamapper.py`, `ingest_zimrate.py` — live API pulls. Each raises a clear `DataFetchError` on any network failure or unexpected response shape; nothing here silently falls back to 0 or a cached guess.
- `scripts/ingest_manual.py` — loads and *validates* `raw/manual/*.csv`: every row must carry a `source_url` and `date_retrieved`, or the loader refuses to proceed.
- `scripts/process.py` — cleans/aligns the data, computes derived metrics, computes the composite index, runs sanity checks, writes `data/processed/*.json`.
- `frontend/index.html` + `style.css` + `script.js` — presentation only. No business logic; every number on screen comes from a JSON file. No charting library — the charts are hand-built inline SVG (see `references` note below on why).
- `tests/` — pytest unit tests for the calculation logic (offline, deterministic fixtures) and for the ingestion scripts' JSON parsers (offline, using a real captured ZimRate payload plus schema-accurate World Bank/IMF fixtures).

## Methodology

Full methodology (metrics tracked, why each one, composite index formula, data-source table) was written and approved *before* any code was written — see [`methodology.md`](methodology.md) in this repo, or the same document in the project's Claude workspace. Summary:

### Metrics tracked per country

Headline inflation, inflation volatility (coefficient of variation over a trailing window — computed on the annual series, not monthly IMF IFS/SDMX data; see "Known simplifications" below), official exchange rate, parallel-market exchange rate (Zimbabwe only), currency risk premium, currency devaluation/redenomination events, central bank policy rate decisions, FX reserves (USD and months-of-import-cover), external debt / GDP, IMF program status.

### Composite Currency Stability & Sovereign Risk Index (CSRI)

A weighted blend of min-max-normalized sub-indicators (0–100, higher = more stable) — the same building-block approach used by established country-risk indices such as Institutional Investor Country Credit ratings:

| Component | Weight |
|---|---|
| Inflation level & volatility | 25% |
| FX spread (official vs. parallel) | 20% |
| Reserve adequacy (months import cover) | 20% |
| External debt / GDP | 15% |
| Policy-rate volatility | 10% |
| IMF program / qualitative risk flag | 10% |

**These weights are a disclosed judgment call, not a derived statistical result.** When a component can't be computed for a country (e.g. no live parallel-market rate for Kenya or Malawi — see below), it is *excluded and the remaining weights renormalized*, with the exclusion and its reason recorded in the output JSON and surfaced in the dashboard's methodology section — never silently treated as zero.

### Data provenance — programmatic vs. manual

| Metric | Source | Access |
|---|---|---|
| CPI / inflation | World Bank `FP.CPI.TOTL.ZG`; IMF DataMapper `PCPIPCH` (cross-check) | Programmatic |
| FX reserves, reserve adequacy | World Bank `FI.RES.TOTL.CD`, `FI.RES.TOTL.MO` | Programmatic |
| External debt, GDP | World Bank `DT.DOD.DECT.CD`, `NY.GDP.MKTP.CD` | Programmatic |
| Zimbabwe official & parallel FX rate | [ZimRate](https://zimrate.com/api-docs) | Programmatic — **third-party aggregator, not an RBZ figure**; see provenance note below |
| Kenya / Malawi parallel FX rate | — | Not available — no programmatic source identified during methodology research; field is `null` with a stated reason, not fabricated |
| Currency devaluation/redenomination events | IMF Article IV reports, central bank announcements | Manual, cited — `raw/manual/devaluation_events.csv` |
| Central bank policy rate decisions | RBZ / CBK / RBM press releases | Manual, cited — `raw/manual/policy_rate_decisions.csv`. **This is a curated set of key decisions, not a complete series** — none of the three central banks publishes a queryable rate-history API. |
| IMF program status | IMF press releases | Manual, cited — `raw/manual/imf_program_status.csv`. One row (Kenya) is explicitly flagged as needing re-verification rather than asserted as current — see the file. |

Every manual row carries a `source_url` and `date_retrieved`; `scripts/ingest_manual.py` refuses to load a row missing either.

**ZimRate provenance note:** ZimRate tags one of its upstream sources `official_api`, but that source is a generic exchange-rate API's USD/ZWG quote — not a direct scrape of the Reserve Bank of Zimbabwe's own published rate (RBZ has no public API). The dashboard labels this explicitly rather than presenting it as an RBZ figure. Its `parallel_market` tag aggregates informal/street-rate price checks.

### Known simplifications (disclosed, not hidden)

- **Inflation volatility is computed on the annual series** (coefficient of variation over a trailing 5-year window), not monthly IMF IFS/SDMX data. Monthly IFS access requires the heavier `sdmx1` client and a different, less standardized query pattern; the annual measure is simpler, more robust, and still a defensible statistic — but it's coarser. Swapping in monthly data is a natural extension (see `scripts/ingest_imf_datamapper.py` docstring).
- **Zimbabwe's currency redenomination is not spliced into one continuous series.** RTGS$ (2019) → ZWL → ZiG (April 2024) are treated as a discontinuity, flagged in the event log, not smoothed over.
- **The composite index weights are a stated assumption**, not fitted or derived. A hiring manager should be able to disagree with them — that's why they're in `config.py` as a named constant and printed in the dashboard's methodology section, not buried in a formula.
- **The IMF DataMapper cross-check can be unreachable when the pipeline runs on GitHub Actions (or other cloud CI).** `imf.org` sits behind an Akamai edge that, in practice, sometimes blocks requests from cloud/datacenter IP ranges outright — this is access control on IMF's end, not a bug in the ingestion code (confirmed by fetching the same URL successfully from a different network path). Because this series is explicitly a *cross-check* — reported alongside, but never merged into, the World Bank-sourced inflation figures that actually drive the CSRI and panel charts (see `scripts/ingest_imf_datamapper.py` docstring) — `scripts/process.py` treats a failure here as non-fatal: it records `imf_datamapper_crosscheck.json` as `{"available": false, "reason": "..."}` and surfaces the failure in `meta.json`'s `sanity_check_warnings` (rendered in the dashboard's methodology section), rather than either crashing the whole run or silently omitting the gap.

## Running it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Validate manual data (no network needed)
python scripts/ingest_manual.py

# Run the full pipeline (needs network access to worldbank.org, imf.org, zimrate.com)
python scripts/process.py

# Then open frontend/index.html in a browser (or serve it: python -m http.server, from the repo root)
```

## Testing

```bash
pytest tests/ -v
```

23 tests, all offline: calculation-logic tests (normalization, index math, exclusion handling — including a regression test for a real bug caught during development, where a blank CSV cell serialized as an invalid bare `NaN` in the JSON output and broke `JSON.parse` in the browser even though Python's own `json` module tolerated it) and ingestion-parser tests against fixture JSON, one of which is a real captured ZimRate API response.

## Limitations

- This is a portfolio project, not investment or policy advice.
- The composite index's weighting is a disclosed judgment call (see Methodology).
- Manual data (policy rates, devaluation events, IMF status) is a curated snapshot, current as of the `date_retrieved` on each row — re-verify before relying on it, especially IMF program status, which changes on the IMF Executive Board's own schedule.
- No monthly inflation series (see Known simplifications).
- Kenya and Malawi have no parallel-market FX field populated — not because it doesn't matter, but because no reliable programmatic source was found for either during methodology research.

## License

MIT — see [`LICENSE`](LICENSE).
