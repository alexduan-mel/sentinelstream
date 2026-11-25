"""SEC client, filing normalisation, issuer identity and the ``ingest_sec_filings`` handler.

All SEC traffic goes to an ``httpx.MockTransport``; no test touches the network.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

from ingestion.http import Pacer
from ingestion.sec_client import SecClient, SecConfigurationError, SecError, user_agent_from_env
from ingestion.sec_filings import ingest_sec_filings, normalise_recent_filings, parse_acceptance
from ingestion.sec_identity import IdentityError, detect_filer_type, resolve_issuer
from orchestration.task_runs import TaskRun, enqueue_run

UA = "SentinelStream tests@example.com"
NVDA_CIK = "0001045810"
NBIS_CIK = "0001513845"
NEW_YORK = ZoneInfo("America/New_York")


def _filing(accession: str, form: str, accepted: str, *, filed: str, items: str = "", doc: str = "doc.htm") -> dict:
    return {
        "accessionNumber": accession,
        "filingDate": filed,
        "reportDate": "",
        "acceptanceDateTime": accepted,
        "form": form,
        "items": items,
        "primaryDocument": doc,
        "isXBRL": 1 if form in ("10-K", "10-Q", "8-K") else 0,
    }


def _submissions(name: str, tickers: list[str], filings: list[dict], *, entity_type: str = "operating") -> dict:
    # Real records carry entityType; resolution ignores it (see test_a_new_issuer_labelled_other_resolves).
    recent = {key: [f[key] for f in filings] for key in _filing("0000000000-00-000000", "x", "", filed="").keys()}
    return {"name": name, "tickers": tickers, "entityType": entity_type, "filings": {"recent": recent}}


TICKER_MAP = {
    "fields": ["cik", "name", "ticker", "exchange"],
    "data": [
        [1045810, "NVIDIA CORP", "NVDA", "Nasdaq"],
        [1513845, "Nebius Group N.V.", "NBIS", "Nasdaq"],
        [9999999, "Example Fund Trust", "ANT", "NYSE"],  # fictional: a fund holding the ticker
    ],
}

NVDA_FILINGS = [
    _filing("0001045810-26-000101", "8-K", "2026-11-19T16:05:12.000Z", filed="2026-11-19", items="2.02,9.01"),
    _filing("0001045810-26-000100", "10-Q", "2026-11-19T16:20:00.000Z", filed="2026-11-19"),
    _filing("0001045810-26-000099", "4", "2026-11-18T18:40:00.000Z", filed="2026-11-18", doc="xslF345X05/form4.xml"),
]
NBIS_FILINGS = [
    _filing("0001513845-26-000040", "6-K", "2026-11-11T07:01:00.000Z", filed="2026-11-11"),
    _filing("0001513845-26-000010", "20-F", "2026-04-30T16:30:00.000Z", filed="2026-04-30"),
]


class FakeSec:
    """Routes SEC URLs to canned JSON and records every request."""

    def __init__(self) -> None:
        self.routes: dict[str, object] = {
            "/files/company_tickers_exchange.json": TICKER_MAP,
            f"/submissions/CIK{NVDA_CIK}.json": _submissions("NVIDIA CORP", ["NVDA"], NVDA_FILINGS),
            f"/submissions/CIK{NBIS_CIK}.json": _submissions("Nebius Group N.V.", ["NBIS"], NBIS_FILINGS),
            "/submissions/CIK0009999999.json": _submissions(
                "Example Fund Trust",
                ["ANT"],
                [_filing("0009999999-26-000001", "485BPOS", "2026-10-01T09:00:00.000Z", filed="2026-10-01")],
                entity_type="other",
            ),
        }
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = self.routes.get(request.url.path)
        if body is None:
            return httpx.Response(404)
        if isinstance(body, httpx.Response):
            return body
        return httpx.Response(200, json=body)

    def client(self) -> SecClient:
        return SecClient(UA, transport=httpx.MockTransport(self.handler), pacer=Pacer(0), base_delay=0)

    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]


# --- client ----------------------------------------------------------------------------------------


def test_user_agent_is_required_and_must_name_a_contact(monkeypatch) -> None:
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(SecConfigurationError, match="SEC_USER_AGENT is not set"):
        user_agent_from_env()
    monkeypatch.setenv("SEC_USER_AGENT", "SentinelStream")
    with pytest.raises(SecConfigurationError, match="e-mail"):
        user_agent_from_env()
    monkeypatch.setenv("SEC_USER_AGENT", UA)
    assert user_agent_from_env() == UA


def test_client_sends_the_user_agent_and_retries_sec_throttling() -> None:
    fake = FakeSec()
    answers = iter([httpx.Response(403, headers={"Retry-After": "0"}), httpx.Response(200, json=TICKER_MAP)])

    def handler(request: httpx.Request) -> httpx.Response:
        fake.requests.append(request)
        return next(answers)

    with SecClient(UA, transport=httpx.MockTransport(handler), pacer=Pacer(0), base_delay=0) as client:
        rows = client.ticker_map()
    assert rows[0] == {"cik": 1045810, "name": "NVIDIA CORP", "ticker": "NVDA", "exchange": "Nasdaq"}
    assert [r.headers["User-Agent"] for r in fake.requests] == [UA, UA]


def test_client_errors_are_clear() -> None:
    with FakeSec().client() as client:
        with pytest.raises(SecError, match="404"):
            client.submissions("0000000001")
        with pytest.raises(ValueError, match="10 digits"):
            client.submissions("1045810")


# --- normalisation ---------------------------------------------------------------------------------


def test_acceptance_time_is_new_york_wall_clock_despite_the_z() -> None:
    accepted = parse_acceptance("2026-11-19T16:05:12.000Z")
    assert accepted == datetime(2026, 11, 19, 16, 5, 12, tzinfo=NEW_YORK)
    assert accepted.astimezone(UTC) == datetime(2026, 11, 19, 21, 5, 12, tzinfo=UTC)
    # Summer time: 16:30 EDT is 20:30 UTC.
    assert parse_acceptance("2026-04-30T16:30:00.000Z").astimezone(UTC).hour == 20
    # An explicit offset is taken as written.
    assert parse_acceptance("2026-11-19T21:05:12+00:00") == datetime(2026, 11, 19, 21, 5, 12, tzinfo=UTC)


def test_normalise_keeps_valid_rows_and_counts_invalid_ones() -> None:
    bad = _filing("not-an-accession", "8-K", "2026-11-19T16:05:12.000Z", filed="2026-11-19")
    no_time = _filing("0001045810-26-000102", "8-K", "", filed="2026-11-19")
    records, invalid = normalise_recent_filings(
        NVDA_CIK, _submissions("NVIDIA CORP", ["NVDA"], [*NVDA_FILINGS, bad, no_time])
    )

    assert invalid == 2
    assert [r.accession_no for r in records] == [f["accessionNumber"] for f in NVDA_FILINGS]
    eight_k = records[0]
    assert (eight_k.form_type, eight_k.items, eight_k.filed_date, eight_k.report_date) == (
        "8-K",
        ["2.02", "9.01"],
        date(2026, 11, 19),
        None,
    )
    assert eight_k.is_xbrl and eight_k.cik == NVDA_CIK


def test_normalise_rejects_ragged_columns() -> None:
    submissions = _submissions("NVIDIA CORP", ["NVDA"], NVDA_FILINGS)
    submissions["filings"]["recent"]["form"].pop()
    with pytest.raises(SecError, match="different lengths"):
        normalise_recent_filings(NVDA_CIK, submissions)


# --- identity --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("forms", "expected"),
    [
        (["4", "8-K", "10-Q", "10-K"], "domestic"),
        (["6-K", "20-F/A", "6-K"], "foreign_private"),
        # An issuer that moved from 20-F to 10-K reporting is domestic now.
        (["8-K", "10-K", "6-K", "20-F"], "domestic"),
        (["6-K", "424B4", "F-1"], "foreign_private"),
        (["S-1/A", "S-1"], "domestic"),
        # A fresh IPO: the S-1 has dropped out of view behind insider filings.
        (["4", "4", "424B4", "FWP", "S-8", "EFFECT", "3"], "domestic"),
        (["SC 13G"], None),
    ],
)
def test_detect_filer_type(forms: list[str], expected: str | None) -> None:
    assert detect_filer_type({"filings": {"recent": {"form": forms}}}) == expected


def test_resolve_issuer_confirms_against_submissions() -> None:
    fake = FakeSec()
    with fake.client() as client:
        rows = client.ticker_map()
        issuer, submissions = resolve_issuer("NBIS", rows, client)
        assert (issuer.cik, issuer.name, issuer.exchange, issuer.filer_type) == (
            NBIS_CIK,
            "Nebius Group N.V.",
            "Nasdaq",
            "foreign_private",
        )
        assert submissions["name"] == "Nebius Group N.V."
        with pytest.raises(IdentityError, match="not in SEC's ticker map"):
            resolve_issuer("SPCX", rows, client)
        with pytest.raises(IdentityError, match="files as a fund"):
            resolve_issuer("ANT", rows, client)


def test_a_new_issuer_labelled_other_resolves() -> None:
    # Shaped like a company that listed days ago: SEC labels it entityType "other" (fictional CIK).
    fake = FakeSec()
    forms = [("4", "2026-11-23"), ("424B4", "2026-11-19"), ("S-8", "2026-11-19"), ("3", "2026-11-18")]
    filings = [
        _filing(f"0001234568-26-00000{n}", form, f"{day}T17:00:00.000Z", filed=day)
        for n, (form, day) in enumerate(forms)
    ]
    fake.routes["/submissions/CIK0001234568.json"] = _submissions(
        "Example Software, PBC", ["EXSW"], filings, entity_type="other"
    )
    rows = [{"cik": 1234568, "name": "Example Software, PBC", "ticker": "EXSW", "exchange": "Nasdaq"}]
    with fake.client() as client:
        issuer, _ = resolve_issuer("EXSW", rows, client)
    assert (issuer.cik, issuer.filer_type) == ("0001234568", "domestic")


# --- handler against Postgres ----------------------------------------------------------------------


def _run(conn, *, tickers: list[str] | None = None, force_refresh: bool = False, run_mode: str = "manual") -> TaskRun:
    run_id = enqueue_run(
        conn,
        "ingest_sec_filings",
        trigger_source="test",
        run_mode=run_mode,
        requested_by="pytest",
        tickers=tickers,
        force_refresh=force_refresh,
    )
    conn.commit()
    return TaskRun(
        run_id, "ingest_sec_filings", "t", "test", run_mode, "pytest", tickers or [], None, force_refresh, {}
    )


def _instrument(conn, symbol: str) -> tuple:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT cik, name, exchange, filer_type, onboarding_status, onboarding_error FROM instruments "
            "WHERE symbol = %s",
            (symbol,),
        )
        row = cursor.fetchone()
    conn.rollback()
    return row


def _filings(conn) -> list[tuple]:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT accession_no, cik, form_type, items, known_at, accepted_at, is_backfill, task_run_id "
            "FROM filings ORDER BY accession_no"
        )
        rows = cursor.fetchall()
    conn.rollback()
    return rows


def test_first_poll_resolves_identities_and_records_history_as_backfill(connect_app_db) -> None:
    conn = connect_app_db()
    fake = FakeSec()
    run = _run(conn)

    result = ingest_sec_filings(run, conn, sec=fake.client())

    # Seeded watchlist: NVDA, NBIS resolve; SPCX is not in the map; ANT belongs to a fund; GOOGL and
    # AMZN are not in this fake map either.
    assert result.status == "partial"
    assert (result.fetched_count, result.inserted_count, result.deduped_count) == (5, 5, 0)
    assert {r["symbol"] for r in result.meta["resolved"]} == {"NVDA", "NBIS"}
    assert {u["symbol"] for u in result.meta["unresolved"]} == {"AMZN", "ANT", "GOOGL", "SPCX"}
    assert "files as a fund (485BPOS)" in result.error_message

    assert _instrument(conn, "NVDA") == (NVDA_CIK, "NVIDIA CORP", "Nasdaq", "domestic", "pending", None)
    assert _instrument(conn, "NBIS")[3] == "foreign_private"
    assert _instrument(conn, "SPCX")[4:] == ("failed", "SPCX is not in SEC's ticker map")
    assert _instrument(conn, "QQQ")[0] is None  # benchmarks are not SEC-polled

    rows = _filings(conn)
    assert len(rows) == 5
    assert all(row[6] is True and row[7] == run.id for row in rows)
    eight_k = next(row for row in rows if row[0] == "0001045810-26-000101")
    assert eight_k[2:4] == ("8-K", ["2.02", "9.01"])
    assert eight_k[4] == eight_k[5] == datetime(2026, 11, 19, 21, 5, 12, tzinfo=UTC)

    # The ticker map is fetched once, and each resolved issuer's submissions only once.
    assert fake.paths().count("/files/company_tickers_exchange.json") == 1
    assert fake.paths().count(f"/submissions/CIK{NVDA_CIK}.json") == 1


def test_later_polls_add_only_new_filings_and_skip_unresolved_symbols(connect_app_db) -> None:
    conn = connect_app_db()
    fake = FakeSec()
    ingest_sec_filings(_run(conn), conn, sec=fake.client())
    fake.requests.clear()

    new_8k = _filing("0001045810-26-000102", "8-K", "2026-11-24T08:00:00.000Z", filed="2026-11-24", items="5.02")
    fake.routes[f"/submissions/CIK{NVDA_CIK}.json"] = _submissions("NVIDIA CORP", ["NVDA"], [new_8k, *NVDA_FILINGS])
    second = _run(conn)
    result = ingest_sec_filings(second, conn, sec=fake.client())

    assert result.status == "succeeded"
    assert result.error_message is None
    assert (result.fetched_count, result.inserted_count, result.deduped_count) == (6, 1, 5)
    # Unresolved symbols are not looked up again, so the ticker map is not fetched.
    assert "/files/company_tickers_exchange.json" not in fake.paths()
    assert {u["symbol"] for u in result.meta["unresolved"]} == {"AMZN", "ANT", "GOOGL", "SPCX"}

    rows = {row[0]: row for row in _filings(conn)}
    assert rows["0001045810-26-000102"][6:] == (False, second.id)
    assert rows["0001045810-26-000101"][7] != second.id  # first sighting is kept


def test_force_refresh_retries_failed_identities(connect_app_db) -> None:
    conn = connect_app_db()
    fake = FakeSec()
    ingest_sec_filings(_run(conn, tickers=["SPCX"]), conn, sec=fake.client())
    assert _instrument(conn, "SPCX")[4] == "failed"

    fake.routes["/files/company_tickers_exchange.json"] = {
        "fields": TICKER_MAP["fields"],
        # A fictional issuer, so the test asserts nothing about the real SPCX.
        "data": [*TICKER_MAP["data"], [1234567, "Example Space Co", "SPCX", "Nasdaq"]],
    }
    fake.routes["/submissions/CIK0001234567.json"] = _submissions("Example Space Co", ["SPCX"], [])

    assert ingest_sec_filings(_run(conn, tickers=["SPCX"]), conn, sec=fake.client()).status == "succeeded"
    assert _instrument(conn, "SPCX")[4] == "failed"  # not retried without force_refresh

    result = ingest_sec_filings(_run(conn, tickers=["SPCX"], force_refresh=True), conn, sec=fake.client())
    assert result.status == "succeeded"
    assert _instrument(conn, "SPCX")[:1] + _instrument(conn, "SPCX")[4:] == ("0001234567", "pending", None)


def test_an_unreachable_sec_fails_the_run_without_marking_instruments(connect_app_db) -> None:
    conn = connect_app_db()
    fake = FakeSec()
    fake.routes["/files/company_tickers_exchange.json"] = httpx.Response(503)

    result = ingest_sec_filings(_run(conn, tickers=["NVDA", "NBIS"]), conn, sec=fake.client())

    assert result.status == "failed"
    assert "HTTP 503" in result.error_message
    assert _instrument(conn, "NVDA")[4:] == ("pending", None)


def test_backfill_runs_mark_filings_as_backfill_and_missing_user_agent_fails_clearly(
    connect_app_db, monkeypatch
) -> None:
    conn = connect_app_db()
    fake = FakeSec()
    ingest_sec_filings(_run(conn, tickers=["NVDA"]), conn, sec=fake.client())
    newer = _filing("0001045810-26-000103", "8-K", "2026-11-24T09:00:00.000Z", filed="2026-11-24")
    fake.routes[f"/submissions/CIK{NVDA_CIK}.json"] = _submissions("NVIDIA CORP", ["NVDA"], [newer, *NVDA_FILINGS])

    ingest_sec_filings(_run(conn, tickers=["NVDA"], run_mode="backfill"), conn, sec=fake.client())
    assert {row[0]: row[6] for row in _filings(conn)}["0001045810-26-000103"] is True

    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(SecConfigurationError):
        ingest_sec_filings(_run(conn, tickers=["NVDA"]), conn)


def test_acceptance_times_in_the_future_are_flagged(connect_app_db) -> None:
    conn = connect_app_db()
    fake = FakeSec()
    later = (datetime.now(NEW_YORK) + timedelta(hours=2)).replace(tzinfo=None).isoformat(timespec="seconds") + ".000Z"
    future = _filing("0001045810-26-000104", "8-K", later, filed=date.today().isoformat())
    fake.routes[f"/submissions/CIK{NVDA_CIK}.json"] = _submissions("NVIDIA CORP", ["NVDA"], [future])

    result = ingest_sec_filings(_run(conn, tickers=["NVDA"]), conn, sec=fake.client())
    assert result.meta["future_acceptance"] == 1
