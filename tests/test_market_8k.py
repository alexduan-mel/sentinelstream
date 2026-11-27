"""Market-wide 8-K history: EDGAR's full index, submissions records, as-of reads and 8-K events. No network."""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from ingestion.http import Pacer
from ingestion.market_8k import ingest_market_8k, parse_form_index
from ingestion.sec_client import SecClient, SecConfigurationError
from orchestration.task_runs import TaskRun, enqueue_run
from research.asof import AsOf
from research.base_rates import compute_base_rates
from research.events import eight_k_events, listed_symbol
from tests.test_sec_ingestion import _filing, _submissions

UA = "SentinelStream tests test@example.com"
# Friday 27 Nov 2026: the two-year window starts 27 Nov 2024; quarters 2024q4 to 2026q4.
NOW = datetime(2026, 11, 27, 15, 0, tzinfo=UTC)
QUARTERS = ["2024q4", "2025q1", "2025q2", "2025q3", "2025q4", "2026q1", "2026q2", "2026q3", "2026q4"]
ACME, BETA = "0000111111", "0000222222"


def _line(form: str, name: str, cik: str, filed: str) -> str:
    return f"{form:<12}{name:<62}{int(cik):<12}{filed:<12}edgar/data/{int(cik)}/0000000000-00-000000.txt\n"


def _index(lines: list[str]) -> str:
    header = "Description: Master Index of EDGAR Dissemination Feed by Form Type\n\n"
    header += (
        "Form Type   Company Name                                                  CIK         Date Filed  File Name\n"
    )
    return header + "-" * 140 + "\n" + "".join(lines)


class FakeSec:
    def __init__(self) -> None:
        self.indexes: dict[str, str] = {q: _index([]) for q in QUARTERS}
        self.submissions: dict[str, dict] = {}
        self.pages: dict[str, dict] = {}
        self.failing: dict[str, httpx.Response] = {}
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path in self.failing:
            return self.failing[path]
        if path.endswith("/form.idx"):
            parts = path.split("/")  # /Archives/edgar/full-index/{year}/QTR{q}/form.idx
            quarter = f"{parts[4]}q{parts[5][3]}"
            return httpx.Response(200, text=self.indexes[quarter]) if quarter in self.indexes else httpx.Response(404)
        name = path.rsplit("/", 1)[1]
        if name in self.pages:
            return httpx.Response(200, json=self.pages[name])
        cik = name.removeprefix("CIK").removesuffix(".json")
        return httpx.Response(200, json=self.submissions[cik]) if cik in self.submissions else httpx.Response(404)

    def client(self) -> SecClient:
        return SecClient(UA, transport=httpx.MockTransport(self.handler), pacer=Pacer(0), base_delay=0)

    def submission_reads(self) -> list[str]:
        return [r.url.path.rsplit("/", 1)[1] for r in self.requests if "/submissions/" in r.url.path]


def _acme_record(*filings: dict) -> dict:
    record = _submissions("ACME CORP", ["ACME", "ACMEW"], list(filings))
    record.update(exchanges=["Nasdaq", "OTC"], sic="3674", sicDescription="Semiconductors", entityType="operating")
    return record


def _run(conn, **payload) -> TaskRun:
    run_id = enqueue_run(
        conn, "ingest_market_8k", trigger_source="test", run_mode="manual", requested_by="pytest", payload=payload
    )
    conn.commit()
    return TaskRun(run_id, "ingest_market_8k", "t", "test", "manual", "pytest", [], None, False, payload)


def _rows(conn, sql: str) -> list[tuple]:
    with conn.cursor() as cursor:
        cursor.execute(sql)
        rows = cursor.fetchall()
    conn.commit()
    return rows


# --- the index -------------------------------------------------------------------------------------


def test_index_lines_give_each_filers_latest_8k_in_the_window() -> None:
    text = _index(
        [
            _line("8-K", "ACME CORP", ACME, "2025-01-10"),
            _line("8-K/A", "ACME CORP", ACME, "2025-02-03"),
            _line("10-K", "ACME CORP", ACME, "2025-03-01"),  # not an 8-K
            _line("8-K", "BETA HOLDINGS & CO INC /DE/", BETA, "2024-11-01"),  # before the window
            "8-K         garbage\n",
        ]
    )
    assert parse_form_index(text, date(2024, 11, 27)) == {ACME: ("ACME CORP", date(2025, 2, 3))}


# --- the job ---------------------------------------------------------------------------------------


def test_a_run_reads_one_index_then_the_filers_it_names(connect_app_db) -> None:
    conn = connect_app_db()
    fake = FakeSec()
    fake.indexes["2024q4"] = _index([_line("8-K", "ACME CORP", ACME, "2024-12-02")])
    fake.submissions[ACME] = _acme_record(
        _filing("0000111111-24-000009", "8-K", "2024-12-02T21:05:00.000Z", filed="2024-12-02", items="2.02,9.01"),
        _filing("0000111111-24-000008", "10-Q", "2024-11-30T21:05:00.000Z", filed="2024-11-30"),
        _filing("0000111111-24-000001", "8-K", "2024-01-05T21:05:00.000Z", filed="2024-01-05", items="5.02"),
    )
    with fake.client() as client:
        first = ingest_market_8k(_run(conn), conn, sec=client, now=NOW)
        assert first.status == "succeeded"
        assert (first.meta["index_quarter"], first.meta["issuers"], first.meta["filings"]) == ("2024q4", 1, 1)
        assert first.meta["remaining_issuers"] == 0

        for _ in QUARTERS[1:]:
            ingest_market_8k(_run(conn), conn, sec=client, now=NOW)
        current = ingest_market_8k(_run(conn), conn, sec=client, now=NOW)
    assert current.meta["message"] == "already_current"
    assert fake.submission_reads() == [f"CIK{ACME}.json"]
    assert _rows(conn, "SELECT accession_no, items, known_at FROM market_8k_filings") == [
        ("0000111111-24-000009", ["2.02", "9.01"], datetime(2024, 12, 2, 21, 5, tzinfo=UTC))
    ]
    assert _rows(conn, "SELECT tickers, exchanges, sic, covered_through FROM market_issuers") == [
        (["ACME", "ACMEW"], ["Nasdaq", "OTC"], "3674", date(2026, 11, 26))
    ]
    complete = dict(_rows(conn, "SELECT quarter, complete FROM edgar_index_quarters"))
    assert complete["2026q3"] is True and complete["2026q4"] is False


def test_a_company_is_read_again_only_after_a_newer_8k_dated_before_today(connect_app_db) -> None:
    conn = connect_app_db()
    fake = FakeSec()
    fake.indexes["2026q4"] = _index([_line("8-K", "ACME CORP", ACME, "2026-11-27")])  # filed today
    fake.submissions[ACME] = _acme_record()
    with fake.client() as client:
        for _ in QUARTERS:
            ingest_market_8k(_run(conn), conn, sec=client, now=NOW)
        assert fake.submission_reads() == []  # waits until the filing date has passed

        tomorrow = NOW + timedelta(days=1)
        ingest_market_8k(_run(conn), conn, sec=client, now=tomorrow)
        ingest_market_8k(_run(conn), conn, sec=client, now=tomorrow)
        assert fake.submission_reads() == [f"CIK{ACME}.json"]  # read once, not on every run

        fake.indexes["2026q4"] += _line("8-K", "ACME CORP", ACME, "2026-11-30")
        later = NOW + timedelta(days=4)  # Tuesday 1 Dec: the index is re-read after 20 hours
        ingest_market_8k(_run(conn), conn, sec=client, now=later)
    assert fake.submission_reads() == [f"CIK{ACME}.json"] * 2


def test_older_pages_are_read_only_when_the_recent_block_stops_short(connect_app_db) -> None:
    conn = connect_app_db()
    fake = FakeSec()
    fake.indexes["2024q4"] = _index([_line("8-K", "ACME CORP", ACME, "2024-12-02")])
    record = _acme_record(_filing("0000111111-25-000100", "4", "2025-06-01T21:00:00.000Z", filed="2025-06-01"))
    record["filings"]["files"] = [
        {"name": "CIK0000111111-submissions-001.json", "filingFrom": "2024-01-01", "filingTo": "2025-05-31"},
        {"name": "CIK0000111111-submissions-002.json", "filingFrom": "2010-01-01", "filingTo": "2023-12-31"},
    ]
    fake.submissions[ACME] = record
    fake.pages["CIK0000111111-submissions-001.json"] = record["filings"]["recent"] | {
        k: [v]
        for k, v in _filing(
            "0000111111-24-000009", "8-K", "2024-12-02T21:05:00.000Z", filed="2024-12-02", items="1.01"
        ).items()
    }
    with fake.client() as client:
        result = ingest_market_8k(_run(conn), conn, sec=client, now=NOW)
    assert result.meta["filings"] == 1
    assert fake.submission_reads() == [f"CIK{ACME}.json", "CIK0000111111-submissions-001.json"]


def test_an_unknown_company_is_marked_and_other_errors_stop_the_run(connect_app_db, monkeypatch) -> None:
    conn = connect_app_db()
    fake = FakeSec()
    fake.indexes["2024q4"] = _index(
        [_line("8-K", "ACME CORP", ACME, "2024-12-02"), _line("8-K", "BETA CORP", BETA, "2024-12-03")]
    )
    fake.failing[f"/submissions/CIK{BETA}.json"] = httpx.Response(500)
    with fake.client() as client:
        result = ingest_market_8k(_run(conn), conn, sec=client, now=NOW)
    assert result.status == "partial"  # index stored, ACME marked, BETA stopped the run
    assert _rows(
        conn, "SELECT cik, fetch_error IS NOT NULL, covered_through IS NOT NULL FROM market_issuers ORDER BY 1"
    ) == [
        (ACME, True, True),
        (BETA, False, False),
    ]

    with pytest.raises(ValueError, match="max_issuers"):
        ingest_market_8k(_run(conn, max_issuers=0), conn, sec=fake.client(), now=NOW)
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(SecConfigurationError):
        ingest_market_8k(_run(conn), conn, now=NOW)


# --- as-of access and events -----------------------------------------------------------------------


def _load_acme(conn, *filings: dict) -> None:
    fake = FakeSec()
    fake.indexes["2026q4"] = _index([_line("8-K", "ACME CORP", ACME, "2026-11-02")])
    fake.submissions[ACME] = _acme_record(*filings)
    with fake.client() as client:
        for _ in QUARTERS:
            ingest_market_8k(_run(conn), conn, sec=client, now=NOW)


def test_8ks_are_read_from_acceptance_and_become_one_event_a_day(connect_app_db) -> None:
    conn = connect_app_db()
    accepted = datetime(2026, 11, 2, 21, 5, tzinfo=UTC)
    _load_acme(
        conn,
        _filing("0000111111-26-000003", "8-K", "2026-11-02T21:30:00.000Z", filed="2026-11-02", items="7.01"),
        _filing("0000111111-26-000002", "8-K", "2026-11-02T21:05:00.000Z", filed="2026-11-02", items="2.02,9.01"),
        _filing("0000111111-26-000001", "8-K/A", "2026-11-02T22:00:00.000Z", filed="2026-11-02", items="2.02"),
    )
    assert AsOf(conn, accepted).market_8k_filings() == []
    [results] = AsOf(conn, accepted + timedelta(seconds=1)).market_8k_filings(items=["2.02"])
    assert (results.accession_no, results.items, results.sic) == ("0000111111-26-000002", ("2.02", "9.01"), "3674")

    events = eight_k_events(AsOf(conn, NOW), ["2.02", "7.01"])
    assert [(e.symbol, e.known_at, e.label) for e in events] == [("ACME", accepted, "ACME CORP 8-K 2.02")]


def test_listed_symbol_skips_over_the_counter_tickers() -> None:
    assert listed_symbol(("ACMEF", "ACME"), ("OTC", "NYSE")) == "ACME"
    assert listed_symbol(("ACMEF",), ("OTC",)) is None
    assert listed_symbol((), ()) is None


def test_the_calculator_takes_8k_items(connect_app_db) -> None:
    conn = connect_app_db()
    run_id = enqueue_run(conn, "compute_base_rates", trigger_source="test", run_mode="manual", requested_by="pytest")
    conn.commit()
    run = TaskRun(run_id, "compute_base_rates", "t", "test", "manual", "pytest", [], None, False, {})
    with pytest.raises(ValueError, match="8k_items needs items"):
        compute_base_rates(dataclasses.replace(run, payload={"event": "8k_items", "items": ["results"]}), conn)
    with pytest.raises(ValueError, match="no QQQ bars"):  # valid request; nothing loaded to measure against
        compute_base_rates(dataclasses.replace(run, payload={"event": "8k_items", "items": ["2.02"]}), conn)
