# 0026: Journal scores, worked out on request against QQQ and SPY

Status: accepted, 28 Nov 2026.

## Context

Each Journal entry ([ADR 0025](0025-journal-ledger.md)) is measured against QQQ, with SPY shown for reference. The owner chose to score an entry from its real trade price when one is given, and otherwise from the first close after it was logged. An entry must never start from a price already known when it was logged (ground rule 2), and tests have to prove that. The milestone first planned a scheduled task to keep scores current.

## Decision

**Scores are worked out on each request**, not stored. `GET /api/journal` reads the bars and splits of each ticker in the response, plus QQQ and SPY, through the as-of layer at the moment of the request (`AsOf(now)`), and scores every entry that has not been withdrawn. Even for years of entries this is a few thousand rows, read in milliseconds. A day's bars count as soon as `ingest_prices` stores them. Nothing is stored, so nothing can go stale or need recomputing after a correction or a split re-fetch. The planned "scores kept current" task is therefore not needed, and its box is dropped from M4.

**Start:**

- **No trade price, or a Pass:** the first stored close strictly after `known_at`. A bar is public at 16:00 New York, so an entry logged at 16:00:00 starts the next session. The stock, QQQ and SPY all start on that close.
- **Trade price:** the typed price, moved to today's share basis by dividing by the splits after the trade day (stored bars are split-adjusted). QQQ and SPY start from that day's open, or the previous close if no open is stored, because the time of day of the trade isn't known. The error is at most part of one day's benchmark move.
- **A trade typed for today** was accepted at logging without a check, because that day's bar arrives after the close. It is checked here once the bar is stored, against the same low–high range and 1% leeway as at logging. Outside the range, the entry falls back to the next close after it was logged, and the score says `price_check: out_of_range`.
- **Waiting:** an entry with no close to start from yet, or whose trade day's bar hasn't arrived, is `waiting` with no returns.

**End:**

- **Opened and Added** end where the position's Closed entry starts: its trade price, or the next close after it was logged. QQQ and SPY end at the same point, by the same rule.
- **Trimmed, Closed and Pass, and a position still open,** run to the latest stored close.
- A withdrawn entry is not scored and doesn't end a position.

**Returns** are price returns from split-adjusted closes: `stock_return`, `qqq_return`, `spy_return`, then `vs_qqq` and `vs_spy` (the stock's return minus the benchmark's). The score also gives the start and end day, the start basis (`trade` or `next_close`), start and end prices in today's share basis, how it ended (`closed` with the closing entry's id, or `latest`), and the number of sessions.

**Lookahead tests.** `ledger/scoring.py` reads facts only through `AsOf`. The test that keeps research modules off the fact tables now scans it too. `tests/test_scoring.py` scores the same entry at moments just before, exactly at, and one second after a close, and later in the week, and checks that only bars public at that moment are used.

## Consequences

- Dividends are left out. QQQ yields about 0.6% a year, so a non-paying stock looks slightly better against it than it was. Total-return data would fix this if it is added later.
- The score is a plain difference in returns. Alpha and beta (the M4 box after this) separate the part explained by QQQ's moves from the rest.
- Each entry is scored in the direction of the stock's move. Whether that move supported the decision depends on the action: Opened and Added are calls that it would do well, while Trimmed, Closed and Pass are calls that it wouldn't. That reading belongs to the Journal page's record of calls.
- If the Journal grows large, the page can ask for one ticker at a time (`?symbol=`). Storing scores would only be worth it if requests became slow.
