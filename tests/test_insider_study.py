"""Control groups for the insider-buying study (research/insider_study.py)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from ingestion.market_calendar import NEW_YORK
from research.base_rates import Outcome
from research.events import Event
from research.insider_study import GAP_DAYS, difference, same_date_controls, same_stock_controls

SESSIONS = [date(2025, 1, 1) + timedelta(days=i) for i in range(365)]


def _event(symbol: str, day: date) -> Event:
    return Event(
        issuer_cik=f"{sum(map(ord, symbol)):010d}",
        symbol=symbol,
        event_date=day,
        known_at=datetime(day.year, day.month, day.day, 23, tzinfo=NEW_YORK),
        label="buy",
        value=100_000.0,
    )


EVENTS = [
    _event("AAA", date(2025, 3, 1)),
    _event("AAA", date(2025, 6, 1)),
    _event("BBB", date(2025, 3, 1)),
    _event("CCC", date(2025, 9, 1)),
    _event("DDD", date(2025, 11, 1)),
]


def test_same_stock_controls_avoid_the_stocks_own_buys_and_stay_in_the_event_span():
    controls = same_stock_controls(EVENTS, SESSIONS, per_event=3)
    assert len(controls) == 3 * len(EVENTS)
    buys = {}
    for e in EVENTS:
        buys.setdefault(e.symbol, []).append(e.event_date)
    for c in controls:
        assert all(abs((c.event_date - d).days) >= GAP_DAYS for d in buys[c.symbol])
        assert date(2025, 3, 1) <= c.event_date <= date(2025, 11, 1)
        # Known at midnight New York after the session, like the insider datasets.
        assert c.known_at == datetime.combine(c.event_date + timedelta(days=1), datetime.min.time(), tzinfo=NEW_YORK)


def test_same_date_controls_pick_other_stocks_without_a_buy_near_that_date():
    controls = same_date_controls(EVENTS, per_event=2)
    by_date = {}
    for c in controls:
        by_date.setdefault(c.event_date, []).append(c.symbol)
    # On 1 March both AAA and BBB bought, so only CCC and DDD can stand in.
    assert sorted(by_date[date(2025, 3, 1)]) == ["CCC", "CCC", "DDD", "DDD"]
    for c in controls:
        assert c.label == "control"
        assert all(abs((c.event_date - e.event_date).days) >= GAP_DAYS for e in EVENTS if e.symbol == c.symbol)


def test_controls_are_the_same_on_every_run():
    assert same_stock_controls(EVENTS, SESSIONS) == same_stock_controls(EVENTS, SESSIONS)
    assert same_date_controls(EVENTS) == same_date_controls(EVENTS)


def _outcome(month: int, gross: float) -> Outcome:
    entry = date(2025, month, 2)
    return Outcome(
        event=_event("AAA", entry),
        horizon=20,
        entry_date=entry,
        exit_date=entry + timedelta(days=28),
        gross_return=gross,
        cost=0.002,
        benchmark_return=0.01,
        reference_return=None,
        ended_early=False,
    )


def test_difference_cancels_the_market_moves_both_groups_share():
    # Each month moves both groups by the same amount; insider buys are always 1 point ahead.
    swings = [0.05, -0.04, 0.10, -0.08, 0.02, 0.07, -0.06]
    treated = [_outcome(m + 1, s + 0.01) for m, s in enumerate(swings)]
    control = [_outcome(m + 1, s) for m, s in enumerate(swings) for _ in range(3)]
    result = difference(treated, control, lambda o: o.gross_return - o.benchmark_return)
    assert abs(result["diff"] - 0.01) < 1e-12
    assert abs(result["ci_low"] - 0.01) < 1e-12 and abs(result["ci_high"] - 0.01) < 1e-12
    assert result["months"] == 7
