"""Massive splits client, ``ingest_splits`` and watchlist split detection. Massive is an ``httpx.MockTransport``."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from ingestion.http import Pacer
from ingestion.market_calendar import NEW_YORK
from ingestion.massive_client import MassiveClient, MassiveConfigurationError, MassiveError
from ingestion.splits import ingest_splits, quarter_windows, split_rows
from orchestration.task_runs import TaskRun, enqueue_run
from research.asof import AsOf

KEY = "test-key-not-real"
# Friday 27 Nov 2026, 17:00 New York.
NOW = datetime(2026, 11, 27, 22, 0, tzinfo=UTC)
Q4_2024 = (date(2024, 10, 1), date(2024, 12, 31))
Q4_2026 = (date(2026, 10, 1), date(2026, 12, 31))
Q1_2027 = (date(2027, 1, 1), date(2027, 3, 31))


def _split(symbol: str, executed: str, split_from: float, split_to: float, split_id: str | None = None) -> dict:
    return {
        "id": split_id or f"E-{symbol}-{executed}-{split_from}-{split_to}",
        "ticker": symbol,
        "execution_date": executed,
        "split_from": split_from,
        "split_to": split_to,
    }


class FakeMassive:
    """Serves the splits whose execution date falls in the requested window."""

    def __init__(self, splits: list[dict] | None = None) -> None:
        self.splits = list(splits or [])
        self.requests: list[httpx.Request] = []
        self.failing: dict[date, httpx.Response] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        start = date.fromisoformat(request.url.params["execution_date.gte"])
        end = date.fromisoformat(request.url.params["execution_date.lte"])
        if start in self.failing:
            return self.failing[start]
        results = [s for s in self.splits if start <= date.fromisoformat(s["execution_date"]) <= end]
        return httpx.Response(200, json={"status": "OK", "results": results})

    def client(self) -> MassiveClient:
        return MassiveClient(KEY, transport=httpx.MockTransport(self.handler), pacer=Pacer(0), base_delay=0)

    def windows(self) -> list[date]:
        return [date.fromisoformat(r.url.params["execution_date.gte"]) for r in self.requests]


def _run(conn, **payload) -> TaskRun:
    run_id = enqueue_run(
        conn, "ingest_splits", trigger_source="test", run_mode="manual", requested_by="pytest", payload=payload
    )
    conn.commit()
    return TaskRun(run_id, "ingest_splits", "t", "test", "manual", "pytest", [], None, False, payload)


def _stored(conn) -> list[tuple]:
    with conn.cursor() as cursor:
        cursor.execute("SELECT symbol, execution_date, split_from, split_to FROM stock_splits ORDER BY 1, 2, 3")
        rows = cursor.fetchall()
    conn.commit()
    return rows


def _bars(conn, symbol: str, days: list[date], fetched_at: datetime) -> None:
    with conn.cursor() as cursor:
        for day in days:
            cursor.execute(
                "INSERT INTO price_bars (instrument_id, trading_date, close, provider, fetched_at) "
                "SELECT id, %s, 100, 'massive', %s FROM instruments WHERE symbol = %s",
                (day, fetched_at, symbol),
            )
    conn.commit()


def _price_runs(conn) -> list[tuple]:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT tickers, meta->'dispatch_payload'->>'date_from', trigger_source, run_mode, status "
            "FROM task_runs WHERE task_id = 'ingest_prices' ORDER BY id"
        )
        rows = cursor.fetchall()
    conn.commit()
    return rows


# --- client and parsing ----------------------------------------------------------------------------


def test_client_asks_for_one_window_and_refuses_a_full_page() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.params["execution_date.gte"] == "2025-01-01":
            return httpx.Response(200, json={"status": "OK", "results": [], "next_url": "https://x/next"})
        return httpx.Response(403, text=f"denied {KEY}")

    client = MassiveClient(KEY, transport=httpx.MockTransport(handler), pacer=Pacer(0), base_delay=0)
    with pytest.raises(MassiveError, match="shorter window"):
        client.splits(date(2025, 1, 1), date(2025, 3, 31))
    assert seen[0].url.path == "/v3/reference/splits"
    assert (seen[0].url.params["execution_date.lte"], seen[0].url.params["limit"]) == ("2025-03-31", "1000")

    with pytest.raises(MassiveError) as excinfo:
        client.splits(date(2025, 4, 1), date(2025, 6, 30))
    assert KEY not in str(excinfo.value)


def test_quarters_cover_the_range() -> None:
    assert quarter_windows(date(2024, 11, 27), date(2025, 4, 2)) == [
        (date(2024, 10, 1), date(2024, 12, 31)),
        (date(2025, 1, 1), date(2025, 3, 31)),
        (date(2025, 4, 1), date(2025, 6, 30)),
    ]


def test_split_rows_keep_several_splits_a_day_and_drop_bad_records() -> None:
    results = [
        _split("PMD", "2024-12-04", 5000, 1, "E1"),  # reverse split ...
        _split("PMD", "2024-12-04", 1, 5000, "E2"),  # ... and forward split the same day
        _split("WDPSF", "2024-11-05", 24.72619047619048, 25.72619047619048, "E3"),
        _split("BAD", "2024-12-04", 0, 1, "E4"),
        _split("", "2024-12-04", 1, 2, "E5"),
        _split("OUT", "2025-01-02", 1, 2, "E6"),  # outside the window
        {"id": "E7", "ticker": "X", "execution_date": "soon", "split_from": 1, "split_to": 2},
        _split("PMD", "2024-12-04", 1, 5000, "E2"),  # repeated id
    ]
    rows, invalid = split_rows(results, Q4_2024)
    assert [(r[0], r[1]) for r in rows] == [("E1", "PMD"), ("E2", "PMD"), ("E3", "WDPSF")]
    assert invalid == 5


# --- loading ---------------------------------------------------------------------------------------


def test_runs_fetch_current_quarters_first_then_the_oldest_backlog(connect_app_db) -> None:
    conn = connect_app_db()
    fake = FakeMassive([_split("ELAB", "2024-11-27", 200, 1), _split("IBDSF", "2027-01-13", 1, 2)])
    with fake.client() as client:
        first = ingest_splits(_run(conn), conn, massive=client, now=NOW)
        assert first.status == "succeeded"
        assert fake.windows() == [
            Q4_2026[0],
            Q1_2027[0],
            date(2024, 10, 1),
            date(2025, 1, 1),
            date(2025, 4, 1),
            date(2025, 7, 1),
        ]
        assert first.meta["remaining_windows"] == 4

        second = ingest_splits(_run(conn), conn, massive=client, now=NOW)
        assert fake.windows()[6:] == [
            Q4_2026[0],
            Q1_2027[0],
            date(2025, 10, 1),
            date(2026, 1, 1),
            date(2026, 4, 1),
            date(2026, 7, 1),
        ]
        assert second.meta["remaining_windows"] == 0

        # On 7 Mar 2027 the upcoming quarters come first. Q4 2026 was last fetched before it settled
        # (14 Feb 2027), so it is asked for once more; older quarters fetched after settling are not.
        later = ingest_splits(_run(conn), conn, massive=client, now=NOW + timedelta(days=100))
    assert later.meta["windows"] == [
        "2027-01-01..2027-03-31",
        "2027-04-01..2027-06-30",
        "2027-07-01..2027-09-30",
        "2026-10-01..2026-12-31",
    ]
    assert _stored(conn) == [("ELAB", date(2024, 11, 27), 200.0, 1.0), ("IBDSF", date(2027, 1, 13), 1.0, 2.0)]


def test_a_refetched_quarter_replaces_corrected_and_withdrawn_splits(connect_app_db) -> None:
    conn = connect_app_db()
    fake = FakeMassive([_split("ABC", "2026-11-02", 1, 3, "A"), _split("XYZ", "2026-11-03", 1, 2, "B")])
    with fake.client() as client:
        ingest_splits(_run(conn, max_requests=1), conn, massive=client, now=NOW)
        fake.splits = [_split("ABC", "2026-11-02", 1, 4, "A")]  # ratio corrected, XYZ withdrawn
        result = ingest_splits(_run(conn, max_requests=1), conn, massive=client, now=NOW)
    assert result.meta["removed"] == 1
    assert _stored(conn) == [("ABC", date(2026, 11, 2), 1.0, 4.0)]


def test_an_empty_answer_for_a_quarter_with_splits_is_an_error(connect_app_db) -> None:
    conn = connect_app_db()
    fake = FakeMassive([_split("ABC", "2026-11-02", 1, 3)])
    with fake.client() as client:
        ingest_splits(_run(conn, max_requests=1), conn, massive=client, now=NOW)
        fake.splits = []
        result = ingest_splits(_run(conn, max_requests=1), conn, massive=client, now=NOW)
    assert result.status == "failed"
    assert "listed no splits" in result.error_message
    assert len(_stored(conn)) == 1


def test_a_provider_error_stops_the_run_and_credentials_fail_clearly(connect_app_db, monkeypatch) -> None:
    conn = connect_app_db()
    fake = FakeMassive()
    fake.failing[Q1_2027[0]] = httpx.Response(500, text="boom")
    with fake.client() as client:
        result = ingest_splits(_run(conn), conn, massive=client, now=NOW)
    assert result.status == "partial"
    assert sorted(set(fake.windows())) == [Q4_2026[0], Q1_2027[0]]  # stopped after the retries ran out

    with pytest.raises(ValueError, match="max_requests"):
        ingest_splits(_run(conn, max_requests="lots"), conn, massive=fake.client(), now=NOW)
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    with pytest.raises(MassiveConfigurationError):
        ingest_splits(_run(conn), conn, now=NOW)


# --- detection -------------------------------------------------------------------------------------


def test_a_split_queues_one_full_refetch_of_stale_watchlist_bars(connect_app_db) -> None:
    conn = connect_app_db()
    before_split = datetime(2026, 11, 20, 23, 30, tzinfo=UTC)
    _bars(conn, "NVDA", [date(2026, 6, 12), date(2026, 11, 20)], fetched_at=before_split)
    _bars(conn, "AMZN", [date(2026, 11, 20)], fetched_at=before_split)
    fake = FakeMassive(
        [
            _split("NVDA", "2026-11-23", 1, 4),
            _split("AMZN", "2026-12-14", 1, 20),  # announced, not yet in effect
            _split("QQQ", "2026-11-24", 1, 2),  # benchmark without stored bars: nothing to re-fetch
        ]
    )
    with fake.client() as client:
        result = ingest_splits(_run(conn, max_requests=1), conn, massive=client, now=NOW)
        again = ingest_splits(_run(conn, max_requests=1), conn, massive=client, now=NOW)

    assert [q["symbol"] for q in result.meta["refetch_queued"]] == ["NVDA"]
    assert result.meta["refetch_queued"][0]["splits"] == ["2026-11-23"]
    assert again.meta["refetch_waiting"] == ["NVDA"]
    assert _price_runs(conn) == [(["NVDA"], "2026-06-12", "split_detected", "backfill", "pending")]


def test_no_refetch_once_bars_were_fetched_after_the_split_or_when_splits_cancel(connect_app_db) -> None:
    conn = connect_app_db()
    _bars(conn, "NVDA", [date(2026, 11, 20)], fetched_at=datetime(2026, 11, 23, 23, 30, tzinfo=UTC))
    _bars(conn, "GOOGL", [date(2026, 11, 20)], fetched_at=datetime(2026, 11, 20, 23, 30, tzinfo=UTC))
    fake = FakeMassive(
        [
            _split("NVDA", "2026-11-23", 1, 4),
            _split("GOOGL", "2026-11-24", 5000, 1, "R"),
            _split("GOOGL", "2026-11-24", 1, 5000, "F"),
        ]
    )
    with fake.client() as client:
        result = ingest_splits(_run(conn, max_requests=1), conn, massive=client, now=NOW)
    assert result.meta["refetch_queued"] == []
    assert _price_runs(conn) == []


# --- as-of access ----------------------------------------------------------------------------------


def test_splits_are_read_from_the_start_of_their_execution_day(connect_app_db) -> None:
    conn = connect_app_db()
    fake = FakeMassive([_split("NVDA", "2026-11-23", 1, 4), _split("SCHWpD", "2026-11-24", 10, 1)])
    with fake.client() as client:
        ingest_splits(_run(conn, max_requests=1), conn, massive=client, now=NOW)

    start_of_day = datetime(2026, 11, 23, 0, 0, tzinfo=NEW_YORK)
    assert AsOf(conn, start_of_day).splits("NVDA") == []
    [split] = AsOf(conn, start_of_day + timedelta(microseconds=1)).splits("NVDA")
    assert (split.execution_date, split.share_factor) == (date(2026, 11, 23), 4.0)
    [reverse] = AsOf(conn, NOW).splits("SCHWpD", start=date(2026, 11, 1))
    assert reverse.share_factor == pytest.approx(0.1)
