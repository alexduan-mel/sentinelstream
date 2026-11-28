"""How each Journal entry has done since it was made, against QQQ and SPY (ADR 0026).

Scores are worked out from stored daily bars on each request, read through the as-of layer at the
moment of the request, so a day's bars count as soon as they are stored and nothing is kept that
could go stale.

**Start.** An entry with a trade price starts from that price on its trade day (moved to today's
share basis through later splits); QQQ and SPY start from that day's open, since the time of day of
the trade is not known. Any other entry, and every Pass, starts from the first daily close after it
was logged: a bar is public at its close (16:00 New York), and the comparison is strict, so an entry
logged at 16:00:00 starts the next session (ground rule 2). The stock and both benchmarks start on
the same close.

**Trade price check.** A price for a day whose bar arrived after logging is checked here against
that day's low-high range, as logging checks the others (ADR 0025). Outside it, the entry falls back
to the next close after it was logged and says so.

**End.** Opened and Added end where the position's Closed entry starts; every other entry, and a
position still open, runs to the latest stored close.

**Returns** are price returns from split-adjusted closes; dividends are left out (QQQ pays about 0.6%
a year, so this slightly flatters a stock that pays none against it). ``vs_qqq`` is the stock's
return minus QQQ's over the same days.
"""

from __future__ import annotations

import bisect
import dataclasses
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from typing import Literal

from psycopg2.extensions import connection as Connection

from ingestion.market_calendar import NEW_YORK
from ledger.journal import PRICE_TOLERANCE, Entry
from research.asof import AsOf, Bar, Split
from research.returns import split_factor

BENCHMARK = "QQQ"
REFERENCE = "SPY"

Basis = Literal["trade", "next_close"]
PriceCheck = Literal["ok", "out_of_range"]


class Series:
    """One symbol's stored bars (split-adjusted, oldest first) and its splits, as of one moment."""

    def __init__(self, bars: list[Bar], splits: list[Split]) -> None:
        self.bars = bars
        self.splits = splits
        self._dates = [bar.trading_date for bar in bars]
        self._closes_at = [bar.known_at for bar in bars]

    def first_close_after(self, moment: datetime) -> Bar | None:
        """The first bar whose close came strictly after ``moment``."""
        index = bisect.bisect_right(self._closes_at, moment)
        return self.bars[index] if index < len(self.bars) else None

    def on(self, day: date) -> Bar | None:
        index = bisect.bisect_left(self._dates, day)
        if index < len(self.bars) and self.bars[index].trading_date == day:
            return self.bars[index]
        return None

    def last_on_or_before(self, day: date) -> Bar | None:
        index = bisect.bisect_right(self._dates, day) - 1
        return self.bars[index] if index >= 0 else None

    def latest(self) -> Bar | None:
        return self.bars[-1] if self.bars else None

    def sessions_between(self, start: date, end: date) -> int:
        """Sessions after ``start`` up to and including ``end``."""
        return bisect.bisect_right(self._dates, end) - bisect.bisect_right(self._dates, start)


@dataclasses.dataclass(frozen=True)
class Point:
    """Where a holding starts or ends: a day and a price for the stock and each benchmark."""

    day: date
    basis: Basis
    stock: float
    qqq: float
    spy: float | None


@dataclasses.dataclass(frozen=True)
class Score:
    # waiting: no close after the entry yet (or its trade day's bar has not arrived).
    status: Literal["scored", "waiting"]
    start_date: date | None
    start_basis: Basis | None
    # In today's share basis, so it compares with today's prices.
    start_price: float | None
    price_check: PriceCheck | None
    end_date: date | None
    end_price: float | None
    ends: Literal["closed", "latest"] | None
    closed_by: int | None
    sessions: int
    stock_return: float | None
    qqq_return: float | None
    spy_return: float | None
    vs_qqq: float | None
    vs_spy: float | None


WAITING = Score("waiting", None, None, None, None, None, None, None, None, 0, None, None, None, None, None)


def _close(bar: Bar | None) -> float | None:
    return None if bar is None or bar.close is None or bar.close <= 0 else float(bar.close)


def _open_or_prior_close(series: Series, day: date) -> float | None:
    bar = series.on(day)
    if bar is not None and bar.open is not None and bar.open > 0:
        return float(bar.open)
    return _close(series.last_on_or_before(day - timedelta(days=1)))


def _next_close_point(entry: Entry, stock: Series, qqq: Series, spy: Series | None) -> Point | None:
    bar = stock.first_close_after(entry.known_at)
    if bar is None:
        return None
    stock_close, qqq_close = _close(bar), _close(qqq.on(bar.trading_date))
    if stock_close is None or qqq_close is None:
        return None
    spy_close = _close(spy.on(bar.trading_date)) if spy else None
    return Point(bar.trading_date, "next_close", stock_close, qqq_close, spy_close)


def _start(entry: Entry, stock: Series, qqq: Series, spy: Series | None) -> tuple[Point | None, PriceCheck | None]:
    """Where the entry starts, and how its trade price checked out (None without one)."""
    if entry.traded_on is None or entry.price is None:
        return _next_close_point(entry, stock, qqq, spy), None
    bar = stock.on(entry.traded_on)
    if bar is None or bar.low is None or bar.high is None:
        return None, None  # the trade day's bar has not arrived yet
    # The typed price is as traded; stored bars are in today's share basis.
    price = entry.price / split_factor(stock.splits, entry.traded_on, date.max)
    low, high = float(bar.low), float(bar.high)
    if not low * (1 - PRICE_TOLERANCE) <= price <= high * (1 + PRICE_TOLERANCE):
        return _next_close_point(entry, stock, qqq, spy), "out_of_range"
    qqq_open = _open_or_prior_close(qqq, entry.traded_on)
    if qqq_open is None:
        return None, "ok"
    spy_open = _open_or_prior_close(spy, entry.traded_on) if spy else None
    return Point(entry.traded_on, "trade", price, qqq_open, spy_open), "ok"


def _latest_point(stock: Series, qqq: Series, spy: Series | None) -> Point | None:
    bar = stock.latest()
    if bar is None:
        return None
    day = bar.trading_date
    stock_close, qqq_close = _close(bar), _close(qqq.last_on_or_before(day))
    if stock_close is None or qqq_close is None:
        return None
    spy_close = _close(spy.last_on_or_before(day)) if spy else None
    return Point(day, "next_close", stock_close, qqq_close, spy_close)


def _closing_entries(entries: Iterable[Entry]) -> dict[int, Entry]:
    """For each counted Opened and Added, the counted Closed that ended its position, if any."""
    by_symbol: dict[str, list[Entry]] = {}
    for entry in entries:
        if entry.withdrawn is None:
            by_symbol.setdefault(entry.symbol, []).append(entry)
    closing: dict[int, Entry] = {}
    for symbol_entries in by_symbol.values():
        ordered = sorted(
            symbol_entries,
            key=lambda e: (e.traded_on or e.known_at.astimezone(NEW_YORK).date(), e.known_at, e.id),
        )
        holding: list[Entry] = []
        for entry in ordered:
            if entry.action in ("opened", "added"):
                holding.append(entry)
            elif entry.action == "closed":
                for held in holding:
                    closing[held.id] = entry
                holding = []
    return closing


def _ret(start: float | None, end: float | None) -> float | None:
    return None if start is None or end is None else end / start - 1.0


def score_entries(entries: list[Entry], series: dict[str, Series]) -> dict[int, Score]:
    """Scores for every counted entry; withdrawn ones are left out. ``series`` holds each entry's
    ticker, QQQ and (when stored) SPY."""
    qqq = series.get(BENCHMARK)
    spy = series.get(REFERENCE)
    closing = _closing_entries(entries)
    scores: dict[int, Score] = {}
    for entry in entries:
        if entry.withdrawn is not None:
            continue
        stock = series.get(entry.symbol)
        if stock is None or qqq is None:
            scores[entry.id] = WAITING
            continue
        start, check = _start(entry, stock, qqq, spy)
        if start is None:
            scores[entry.id] = dataclasses.replace(WAITING, price_check=check)
            continue

        closed_by = closing.get(entry.id)
        end, ends = None, "latest"
        if closed_by is not None:
            end, _ = _start(closed_by, stock, qqq, spy)
            ends = "closed" if end is not None else "latest"
        if end is None:
            end = _latest_point(stock, qqq, spy)
        if end is None or end.day < start.day:
            end = start

        stock_return = _ret(start.stock, end.stock)
        qqq_return = _ret(start.qqq, end.qqq)
        spy_return = _ret(start.spy, end.spy)
        scores[entry.id] = Score(
            status="scored",
            start_date=start.day,
            start_basis=start.basis,
            start_price=start.stock,
            price_check=check,
            end_date=end.day,
            end_price=end.stock,
            ends=ends,
            closed_by=closed_by.id if ends == "closed" and closed_by else None,
            sessions=stock.sessions_between(start.day, end.day),
            stock_return=stock_return,
            qqq_return=qqq_return,
            spy_return=spy_return,
            vs_qqq=None if stock_return is None or qqq_return is None else stock_return - qqq_return,
            vs_spy=None if stock_return is None or spy_return is None else stock_return - spy_return,
        )
    return scores


def load_series(conn: Connection, symbols: Iterable[str], as_of: datetime) -> dict[str, Series]:
    """Each symbol's bars and splits as of ``as_of``, plus QQQ's and SPY's (ground rule 2)."""
    view = AsOf(conn, as_of)
    loaded: dict[str, Series] = {}
    for symbol in {*symbols, BENCHMARK, REFERENCE}:
        bars = view.bars(symbol)
        if bars:
            loaded[symbol] = Series(bars, view.splits(symbol))
    return loaded


def score_journal(conn: Connection, entries: list[Entry], as_of: datetime) -> dict[int, Score]:
    """Scores for ``entries`` from what was stored before ``as_of``."""
    return score_entries(entries, load_series(conn, {e.symbol for e in entries}, as_of))
