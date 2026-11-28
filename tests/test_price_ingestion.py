"""Massive client and the ``ingest_prices`` handler. Massive is an ``httpx.MockTransport``."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import httpx
import pytest

from ingestion.http import Pacer
from ingestion.market_calendar import NEW_YORK, is_trading_day
from ingestion.massive_client import (
    MassiveClient,
    MassiveConfigurationError,
    MassiveError,
    api_key_from_env,
    rate_limit_from_env,
)
from ingestion.price_bars import bar_rows, ingest_prices
from orchestration.task_runs import TaskRun, enqueue_run

KEY = "test-key-not-real"
# Thursday 26 Nov 2026, 10:00 UTC = 05:00 New York, the morning after Wednesday 25 Nov ended: the
# target session is 25 Nov (Massive serves a day only once it has ended in New York).
NOW = datetime(2026, 11, 26, 10, 0, tzinfo=UTC)
TARGET = date(2026, 11, 25)


def _ms(day: date) -> int:
    return int(datetime.combine(day, time(0), tzinfo=NEW_YORK).timestamp() * 1000)


def _bar(day: date, close: float = 100.0) -> dict:
    return {"t": _ms(day), "o": close - 1, "h": close + 2, "l": close - 2, "c": close, "v": 1_234_567.0}


class FakeMassive:
    """Serves bars for every trading day in the requested range, including an unclosed session."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.failing: dict[str, httpx.Response] = {}
        self.no_data: set[str] = set()
        # Ticker details by symbol (``/v3/reference/tickers/{sym}``); a symbol not listed has none.
        self.details: dict[str, dict] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        parts = request.url.path.split("/")  # /v2/aggs/ticker/{sym}/range/1/day/{from}/{to}
        if parts[1:4] == ["v3", "reference", "tickers"]:
            return httpx.Response(200, json={"status": "OK", "results": self.details.get(parts[4], {})})
        symbol, start, end = parts[4], date.fromisoformat(parts[8]), date.fromisoformat(parts[9])
        if symbol in self.failing:
            return self.failing[symbol]
        days = [] if symbol in self.no_data else [start + timedelta(n) for n in range((end - start).days + 2)]
        # The extra day past ``end`` stands in for a provider returning more than asked.
        results = [_bar(day) for day in days if is_trading_day(day)]
        return httpx.Response(200, json={"status": "OK", "request_id": "r1", "adjusted": True, "results": results})

    def client(self) -> MassiveClient:
        return MassiveClient(KEY, transport=httpx.MockTransport(self.handler), pacer=Pacer(0), base_delay=0)

    def symbols(self) -> list[str]:
        """Symbols whose bars were requested."""
        return [request.url.path.split("/")[4] for request in self.requests if "/aggs/" in request.url.path]


# --- client ----------------------------------------------------------------------------------------


def test_settings_fail_clearly(monkeypatch) -> None:
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    with pytest.raises(MassiveConfigurationError, match="MASSIVE_API_KEY is not set"):
        api_key_from_env()
    monkeypatch.setenv("MASSIVE_RATE_LIMIT_PER_MINUTE", "0")
    with pytest.raises(MassiveConfigurationError, match="positive integer"):
        rate_limit_from_env()
    monkeypatch.delenv("MASSIVE_RATE_LIMIT_PER_MINUTE")
    assert rate_limit_from_env() == 5


def test_client_requests_adjusted_bars_and_redacts_the_key_from_errors() -> None:
    fake = FakeMassive()
    with fake.client() as client:
        payload = client.daily_bars("NVDA", date(2026, 11, 23), date(2026, 11, 24))
    assert len(payload["results"]) == 3
    params = fake.requests[0].url.params
    assert (params["adjusted"], params["apiKey"]) == ("true", KEY)

    fake.failing["NVDA"] = httpx.Response(403, text=f"bad key {KEY}")
    with fake.client() as client, pytest.raises(MassiveError) as error:
        client.daily_bars("NVDA", date(2026, 11, 23), date(2026, 11, 24))
    assert KEY not in str(error.value)
    assert "[redacted]" in str(error.value) and "MASSIVE_API_KEY" in str(error.value)


def test_client_rejects_error_payloads() -> None:
    fake = FakeMassive()
    fake.failing["NVDA"] = httpx.Response(200, json={"status": "ERROR", "error": "nope"})
    with fake.client() as client, pytest.raises(MassiveError, match="status ERROR"):
        client.daily_bars("NVDA", date(2026, 11, 23), date(2026, 11, 24))


def test_bar_rows_use_the_new_york_session_date_and_drop_bad_bars() -> None:
    payload = {
        "results": [
            _bar(date(2026, 11, 24)),
            _bar(date(2026, 11, 25)),  # outside the window
            {"t": _ms(date(2026, 11, 23)), "o": 1, "h": 1, "l": 2, "c": 1},  # high below low
            {"o": 1},  # no timestamp
        ]
    }
    rows, invalid = bar_rows(7, payload, date_from=date(2026, 11, 23), date_to=date(2026, 11, 24), task_run_id=1)
    assert invalid == 2
    assert [(row[0], row[1], row[5], row[6], row[8]) for row in rows] == [(7, date(2026, 11, 24), 100.0, 1234567, "ok")]


# --- handler against Postgres ----------------------------------------------------------------------


def _run(conn, *, tickers=None, payload=None, force_refresh=False) -> TaskRun:
    run_id = enqueue_run(
        conn,
        "ingest_prices",
        trigger_source="test",
        run_mode="manual",
        requested_by="pytest",
        tickers=tickers,
        payload=payload,
        force_refresh=force_refresh,
    )
    conn.commit()
    return TaskRun(
        run_id, "ingest_prices", "t", "test", "manual", "pytest", tickers or [], None, force_refresh, payload or {}
    )


def _bars(conn, symbol: str) -> list[tuple]:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT b.trading_date, b.close, b.volume, b.known_at, b.task_run_id FROM price_bars b "
            "JOIN instruments i ON i.id = b.instrument_id WHERE i.symbol = %s ORDER BY b.trading_date",
            (symbol,),
        )
        rows = cursor.fetchall()
    conn.rollback()
    return rows


def test_first_run_bootstraps_the_lookback_and_never_stores_an_unclosed_session(connect_app_db) -> None:
    conn = connect_app_db()
    fake = FakeMassive()
    run = _run(conn, payload={"lookback_days": 7})

    result = ingest_prices(run, conn, massive=fake.client(), now=NOW)

    assert result.status == "succeeded", result.error_message
    assert sorted(fake.symbols()) == ["AMZN", "ANT", "GOOGL", "NBIS", "NVDA", "QQQ", "SPCX", "SPY"]
    bars = _bars(conn, "NVDA")
    # 18–25 Nov 2026: six sessions; 26 Nov (Thanksgiving) and anything later are not stored.
    assert [row[0] for row in bars] == [date(2026, 11, d) for d in (18, 19, 20, 23, 24, 25)]
    assert bars[-1][1:3] == (Decimal("100.000000"), 1234567)
    assert bars[-1][3] == datetime(2026, 11, 25, 21, 0, tzinfo=UTC)  # known_at: the 16:00 close
    assert result.inserted_count == 8 * 6 and result.deduped_count == 0


def test_rerun_is_incremental_and_a_current_watchlist_makes_no_requests(connect_app_db) -> None:
    conn = connect_app_db()
    fake = FakeMassive()
    ingest_prices(_run(conn, payload={"lookback_days": 7}), conn, massive=fake.client(), now=NOW - timedelta(days=1))
    fake.requests.clear()

    result = ingest_prices(_run(conn, payload={"lookback_days": 7}), conn, massive=fake.client(), now=NOW)
    assert result.status == "succeeded"
    assert result.inserted_count == 8  # one new session each
    assert {request.url.path.split("/")[8] for request in fake.requests} == {"2026-11-25"}

    fake.requests.clear()
    again = ingest_prices(_run(conn), conn, massive=fake.client(), now=NOW)
    assert (again.status, again.meta["message"], fake.requests) == ("succeeded", "already_current", [])


def test_force_refresh_and_explicit_windows_overwrite_without_duplicating(connect_app_db) -> None:
    conn = connect_app_db()
    fake = FakeMassive()
    first = _run(conn, tickers=["NVDA"], payload={"lookback_days": 7})
    ingest_prices(first, conn, massive=fake.client(), now=NOW)

    refresh = _run(conn, tickers=["NVDA"], payload={"lookback_days": 7}, force_refresh=True)
    result = ingest_prices(refresh, conn, massive=fake.client(), now=NOW)
    assert (result.inserted_count, result.deduped_count) == (0, 6)
    assert {row[4] for row in _bars(conn, "NVDA")} == {refresh.id}

    explicit = _run(conn, tickers=["NVDA"], payload={"date_from": "2026-11-02", "date_to": "2026-12-31"})
    result = ingest_prices(explicit, conn, massive=fake.client(), now=NOW)
    assert result.status == "succeeded"
    assert fake.requests[-1].url.path.endswith("/2026-11-02/2026-11-25")  # capped at the last closed session
    assert len(_bars(conn, "NVDA")) == 18


def test_failures_and_missing_sessions_make_the_run_partial_or_failed(connect_app_db) -> None:
    conn = connect_app_db()
    fake = FakeMassive()
    fake.failing["NVDA"] = httpx.Response(500)
    fake.no_data.add("ANT")

    result = ingest_prices(_run(conn, payload={"lookback_days": 7}), conn, massive=fake.client(), now=NOW)
    assert result.status == "partial"
    assert "HTTP 500" in result.error_message and "no 2026-11-25 bar for: ANT, NVDA" in result.error_message
    assert result.meta["missing_target_session"] == ["ANT", "NVDA"]
    # 500 is retried: four attempts for NVDA.
    assert fake.symbols().count("NVDA") == 4

    fake.failing = {symbol: httpx.Response(401) for symbol in ["NVDA", "ANT"]}
    only_bad = ingest_prices(_run(conn, tickers=["NVDA", "ANT"]), conn, massive=fake.client(), now=NOW)
    assert only_bad.status == "failed"


def test_bad_payloads_and_a_missing_key_fail_the_run(connect_app_db, monkeypatch) -> None:
    conn = connect_app_db()
    with pytest.raises(ValueError, match="after date_to"):
        ingest_prices(_run(conn, payload={"date_from": "2026-12-01", "date_to": "2026-11-01"}), conn, now=NOW)
    with pytest.raises(ValueError, match="lookback_days"):
        ingest_prices(_run(conn, payload={"lookback_days": "lots"}), conn, now=NOW)
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    with pytest.raises(MassiveConfigurationError):
        ingest_prices(_run(conn), conn, now=NOW)
