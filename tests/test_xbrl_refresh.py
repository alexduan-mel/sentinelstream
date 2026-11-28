"""Keeping XBRL facts current after onboarding, against Postgres and a fake SEC. No network."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx

from ingestion.xbrl_refresh import refresh_xbrl_facts
from tests.test_onboarding import NOW, NVDA_FACTS, TEN_Q, _count, _onboard, _run
from tests.test_price_ingestion import FakeMassive
from tests.test_sec_ingestion import NVDA_CIK, FakeSec

FACTS_PATH = f"/api/xbrl/companyfacts/CIK{NVDA_CIK}.json"
TEN_Q_ACCEPTED = datetime(2026, 11, 19, 21, 20, tzinfo=UTC)
# What companyfacts held before the 10-Q was filed: only a fact from another filing.
FACTS_BEFORE_TEN_Q = {"cik": 1045810, "facts": {"dei": NVDA_FACTS["facts"]["dei"]}}


def _onboarded_before_the_ten_q(conn) -> FakeSec:
    """NVDA onboarded (with its 10-Q in ``filings``) while companyfacts did not have the 10-Q yet."""
    sec = FakeSec()
    sec.routes[FACTS_PATH] = FACTS_BEFORE_TEN_Q
    _onboard(conn, sec, FakeMassive(), tickers=["NVDA"])
    assert _count(conn, "SELECT COUNT(*) FROM xbrl_facts WHERE accession_no = %s", TEN_Q) == 0
    sec.requests.clear()
    return sec


def _refresh(conn, sec: FakeSec, *, now: datetime = NOW, tickers: list[str] | None = None):
    run = _run(conn, task_id="refresh_xbrl_facts", tickers=tickers)
    return refresh_xbrl_facts(run, conn, sec=sec.client(), now=now)


def test_a_new_ten_q_reaches_xbrl_facts_as_of_its_acceptance(connect_app_db) -> None:
    conn = connect_app_db()
    sec = _onboarded_before_the_ten_q(conn)
    sec.routes[FACTS_PATH] = NVDA_FACTS

    result = _refresh(conn, sec)

    assert result.status == "succeeded"
    # The 8-K and the Form 4 in NVDA_FILINGS are not periodic reports, so only the 10-Q was due.
    assert result.meta["due"] == result.meta["loaded"] == [TEN_Q]
    assert result.meta["waiting"] == []
    assert result.inserted_count == 1
    assert sec.paths() == [FACTS_PATH]  # one request per issuer
    with conn.cursor() as cursor:
        cursor.execute("SELECT concept, value, known_at, task_run_id FROM xbrl_facts WHERE accession_no = %s", (TEN_Q,))
        rows = cursor.fetchall()
    conn.rollback()
    assert [(c, int(v), k) for c, v, k, _ in rows] == [("Revenues", 57006000000, TEN_Q_ACCEPTED)]
    assert rows[0][3] is not None

    # Rerunning finds nothing due and does not call SEC.
    sec.requests.clear()
    again = _refresh(conn, sec)
    assert again.status == "succeeded" and again.meta == {"message": "nothing_due"}
    assert sec.paths() == []


def test_a_filing_not_in_companyfacts_yet_waits_then_shows_as_partial(connect_app_db) -> None:
    conn = connect_app_db()
    sec = _onboarded_before_the_ten_q(conn)

    # An hour after acceptance, SEC not having processed it yet is normal.
    early = _refresh(conn, sec, now=TEN_Q_ACCEPTED + timedelta(hours=1))
    assert early.status == "succeeded"
    assert early.meta["waiting"] == [TEN_Q] and early.meta["loaded"] == []

    # A day later it is worth a look on the Tasks page, but not an alarm.
    late = _refresh(conn, sec, now=TEN_Q_ACCEPTED + timedelta(days=1, minutes=1))
    assert late.status == "partial"
    assert late.error_message == f"not in SEC company facts a day after acceptance: 10-Q {TEN_Q}"

    # Past the lookback it is no longer tried.
    sec.requests.clear()
    gone = _refresh(conn, sec, now=TEN_Q_ACCEPTED + timedelta(days=14, minutes=1))
    assert gone.meta == {"message": "nothing_due"} and sec.paths() == []


def test_sec_unreachable_fails_the_run_and_the_next_run_loads_the_filing(connect_app_db) -> None:
    conn = connect_app_db()
    sec = _onboarded_before_the_ten_q(conn)
    sec.routes[FACTS_PATH] = httpx.Response(503)

    failed = _refresh(conn, sec)
    assert failed.status == "failed"
    assert failed.error_message.startswith(f"{NVDA_CIK}: SEC request failed: HTTP 503")

    sec.routes[FACTS_PATH] = NVDA_FACTS
    assert _refresh(conn, sec).meta["loaded"] == [TEN_Q]


def test_only_ready_watchlist_issuers_and_named_tickers_are_refreshed(connect_app_db) -> None:
    conn = connect_app_db()
    sec = _onboarded_before_the_ten_q(conn)
    sec.routes[FACTS_PATH] = NVDA_FACTS

    assert _refresh(conn, sec, tickers=["NBIS"]).meta == {"message": "nothing_due"}

    # Still backfilling: onboarding loads its facts itself.
    with conn.cursor() as cursor:
        cursor.execute("UPDATE instruments SET onboarding_status = 'backfilling' WHERE symbol = 'NVDA'")
    conn.commit()
    assert _refresh(conn, sec).meta == {"message": "nothing_due"}
    assert sec.paths() == []
