"""Returns after an event from as-traded daily bars, corrected for splits (ADR 0015).

Everything here is a pure function of bars and splits already read through the as-of layer.

- **Sessions** come from the benchmark's bars: QQQ trades in every regular session, so its dates are
  the market calendar as loaded, including closures the rule-based calendar does not know.
- **Entry** is the close of the first session whose close is after the event became public.
- **Exit** is the close ``h`` sessions after entry. A stock with no bar that day (halted, or gone)
  exits at its last close before it, and the outcome is flagged ``ended_early``: dropping such
  stocks would leave out the failures.
- **Splits** between entry and exit multiply the exit close by their share factor, so a 4-for-1
  split is not a 75% loss.
- **Spread** is estimated from daily close, high and low (Abdi and Ranaldo, 2017).
"""

from __future__ import annotations

import bisect
import math
import statistics
from dataclasses import dataclass
from datetime import date, datetime

from research.asof import MarketBar, Split


@dataclass(frozen=True)
class Window:
    """Where an event's trade starts and ends in the session list."""

    entry_index: int
    exit_index: int


def entry_index(sessions: list[date], closes_at: list[datetime], known_at: datetime) -> int | None:
    """Index of the first session whose close is after ``known_at``; None if none is loaded yet."""
    index = bisect.bisect_right(closes_at, known_at)
    return index if index < len(sessions) else None


def split_factor(splits: list[Split], after: date, through: date) -> float:
    """Shares held at ``through`` per share held at ``after``: the product of splits in between."""
    factor = 1.0
    for split in splits:
        if after < split.execution_date <= through:
            factor *= split.share_factor
    return factor


@dataclass(frozen=True)
class Leg:
    """One holding from an entry close to an exit close."""

    entry_date: date
    exit_date: date
    entry_close: float
    exit_close: float
    gross_return: float
    ended_early: bool


def hold(
    bars_by_date: dict[date, MarketBar], dates: list[date], splits: list[Split], start: date, end: date
) -> Leg | None:
    """Buy at the close on ``start``, sell at the close on ``end`` or the last one before it.

    ``dates`` is the symbol's sorted bar dates. None without a bar on ``start``.
    """
    entry = bars_by_date.get(start)
    if entry is None or entry.close <= 0:
        return None
    position = bisect.bisect_right(dates, end) - 1
    exit_date = dates[position]
    exit_bar = bars_by_date[exit_date]
    if exit_date < start or exit_bar.close <= 0:
        return None
    gross = exit_bar.close * split_factor(splits, start, exit_date) / entry.close - 1.0
    return Leg(start, exit_date, entry.close, exit_bar.close, gross, ended_early=exit_date < end)


def abdi_ranaldo_spread(bars: list[MarketBar], splits: list[Split]) -> float | None:
    """Estimated bid–ask spread as a fraction of price, from consecutive days' close, high and low.

    Each pair of days gives ``sqrt(max(0, 4 (c_t - m_t)(c_t - m_t+1)))`` in logs, where ``m`` is the
    mid-range ``(log high + log low) / 2``; the estimate is their average. Pairs that straddle a split
    are skipped. None with fewer than five usable pairs.
    """
    split_days = {split.execution_date for split in splits}
    estimates = []
    for today, tomorrow in zip(bars, bars[1:], strict=False):
        if tomorrow.trading_date in split_days:
            continue
        values = (today.close, today.high, today.low, tomorrow.high, tomorrow.low)
        if any(v is None or v <= 0 for v in values):
            continue
        close = math.log(today.close)
        mid_today = (math.log(today.high) + math.log(today.low)) / 2
        mid_tomorrow = (math.log(tomorrow.high) + math.log(tomorrow.low)) / 2
        estimates.append(math.sqrt(max(0.0, 4 * (close - mid_today) * (close - mid_tomorrow))))
    if len(estimates) < 5:
        return None
    return sum(estimates) / len(estimates)


def median_traded_value(bars: list[MarketBar]) -> float | None:
    values = [bar.traded_value for bar in bars if bar.traded_value is not None]
    return statistics.median(values) if values else None
