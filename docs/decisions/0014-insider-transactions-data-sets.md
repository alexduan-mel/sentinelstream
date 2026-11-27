# 0014: Market-wide insider transactions from SEC's datasets

Status: accepted, 27 Nov 2026.

## Context

The base-rate calculator's first event is the open-market insider purchase: a Form 4 with transaction code P. To say what usually follows one, it needs every such purchase at every US company over the last two years, not only at the watchlist companies. `form4_transactions` holds just the watchlist's, each read from its own Form 4 XML ([ADR 0008](0008-materiality-rules-v0.md)). Fetching tens of thousands of XML files one by one would take days of SEC requests.

SEC publishes the **Insider Transactions Data Sets** for free: one ZIP per calendar quarter, holding every Form 3, 4 and 5 filed in it as tab-separated files. Checked on 27 Nov 2026 from the production worker, with the app's declared User-Agent:

- The index page lists quarters from 2006 to 2026q3. Older files live under `/files/structureddata/…`, 2026q2 and later under `/files/datastandardsinnovation/…`. 2025q3's file is dated 18 Nov 2025, so a quarter appears about six weeks after it ends.
- One quarter: 8–14 MB zipped, 38,000–69,000 filings, 60,000–104,000 non-derivative transactions, 5,000–6,000 of them code P. Parsing one took 8–15 s and at most 134 MB in the worker. About 1 row in 25,000 was invalid.
- `SUBMISSION.tsv` gives the issuer's CIK, name and trading symbol as filed, the filing date (`31-JUL-2025`), the document type, and the 10b5-1 box (`AFF10B5ONE`). `REPORTINGOWNER.tsv` gives each filer's name, CIK, relationship and title. `NONDERIV_TRANS.tsv` gives the date, code, shares, price, acquired or disposed, holding after, and direct or indirect ownership.
- Symbols are typed by the filers: `NYSE: KRC`, `(SIRI)`, `HEI, HEI.A`, `N/A`, lower case.
- There is no acceptance time, only the filing date.

## Decision

**Three new tables, loaded by a new task, `ingest_insider_datasets`.** `insider_filings` has one row per filing, with the owners folded in. `insider_transactions` has its non-derivative rows. `insider_datasets` records each quarter loaded, with the file's URL, size, SHA-256 and SEC's Last-Modified date. `form4_transactions` stays as it is, for the watchlist's alerts.

**Everything is kept:** Forms 3, 4 and 5 with their amendments, and every transaction code. Which rows count for a base rate is decided at calculation time. For example, original Form 4s, code P, maybe leaving out 10b5-1 plans. Derivative transactions (options) are left out for now.

**Symbols are stored exactly as filed.** The calculator cleans them when it matches a purchase to `market_bars`, and falls back to the issuer's CIK through SEC's ticker map when a symbol can't be read.

**`known_at` is midnight New York after the filing date.** EDGAR gives a filing accepted after 17:30 the next business day's date, so acceptance always falls before the end of the filing date. This is never early. It can be up to a day late for a filing accepted during market hours, which makes measured effects slightly smaller than they really were, never larger.

**Loading.**

- It covers every ended quarter from the one holding the start of the two-year history. Links come from the index page, because the folders differ.
- Each run loads at most 2 quarters, oldest first (`max_files` in the payload overrides this). That is about 40 seconds on the worker, so the SEC poll is never held for long.
- A quarter is replaced whole in one transaction. To load it again, delete its `insider_datasets` row, which removes its filings and transactions with it.
- The task runs hourly from straight after deploy. The backfill takes four runs. Once current, a run makes no request until a quarter ends, then one index request an hour until SEC publishes it. A quarter still missing 100 days after it ended makes the run `partial`, because SEC should have published it by then.
- A file that isn't a readable dataset (an HTML maintenance page, a missing member) stops the run without storing anything, and the next run retries.

Research reads these rows through `AsOf.insider_transactions`, filtered by issuer, code, document type and date.

## Consequences

- About 65 MB per quarter in Postgres, about 0.5 GB for two years.
- The most recent quarter is missing for about six weeks after it ends. Base rates don't need it: a 60-session return needs about three months after the event anyway.
- Matching filers' symbols to traded symbols is the calculator's job, and its match rate should be reported with each result.
