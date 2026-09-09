# Manual data

These CSVs cannot be pulled from any live API (verified during methodology research — see `/methodology.md` in the project and `README.md` at repo root). Every row carries a `source_url` and `date_retrieved` so claims are auditable.

- `devaluation_events.csv` — dated currency devaluation/redenomination events per country.
- `policy_rate_decisions.csv` — a curated set of key central bank policy rate decisions, not a complete monthly series (RBZ/CBK/RBM publish these as press releases/PDFs, not a queryable API). Extending this to a full series would mean scraping each central bank's press-release archive by hand.
- `imf_program_status.csv` — current IMF program status per country. **Flagged explicitly where status could not be independently re-confirmed** (see Kenya row) rather than guessed.

If you refresh this project later, re-verify every row here before trusting it — especially `imf_program_status.csv`, which changes on IMF Executive Board timelines.
