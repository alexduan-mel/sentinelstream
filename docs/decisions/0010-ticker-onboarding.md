# 0010: Ticker onboarding

Status: accepted, 26 Nov 2026.

## Context

M1 needs an onboarding job: validate a symbol, map it to its SEC CIK, detect the filer type, backfill two years of filing metadata and daily prices, pull XBRL company facts, and add the ticker to the poller's watch set, with its status shown as pending, backfilling, then ready (CLAUDE.md, PRD). Until now the SEC poller resolved identities itself for any watchlist symbol without a CIK, so the seeded tickers were polled but never got two years of prices or any XBRL facts, and they all still read `pending`.

## Decision

**One task, `onboard_tickers`, owns a ticker's identity and status.** The poller no longer resolves symbols. Its watch set is every active watchlist equity that onboarding has identified (`backfilling` or `ready`).

**Statuses:** `pending` (added) → `backfilling` (tied to its SEC issuer; filings, prices and XBRL facts loading) → `ready`. A ticker becomes `failed` when SEC cannot tie the symbol to exactly one operating company, with the reason in `onboarding_error`.

**A ticker joins the watch set at `backfilling`, not at `ready`.** The spec lists "add to the watch set" last, but polling from the moment the identity is known means nothing filed during the backfill is missed. It is also safe: everything onboarding stores is history (`is_backfill = TRUE`), and a link the poller sees first, while the issuer has no filings stored yet, is history too ([ADR 0007](0007-filing-issuer-links.md)). So no old filing is ever pushed to Slack.

**Steps, each an upsert on stable keys, so reruns repeat nothing and a cut-off run resumes:**

1. Identity, from SEC's ticker map confirmed against the submissions record. Funds are rejected, and so is a symbol whose filer type the filings don't reveal. Already identified tickers skip this unless the run has `force_refresh`.
2. Filings: the submissions record's recent block, plus older pages back two years when the recent block does not reach that far.
3. Prices: two years of split-adjusted daily bars from Massive in one request, which starts at the listing date for a recent IPO. Benchmark ETFs only need this step.
4. XBRL: every company fact filed in the last two years, flattened into `xbrl_facts` as filed. `known_at` is the filing's acceptance time, or midnight New York after the filing date when the filing is not stored. Measured on 26 Nov 2026: 3–4.5 MB and 3,000–4,400 facts per large company, with a 64 MB peak in the worker.

**When it runs:** adding a ticker (`POST /api/instruments`) stores it as `pending` and queues a run for it (202). Each run handles its named tickers, then every other `pending` or `backfilling` instrument, until none is left. So a ticker added while a run is in progress, whose own run ends `skipped` under the task lock, is still onboarded. The task is also scheduled daily at 19:00 ET to catch anything left behind. Migration 009 makes the first scheduled run due straight after the deploy, which onboards the seeded watchlist and benchmarks.

**Errors:** a symbol SEC rejects is the user's input being wrong, not a system failure, so it marks the ticker `failed` and the run still succeeds, without paging anyone. A transient error (SEC or Massive unreachable) keeps the ticker's status, records `step: error`, and makes the run `partial` or `failed`; the next run resumes it. Missing credentials fail the run with a clear error (ground rule 5). `POST /api/instruments/{symbol}/onboard` retries with a fresh identity lookup.

**Removing** a ticker sets `active = false` and keeps its history. Adding it back reactivates it and re-runs onboarding with a fresh identity lookup. Benchmarks cannot be added or removed through the API.

## Consequences

- One worker runs tasks one at a time, so an onboarding run delays the next SEC poll by its length: about 15 seconds for one new ticker, a few minutes for the first run over the eight seeded instruments (Massive's free plan allows 5 requests a minute).
- `xbrl_facts` holds values as each company tagged them. Mapping different tags to one line item is M3 work.
- Older submissions pages and companies without XBRL (a 404 from SEC, as for a fresh IPO) are handled. A ticker whose name SEC lists under several CIKs is rejected rather than guessed.
