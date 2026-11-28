"""US equity market calendar (NYSE/Nasdaq regular sessions), computed by rule.

Carried over from the prototype's ``analysis/outcomes.py`` and ``analysis/portfolio.py``. One fix:
when 1 January falls on a Saturday the exchanges observe no holiday (they do not close the Friday
before, 31 December), which the prototype got wrong. One-off closures (national days of mourning)
are not modelled; on such a day the price job finds no bar and the run ends ``partial``.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import cache
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")
REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    current = date(year, month, 1)
    while current.weekday() != weekday:
        current += timedelta(days=1)
    return current + timedelta(days=7 * (occurrence - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    current = date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)
    while current.weekday() != weekday:
        current -= timedelta(days=1)
    return current


def _easter(year: int) -> date:
    """Gregorian Easter Sunday (anonymous Gregorian algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741 - the algorithm's own name
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _observed(holiday: date) -> date:
    """Saturday holidays move to Friday, Sunday holidays to Monday."""
    if holiday.weekday() == 5:
        return holiday - timedelta(days=1)
    if holiday.weekday() == 6:
        return holiday + timedelta(days=1)
    return holiday


@cache
def holidays(year: int) -> frozenset[date]:
    days = {
        _nth_weekday(year, 1, 0, 3),  # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),  # Washington's Birthday
        _easter(year) - timedelta(days=2),  # Good Friday
        _last_weekday(year, 5, 0),  # Memorial Day
        _observed(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),  # Labor Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
        _observed(date(year, 12, 25)),
    }
    if year >= 2022:
        days.add(_observed(date(year, 6, 19)))  # Juneteenth, first observed in 2022
    new_year = date(year, 1, 1)
    if new_year.weekday() != 5:  # a Saturday New Year's Day is not observed
        days.add(_observed(new_year))
    return frozenset(days)


def is_trading_day(day: date) -> bool:
    return day.weekday() < 5 and day not in holidays(day.year)


def session_close(day: date) -> time:
    """Regular-session close in New York time. Early closes: the day after Thanksgiving, and
    3 July and 24 December when the holiday itself falls Tuesday to Friday."""
    if day == _nth_weekday(day.year, 11, 3, 4) + timedelta(days=1):
        return EARLY_CLOSE
    for holiday in (date(day.year, 7, 4), date(day.year, 12, 25)):
        if day == holiday - timedelta(days=1) and holiday.weekday() in (1, 2, 3, 4):
            return EARLY_CLOSE
    return REGULAR_CLOSE


def latest_completed_session(now: datetime) -> date:
    """The most recent trading day whose regular session has closed at ``now``."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    local = now.astimezone(NEW_YORK)
    candidate = local.date()
    if not is_trading_day(candidate) or local.time() < session_close(candidate):
        candidate -= timedelta(days=1)
    while not is_trading_day(candidate):
        candidate -= timedelta(days=1)
    return candidate


def latest_published_session(now: datetime) -> date:
    """The most recent trading day whose whole calendar day has ended in New York at ``now``.

    Massive's plan serves a day's bars only once that day is over: asking after the close but before
    midnight New York gets HTTP 403 "Attempted to request today's data before end of day" from the
    grouped endpoint and no bar from the per-symbol one. Jobs that fetch from Massive aim at this
    session, not :func:`latest_completed_session`. A bar's ``known_at`` is still its session close.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    candidate = now.astimezone(NEW_YORK).date() - timedelta(days=1)
    while not is_trading_day(candidate):
        candidate -= timedelta(days=1)
    return candidate
