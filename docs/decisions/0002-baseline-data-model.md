# 0002: Baseline data model

Status: accepted, 24 Nov 2026

## Context

The prototype has 35 migrations and 29 tables. All 35 apply cleanly to an empty Postgres 16, but the resulting schema has four problems for this repo:

- **Facts are keyed by symbol text, with `ON DELETE CASCADE`.** Deleting a ticker deletes its price history, and a reused ticker merges two companies' data. This is not hypothetical: the prototype seeds `SPCX` as the SPAC and New Issue ETF, while SpaceX has listed under `SPCX` since 12 Jun 2026.
- **No fact table has `known_at`**, so ground rule 2 (point-in-time correctness) cannot be enforced.
- **Bars store `adjusted_close`**, which is rewritten after every split. A backtest run before and after a split sees different history.
- **There is no filings table.** The prototype's SEC client only fetches the latest annual report for Company Context; M1 needs filings stored by accession number.

The prototype also runs on the TimescaleDB image but creates no hypertables.

## Decision

**Scope.** `001_baseline.sql` contains only what M0 and M1 use: `schema_migrations`, `app_users`, `instruments`, `task_definitions`, `task_runs`, `price_bars` and a new `filings` table. Other carry-over tables arrive as forward migrations when their code is carried over, each rewritten to these rules:

| Later forward migration | Tables |
| --- | --- |
| News (after M2) | `raw_news_items`, `news_events`, `news_source_poll_state` |
| Rules-based sentiment | `article_sentiment`, `sentiment_daily` |
| Outcome evaluation (M2) | `outcomes`, `scored_signals` |
| Company Context | `context_entities`, `company_exposures`, `entity_relationships` |

Left in the prototype: `analysis_jobs`, `market_briefs`, `holdings`, `topics`, `topic_entities`, `ticker_tags`, `events`, `event_mentions`, `event_analyses`, `event_impacts`, `event_strength_history`, `divergence_signals`, `price_quotes`.

**Identity.** `instruments` replaces `tickers`. Facts reference `instrument_id` with `ON DELETE RESTRICT`. A symbol is unique among active instruments only, so a reused ticker becomes a new row and the old one stays inactive with its history. `cik` is indexed but not unique, because share classes share a CIK. CIK, filer type and listing date are filled by the onboarding job, and an equity cannot reach `ready` without them. A ticker-change history table is deferred until a watchlist ticker actually changes.

**Time.** Every fact table has `known_at`, and research reads only rows with `known_at < as_of`.

- `price_bars.known_at` is generated as 16:00 New York time on the trading date. This is conservative on 13:00 half-days.
- `filings.known_at` is set to EDGAR's acceptance time. `first_seen_at`, the time our poller first saw the filing, is stored separately. Polling latency must not leak into research timing, and backfilled and live filings must mean the same thing. `is_backfill` marks rows that the poller never saw live.

**Prices.** Bars are stored unadjusted. Split and dividend adjustment will be derived from a corporate-actions table (M2), so stored history never changes.

**Lineage.** Ingested rows carry `task_run_id`. Raw filing documents go to S3, and the row keeps `raw_s3_key` and `raw_sha256`.

**Database.** Plain `postgres:16`, in the `sentinel_stream` schema. `pgcrypto` is not needed on Postgres 13 and later. `btree_gist` waits for Company Context.

**Migrations.** Each file records itself in `schema_migrations` inside its own transaction, and the runner checks this for every file. The prototype checked only files from 025 onwards.

## Consequences

- Carried-over code that queries by symbol must join through `instruments`. This applies to the price-bars worker, the bars API and the task-runs view.
- The Massive client must request unadjusted bars. The prototype sends `adjusted=true`.
- The `filings` ingestion is new code, not a carry-over. Before trusting `accepted_at`, check the time zone of EDGAR's `acceptanceDateTime` against a filing index page.
- Prototype data is not imported now. The prototype database is archived to S3. If it is imported later, its `SPCX` rows belong to the ETF, not SpaceX, and backfilled news has no real `first_seen_at`.
- Both seeded tasks start as `manual`. Switch them to `automatic` once the provider credentials are in Parameter Store.
