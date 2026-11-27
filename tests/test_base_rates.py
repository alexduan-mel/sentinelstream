"""The base-rate calculator: events, split-corrected returns, costs, filters and summaries.

Prices are built from formulas over the session index, so each expected result is written out.
"""

from __future__ import annotations

import dataclasses
import math
from datetime import UTC, date, datetime, timedelta

import pytest

from ingestion.market_bars import trading_days
from ingestion.market_calendar import NEW_YORK
from orchestration.task_runs import TaskRun, enqueue_run
from research.asof import AsOf, MarketBar, Split
from research.base_rates import MAIN, UNFILTERED, compute_base_rates, evaluate
from research.events import clean_symbol, insider_purchase_events
from research.returns import abdi_ranaldo_spread, entry_index, hold, split_factor
from research.stats import summarise

SESSIONS = trading_days(date(2026, 1, 5), date(2026, 4, 30))
FILED = date(2026, 2, 2)  # Monday; public from midnight after, so entry is Tuesday's close
E = SESSIONS.index(date(2026, 2, 3))
AFTER_ALL = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


# --- building data ---------------------------------------------------------------------------------


def _qqq(i: int) -> float:
    return 500 * 1.001**i


def _rising(base: float):
    return lambda i: base if i <= E else base * 1.01 ** (i - E)


def _bars(conn, symbol: str, close, *, volume: float = 100_000, band: float = 0.0, close_at_high: bool = False):
    rows = []
    for i, day in enumerate(SESSIONS):
        c = close(i)
        high, low = c * (1 + band), c * (1 - band)
        if close_at_high:  # the close is the day's high, with a 2 * band range below it
            high, low = c, c * (1 - 2 * band)
        rows.append((symbol, day, c, high, low, c, volume, c, 10))
    with conn.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO market_bar_days (trading_date, status) VALUES (%s, 'loaded') ON CONFLICT DO NOTHING",
            [(day,) for day in SESSIONS],
        )
        cursor.executemany(
            "INSERT INTO market_bars (symbol, trading_date, open, high, low, close, volume, vwap, transactions) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            rows,
        )
    conn.commit()


def _split(conn, symbol: str, executed: date, split_from: float, split_to: float) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            "INSERT INTO stock_splits (id, symbol, execution_date, split_from, split_to) VALUES (%s, %s, %s, %s, %s)",
            (f"{symbol}-{executed}", symbol, executed, split_from, split_to),
        )
    conn.commit()


_SERIAL = iter(range(1, 10_000))


def _purchase(
    conn,
    symbol: str,
    cik: str,
    *,
    filed: date = FILED,
    code: str = "P",
    acquired: str = "A",
    price: float = 20.0,
    shares: float = 1000.0,
    document_type: str = "4",
    plan: bool = False,
) -> str:
    accession = f"{cik}-26-{next(_SERIAL):06d}"
    with conn.cursor() as cursor:
        cursor.execute(
            "INSERT INTO insider_datasets (quarter, url, sha256, bytes, filing_count, transaction_count, "
            "invalid_count) VALUES ('2026q1', 'https://www.sec.gov/x', %s, 1, 0, 0, 0) ON CONFLICT DO NOTHING",
            ("0" * 64,),
        )
        cursor.execute(
            "INSERT INTO insider_filings (accession_no, document_type, filing_date, issuer_cik, issuer_name, "
            "issuer_symbol, owner_names, aff10b5one, dataset) "
            "VALUES (%s, %s, %s, %s, %s, %s, 'Doe Jane', %s, '2026q1')",
            (accession, document_type, filed, cik, f"{symbol} Inc", symbol, plan),
        )
        cursor.execute(
            "INSERT INTO insider_transactions (accession_no, line_sk, transaction_code, acquired_disposed, shares, "
            "price_per_share) VALUES (%s, 1, %s, %s, %s, %s)",
            (accession, code, acquired, shares, price),
        )
    conn.commit()
    return accession


def _market(conn) -> None:
    _bars(conn, "QQQ", _qqq, volume=10_000_000)
    _bars(conn, "SPY", lambda i: 600.0, volume=10_000_000)


def _expected_excess(h: int, cost: float = 0.002) -> float:
    return (1.01**h - 1) - cost - (_qqq(E + h) / _qqq(E) - 1)


# --- symbols and events ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("NYSE: KRC", "KRC"),
        ("(SIRI)", "SIRI"),
        ("HEI, HEI.A", "HEI"),
        ("N O G", "NOG"),
        ("NYSE/TRN", "TRN"),
        ("utgn", "UTGN"),
        ("BRK-B", "BRK.B"),
        ("NASDAQ:RMR", "RMR"),
        ("SMX; SMXWW", "SMX"),
        ("N/A", None),
        ("[NONE]", None),
        ("-", None),
        ("1314152", None),
        (None, None),
    ],
)
def test_clean_symbol(raw, expected) -> None:
    assert clean_symbol(raw) == expected


def test_purchase_events_are_one_per_company_and_day(connect_app_db) -> None:
    conn = connect_app_db()
    _purchase(conn, "AAA", "0000000001", shares=1000, price=20)
    _purchase(conn, "AAA", "0000000001", shares=500, price=20)  # a second insider, same day
    _purchase(conn, "AAA", "0000000001", filed=FILED + timedelta(days=1))
    _purchase(conn, "BBB", "0000000002", code="S", acquired="D")  # a sale
    _purchase(conn, "CCC", "0000000003", price=0)  # no price: not an open-market purchase
    _purchase(conn, "DDD", "0000000004", document_type="4/A")  # amendment
    _purchase(conn, "EEE", "0000000005", plan=True)  # 10b5-1 plan
    reader = AsOf(conn, AFTER_ALL)

    events = insider_purchase_events(reader)
    assert [(e.symbol, e.event_date, e.value) for e in events] == [
        ("AAA", FILED, 30_000.0),
        ("EEE", FILED, 20_000.0),
        ("AAA", FILED + timedelta(days=1), 20_000.0),
    ]
    assert events[0].known_at == datetime(2026, 2, 3, tzinfo=NEW_YORK)
    assert [e.symbol for e in insider_purchase_events(reader, exclude_10b5_1=True)] == ["AAA", "AAA"]
    # Not public yet a minute before midnight after the filing date.
    assert insider_purchase_events(AsOf(conn, datetime(2026, 2, 2, 23, 59, tzinfo=NEW_YORK))) == []


# --- returns ---------------------------------------------------------------------------------------


def _mb(day: date, close: float, high: float | None = None, low: float | None = None) -> MarketBar:
    known = datetime.combine(day, datetime.min.time(), tzinfo=NEW_YORK) + timedelta(hours=16)
    return MarketBar("X", day, close, high or close, low or close, close, 1.0, close, 1, known)


def test_entry_is_the_first_close_after_the_event() -> None:
    days = SESSIONS[:3]
    closes = [datetime.combine(d, datetime.min.time(), tzinfo=NEW_YORK) + timedelta(hours=16) for d in days]
    assert entry_index(days, closes, closes[0] - timedelta(seconds=1)) == 0
    assert entry_index(days, closes, closes[0]) == 1  # at the close itself: too late for that close
    assert entry_index(days, closes, closes[2]) is None


def test_a_split_is_not_a_loss_and_a_vanished_stock_exits_at_its_last_close() -> None:
    days = SESSIONS[:6]
    bars = [_mb(days[0], 400), _mb(days[1], 404), _mb(days[2], 101), _mb(days[3], 105)]  # 4-for-1 on day 2
    splits = [Split("X", days[2], 1, 4, datetime.combine(days[2], datetime.min.time(), tzinfo=NEW_YORK))]
    by_date = {b.trading_date: b for b in bars}
    dates = [b.trading_date for b in bars]

    assert split_factor(splits, days[0], days[3]) == 4.0
    assert split_factor(splits, days[2], days[3]) == 1.0  # bought on the split day: already new shares
    leg = hold(by_date, dates, splits, days[0], days[3])
    assert leg.gross_return == pytest.approx(105 * 4 / 400 - 1)  # +5%, not -74%
    assert not leg.ended_early

    gone = hold(by_date, dates, splits, days[0], days[5])  # no bars after day 3
    assert (gone.exit_date, gone.ended_early) == (days[3], True)
    assert hold(by_date, dates, splits, days[4], days[5]) is None  # no bar to buy at


def test_spread_estimate() -> None:
    days = SESSIONS[:10]
    # Closing at the day's high with the same range every day: the estimate is log(high / low).
    wide = [_mb(d, 102.0, high=102.0, low=98.0) for d in days]
    assert abdi_ranaldo_spread(wide, []) == pytest.approx(math.log(102 / 98))
    assert abdi_ranaldo_spread([_mb(d, 100.0) for d in days], []) == 0.0
    assert abdi_ranaldo_spread(wide[:5], []) is None  # four pairs are too few


# --- statistics ------------------------------------------------------------------------------------


def test_summary_and_a_cluster_range_only_with_enough_months() -> None:
    few = summarise([0.01, -0.02, 0.03], ["2026-01", "2026-01", "2026-02"])
    assert (few.n, few.median, few.hit_rate) == (3, 0.01, pytest.approx(2 / 3))
    assert few.mean == pytest.approx(0.02 / 3)
    assert few.ci_low is None and few.clusters == 2

    values = [0.01 * (i % 5 - 1) for i in range(60)]
    months = [f"2025-{i % 12 + 1:02d}" for i in range(60)]
    summary = summarise(values, months)
    assert summary.ci_low <= summary.mean <= summary.ci_high
    assert summarise(values, months) == summary  # fixed seed
    assert summarise([], []) is None
    with pytest.raises(ValueError):
        summarise([1.0], [])


# --- the calculator end to end ---------------------------------------------------------------------


def test_evaluate_reports_excess_returns_after_costs_with_filters_side_by_side(connect_app_db) -> None:
    conn = connect_app_db()
    _market(conn)
    _bars(conn, "AAA", _rising(20.0))  # $2M a day
    _bars(conn, "PENY", lambda i: 2.0, volume=10_000_000)  # under $5
    _bars(conn, "THIN", _rising(20.0), volume=1_000)  # $20k a day
    split_day = SESSIONS[E + 3]
    _bars(conn, "SPLT", lambda i: (40.0 if i <= E else 40.0 * 1.01 ** (i - E)) / (2 if i >= E + 3 else 1))
    _split(conn, "SPLT", split_day, 1, 2)
    _bars(conn, "WIDE", _rising(20.0), band=0.02, close_at_high=True)
    for symbol, cik in (
        ("AAA", "1"),
        ("PENY", "2"),
        ("THIN", "3"),
        ("SPLT", "4"),
        ("WIDE", "5"),
        ("N/A", "6"),
        ("ZZZZ", "7"),
    ):
        _purchase(conn, symbol, cik.zfill(10))
    reader = AsOf(conn, AFTER_ALL)

    report = evaluate(reader, insider_purchase_events(reader), (MAIN, UNFILTERED))

    assert (report["events"], report["matched_to_prices"]) == (7, 5)
    main, none = report["variants"]["main"], report["variants"]["none"]
    assert main["skipped"] == {"below_min_price": 1, "below_min_traded_value": 1, "no_price_data": 1, "no_symbol": 1}
    assert none["skipped"] == {"no_price_data": 1, "no_symbol": 1}

    five = main["horizons"]["5"]  # AAA, SPLT and WIDE
    assert five["n"] == 3
    wide_cost = 2 * math.log(1 / (1 - 0.04)) / 2  # half the estimated spread, each way
    expected = [_expected_excess(5), _expected_excess(5), _expected_excess(5, cost=wide_cost)]
    assert five["mean"] == pytest.approx(sum(expected) / 3, abs=1e-6)
    assert five["median"] == pytest.approx(_expected_excess(5), abs=1e-6)
    assert five["mean_cost"] == pytest.approx((0.002 + 0.002 + wide_cost) / 3, abs=1e-6)
    assert five["mean_excess_vs_reference"] == pytest.approx(
        sum(e + (_qqq(E + 5) / _qqq(E) - 1) for e in expected) / 3, abs=1e-6
    )
    assert five["hit_rate"] == 1.0 and five["ci_low"] is None and five["ended_early"] == 0
    assert none["horizons"]["5"]["n"] == 5  # PENY and THIN too


def test_results_wait_until_the_horizon_has_closed(connect_app_db) -> None:
    conn = connect_app_db()
    _market(conn)
    _bars(conn, "AAA", _rising(20.0))
    _purchase(conn, "AAA", "0000000001")
    # Dated the morning after the 20th session: 1, 5 and 20 sessions have closed, 60 have not.
    as_of = datetime.combine(SESSIONS[E + 21], datetime.min.time(), tzinfo=NEW_YORK)
    reader = AsOf(conn, as_of)

    report = evaluate(reader, insider_purchase_events(reader))

    horizons = report["variants"]["main"]["horizons"]
    assert [horizons[h]["n"] if horizons[h] else 0 for h in ("1", "5", "20", "60")] == [1, 1, 1, 0]
    assert report["variants"]["main"]["skipped"] == {"horizon_60_not_reached": 1}
    assert horizons["20"]["mean"] == pytest.approx(_expected_excess(20), abs=1e-6)


def test_the_task_records_the_report_dated_as_asked(connect_app_db) -> None:
    conn = connect_app_db()
    _market(conn)
    _bars(conn, "AAA", _rising(20.0))
    _purchase(conn, "AAA", "0000000001")
    run_id = enqueue_run(conn, "compute_base_rates", trigger_source="test", run_mode="manual", requested_by="pytest")
    conn.commit()
    run = TaskRun(
        run_id,
        "compute_base_rates",
        "t",
        "test",
        "manual",
        "pytest",
        [],
        date(2026, 2, 3),
        False,
        {"event": "insider_purchases"},
    )

    result = compute_base_rates(run, conn)

    assert result.status == "succeeded"
    report = result.meta["report"]
    assert report["as_of"] == "2026-02-03T00:00:00-05:00"
    assert report["events"] == 0  # public at midnight starting 3 Feb: not before it

    later = compute_base_rates(dataclasses.replace(run, as_of_date=None, payload={}), conn, now=AFTER_ALL)
    assert later.meta["report"]["variants"]["main"]["horizons"]["1"]["n"] == 1
    with pytest.raises(ValueError, match="event must be one of"):
        compute_base_rates(dataclasses.replace(run, payload={"event": "earnings"}), conn, now=AFTER_ALL)
