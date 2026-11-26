"""Ticker onboarding against Postgres, a fake SEC and a fake Massive. No test touches the network."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest

from filings.xbrl import parse_company_facts
from ingestion.massive_client import MassiveConfigurationError
from ingestion.onboarding import onboard_tickers
from ingestion.sec_filings import ingest_sec_filings
from orchestration.task_runs import TaskRun, enqueue_run
from tests.test_price_ingestion import FakeMassive
from tests.test_sec_ingestion import NBIS_CIK, NVDA_CIK, NVDA_FILINGS, TICKER_MAP, FakeSec, _filing, _submissions

# Wednesday 25 Nov 2026, after the close: two years back is 25 Nov 2024.
NOW = datetime(2026, 11, 25, 22, 0, tzinfo=UTC)
TEN_Q = "0001045810-26-000100"  # NVDA's 10-Q in NVDA_FILINGS, accepted 2026-11-19 21:20 UTC

NVDA_FACTS = {
    "cik": 1045810,
    "entityName": "NVIDIA CORP",
    "facts": {
        "us-gaap": {
            "Revenues": {
                "label": "Revenues",
                "units": {
                    "USD": [
                        {
                            "start": "2026-08-01",
                            "end": "2026-10-31",
                            "val": 57006000000,
                            "accn": TEN_Q,
                            "fy": 2027,
                            "fp": "Q3",
                            "form": "10-Q",
                            "filed": "2026-11-19",
                            "frame": "CY2026Q3",
                        },
                        # Filed before the two-year window: not loaded.
                        {"start": "2021-02-01", "end": "2021-04-30", "val": 5, "accn": "0001045810-21-000001",
                         "filed": "2021-05-26"},
                        # Malformed: skipped and counted.
                        {"end": "2026-10-31", "val": "n/a", "accn": TEN_Q, "filed": "2026-11-19"},
                    ]
                },
            }
        },
        "dei": {
            "EntityCommonStockSharesOutstanding": {
                "units": {
                    "shares": [
                        # From a filing not stored in ``filings``.
                        {"end": "2026-11-14", "val": 24300000000, "accn": "0001045810-26-000555",
                         "filed": "2026-11-20"},
                    ]
                }
            }
        },
    },
}  # fmt: skip


def _fake_sec() -> FakeSec:
    fake = FakeSec()
    fake.routes[f"/api/xbrl/companyfacts/CIK{NVDA_CIK}.json"] = NVDA_FACTS
    return fake


def _run(conn, *, tickers: list[str] | None = None, force_refresh: bool = False, task_id: str = "onboard_tickers"):
    run_id = enqueue_run(
        conn, task_id, trigger_source="test", run_mode="manual", requested_by="pytest", tickers=tickers,
        force_refresh=force_refresh,
    )  # fmt: skip
    conn.commit()
    return TaskRun(run_id, task_id, "t", "test", "manual", "pytest", tickers or [], None, force_refresh, {})


def _onboard(conn, sec: FakeSec, massive: FakeMassive, **kwargs):
    return onboard_tickers(_run(conn, **kwargs), conn, sec=sec.client(), massive=massive.client(), now=NOW)


def _status(conn) -> dict[str, tuple]:
    with conn.cursor() as cursor:
        cursor.execute("SELECT symbol, onboarding_status, cik, filer_type, onboarding_error FROM instruments")
        rows = {row[0]: row[1:] for row in cursor.fetchall()}
    conn.rollback()
    return rows


def _count(conn, sql: str, *params) -> int:
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        value = cursor.fetchone()[0]
    conn.rollback()
    return value


def test_the_seeded_watchlist_is_onboarded_or_rejected_with_reasons(connect_app_db) -> None:
    conn = connect_app_db()
    sec, massive = _fake_sec(), FakeMassive()

    result = _onboard(conn, sec, massive)

    status = _status(conn)
    assert status["NVDA"] == ("ready", NVDA_CIK, "domestic", None)
    assert status["NBIS"] == ("ready", NBIS_CIK, "foreign_private", None)
    assert status["QQQ"][0] == status["SPY"][0] == "ready"  # benchmarks: prices only
    assert status["ANT"][0] == "failed" and "files as a fund" in status["ANT"][3]
    for symbol in ("AMZN", "GOOGL", "SPCX"):
        assert status[symbol] == ("failed", None, None, f"{symbol} is not in SEC's ticker map")

    # A symbol SEC rejects is the input being wrong, not the system failing.
    assert result.status == "succeeded"
    assert sorted(result.meta["ready"]) == ["NBIS", "NVDA", "QQQ", "SPY"]
    assert {r["symbol"] for r in result.meta["rejected"]} == {"AMZN", "ANT", "GOOGL", "SPCX"}

    # Filings are history, never news for Slack.
    assert _count(conn, "SELECT COUNT(*) FROM filing_issuers WHERE NOT is_backfill") == 0
    assert _count(conn, "SELECT COUNT(*) FROM filing_issuers WHERE cik = %s", NVDA_CIK) == 3
    # Two years of bars, ending at the last closed session.
    bars = _count(conn, "SELECT COUNT(*) FROM price_bars p JOIN instruments i ON i.id = p.instrument_id "
                        "WHERE i.symbol = 'NVDA' AND trading_date BETWEEN '2024-11-25' AND '2026-11-25'")  # fmt: skip
    assert 495 <= bars <= 505
    assert _count(conn, "SELECT MAX(trading_date) = '2026-11-25' FROM price_bars") is True
    # Benchmarks never touch SEC; NBIS has no XBRL facts (SEC answers 404).
    assert not any("QQQ" in path or "SPY" in path for path in sec.paths())
    assert _count(conn, "SELECT COUNT(*) FROM xbrl_facts WHERE cik = %s", NBIS_CIK) == 0


def test_xbrl_facts_carry_when_they_became_public(connect_app_db) -> None:
    conn = connect_app_db()
    sec = _fake_sec()
    onboard_tickers(_run(conn, tickers=["NVDA"]), conn, sec=sec.client(), massive=FakeMassive().client(), now=NOW)

    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT taxonomy, concept, unit, period_start, period_end, value, accession_no, fiscal_period, known_at "
            "FROM xbrl_facts ORDER BY concept"
        )
        rows = cursor.fetchall()
    conn.rollback()
    assert rows == [
        # Not in ``filings``: midnight New York after the filing date, never before acceptance.
        ("dei", "EntityCommonStockSharesOutstanding", "shares", None, date(2026, 11, 14), Decimal(24300000000),
         "0001045810-26-000555", None, datetime(2026, 11, 21, 5, 0, tzinfo=UTC)),
        # In ``filings``: its EDGAR acceptance time.
        ("us-gaap", "Revenues", "USD", date(2026, 8, 1), date(2026, 10, 31), Decimal(57006000000), TEN_Q, "Q3",
         datetime(2026, 11, 19, 21, 20, tzinfo=UTC)),
    ]  # fmt: skip


def test_a_rerun_adds_nothing_and_keeps_everything_ready(connect_app_db) -> None:
    conn = connect_app_db()
    sec, massive = _fake_sec(), FakeMassive()
    _onboard(conn, sec, massive, tickers=["NVDA"])
    counts = [_count(conn, f"SELECT COUNT(*) FROM {table}") for table in ("filing_issuers", "price_bars", "xbrl_facts")]

    again = _onboard(conn, sec, massive, tickers=["NVDA"])

    assert again.status == "succeeded"
    assert [
        _count(conn, f"SELECT COUNT(*) FROM {table}") for table in ("filing_issuers", "price_bars", "xbrl_facts")
    ] == counts
    assert _status(conn)["NVDA"][0] == "ready"
    # Nothing is left pending, so a scheduled sweep finds nothing to do.
    assert _onboard(conn, sec, massive).meta.get("message") == "nothing_to_onboard"


def test_a_transient_error_keeps_the_status_and_the_next_run_resumes(connect_app_db) -> None:
    conn = connect_app_db()
    sec, massive = _fake_sec(), FakeMassive()
    sec.routes[f"/api/xbrl/companyfacts/CIK{NVDA_CIK}.json"] = httpx.Response(503)

    failed = _onboard(conn, sec, massive, tickers=["NVDA", "NBIS"])

    assert failed.status == "partial"
    nvda = _status(conn)["NVDA"]
    assert nvda[:3] == ("backfilling", NVDA_CIK, "domestic")  # identified, so the poller watches it
    assert nvda[3].startswith("xbrl: SEC request failed: HTTP 503")
    assert _status(conn)["NBIS"][0] == "ready"

    sec.routes[f"/api/xbrl/companyfacts/CIK{NVDA_CIK}.json"] = NVDA_FACTS
    resumed = _onboard(conn, sec, massive, tickers=["NVDA"])
    assert resumed.status == "succeeded"
    assert _status(conn)["NVDA"] == ("ready", NVDA_CIK, "domestic", None)


def test_backfilling_tickers_are_polled_and_their_history_is_never_news(connect_app_db) -> None:
    conn = connect_app_db()
    sec, massive = _fake_sec(), FakeMassive()
    sec.routes[f"/api/xbrl/companyfacts/CIK{NVDA_CIK}.json"] = httpx.Response(503)
    _onboard(conn, sec, massive, tickers=["NVDA"])  # stops at XBRL: backfilling

    new_8k = _filing("0001045810-26-000102", "8-K", "2026-11-25T13:00:00.000Z", filed="2026-11-25", items="5.02")
    sec.routes[f"/submissions/CIK{NVDA_CIK}.json"] = _submissions("NVIDIA CORP", ["NVDA"], [new_8k, *NVDA_FILINGS])
    poll = ingest_sec_filings(_run(conn, task_id="ingest_sec_filings"), conn, sec=sec.client())

    assert NVDA_CIK in poll.meta["issuers"]
    with conn.cursor() as cursor:
        cursor.execute("SELECT accession_no FROM filing_issuers WHERE NOT is_backfill")
        news = [row[0] for row in cursor.fetchall()]
    conn.rollback()
    assert news == ["0001045810-26-000102"]


def test_older_filing_pages_are_read_back_two_years_only(connect_app_db) -> None:
    conn = connect_app_db()
    sec, massive = _fake_sec(), FakeMassive()
    recent = [_filing("0001045810-26-000300", "10-Q", "2026-08-27T20:00:00.000Z", filed="2026-08-27")]
    record = _submissions("NVIDIA CORP", ["NVDA"], recent)
    record["filings"]["files"] = [
        {"name": "CIK0001045810-submissions-001.json", "filingFrom": "2024-06-01", "filingTo": "2026-05-31"},
        {"name": "CIK0001045810-submissions-002.json", "filingFrom": "2019-01-01", "filingTo": "2024-05-31"},
    ]
    sec.routes[f"/submissions/CIK{NVDA_CIK}.json"] = record
    page = _submissions("x", [], [
        _filing("0001045810-25-000050", "10-K", "2025-02-26T21:00:00.000Z", filed="2025-02-26"),
        _filing("0001045810-24-000040", "10-Q", "2024-08-28T20:00:00.000Z", filed="2024-08-28"),  # too old
    ])["filings"]["recent"]  # fmt: skip
    sec.routes["/submissions/CIK0001045810-submissions-001.json"] = page

    _onboard(conn, sec, massive, tickers=["NVDA"])

    with conn.cursor() as cursor:
        cursor.execute("SELECT accession_no FROM filing_issuers WHERE cik = %s ORDER BY accession_no", (NVDA_CIK,))
        stored = [row[0] for row in cursor.fetchall()]
    conn.rollback()
    assert stored == ["0001045810-25-000050", "0001045810-26-000300"]
    assert "/submissions/CIK0001045810-submissions-002.json" not in sec.paths()


def test_prices_start_at_the_listing_and_bars_of_an_earlier_security_are_dropped(connect_app_db) -> None:
    conn = connect_app_db()
    sec, massive = _fake_sec(), FakeMassive()
    # NVDA stands in for a reused symbol: an ETF traded under it before this company listed.
    massive.details["NVDA"] = {"ticker": "NVDA", "name": "NVIDIA", "cik": "0001045810", "list_date": "2026-06-12"}
    with conn.cursor() as cursor:
        cursor.execute(
            "INSERT INTO price_bars (instrument_id, trading_date, close, provider) "
            "SELECT id, DATE '2026-03-02', 22.0, 'massive' FROM instruments WHERE symbol = 'NVDA'"
        )
    conn.commit()

    result = _onboard(conn, sec, massive, tickers=["NVDA"])

    assert result.meta["bars_before_listing_dropped"] == {"NVDA": 1}
    assert _count(conn, "SELECT MIN(trading_date) = '2026-06-12' FROM price_bars p JOIN instruments i "
                        "ON i.id = p.instrument_id WHERE i.symbol = 'NVDA'") is True  # fmt: skip
    aggs = next(r for r in massive.requests if "/aggs/" in r.url.path)
    assert "/2026-06-12/2026-11-25" in aggs.url.path


def test_benchmarks_are_named_from_massive_and_equities_keep_secs_name(connect_app_db) -> None:
    conn = connect_app_db()
    sec, massive = _fake_sec(), FakeMassive()
    massive.details["QQQ"] = {"ticker": "QQQ", "name": "Invesco QQQ Trust, Series 1", "list_date": "1999-03-10"}
    massive.details["NVDA"] = {"ticker": "NVDA", "name": "Nvidia Corp", "cik": "0001045810", "list_date": "1999-01-22"}

    _onboard(conn, sec, massive, tickers=["QQQ", "NVDA"])

    with conn.cursor() as cursor:
        cursor.execute("SELECT symbol, name FROM instruments WHERE symbol IN ('QQQ', 'NVDA', 'SPY') ORDER BY symbol")
        names = dict(cursor.fetchall())
    conn.rollback()
    assert names == {"NVDA": "NVIDIA CORP", "QQQ": "Invesco QQQ Trust, Series 1", "SPY": None}


def test_a_symbol_massive_ties_to_another_company_loads_no_prices(connect_app_db) -> None:
    conn = connect_app_db()
    sec, massive = _fake_sec(), FakeMassive()
    massive.details["NVDA"] = {
        "ticker": "NVDA",
        "name": "Some Other Co",
        "cik": "0000000042",
        "list_date": "2020-01-02",
    }

    result = _onboard(conn, sec, massive, tickers=["NVDA"])

    assert result.status == "partial"  # the sweep onboards the other seeded tickers
    nvda = _status(conn)["NVDA"]
    assert nvda[0] == "backfilling"
    assert nvda[3] == "prices: Massive lists NVDA under CIK 0000000042 (Some Other Co), but SEC under 0001045810"
    assert "NVDA" not in massive.symbols()
    assert _count(conn, "SELECT COUNT(*) FROM price_bars p JOIN instruments i ON i.id = p.instrument_id "
                        "WHERE i.symbol = 'NVDA'") == 0  # fmt: skip


def test_force_refresh_looks_a_rejected_symbol_up_again(connect_app_db) -> None:
    conn = connect_app_db()
    sec, massive = _fake_sec(), FakeMassive()
    _onboard(conn, sec, massive, tickers=["SPCX"])
    assert _status(conn)["SPCX"][0] == "failed"

    # A fictional issuer, so the test asserts nothing about the real SPCX.
    sec.routes["/files/company_tickers_exchange.json"] = {
        "fields": TICKER_MAP["fields"],
        "data": [*TICKER_MAP["data"], [1234567, "Example Space Co", "SPCX", "Nasdaq"]],
    }
    sec.routes["/submissions/CIK0001234567.json"] = _submissions(
        "Example Space Co", ["SPCX"], [_filing("0001234567-26-000001", "8-K", "2026-11-02T14:00:00.000Z",
                                                filed="2026-11-02")]
    )  # fmt: skip

    # A sweep leaves failed tickers alone; a retry for the symbol looks it up again.
    _onboard(conn, sec, massive)
    assert _status(conn)["SPCX"][0] == "failed"
    _onboard(conn, sec, massive, tickers=["SPCX"], force_refresh=True)
    assert _status(conn)["SPCX"] == ("ready", "0001234567", "domestic", None)


def test_missing_price_credentials_fail_the_run_clearly(connect_app_db, monkeypatch) -> None:
    conn = connect_app_db()
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    with pytest.raises(MassiveConfigurationError, match="MASSIVE_API_KEY is not set"):
        onboard_tickers(_run(conn, tickers=["QQQ"]), conn, sec=_fake_sec().client(), now=NOW)


# --- XBRL parsing ----------------------------------------------------------------------------------


def test_company_facts_are_flattened_and_filtered_by_filing_date() -> None:
    facts, invalid = parse_company_facts(NVDA_FACTS, filed_since=date(2024, 11, 25))

    assert invalid == 1
    assert [(f.taxonomy, f.concept, f.unit, f.value) for f in facts] == [
        ("us-gaap", "Revenues", "USD", Decimal(57006000000)),
        ("dei", "EntityCommonStockSharesOutstanding", "shares", Decimal(24300000000)),
    ]
    revenue = facts[0]
    assert (revenue.period_start, revenue.period_end, revenue.fiscal_year, revenue.frame) == (
        date(2026, 8, 1),
        date(2026, 10, 31),
        2027,
        "CY2026Q3",
    )
    assert parse_company_facts({}, filed_since=date(2024, 1, 1)) == ([], 0)
