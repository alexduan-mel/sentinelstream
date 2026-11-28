"""Journal scores (ADR 0026): where an entry starts and ends, its return against QQQ and SPY, and that
it never starts from a price already known when it was logged (ground rule 2)."""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest

from ingestion.market_calendar import NEW_YORK
from ledger.journal import Correction, Entry
from ledger.scoring import Series, combine, fit, money, score_entries, score_journal
from research.asof import Bar, Split

# Mon 16 Nov to Fri 20 Nov 2026, then Mon 23 Nov.
DAYS = [date(2026, 11, d) for d in (16, 17, 18, 19, 20, 23)]


def et(day: date, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute, second), NEW_YORK)


def bars(symbol: str, closes: list[float], opens: list[float] | None = None, days=DAYS) -> list[Bar]:
    opens = opens or closes
    return [
        Bar(symbol, day, Decimal(str(o)), Decimal(str(max(o, c) * 1.02)), Decimal(str(min(o, c) * 0.98)),
            Decimal(str(c)), 1000, "ok", et(day, 16))
        for day, o, c in zip(days, opens, closes, strict=True)
    ]  # fmt: skip


def series(nvda_splits: list[Split] | None = None, **overrides) -> dict[str, Series]:
    data = {
        "NVDA": Series(bars("NVDA", [100, 102, 104, 106, 108, 110], opens=[99, 101, 103, 105, 107, 109]),
                       nvda_splits or []),
        "QQQ": Series(bars("QQQ", [500, 505, 510, 515, 520, 525], opens=[498, 503, 508, 513, 518, 523]), []),
        "SPY": Series(bars("SPY", [600, 600, 603, 606, 609, 612]), []),
    }  # fmt: skip
    data.update(overrides)
    return data


def entry(id: int, action: str, logged: datetime, **extra) -> Entry:
    fields = dict(
        id=id, symbol="NVDA", name="NVIDIA CORP", action=action, thesis="t", source=None, original_thesis="t",
        original_source=None, traded_on=None, price=None, shares=None, known_at=logged, recorded_by="admin",
        withdrawn=None, corrections=[],
    )  # fmt: skip
    fields.update(extra)
    return Entry(**fields)


WITHDRAWN = Correction("withdrawn", None, None, "Wrong ticker", et(DAYS[-1], 20), "admin")


def test_an_entry_starts_at_the_first_close_after_it_was_logged() -> None:
    before_close = entry(1, "opened", et(DAYS[0], 15, 59, 59))
    at_close = entry(2, "pass", et(DAYS[0], 16, 0, 0))
    after_close = entry(3, "pass", et(DAYS[0], 16, 0, 1))
    scores = score_entries([before_close, at_close, after_close], series())

    assert scores[1].start_date == DAYS[0] and scores[1].start_price == 100
    # A bar is public at its close and the comparison is strict: logged at 16:00:00, it starts the next day.
    assert scores[2].start_date == DAYS[1] and scores[2].start_price == 102
    assert scores[3].start_date == DAYS[1]
    assert {s.start_basis for s in scores.values()} == {"next_close"}


def test_returns_against_qqq_and_spy_over_the_same_days() -> None:
    [score] = score_entries([entry(1, "opened", et(DAYS[0], 10))], series()).values()

    assert (score.status, score.end_date, score.ends, score.sessions) == ("scored", DAYS[-1], "latest", 5)
    assert score.stock_return == pytest.approx(0.10)  # 100 -> 110
    assert score.qqq_return == pytest.approx(0.05)  # 500 -> 525
    assert score.spy_return == pytest.approx(0.02)  # 600 -> 612
    assert score.vs_qqq == pytest.approx(0.05)
    assert score.vs_spy == pytest.approx(0.08)


def test_a_trade_starts_at_its_price_and_the_benchmarks_at_that_days_open() -> None:
    trade = entry(1, "opened", et(DAYS[-1], 20), traded_on=DAYS[2], price=103.5)
    [score] = score_entries([trade], series()).values()

    assert (score.start_date, score.start_basis, score.price_check) == (DAYS[2], "trade", "ok")
    assert score.start_price == 103.5
    assert score.stock_return == pytest.approx(110 / 103.5 - 1)
    assert score.qqq_return == pytest.approx(525 / 508 - 1)  # from QQQ's open on the trade day
    assert score.sessions == 3


def test_a_trade_price_before_a_later_split_is_moved_to_todays_share_basis() -> None:
    """A 2-for-1 split on Fri 20 Nov: stored bars are adjusted, so 207 paid on Wed is 103.5 today."""
    split = Split("NVDA", DAYS[4], 1.0, 2.0, et(DAYS[4], 0))
    trade = entry(1, "opened", et(DAYS[-1], 20), traded_on=DAYS[2], price=207.0)
    [score] = score_entries([trade], series([split])).values()

    assert (score.price_check, score.start_price) == ("ok", 103.5)


def test_a_price_outside_the_days_range_falls_back_to_the_next_close() -> None:
    """A trade typed for the day it was logged is checked once that day's bar arrives."""
    trade = entry(1, "opened", et(DAYS[2], 11), traded_on=DAYS[2], price=150.0)
    [score] = score_entries([trade], series()).values()

    assert (score.price_check, score.start_basis, score.start_date) == ("out_of_range", "next_close", DAYS[2])
    assert score.start_price == 104


def test_waiting_until_there_is_a_close_to_start_from() -> None:
    late = entry(1, "pass", et(DAYS[-1], 17))
    unchecked = entry(2, "opened", et(DAYS[-1], 12), traded_on=date(2026, 11, 24), price=110.0)
    scores = score_entries([late, unchecked], series())

    assert scores[1].status == "waiting" and scores[1].stock_return is None
    assert scores[2].status == "waiting"


def test_opened_and_added_end_where_the_closed_entry_starts() -> None:
    opened = entry(1, "opened", et(DAYS[0], 10))
    added = entry(2, "added", et(DAYS[1], 10))
    trimmed = entry(3, "trimmed", et(DAYS[2], 10))
    closed = entry(4, "closed", et(DAYS[3], 12), traded_on=DAYS[3], price=105.5)
    passed = entry(5, "pass", et(DAYS[3], 13))
    scores = score_entries([opened, added, trimmed, closed, passed], series())

    for held in (1, 2):
        assert (scores[held].ends, scores[held].closed_by, scores[held].end_date) == ("closed", 4, DAYS[3])
        assert scores[held].end_price == 105.5
    assert scores[1].qqq_return == pytest.approx(513 / 500 - 1)  # to QQQ's open on the Closed trade day
    # Trimmed, Closed and Pass run to the latest close.
    assert {scores[i].ends for i in (3, 4, 5)} == {"latest"}
    assert scores[4].start_price == 105.5 and scores[4].end_date == DAYS[-1]


def test_withdrawn_entries_are_not_scored_and_do_not_end_a_position() -> None:
    opened = entry(1, "opened", et(DAYS[0], 10))
    closed = entry(2, "closed", et(DAYS[2], 10), withdrawn=WITHDRAWN)
    scores = score_entries([opened, closed], series())

    assert 2 not in scores
    assert (scores[1].ends, scores[1].end_date) == ("latest", DAYS[-1])


def test_a_ticker_without_bars_waits_and_a_missing_spy_leaves_only_spy_out() -> None:
    no_spy = {k: v for k, v in series().items() if k != "SPY"}
    [score] = score_entries([entry(1, "opened", et(DAYS[0], 10))], no_spy).values()
    assert score.status == "scored" and score.spy_return is None and score.vs_spy is None

    [waiting] = score_entries([entry(1, "opened", et(DAYS[0], 10), symbol="AMZN")], series()).values()
    assert waiting.status == "waiting"


# --- Against Postgres: the as-of layer decides which bars a score may use. ---


@pytest.fixture
def stored_bars(connect_app_db):
    conn = connect_app_db()
    with conn.cursor() as cursor:
        for symbol, closes in (("NVDA", [100, 102, 104, 106, 108, 110]), ("QQQ", [500, 505, 510, 515, 520, 525])):
            for day, close in zip(DAYS, closes, strict=True):
                cursor.execute(
                    """
                    INSERT INTO price_bars (instrument_id, trading_date, open, high, low, close, provider)
                    SELECT id, %s, %s, %s, %s, %s, 'massive' FROM instruments WHERE symbol = %s
                    """,
                    (day, close, close * 1.02, close * 0.98, close, symbol),
                )
    conn.commit()
    yield conn
    conn.close()


def test_a_score_uses_only_bars_public_before_the_moment_it_is_worked_out(stored_bars) -> None:
    logged = entry(1, "opened", et(DAYS[0], 16, 30))  # after Monday's close: starts Tuesday

    # Tuesday 16:00 has not happened yet at 15:59: nothing to start from.
    assert score_journal(stored_bars, [logged], et(DAYS[1], 15, 59))[1].status == "waiting"
    # At Tuesday's close the bar is still not visible (strict); a second later it is, and ends there too.
    assert score_journal(stored_bars, [logged], et(DAYS[1], 16))[1].status == "waiting"
    started = score_journal(stored_bars, [logged], et(DAYS[1], 16, 0, 1))[1]
    assert (started.start_date, started.end_date, started.stock_return) == (DAYS[1], DAYS[1], 0.0)
    # On Thursday evening it runs to Thursday's close, never to a later bar already stored.
    thursday = score_journal(stored_bars, [logged], et(DAYS[3], 18))[1]
    assert thursday.end_date == DAYS[3]
    assert thursday.stock_return == pytest.approx(106 / 102 - 1)
    assert thursday.qqq_return == pytest.approx(515 / 505 - 1)
    assert thursday.spy_return is None  # no SPY bars stored


def test_the_journal_api_carries_each_entrys_score(signed_in_client, stored_bars) -> None:
    with stored_bars.cursor() as cursor:
        cursor.execute(
            "UPDATE instruments SET onboarding_status = 'ready', filer_type = 'domestic', cik = '0001045810' "
            "WHERE symbol = 'NVDA'"
        )
    stored_bars.commit()

    response = signed_in_client.post(
        "/api/journal",
        json={"symbol": "NVDA", "action": "opened", "thesis": "t", "traded_on": "2026-11-18", "price": 103.0},
    )
    assert response.status_code == 201, response.text
    score = response.json()["score"]
    assert (score["status"], score["start_basis"], score["start_date"]) == ("scored", "trade", "2026-11-18")
    assert score["stock_return"] == pytest.approx(110 / 103 - 1)
    assert score["qqq_return"] == pytest.approx(525 / 510 - 1)  # open = close in these bars

    passed = signed_in_client.post("/api/journal", json={"symbol": "NVDA", "action": "pass", "thesis": "t"})
    assert passed.json()["score"]["status"] == "waiting"  # logged now: no close after it yet

    withdrawn = signed_in_client.post(
        f"/api/journal/{passed.json()['id']}/corrections", json={"kind": "withdrawn", "reason": "x"}
    ).json()
    assert withdrawn["score"] is None
    entries = signed_in_client.get("/api/journal").json()["entries"]
    assert [e["score"] is None for e in entries] == [True, False]
    journal = signed_in_client.get("/api/journal").json()
    assert journal["combined"]["scored"] == 1 and journal["combined"]["too_early"] == 1
    assert journal["entries"][1]["score"]["fit"] == "too_early"
    assert "daily" not in journal["entries"][1]["score"]
    assert journal["money"]["excluded"] == 1  # the Opened gave no share count
    assert journal["money"]["positions"][0]["excluded"] == "no_shares"


# --- Alpha and beta (ADR 0027) ---


def _sessions(start: date, count: int) -> list[date]:
    days, day = [], start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def _paths(qqq_daily: list[float], beta: float, extra: float) -> tuple[list[float], list[float]]:
    """Closes for QQQ from its daily returns, and a stock whose daily return is beta x QQQ's + extra."""
    qqq, stock = [500.0], [100.0]
    for r in qqq_daily:
        qqq.append(qqq[-1] * (1 + r))
        stock.append(stock[-1] * (1 + beta * r + extra))
    return stock, qqq


QQQ_DAILY = [0.01, -0.008, 0.004, 0.012, -0.015, 0.003, 0.007, -0.002, 0.009, -0.011] * 3  # 30 days
LONG = _sessions(date(2026, 9, 1), len(QQQ_DAILY) + 1)


def long_series(beta: float = 1.5, extra: float = 0.001) -> dict[str, Series]:
    stock, qqq = _paths(QQQ_DAILY, beta, extra)
    return {
        "NVDA": Series(bars("NVDA", stock, days=LONG), []),
        "QQQ": Series(bars("QQQ", qqq, days=LONG), []),
    }


def test_fit_finds_the_slope_and_how_well_it_explains() -> None:
    exact = fit([(0.001 + 1.5 * q, q) for q in QQQ_DAILY])
    assert exact.beta == pytest.approx(1.5) and exact.r_squared == pytest.approx(1.0)
    assert fit([(0.01, 0.0), (0.02, 0.0), (0.0, 0.0)]) is None  # QQQ never moved
    assert fit([(0.01, 0.01)]) is None


def test_alpha_and_beta_split_the_return() -> None:
    [score] = score_entries([entry(1, "opened", et(LONG[0], 10))], long_series()).values()

    assert (score.fit, score.days, score.sessions) == ("ok", 30, 30)
    assert score.beta == pytest.approx(1.5)
    assert score.r_squared == pytest.approx(1.0)
    assert score.market_part == pytest.approx(1.5 * score.qqq_return)
    assert score.alpha == pytest.approx(score.stock_return - score.market_part)
    assert score.alpha > 0.02  # 0.1% a day beyond QQQ's pull, for 30 days
    # The daily pairs compound to the score's own returns.
    assert math.prod(1 + s for s, _ in score.daily) - 1 == pytest.approx(score.stock_return)
    assert math.prod(1 + q for _, q in score.daily) - 1 == pytest.approx(score.qqq_return)


def test_a_trade_start_and_a_closed_end_are_in_the_daily_pairs() -> None:
    data = long_series()
    trade_day, close_day = LONG[2], LONG[-3]
    opened = entry(1, "opened", et(LONG[-1], 20), traded_on=trade_day, price=float(data["NVDA"].on(trade_day).low))
    closed = entry(2, "closed", et(LONG[-1], 20, 1), traded_on=close_day,
                   price=float(data["NVDA"].on(close_day).high))  # fmt: skip
    score = score_entries([opened, closed], data)[1]

    assert (score.ends, score.end_date) == ("closed", close_day)
    assert math.prod(1 + s for s, _ in score.daily) - 1 == pytest.approx(score.stock_return)
    assert math.prod(1 + q for _, q in score.daily) - 1 == pytest.approx(score.qqq_return)


def test_too_early_under_twenty_daily_returns() -> None:
    [score] = score_entries([entry(1, "opened", et(DAYS[0], 10))], series()).values()
    assert (score.fit, score.days) == ("too_early", 5)
    assert score.beta is None and score.alpha is None and score.market_part is None
    assert score.stock_return == pytest.approx(0.10)  # the plain return is still given


def test_combined_signs_each_call_and_fits_beta_on_what_was_held() -> None:
    data = long_series(beta=1.5, extra=0.001)
    opened = entry(1, "opened", et(LONG[0], 10))
    passed = entry(2, "pass", et(LONG[0], 11))
    early = entry(3, "trimmed", et(LONG[-4], 10))
    withdrawn = entry(4, "pass", et(LONG[0], 12), withdrawn=WITHDRAWN)
    entries = [opened, passed, early, withdrawn]
    scores = score_entries(entries, data)
    together = combine(entries, scores)

    alpha = scores[1].alpha
    assert scores[2].alpha == pytest.approx(alpha)  # same stock, same days
    # The Opened was right (positive alpha); the Pass on the same move was wrong, so they cancel.
    assert (together.scored, together.fitted, together.too_early) == (3, 2, 1)
    assert together.alpha_of_calls == pytest.approx(0.0)
    assert together.calls_right == 1
    assert together.beta_of_holdings == pytest.approx(1.5)  # from the Opened only
    assert together.holdings_days == 30


def test_combined_with_nothing_fitted() -> None:
    together = combine([], {})
    assert (together.scored, together.alpha_of_calls, together.beta_of_holdings) == (0, None, None)


# --- The money view (ADR 0028) ---


def test_a_position_in_dollars_against_the_same_dollars_in_qqq() -> None:
    opened = entry(1, "opened", et(DAYS[-1], 20), traded_on=DAYS[2], price=103.5, shares=10)  # QQQ open 508
    added = entry(2, "added", et(DAYS[3], 10), shares=5)  # Thursday's close: 106, QQQ 515
    trimmed = entry(3, "trimmed", et(DAYS[4], 10), shares=6)  # Friday's close: 108, QQQ 520
    passed = entry(4, "pass", et(DAYS[4], 11))
    entries = [opened, added, trimmed, passed]
    data = series()
    view = money(entries, score_entries(entries, data), data)

    [position] = view.positions
    assert (position.symbol, position.opened_id, position.status, position.excluded) == ("NVDA", 1, "open", None)
    assert (position.start_date, position.end_date) == (DAYS[2], DAYS[-1])
    assert position.bought == pytest.approx(10 * 103.5 + 5 * 106)
    assert position.sold == pytest.approx(6 * 108)
    assert position.value == pytest.approx(9 * 110)  # latest close
    assert position.profit == pytest.approx(6 * 108 + 9 * 110 - 1565)
    units = 1035 / 508 + 530 / 515
    assert position.qqq_profit == pytest.approx(units * 0.4 * 520 + units * 0.6 * 525 - 1565)
    assert (view.bought, view.profit, view.qqq_profit, view.excluded) == pytest.approx(
        (1565, position.profit, position.qqq_profit, 0)
    )


def test_share_counts_before_a_split_are_moved_to_todays_basis() -> None:
    split = Split("NVDA", DAYS[4], 1.0, 2.0, et(DAYS[4], 0))
    opened = entry(1, "opened", et(DAYS[-1], 20), traded_on=DAYS[2], price=207.0, shares=10)
    closed = entry(2, "closed", et(DAYS[-1], 20, 1), traded_on=DAYS[-1], price=109.0, shares=20)
    entries = [opened, closed]
    data = series([split])
    [position] = money(entries, score_entries(entries, data), data).positions

    assert position.status == "closed" and position.end_date == DAYS[-1]
    assert position.bought == pytest.approx(2070)  # 10 x 207 as traded
    assert position.sold == pytest.approx(20 * 109)
    assert position.value == 0 and position.profit == pytest.approx(2180 - 2070)


def test_positions_without_share_counts_or_prices_are_left_out_of_the_totals() -> None:
    no_shares = [entry(1, "opened", et(DAYS[0], 10), shares=10), entry(2, "added", et(DAYS[1], 10))]
    waiting = [entry(3, "opened", et(DAYS[-1], 17), symbol="AMZN", shares=3)]
    withdrawn = [entry(4, "opened", et(DAYS[0], 10), symbol="GOOGL", shares=1, withdrawn=WITHDRAWN)]
    entries = [*no_shares, *waiting, *withdrawn]
    data = series()
    view = money(entries, score_entries(entries, data), data)

    assert sorted((p.symbol, p.excluded) for p in view.positions) == [("AMZN", "waiting"), ("NVDA", "no_shares")]
    assert (view.bought, view.profit, view.qqq_profit, view.excluded) == (0, 0, 0, 2)
