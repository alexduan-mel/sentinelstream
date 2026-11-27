# 0017: Market-wide 8-K item history

Status: accepted, 27 Nov 2026.

## Context

Watchlist alerts rank 8-Ks by item: 1.01 a material agreement, 2.02 results, 4.02 restated financials, 5.02 an officer or director change ([ADR 0008](0008-materiality-rules-v0.md)). For a base rate on such an alert, the calculator needs every 8-K with those items at every company over the last two years. `filings` holds only the watchlist's, and it drives alerts.

Checked on 27 Nov 2026 from the production worker:

- **EDGAR's quarterly full index** (`/Archives/edgar/full-index/{year}/QTR{n}/form.idx`) lists every filing by form type, with company name, CIK and filing date. 2025 Q1 was 53 MB and held 16,723 8-Ks from 5,862 companies. Each quarter names 5,000 to 5,900 8-K filers, and three quarters together named about 7,000 distinct ones. Parsing a quarter took about 1 s, with a 270 MB peak in the worker. **It has no items.**
- **Each company's submissions record** (`data.sec.gov/submissions/CIK….json`) has every filing's items and acceptance time (UTC, [ADR 0006](0006-edgar-acceptance-time-is-utc.md)). It also has the company's current tickers, exchanges and industry code (SIC). The poller already parses it.

## Decision

**Three new tables and a task, `ingest_market_8k`.**

- `edgar_index_quarters` records each quarter's index read.
- `market_issuers` holds every company the index shows filing an 8-K: the date of its latest one, and what its submissions record said when last read.
- `market_8k_filings` has one row per 8-K and 8-K/A, with items and `known_at` = acceptance.

These are kept apart from `filings` and `filing_issuers`, so nothing here can reach Slack.

**Each run does two small steps.** It runs every 10 minutes on the default queue, because it calls SEC.

1. **Index.** It reads at most one quarter's index: the oldest not read yet, or else an incomplete one last read over 20 hours ago. Each 8-K line adds the company or moves its `latest_8k_date` forward. A quarter is complete once read more than 3 days after it ended.
2. **Submissions.** It reads up to 150 companies' records, about 30 seconds at SEC's pace. Older pages are read only when the recent block doesn't reach back two years. A company SEC has no record for is marked and skipped. Any other error stops the run.

**When a company is read again.** `covered_through` is the day before a company's last read. EDGAR dates a filing accepted after 17:30 with the next business day, so every filing dated up to then was accepted before the read. A company is due again when the index shows an 8-K dated after `covered_through` and before today. So a company is never read twice in one day, and nothing dated before the read is missed.

**Backfill** is about 9,000 companies at 150 a run: about 60 runs, roughly 10 hours once. After that, a few hundred companies a day.

**Events.** `research.events.eight_k_events(reader, items)` gives one event per company and filing date, from original 8-Ks carrying any of the items, timed at the earliest acceptance. `compute_base_rates` takes `{"event": "8k_items", "items": ["2.02"]}`.

**The symbol is the company's listed ticker as SEC lists it now.** It is the first ticker on NYSE, Nasdaq or another US exchange; over-the-counter tickers aren't in the market-wide bars. Submissions records keep no ticker history. So a company that has since changed ticker goes unmatched rather than mismatched, unless its old ticker passed to another company. The report's match counts show how many.

## Consequences

- About 70,000 8-Ks a year, a few tens of megabytes in all.
- `tickers`, `exchanges` and `sic` are as last read, not as of each filing. SIC is there for pooling by industry later.
- A watchlist 8-K appears in both `filings` and `market_8k_filings`. The two are written by different jobs for different purposes, and both agree on acceptance time.
- The index read briefly takes about 270 MB in the worker. The instance has about 1 GB free.
