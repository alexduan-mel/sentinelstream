"""Queueing and delivering alerts: dedup by accession, history and stale filings never pushed."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx

from alerts.deliver import MAX_AGE, deliver_alerts
from alerts.notifier import NotifierConfigError, NotifierError
from ingestion.sec_filings import ingest_sec_filings
from tests.form4_xml import form4, transaction
from tests.test_classify import _archive, _form4_filing, _nvda_lists
from tests.test_sec_ingestion import NBIS_CIK, NVDA_CIK, FakeSec, _filing, _run

ANT_CIK = "0002130357"
NOW = datetime(2026, 11, 26, 22, 0, tzinfo=UTC)
ITEM_101 = "Item 1.01 Entry into a Material Definitive Agreement"


def _setup(conn) -> None:
    with conn.cursor() as cursor:
        for symbol, cik in (("NVDA", NVDA_CIK), ("NBIS", NBIS_CIK), ("ANT", ANT_CIK)):
            cursor.execute("UPDATE instruments SET cik = %s WHERE symbol = %s", (cik, symbol))
    conn.commit()


def _filing_row(conn, accession: str, *, accepted_at: datetime, form_type: str = "8-K") -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            "INSERT INTO filings (accession_no, cik, form_type, filed_date, accepted_at, known_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (accession, accession[:10], form_type, accepted_at.date(), accepted_at, accepted_at),
        )
    conn.commit()


def _link(conn, accession: str, cik: str, level: str | None, *, is_backfill: bool = False, reason: str = ITEM_101):
    with conn.cursor() as cursor:
        cursor.execute(
            "INSERT INTO filing_issuers "
            "(accession_no, cik, is_backfill, materiality, materiality_reason, classified_at) "
            "VALUES (%s, %s, %s, %s, %s, CASE WHEN %s::text IS NULL THEN NULL ELSE NOW() END)",
            (accession, cik, is_backfill, level, reason if level else None, level),
        )
    conn.commit()


def _deliveries(conn) -> dict[str, tuple]:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT accession_no, status, attempts, last_error, skip_reason, delivered_at IS NOT NULL "
            "FROM alert_deliveries WHERE channel = 'slack' ORDER BY accession_no"
        )
        rows = {row[0]: row[1:] for row in cursor.fetchall()}
    conn.rollback()
    return rows


def _edgar(when: datetime) -> str:
    """An acceptance time as the submissions API writes it."""
    return when.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _deliver(conn, notifier, *, now: datetime = NOW):
    return deliver_alerts(conn, channel="slack", make_notifier=lambda: notifier, now=now)


def test_a_high_news_filing_is_sent_once_naming_every_high_issuer(connect_app_db, notifier) -> None:
    conn = connect_app_db()
    _setup(conn)
    accession = "0002130357-26-000010"
    _filing_row(conn, accession, accepted_at=NOW - timedelta(minutes=4))
    _link(conn, accession, ANT_CIK, "high")
    _link(conn, accession, NBIS_CIK, "high")
    _link(
        conn, accession, NVDA_CIK, "low", reason="Form 4 for issuer CIK 0002130357; this company is a reporting owner"
    )

    stats = _deliver(conn, notifier)
    again = _deliver(conn, notifier)

    assert [alert.tickers for alert in notifier.sent] == [["ANT", "NBIS"]]
    assert notifier.sent[0].reasons == [ITEM_101]
    assert notifier.sent[0].sec_url.endswith(f"/2130357/000213035726000010/{accession}-index.htm")
    assert stats.as_meta() == {"queued": 1, "skipped": 0, "delivered": 1, "pending": 0}
    assert again.as_meta() == {"queued": 0, "skipped": 0, "delivered": 0, "pending": 0}
    assert _deliveries(conn) == {accession: ("delivered", 1, None, None, True)}


def test_history_low_and_unclassified_links_are_never_queued(connect_app_db, notifier) -> None:
    conn = connect_app_db()
    _setup(conn)
    for accession, level, backfill in (
        ("0001045810-26-000001", "high", True),
        ("0001045810-26-000002", "low", False),
        ("0001045810-26-000003", None, False),
    ):
        _filing_row(conn, accession, accepted_at=NOW - timedelta(hours=1))
        _link(conn, accession, NVDA_CIK, level, is_backfill=backfill)

    stats = _deliver(conn, notifier)

    assert notifier.sent == []
    assert notifier.closed == 0  # nothing pending, so no notifier was even created
    assert stats.queued == 0
    assert _deliveries(conn) == {}


def test_a_filing_accepted_too_long_ago_is_recorded_as_skipped_not_sent(connect_app_db, notifier) -> None:
    conn = connect_app_db()
    _setup(conn)
    old, fresh = "0001045810-26-000001", "0001045810-26-000002"
    _filing_row(conn, old, accepted_at=NOW - MAX_AGE - timedelta(minutes=1))
    _filing_row(conn, fresh, accepted_at=NOW - MAX_AGE + timedelta(minutes=1))
    for accession in (old, fresh):
        _link(conn, accession, NVDA_CIK, "high")

    stats = _deliver(conn, notifier)

    assert [alert.accession_no for alert in notifier.sent] == [fresh]
    assert stats.skipped == 1
    assert _deliveries(conn)[old] == ("skipped", 0, None, "accepted more than 3 days before it could be sent", False)


def test_a_failed_send_stays_pending_stops_the_batch_and_is_retried(connect_app_db, notifier) -> None:
    conn = connect_app_db()
    _setup(conn)
    first, second = "0001045810-26-000001", "0001045810-26-000002"
    _filing_row(conn, first, accepted_at=NOW - timedelta(minutes=9))
    _filing_row(conn, second, accepted_at=NOW - timedelta(minutes=8))
    for accession in (first, second):
        _link(conn, accession, NVDA_CIK, "high")

    notifier.fail_with = NotifierError("Slack answered HTTP 503: unavailable")
    failed = _deliver(conn, notifier)
    assert failed.errors == [f"{first}: Slack answered HTTP 503: unavailable"]
    assert failed.pending == 2
    assert _deliveries(conn) == {
        first: ("pending", 1, "Slack answered HTTP 503: unavailable", None, False),
        second: ("pending", 0, None, None, False),
    }
    assert notifier.closed == 1

    notifier.fail_with = None
    _deliver(conn, notifier, now=NOW + timedelta(minutes=5))
    assert [alert.accession_no for alert in notifier.sent] == [first, second]  # oldest acceptance first
    assert _deliveries(conn)[first] == ("delivered", 2, None, None, True)


def test_a_pending_alert_never_delivered_within_max_age_is_given_up(connect_app_db, notifier) -> None:
    conn = connect_app_db()
    _setup(conn)
    accession = "0001045810-26-000001"
    _filing_row(conn, accession, accepted_at=NOW - timedelta(minutes=5))
    _link(conn, accession, NVDA_CIK, "high")
    notifier.fail_with = NotifierError("Slack answered HTTP 404: no_service")
    _deliver(conn, notifier)

    stats = _deliver(conn, notifier, now=NOW + MAX_AGE)

    assert stats.skipped == 1 and stats.pending == 0
    assert _deliveries(conn)[accession][0] == "skipped"
    assert _deliveries(conn)[accession][3] == (
        "not delivered within 3 days of acceptance: Slack answered HTTP 404: no_service"
    )


def test_missing_webhook_leaves_alerts_queued_with_a_clear_error(connect_app_db) -> None:
    conn = connect_app_db()
    _setup(conn)
    accession = "0001045810-26-000001"
    _filing_row(conn, accession, accepted_at=NOW - timedelta(minutes=5))
    _link(conn, accession, NVDA_CIK, "high")

    def not_configured():
        raise NotifierConfigError("SLACK_WEBHOOK_URL is not set")

    stats = deliver_alerts(conn, channel="slack", make_notifier=not_configured, now=NOW)

    assert stats.errors == ["1 alert(s) waiting: SLACK_WEBHOOK_URL is not set"]
    assert _deliveries(conn) == {accession: ("pending", 0, None, None, False)}


# --- inside the poll -------------------------------------------------------------------------------


def test_the_poll_pushes_a_new_purchase_but_not_the_first_polls_history(connect_app_db, notifier) -> None:
    conn = connect_app_db()
    fake = FakeSec()
    first = ingest_sec_filings(_run(conn), conn, sec=fake.client())
    assert notifier.sent == []  # NVDA's history includes a high 8-K; history is never pushed
    assert first.meta["alerts"]["queued"] == 0

    accession = "0001045810-26-000300"
    accepted = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=3)
    _nvda_lists(fake, _form4_filing(accession, _edgar(accepted)))
    fake.routes[_archive(accession)] = httpx.Response(200, content=form4(NVDA_CIK, transaction("P", "500", "180")))
    second = ingest_sec_filings(_run(conn), conn, sec=fake.client())

    assert second.status == "succeeded"
    assert second.meta["alerts"] == {"queued": 1, "skipped": 0, "delivered": 1, "pending": 0}
    assert [(alert.accession_no, alert.tickers, alert.form_type) for alert in notifier.sent] == [
        (accession, ["NVDA"], "4")
    ]

    ingest_sec_filings(_run(conn), conn, sec=fake.client())
    assert len(notifier.sent) == 1


def test_a_poll_whose_alert_could_not_be_sent_is_partial(connect_app_db, notifier) -> None:
    conn = connect_app_db()
    fake = FakeSec()
    ingest_sec_filings(_run(conn), conn, sec=fake.client())

    accession = "0001045810-26-000301"
    accepted = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=2)
    _nvda_lists(fake, _filing(accession, "8-K", _edgar(accepted), filed=accepted.date().isoformat(), items="5.02"))
    notifier.fail_with = NotifierError("Slack answered HTTP 500: oops")
    result = ingest_sec_filings(_run(conn), conn, sec=fake.client())

    assert result.status == "partial"
    assert f"{accession}: Slack answered HTTP 500: oops" in result.error_message
    assert _deliveries(conn)[accession][:2] == ("pending", 1)
