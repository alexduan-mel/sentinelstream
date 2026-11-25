# 0004: SEC filings ingestion

Status: accepted, 25 Nov 2026

## Context

The prototype never stored filings. Its SEC client fetched one annual report for Company Context and cached non-Archive responses for an hour. M1 needs a poller that records every filing of every watchlist issuer with a trustworthy `known_at`, is safe to rerun, and can tell new filings from history so that alerts fire only on news. The seeded watchlist has symbols but no CIKs, and the onboarding job that resolves them is M1 work.

## Decision

**`known_at` is EDGAR's acceptance time, read as New York time.** The submissions API writes `acceptanceDateTime` as `2023-11-02T18:08:27.000Z`, but the value is Eastern wall-clock time and the `Z` is wrong. The filing date confirms it: EDGAR dates a filing accepted after 17:30 Eastern on the next business day, and filings shown with times such as 18:08 carry the next day's filing date. Reading the value as UTC would set `known_at` four or five hours too early, which is lookahead (ground rule 2). Reading it as New York time can only err late. As a tripwire, the poller counts any acceptance time more than five minutes after the fetch in `meta.future_acceptance` and logs a warning. If SEC ever fixes the suffix, the tripwire fires on the first fresh filing.

**Identity comes from SEC data, resolved by the poller until onboarding exists.** For an active watchlist equity with no CIK, the poller looks the symbol up in SEC's `company_tickers_exchange.json`. It then confirms the match against the issuer's submissions record: the record must list the symbol and have `entityType = operating`, which rejects a fund or ETF holding the same ticker. It stores the CIK, name, exchange and filer type. The filer type comes from the newest form the issuer files: periodic reports (10-K and 10-Q against 20-F and 40-F) take precedence over current reports (8-K against 6-K), which take precedence over registration statements. An issuer that has switched to domestic reporting is therefore classed by what it files now. A symbol SEC cannot match is marked `onboarding_status = failed`, with the reason. It is not looked up again until a run with `force_refresh`, so a symbol that is not yet listed does not trigger a ticker-map download every five minutes. The M1 onboarding job will call the same functions (`ingestion/sec_identity.py`) and own the `ready` state.

**History is marked as backfill.** The first poll of an issuer stores its recent filings (up to about 1,000) with `is_backfill = TRUE`, and so does any run with `run_mode = backfill`. Later polls store new filings with `is_backfill = FALSE`. A filing missed while the poller was down is still news when it arrives, so it is not marked as backfill.

**First sighting wins.** Filings upsert on `accession_no` with `DO NOTHING`, so `first_seen_at` and `task_run_id` keep the run that first saw each filing, and reruns only add new rows.

**No response cache.** Each run fetches every submissions record fresh. The ticker map is downloaded at most once per run, and only when a symbol needs resolving.

## Consequences

- A filing appears in `filings` within one poll interval of its listing in the submissions API. The 5-minute interval lands in M1 with alerting (`task_definitions` still says 10 and `manual`).
- The raw submissions JSON is not archived yet (`raw_s3_key` and `raw_sha256` stay NULL). Archiving to S3 comes with M1 alerting, so a disputed alert can be replayed from what the poller saw.
- Filings older than the submissions API's `recent` block (more than about a year, or beyond 1,000 filings) need the paged history files. Fetching those is the onboarding backfill (M1).
- A symbol whose SEC identity changes (a merger, or a new CIK) keeps its old CIK until someone clears it and reruns. Ticker changes are already out of scope (ADR 0002).
