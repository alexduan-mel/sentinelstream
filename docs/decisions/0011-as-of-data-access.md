# 0011: As-of data access for research

Status: accepted, 27 Nov 2026.

## Context

M2 builds a base-rate calculator: for past events of one kind (an insider purchase, an 8-K with item 2.02), what did the stock do over the next 1, 5, 20 and 60 trading sessions, compared with QQQ? Its results are only honest if each past event is studied with the data that was public at the time. If a study dated 3 March 2025 can see a 10-K accepted that evening, or the close of a session still in progress, it has looked ahead, and its base rates look better than anything the investor could have acted on.

Ground rule 2 already says every fact row carries `known_at` and research reads facts only where `known_at < as_of`. Every fact table has that column ([ADR 0002](0002-baseline-data-model.md), [ADR 0008](0008-materiality-rules-v0.md), [ADR 0010](0010-ticker-onboarding.md)). Nothing yet enforces the read side.

## Decision

**One reader, `research.asof.AsOf(conn, as_of)`, is the only way research code reads facts.** It has a method per kind of fact: `bars`, `filings`, `form4_transactions`, `xbrl_facts` and `latest_xbrl_values`. Every query is built by one private helper that appends `known_at < as_of` itself, so no method can leave it out.

- **Strictly before.** At `as_of` 16:00:00 ET, that day's bar is not visible yet, and neither is a filing accepted at exactly 16:00:00.
- **`as_of` must be timezone-aware.** A naive datetime is rejected rather than guessed at.
- **What `known_at` means per table is unchanged:** 16:00 New York on the trading date for a daily bar (conservative on 13:00 half-days); EDGAR acceptance for a filing and its Form 4 rows; acceptance of the filing for an XBRL value, or the end of its filing date in New York when that filing is not stored.
- **Filings by issuer** go through `filing_issuers`, so a filing shared by several issuers is found under each of them ([ADR 0007](0007-filing-issuer-links.md)).
- **Restatements.** `latest_xbrl_values` returns one value per period: the latest filed before `as_of`. A restated number replaces the original only once the restating filing is public.
- **Derived labels are not facts.** Materiality and Slack delivery are left out: they are our own outputs, computed later, and can change when the rules change. Research works from the filing's form and items instead.

**Enforcement is a test, not a convention.** `tests/test_asof.py` stores facts on both sides of a moment and checks the boundary for every reader, then checks that no reader ever returns a row with `known_at >= as_of` across a sweep of moments. Loosening the filter to `<=` fails five of its tests. A second test scans every other module in `research/` and fails if it queries a fact table (`FACT_TABLES` in `asof.py`) directly.

## Consequences

- A new fact table (market-wide daily bars, market-wide insider purchases, 8-K item history, all planned for M2) is added to `AsOf` and `FACT_TABLES`, with a boundary test, before research code uses it.
- **Rows are corrected in place; there is no history of versions.** If a stored row is later fixed (bars re-fetched after a split, a corrected acceptance time), the reader sees the corrected row for every `as_of`. That is the right answer for prices and acceptance times, which were wrong, not revised. It means the layer cannot replay a mistake the poller made at the time.
- **Split-adjusted prices.** Bars are stored split-adjusted ([ADR 0002](0002-baseline-data-model.md)), so a price level from before a later split is not the price that traded then. Returns are unaffected. Any rule that compares a price level with a fixed number, such as leaving out stocks under $5, must undo later splits first, or it quietly uses future information.
- Research reads go through Python objects, not pandas. That keeps the dependency list short; a calculator that needs pandas can build a frame from the reader's results.
