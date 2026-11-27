# 0015: The base-rate calculator

Status: accepted, 27 Nov 2026.

## Context

M2's goal is a base rate on every alert: what stocks have done, compared with QQQ, after past events of the same kind. The data is now in place, all read point in time through the as-of layer ([ADR 0011](0011-as-of-data-access.md)):

- market-wide daily bars as traded ([ADR 0012](0012-market-wide-daily-bars.md));
- splits ([ADR 0013](0013-stock-splits.md));
- market-wide insider transactions ([ADR 0014](0014-insider-transactions-data-sets.md)).

Decided with the owner on 27 Nov 2026:

- **Horizons and benchmark:** 1, 5, 20 and 60 sessions, compared with QQQ, with SPY shown.
- **Costs:** 0.10% a trade, but each stock should carry its own estimated cost.
- **Filters:** report two versions side by side. *Main* keeps stocks of $5 or more trading at least $1M a day. *None* keeps all.
- **Filters are settings.** They are measured only from data before the event, and changed only for reasons other than the result.

No machine learning: a base rate is an average over history.

## Decision

`research/` gains four modules. They are pure Python: no numpy or pandas, as ADR 0011 chose.

**Events (`research/events.py`).** An event is one company on one day. Several insiders buying on the same day, or one insider filing several Form 4s, count once, because they are one piece of news and would otherwise inflate the sample.

- Insider purchases are original Form 4s with code P, acquired, and a positive share count and price. 10b5-1 plans can be left out by a setting.
- An event's time is its earliest filing's `known_at`.
- Its symbol is the filer-typed one, cleaned: `NYSE: KRC` → `KRC`, `(SIRI)` → `SIRI`, `N O G` → `NOG`, `BRK-B` → `BRK.B`, `N/A` → none.
- An event without a readable symbol, or whose symbol has no bars, is counted and reported, not guessed. Falling back to the CIK can be added if the match rate turns out low.

**Returns (`research/returns.py`).**

- **Sessions** are the benchmark's loaded bar dates. QQQ trades every session, so unscheduled closures are handled.
- **Entry** is the close of the first session that closes after the event's `known_at`.
- **Exit** is the close `h` sessions later.
- **Missing exit prices.** A stock with no bar on the exit day (halted, or delisted) exits at its last close before it, and the outcome is flagged `ended_early`. Dropping such stocks would leave out the failures.
- **Splits** between entry and exit multiply the exit close by their share factor. A split on the entry day is already in the entry price.
- **QQQ and SPY** are held over exactly the same dates as the stock.

**Costs.** Each trade costs the larger of 0.10% and half the stock's estimated bid–ask spread, so a round trip costs twice that. The spread is estimated with Abdi and Ranaldo's (2017) method from close, high and low over the 20 sessions before entry. Pairs of days that straddle a split are skipped, and the estimate needs at least five pairs.

**Filters.**

| Setting | Price | Traded value | History needed |
| --- | --- | --- | --- |
| `MAIN` | last close before entry ≥ $5, as traded (so a later split can't change it) | median of volume × average price over the 20 sessions before entry ≥ $1M | at least 10 bars in that window |
| `UNFILTERED` | none | none | none |

**Summaries (`research/stats.py`).**

- The figure summarised is the return after costs minus QQQ's. Each summary gives sample size, average, median, hit rate (the share above zero) and a 95% range for the average.
- The range is a cluster bootstrap over calendar months of entry. Events in the same month move together, and resampling single events would give a falsely narrow range.
- It uses 2,000 resamples with a fixed seed, so the same data always gives the same range. With fewer than six months there is no range.
- Average stock return, average QQQ return, average cost, average versus SPY, and the `ended_early` count are reported too.

**One reader for everything.** A calculation dated `as_of` reads every table through one `AsOf`. An event whose 60th session has not closed by then has no 60-session result. It is counted as `horizon_60_not_reached`, never filled in.

**Running it.** The task `compute_base_rates` is manual and runs on the research worker ([ADR 0016](0016-research-worker.md)). Its payload names the event kind (`insider_purchases`) and options. With `as_of_date`, the run is dated the start of that day in New York. The JSON report goes into the run's `meta`, visible on the Tasks page. Storing base rates in a table for alerts comes with the alert integration.

## Consequences

- Returns are price returns: dividends are in neither the stock's nor QQQ's figures.
- The match rate from filed symbols to traded symbols is part of every report (`matched_to_prices` and the `no_symbol` and `no_price_data` counts).
- A full run reads every matched stock's two years of bars, about 6 ms per stock locally. With several thousand stocks that is minutes, which is why it runs on its own worker.
- Base rates pool every company. A company-specific or sector view is a filter on the events, which can come later.
