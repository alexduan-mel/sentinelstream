"""Massive grouped-daily client and the ``ingest_market_bars`` handler. Massive is an ``httpx.MockTransport``."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import httpx
import pytest

from ingestion.http import Pacer
from ingestion.market_bars import history_start, ingest_market_bars, market_bar_rows, trading_days
from ingestion.market_calendar import NEW_YORK
from ingestion.massive_client import MassiveClient, MassiveConfigurationError, MassiveEntitlementError, MassiveError
from orchestration.task_runs import TaskRun, enqueue_run
from research.asof import AsOf

KEY = "test-key-not-real"
# Friday 27 Nov 2026, 20:00 UTC = 15:00 New York, after the 13:00 half-day close: target is 27 Nov.
NOW = datetime(2026, 11, 27, 20, 0, tzinfo=UTC)
TARGET = date(2026, 11, 27)
START = date(2024, 11, 27)
ALL_DAYS = trading_days(START, TARGET)


def _ms(day: date) -> int:
    return int(datetime.combine(day, time(0), tzinfo=NEW_YORK).timestamp() * 1000)


def _agg(symbol: str, day: date, close: float, **extra) -> dict:
    return {
        "T": symbol,
        "t": _ms(day),
        "o": close - 1,
        "h": close + 2,
        "l": close - 2,
        "c": close,
        "v": 1_000.5,
        "vw": close,
        "n": 40,
        **extra,
    }


class FakeMassive:
    """Serves every requested day with three symbols, except days listed as special."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.entitled_from = START
        self.unpublished: set[date] = set()  # no bars (holiday-like, or not yet published)
        self.failing: dict[date, httpx.Response] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        day = date.fromisoformat(request.url.path.rsplit("/", 1)[1])
        if day in self.failing:
            return self.failing[day]
        if day < self.entitled_from:
            return httpx.Response(
                403,
                json={
                    "status": "NOT_AUTHORIZED",
                    "message": "Attempted to request data past historical entitlements. Please upgrade your plan",
                },
            )
        if day in self.unpublished:
            return httpx.Response(200, json={"status": "DELAYED", "resultsCount": 0, "adjusted": False})
        results = [_agg("NVDA", day, 150.0), _agg("SCHWpD", day, 21.5), _agg("BRK.B", day, 480.0)]
        return httpx.Response(
            200, json={"status": "OK", "adjusted": False, "request_id": f"req-{day}", "results": results}
        )

    def client(self) -> MassiveClient:
        return MassiveClient(KEY, transport=httpx.MockTransport(self.handler), pacer=Pacer(0), base_delay=0)

    def days(self) -> list[date]:
        return [date.fromisoformat(r.url.path.rsplit("/", 1)[1]) for r in self.requests]


def _run(conn, **payload) -> TaskRun:
    run_id = enqueue_run(
        conn, "ingest_market_bars", trigger_source="test", run_mode="manual", requested_by="pytest", payload=payload
    )
    return TaskRun(run_id, "ingest_market_bars", "t", "test", "manual", "pytest", [], None, False, payload)


def _days_table(conn) -> dict[date, tuple]:
    with conn.cursor() as cursor:
        cursor.execute("SELECT trading_date, status, symbol_count, request_id FROM market_bar_days")
        rows = {day: rest for day, *rest in cursor.fetchall()}
    conn.commit()
    return {day: tuple(rest) for day, rest in rows.items()}


def _bar_count(conn) -> int:
    with conn.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM market_bars")
        (count,) = cursor.fetchone()
    conn.commit()
    return count


# --- client ----------------------------------------------------------------------------------------


def test_client_asks_for_as_traded_bars_and_redacts_the_key() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(401, text=f"bad key {KEY}")

    client = MassiveClient(KEY, transport=httpx.MockTransport(handler), pacer=Pacer(0), base_delay=0)
    with pytest.raises(MassiveError) as excinfo:
        client.grouped_daily(date(2026, 11, 25))
    assert seen[0].url.path == "/v2/aggs/grouped/locale/us/market/stocks/2026-11-25"
    assert seen[0].url.params["adjusted"] == "false"
    assert KEY not in str(excinfo.value)
    assert "Check MASSIVE_API_KEY" in str(excinfo.value)


def test_client_tells_a_day_before_the_plan_history_from_other_errors() -> None:
    fake = FakeMassive()
    with pytest.raises(MassiveEntitlementError):
        fake.client().grouped_daily(START - timedelta(days=1))

    fake.failing[TARGET] = httpx.Response(403, json={"status": "NOT_AUTHORIZED", "message": "Not entitled"})
    with pytest.raises(MassiveError) as excinfo:
        fake.client().grouped_daily(TARGET)
    assert not isinstance(excinfo.value, MassiveEntitlementError)


def test_client_rejects_split_adjusted_or_malformed_payloads() -> None:
    def respond(body) -> MassiveClient:
        return MassiveClient(
            KEY, transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=body)), pacer=Pacer(0)
        )

    with pytest.raises(MassiveError, match="split-adjusted"):
        respond({"status": "OK", "adjusted": True, "results": []}).grouped_daily(TARGET)
    with pytest.raises(MassiveError, match="status ERROR"):
        respond({"status": "ERROR"}).grouped_daily(TARGET)
    with pytest.raises(MassiveError, match="not a list"):
        respond({"status": "OK", "results": {"T": "NVDA"}}).grouped_daily(TARGET)
    assert respond({"status": "DELAYED", "resultsCount": 0}).grouped_daily(TARGET)["results"] == []


# --- parsing ---------------------------------------------------------------------------------------


def test_rows_keep_symbols_as_traded_and_drop_bad_aggregates() -> None:
    day = date(2026, 11, 25)
    payload = {
        "results": [
            _agg("NVDA", day, 149.3, v=366_571_889.978475),
            _agg("SCHWpD", day, 21.5),
            _agg("ABC", day, 10.0, vw=None, n=None),
            _agg("", day, 5.0),  # no symbol
            _agg("NOCL", day, 5.0, c=None),  # no close
            _agg("BAD", day, 5.0, h=1.0, l=2.0),  # high below low
            _agg("LATE", day + timedelta(days=1), 5.0),  # another day
            _agg("NVDA", day, 150.0),  # repeated symbol
            {"T": "JUNK", "t": "soon", "c": 1},
        ]
    }

    rows, invalid = market_bar_rows(payload, day)

    assert [(r[0], r[5]) for r in rows] == [("NVDA", 149.3), ("SCHWpD", 21.5), ("ABC", 10.0)]
    assert rows[0][6] == 366_571_889.978475  # fractional shares kept
    assert rows[2][7:] == (None, None)
    assert invalid == 6


def test_history_starts_two_years_back() -> None:
    assert history_start(date(2026, 11, 27)) == date(2024, 11, 27)
    assert history_start(date(2028, 2, 29)) == date(2026, 2, 28)


# --- the job ---------------------------------------------------------------------------------------


def test_runs_load_a_few_days_each_oldest_first_then_stop_asking(connect_app_db) -> None:
    conn = connect_app_db()
    fake = FakeMassive()
    with fake.client() as client:
        first = ingest_market_bars(_run(conn), conn, massive=client, now=NOW)
        assert first.status == "succeeded"
        assert fake.days() == ALL_DAYS[:5]
        assert first.meta["remaining_days"] == len(ALL_DAYS) - 5
        assert (first.fetched_count, first.inserted_count) == (15, 15)

        second = ingest_market_bars(_run(conn, max_days=3), conn, massive=client, now=NOW)
        assert fake.days()[5:] == ALL_DAYS[5:8]
        assert second.meta["loaded"] == [d.isoformat() for d in ALL_DAYS[5:8]]

        rest = ingest_market_bars(_run(conn, max_days=1000), conn, massive=client, now=NOW)
        assert rest.status == "succeeded"
        assert rest.meta["remaining_days"] == 0
        asked = len(fake.requests)

        current = ingest_market_bars(_run(conn), conn, massive=client, now=NOW)
    assert current.meta["message"] == "already_current"
    assert len(fake.requests) == asked  # no request once history is complete
    assert _bar_count(conn) == 3 * len(ALL_DAYS)
    assert _days_table(conn)[TARGET] == ("loaded", 3, f"req-{TARGET}")


def test_an_unclosed_session_is_never_requested(connect_app_db) -> None:
    conn = connect_app_db()
    fake = FakeMassive()
    midday = datetime(2026, 11, 25, 17, 0, tzinfo=UTC)  # 12:00 New York, Wednesday 25 Nov
    with fake.client() as client:
        ingest_market_bars(_run(conn, max_days=1000), conn, massive=client, now=midday)
    assert max(fake.days()) == date(2026, 11, 24)


def test_days_before_the_plan_history_are_recorded_unavailable_and_not_asked_again(connect_app_db) -> None:
    conn = connect_app_db()
    fake = FakeMassive()
    fake.entitled_from = date(2024, 12, 2)  # 27 and 29 Nov 2024 fall outside
    with fake.client() as client:
        result = ingest_market_bars(_run(conn, max_days=3), conn, massive=client, now=NOW)
        assert result.status == "succeeded"
        assert result.meta["unavailable"] == ["2024-11-27", "2024-11-29"]
        assert result.meta["loaded"] == ["2024-12-02"]

        ingest_market_bars(_run(conn, max_days=1), conn, massive=client, now=NOW)
    assert fake.days()[3:] == [date(2024, 12, 3)]
    assert _days_table(conn)[date(2024, 11, 27)] == ("unavailable", 0, None)


def test_an_old_day_without_bars_is_recorded_empty_but_a_recent_one_is_asked_again(connect_app_db) -> None:
    conn = connect_app_db()
    fake = FakeMassive()
    # 9 Jan 2025: markets closed for a national day of mourning, which the calendar does not model.
    mourning = date(2025, 1, 9)
    fake.unpublished = {mourning, TARGET}
    with fake.client() as client:
        result = ingest_market_bars(_run(conn, max_days=1000), conn, massive=client, now=NOW)
        assert result.status == "succeeded"
        assert result.meta["empty"] == [mourning.isoformat()]
        assert result.meta["waiting"] == [TARGET.isoformat()]
        assert result.meta["remaining_days"] == 1

        fake.unpublished = set()  # Massive publishes the day
        later = ingest_market_bars(_run(conn), conn, massive=client, now=NOW + timedelta(hours=3))
    assert fake.days()[-1] == TARGET
    assert later.meta["loaded"] == [TARGET.isoformat()]
    assert _days_table(conn)[mourning] == ("empty", 0, None)


def test_a_provider_error_stops_the_run_and_the_next_run_resumes(connect_app_db) -> None:
    conn = connect_app_db()
    fake = FakeMassive()
    fake.failing[ALL_DAYS[2]] = httpx.Response(500, text="boom")
    with fake.client() as client:
        result = ingest_market_bars(_run(conn), conn, massive=client, now=NOW)
        assert result.status == "partial"
        assert result.meta["loaded"] == [d.isoformat() for d in ALL_DAYS[:2]]
        assert "HTTP 500" in result.error_message
        assert ALL_DAYS[3] not in fake.days()  # stopped at the failing day

        fake.failing.clear()
        ingest_market_bars(_run(conn), conn, massive=client, now=NOW)
    assert fake.days()[-5:] == ALL_DAYS[2:7]


def test_a_first_day_error_fails_the_run(connect_app_db) -> None:
    conn = connect_app_db()
    fake = FakeMassive()
    fake.failing[ALL_DAYS[0]] = httpx.Response(429, headers={"Retry-After": "0"})
    with fake.client() as client:
        result = ingest_market_bars(_run(conn), conn, massive=client, now=NOW)
    assert result.status == "failed"
    assert _days_table(conn) == {}


def test_refetching_a_deleted_day_overwrites_without_duplicating(connect_app_db) -> None:
    conn = connect_app_db()
    fake = FakeMassive()
    with fake.client() as client:
        ingest_market_bars(_run(conn, max_days=2), conn, massive=client, now=NOW)
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM market_bar_days WHERE trading_date = %s", (ALL_DAYS[1],))
        conn.commit()
        assert _bar_count(conn) == 3  # its bars went with it

        again = ingest_market_bars(_run(conn, max_days=1), conn, massive=client, now=NOW)
    assert again.meta["loaded"] == [ALL_DAYS[1].isoformat()]
    assert _bar_count(conn) == 6


def test_settings_and_credentials_fail_clearly(connect_app_db, monkeypatch) -> None:
    conn = connect_app_db()
    with pytest.raises(ValueError, match="max_days"):
        ingest_market_bars(_run(conn, max_days=0), conn, massive=FakeMassive().client(), now=NOW)
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    with pytest.raises(MassiveConfigurationError):
        ingest_market_bars(_run(conn), conn, now=NOW)


# --- as-of access ----------------------------------------------------------------------------------


def test_market_bars_are_read_point_in_time_by_exact_symbol(connect_app_db) -> None:
    conn = connect_app_db()
    fake = FakeMassive()
    with fake.client() as client:
        ingest_market_bars(_run(conn, max_days=1000), conn, massive=client, now=NOW)

    close_25th = datetime(2026, 11, 25, 16, 0, tzinfo=NEW_YORK)
    at_close = AsOf(conn, close_25th).market_bars("NVDA", start=date(2026, 11, 20))
    assert [b.trading_date for b in at_close] == [date(2026, 11, 20), date(2026, 11, 23), date(2026, 11, 24)]
    after = AsOf(conn, close_25th + timedelta(microseconds=1)).market_bars("NVDA", start=date(2026, 11, 24))
    assert [b.trading_date for b in after] == [date(2026, 11, 24), date(2026, 11, 25)]
    assert after[-1].traded_value == pytest.approx(1_000.5 * 150.0)

    reader = AsOf(conn, NOW + timedelta(days=1))
    assert len(reader.market_bars("SCHWpD", start=date(2026, 11, 2))) == len(trading_days(date(2026, 11, 2), TARGET))
    assert reader.market_bars("SCHWPD") == []
