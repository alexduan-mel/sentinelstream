# 0012: Market-wide daily bars

Status: accepted, 27 Nov 2026.

## Context

The base-rate calculator measures what stocks did after past events, such as every insider purchase in the last two years, not just those at the eight watchlist companies. It needs daily prices for the whole US market. `price_bars` holds split-adjusted bars per watchlist instrument, fetched one symbol per request. At the free plan's 5 requests a minute that cannot cover thousands of companies.

Checked against the live API on 27 Nov 2026, with the free plan:

- **Grouped daily** (`/v2/aggs/grouped/locale/us/market/stocks/{date}`) returns every US stock's bar for one day in one request: 12,636 symbols on 25 Nov 2026, about 1.4 MB. Each bar has open, high, low, close, volume, volume-weighted average price and trade count.
- **History is two years, counted from today.** 29 Nov 2024 was served on 27 Nov 2026; 20 Nov 2024 got HTTP 403 "past historical entitlements". The oldest day drops out each day.
- **A day with no session** (Thanksgiving) returns status `DELAYED` and no bars.
- **Symbols are the provider's own**, including 372 preferred shares in mixed case (`SCHWpD`) and class shares with dots (`BRK.B`).

## Decision

**A new table, `market_bars`, keyed by symbol and trading day,** loaded by a new task, `ingest_market_bars`. `market_bar_days` records each day fetched. `price_bars` is unchanged and keeps serving the watchlist pages.

**Prices are stored as traded, not split-adjusted.** An as-traded price never changes after the day, so a split needs no re-fetch, and a rule like "leave out stocks under $5" sees the price people actually paid, with no hidden lookahead ([ADR 0011](0011-as-of-data-access.md)). The calculator corrects returns across a split with split data, which Massive's splits endpoint provides on the free plan. Loading that data is the next M2 step. Dividends are in neither version; returns are price returns, as are QQQ's and SPY's, so the comparison stays like for like.

**Everything Massive reports is stored, unfiltered.** That includes ETFs, preferred shares, warrants and stocks that later delist. Which stocks count for a base rate is a calculator setting, applied at calculation time and measured from data before each event. Keeping delisted stocks avoids survivorship bias: studying only today's survivors flatters the past.

**A symbol is not a company.** Rows carry the symbol as it traded that day. A symbol can pass from one company to another (SPCX did in 2026), and a company can change symbol. So events are tied to a symbol through data from their own time; for example, a Form 4 names the issuer's trading symbol as filed.

**Loading, so the SEC poll keeps its timing.** The worker runs one job at a time. Each run fetches at most 5 days (`max_days` in the payload overrides this), about a minute at the rate limit, and the task runs every 10 minutes. That delays a 5-minute SEC poll by about a minute at most, well inside the 10-minute alert target. Days go oldest first, because they are the next to drop out of the plan's history. About 500 trading days take about 17 hours once. After that, a run makes no request until a new session closes.

**A day is stored whole or not at all.** Its bars and its `market_bar_days` row are written in one transaction. What a fetched day becomes:

| Massive's answer | Recorded as | Asked again? |
| --- | --- | --- |
| Bars | `loaded` | No |
| No bars, day closed more than 3 days ago | `empty` (an unscheduled closure, such as the national day of mourning on 9 January 2025) | No |
| No bars, recent day | Nothing (not published yet) | Next run |
| 403 past historical entitlements | `unavailable` | No |
| Any other error | Nothing; the run stops, `partial` or `failed` | Next run |

**Lineage is per day, not per row.** `market_bar_days` keeps the run id and Massive's request id. A `task_run_id` and raw payload on each of about 6 million rows would double the table for no added traceability, since one request loads one whole day. To re-fetch a day, delete its `market_bar_days` row: its bars go with it, and the next run loads it again.

**Research reads it through `AsOf.market_bars`,** with the same `known_at` as `price_bars`: 16:00 New York time on the trading day. `MarketBar.traded_value` is volume times average price, the basis for a traded-value filter.

## Consequences

- About 1.9 MB per day in Postgres, about 1 GB for two years; the data volume has 9.8 GB free.
- One `ingest_market_bars` run every 10 minutes appears in Run history. Runs that find nothing to do make no request.
- Until split data is loaded, returns across a split are wrong, so the calculator must not use `market_bars` before then.
- On 25 Nov 2026, 6,530 symbols traded at least $1M, 4,429 at least $5M and 2,858 at least $20M. Those counts include ETFs, preferred shares and warrants. The traded-value cut-off is chosen from counts like these, never from the results.
- **Update, 28 Nov 2026.** Massive's plan serves a day's bars only once that day has ended in New York. Asked between the close and midnight, the grouped endpoint answers HTTP 403 "Attempted to request today's data before end of day", and the per-symbol endpoint returns no bar. So `ingest_market_bars`, `ingest_prices` and onboarding aim at the latest trading day whose day has ended (`latest_published_session`), and a "before end of day" refusal waits rather than failing. `ingest_prices` moved to 05:00 every day (migration 019). A bar's `known_at` is still its session close, so as-of reads are unchanged: the data simply arrives about eight hours later.
