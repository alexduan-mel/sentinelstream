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

## Amendment, 26 Nov 2026: prices start at the listing date

The first onboarding run loaded 454 bars for SPCX from Nov 2024. Bars until 6 Apr 2026 were about $22 on a few thousand shares a day, then, after a gap, about $160 on over 100 million shares from 12 Jun 2026. Massive serves bars for whatever traded under a symbol, and SPCX belonged to an ETF before SpaceX listed under it. The mix would corrupt every return computed from it.

The prices step now first reads Massive's ticker details, which give the listing date and CIK; for SPCX that is 12 Jun 2026 and CIK 0001181412, the same as SEC's. This is the "validate the symbol with the price provider" step of the spec:

- If Massive ties the symbol to a different CIK than SEC does, no bars are loaded, and the ticker stays `backfilling` with the mismatch as its error.
- Bars start at the later of two years back and the listing date, and stored bars dated before the listing are deleted.

Migration 010 moves ready equities back to `backfilling` and makes onboarding due at once, so the next run cleans up the stored history. A second Massive request per ticker costs about 12 seconds at the free plan's 5 requests a minute.

## Amendment, 27 Nov 2026: symbols are checked with SEC when added

Adding four random letters used to create a row that turned `failed` a few seconds later, once the onboarding job had asked SEC. The watchlist should only ever hold real tickers. `POST /api/instruments` now checks the symbol with SEC first and refuses with 422, storing nothing, when SEC does not list it ("XYZQ: not found on any US exchange"), when it belongs to a fund, or when its filer type cannot be told. If SEC cannot be reached it answers 503, rather than storing an unchecked symbol. A valid ticker is stored with its CIK, name and filer type, so the page shows them at once.

This is a quick read to validate input, not background work, so ground rule 4 still holds: the loading stays in `onboard_tickers`. The check costs one request for the issuer's submissions record. SEC's ticker map, about 1 MB and refreshed by SEC about daily, is cached in the API process for 6 hours, which is fine for deciding whether a symbol exists (the poller never uses this cache). Onboarding still confirms the identity and can still mark a ticker `failed`, for example when Massive ties the symbol to a different company.
