"""Journal scores (ADR 0026): where an entry starts and ends, its return against QQQ and SPY, and that
it never starts from a price already known when it was logged (ground rule 2)."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal

import pytest

from ingestion.market_calendar import NEW_YORK
from ledger.journal import Correction, Entry
from ledger.scoring import Series, score_entries, score_journal
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
