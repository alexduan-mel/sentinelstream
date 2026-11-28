# 0013: Stock splits and split detection

Status: accepted, 27 Nov 2026.

## Context

Two things need split data.

- **Research.** Market-wide bars are stored as traded ([ADR 0012](0012-market-wide-daily-bars.md)). A return across a split is wrong unless it is corrected. After a 4-for-1 split a $400 share trades near $100, which is not a 75% loss.
- **The watchlist.** `price_bars` are split-adjusted as of when they were fetched. [ADR 0002](0002-baseline-data-model.md) promised a daily check of Massive's splits endpoint that re-fetches an instrument's history after a split, so its old bars move to the new scale. Until now nothing did this. ADR 0002's fallback rule, marking any unexplained daily move above 40% as `suspect`, was never built either.

Checked against the live API on 27 Nov 2026, with the free plan:

- **The splits endpoint is included.** Records have `id`, `ticker`, `execution_date`, `split_from` and `split_to`. NVIDIA's 10-for-1 on 10 June 2024 is `1 → 10`. Ratios can be fractional (share dividends such as 24 → 25), and 763 of 1,050 records from 27 Nov 2024 on were reverse splits.
- **One day can hold several splits for one symbol.** PMD did a 1-for-5,000 reverse split and a 5,000-for-1 split on 4 Dec 2024, which together change nothing. Some issuers record two share dividends on one day.
- **Paging repeated rows.** After a first page of 1,000, each next page returned 10 rows, and the same ones kept coming back. A date window, though, returns in one page: 138 splits in June 2025, 783 in the first half of 2025.
- **Future splits are listed,** up to January 2027 on 27 Nov 2026.
- None of the watchlist or benchmark symbols split in the last two years.

## Decision

**A new table, `stock_splits`, keyed by Massive's id,** not by symbol and day, so several splits on one day are all kept. Every split Massive reports is stored, for every symbol, matched exactly like `market_bars`. `known_at` is the start of the execution day in New York, the first day the new share count trades. Splits are announced before that, so it is never earlier than public. Research reads them through `AsOf.splits`. `Split.share_factor` (`split_to / split_from`) is what an as-traded close is multiplied by to compare it with a close from before the split.

**A new daily task, `ingest_splits`,** at 17:00 ET: after the close, before `ingest_prices` (18:30 at first; 05:00 the next morning since migration 019, because Massive serves a day only once it has ended). It also runs once straight after deploy.

- **One calendar quarter per request.** A quarter held about 400 splits in 2025. Paging is never followed; a reply that fills a page is an error, so a quarter is never silently cut short.
- **A quarter is replaced whole,** in one transaction. Splits in it that Massive no longer lists are removed, so corrections and withdrawals come through. An empty reply for a quarter that already has splits stored is treated as a provider error, because every quarter so far has had hundreds.
- **Which quarters.** It covers the two-year history up to 120 days ahead. The current and upcoming quarters are fetched on every run. A past quarter is settled once fetched more than 45 days after it ended, and is not asked again. Those 45 days leave room for late corrections. `split_windows` records each fetch.
- **At most 6 requests per run** (about a minute; `max_requests` in the payload overrides this). Current and upcoming quarters go first, then the oldest backlog. The first two runs load the history.

**Detection.** After loading, the run looks for active watchlist or benchmark instruments with a split that has taken effect, whose stored bars from before the split were fetched before 09:30 ET on the split day. Those bars are on the old scale. For each such instrument it queues one `ingest_prices` run (`trigger_source = split_detected`), which re-fetches the instrument's whole stored history from its first bar. It doesn't queue a second while one is pending or running. Splits on one day that cancel out are ignored. Once the re-fetch has run, every old bar was fetched after the split, so the check is quiet again and reruns queue nothing.

## Consequences

- The `suspect` data-quality rule from ADR 0002 is no longer needed for the watchlist: a split is now detected from split data instead of guessed from a price move. The `data_quality` column stays for other uses.
- About 900 splits a year across the market, mostly over-the-counter and foreign symbols; a few hundred kilobytes in Postgres.
- The calculator can now compare as-traded closes across splits. Returns stay price returns (no dividends), as stated in ADR 0012.
- A split Massive records late, after its execution day, is still caught: detection compares fetch times with the split day, not with when the split was first seen.
