# 0002: Baseline data model

Status: accepted, 24 Nov 2026

## Context

The prototype has 35 migrations and 29 tables. All 35 apply cleanly to an empty Postgres 16, but the resulting schema has three problems for this repo:

- **Facts are keyed by symbol text, with `ON DELETE CASCADE`.** Deleting a ticker deletes its price history.
- **No fact table has `known_at`**, so ground rule 2 (point-in-time correctness) cannot be enforced.
- **There is no filings table.** The prototype's SEC client only fetches the latest annual report for Company Context. M1 needs filings stored by accession number.

The prototype also runs on the TimescaleDB image but creates no hypertables.

## Decision

**Scope.** `001_baseline.sql` contains only what M0 and M1 use: `schema_migrations`, `app_users`, `instruments`, `task_definitions`, `task_runs`, `price_bars`, and a new `filings` table. Other carry-over tables arrive as forward migrations when their code is carried over, and each is rewritten to the rules below:

| Later forward migration | Tables |
| --- | --- |
| News (after M2) | `raw_news_items`, `news_events`, `news_source_poll_state` |
| Rules-based sentiment | `article_sentiment`, `sentiment_daily` |
| Outcome evaluation (M2) | `outcomes`, `scored_signals` |
| Company Context | `context_entities`, `company_exposures`, `entity_relationships` |

These tables stay in the prototype: `analysis_jobs`, `market_briefs`, `holdings`, `topics`, `topic_entities`, `ticker_tags`, `events`, `event_mentions`, `event_analyses`, `event_impacts`, `event_strength_history`, `divergence_signals`, `price_quotes`.

**Identity.** `instruments` replaces `tickers`. Facts reference `instrument_id` with `ON DELETE RESTRICT`. `symbol` is unique. We only track currently listed instruments, so ticker reuse and ticker changes are out of scope; if one happens, rename the old row's symbol by hand. `cik` is indexed but not unique, because share classes of one issuer share a CIK. The onboarding job fills in CIK and filer type, and an equity cannot reach `ready` without them.

**Time.** Every fact table has `known_at`, and research reads only rows with `known_at < as_of`.

- `price_bars.known_at` is generated as 16:00 New York time on the trading date. This is conservative on 13:00 half-days.
- `filings.known_at` is EDGAR's acceptance time, the same for live and backfilled rows. `first_seen_at` stores when our poller saw the filing and is used to measure alert latency. `is_backfill` marks rows loaded by onboarding, which must never trigger alerts.

**Splits.** Bars are stored split-adjusted, exactly as Massive returns them with `adjusted=true`. After a new split, the instrument's whole history is re-fetched and overwritten, which is cheap for daily bars. Split detection is added in M2: a daily check of Massive's splits endpoint triggers the re-fetch. Until then, the guard against a missed split is a data-quality rule. Any daily move above 40% with no recorded split is marked `suspect`.

A split rewrites stored price levels, but returns are unchanged. Research works on returns, so this does not affect event studies.

**Lineage.** Ingested rows carry `task_run_id`. Raw filing documents go to S3, and the row keeps `raw_s3_key` and `raw_sha256`.

**Database.** Plain `postgres:16`, using the `sentinel_stream` schema. `pgcrypto` is not needed on Postgres 13 and later. `btree_gist` waits for Company Context.

**Migrations.** Every file, `001_baseline.sql` included, inserts its own row into `schema_migrations`. The runner (`orchestration/migrations.py`) runs each file in one transaction that it owns, checks that row inside the same transaction, and commits only if it is there with the file's sequence number; otherwise the file is rolled back. So files contain no `BEGIN` or `COMMIT`, and the runner refuses any that do. The prototype only checked files numbered 025 and later, and ran in autocommit, so a failing file could be left half applied.

Every container started from the app image runs the runner first (the image entrypoint), under one Postgres advisory lock held for the whole run. Concurrent starts are safe: the other runners wait, then find nothing pending. A recorded migration with no file on disk is only a warning, so an older image started by a rollback still runs. Forward migrations must therefore stay compatible with the previous release.

## Consequences

- Carried-over code that queries by symbol must join through `instruments`. This applies to the price-bars worker, the bars API and the task-runs view.
- The `filings` ingestion is new code, not a carry-over. Before trusting `accepted_at`, check the time zone of EDGAR's `acceptanceDateTime` against a filing index page.
- Prototype data is not imported now. The prototype database is archived to S3. If it is imported later, note that its `SPCX` rows belong to the SPAC and New Issue ETF, not SpaceX, and that backfilled news has no real `first_seen_at`.
- Both seeded tasks start as `manual`. Switch them to `automatic` once the provider credentials are in Parameter Store.

## Update, 27 Nov 2026

Split detection is built as described, and market-wide splits are stored for research ([ADR 0013](0013-stock-splits.md)). The `suspect` rule for unexplained moves above 40% was never built and is no longer needed for that purpose.
