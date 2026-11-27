"""Queue high filings and push them through a notifier, at most once per filing and channel (ADR 0009).

Runs at the end of ``ingest_sec_filings``, after classification, so a high 8-K reaches Slack in the
same poll that found it. Each run:

1. Queues every filing that has a high news link (``is_backfill = FALSE``) and no delivery row for
   the channel yet. ``alert_deliveries`` is keyed on (accession number, channel), so a filing linked
   to several watchlist issuers is one message naming each of them, and reruns never queue it twice.
   A filing accepted more than ``MAX_AGE`` ago is recorded as skipped instead: pushing it now would
   be late news (say, an old Form 4 classified after an outage).
2. Gives up on pending rows that have grown older than ``MAX_AGE`` without being delivered.
3. Sends what is pending, oldest acceptance first. The notifier is created only when something is
   pending, so a missing webhook setting matters only when there is an alert to send. The first
   failure stops the run's sending; what is left stays pending for the next poll.

A row is marked delivered right after the channel accepts it. If the worker died between the two, the
next run would send that alert again: a duplicate is preferred to a lost alert.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from psycopg2.extensions import connection as Connection

from alerts.base_rate import base_rates_for, history_line
from alerts.notifier import Alert, AlertIssuer, Notifier, NotifierConfigError, NotifierError
from filings.edgar import sec_index_url

LOGGER = logging.getLogger(__name__)

MAX_AGE = timedelta(days=3)


@dataclass
class DeliveryStats:
    queued: int = 0
    skipped: int = 0
    delivered: int = 0
    pending: int = 0
    errors: list[str] = field(default_factory=list)

    def as_meta(self) -> dict[str, Any]:
        return {"queued": self.queued, "skipped": self.skipped, "delivered": self.delivered, "pending": self.pending}


def _queue(conn: Connection, channel: str, cutoff: datetime, run_id: int | None) -> tuple[int, int]:
    """Create delivery rows for high news filings not yet queued. Returns (pending, skipped)."""
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO alert_deliveries (accession_no, channel, status, skip_reason, task_run_id)
            SELECT f.accession_no, %(channel)s,
                   CASE WHEN f.accepted_at < %(cutoff)s THEN 'skipped' ELSE 'pending' END,
                   CASE WHEN f.accepted_at < %(cutoff)s THEN %(too_old)s END,
                   %(run_id)s
            FROM filings f
            WHERE EXISTS (
                SELECT 1 FROM filing_issuers l
                WHERE l.accession_no = f.accession_no AND l.materiality = 'high' AND NOT l.is_backfill
            )
            ON CONFLICT (accession_no, channel) DO NOTHING
            RETURNING status
            """,
            {
                "channel": channel,
                "cutoff": cutoff,
                "too_old": f"accepted more than {MAX_AGE.days} days before it could be sent",
                "run_id": run_id,
            },
        )
        statuses = [row[0] for row in cursor.fetchall()]
    conn.commit()
    return statuses.count("pending"), statuses.count("skipped")


def _expire(conn: Connection, channel: str, cutoff: datetime) -> int:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            UPDATE alert_deliveries d
            SET status = 'skipped',
                skip_reason = 'not delivered within ' || %(days)s || ' days of acceptance'
                              || COALESCE(': ' || d.last_error, '')
            FROM filings f
            WHERE f.accession_no = d.accession_no
              AND d.channel = %(channel)s AND d.status = 'pending' AND f.accepted_at < %(cutoff)s
            """,
            {"channel": channel, "cutoff": cutoff, "days": str(MAX_AGE.days)},
        )
        expired = cursor.rowcount
    conn.commit()
    return expired


def _pending(conn: Connection, channel: str) -> list[Alert]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT f.accession_no, f.form_type, f.accepted_at, f.cik,
                   COALESCE(ticker.symbol, l.cik), l.materiality_reason, f.items, l.cik
            FROM alert_deliveries d
            JOIN filings f USING (accession_no)
            JOIN filing_issuers l
              ON l.accession_no = d.accession_no AND l.materiality = 'high' AND NOT l.is_backfill
            LEFT JOIN LATERAL (
                -- The same ticker the Alerts page shows: an active watchlist symbol first.
                SELECT symbol FROM instruments i
                WHERE i.cik = l.cik
                ORDER BY (i.active AND i.role = 'watchlist') DESC, i.symbol
                LIMIT 1
            ) ticker ON TRUE
            WHERE d.channel = %s AND d.status = 'pending'
            ORDER BY f.accepted_at, f.accession_no, 5
            """,
            (channel,),
        )
        rows = cursor.fetchall()
    conn.commit()
    alerts: dict[str, Alert] = {}
    for accession_no, form_type, accepted_at, filing_cik, ticker, reason, items, issuer_cik in rows:
        alert = alerts.get(accession_no)
        issuer = AlertIssuer(ticker=ticker, reason=reason)
        if alert is None:
            alerts[accession_no] = Alert(
                accession_no=accession_no,
                form_type=form_type,
                accepted_at=accepted_at,
                sec_url=sec_index_url(filing_cik, accession_no),
                issuers=(issuer,),
                history=_history(conn, form_type, list(items or []), issuer_cik),
            )
        else:
            alerts[accession_no] = replace(alert, issuers=(*alert.issuers, issuer))
    return list(alerts.values())


def _history(conn: Connection, form_type: str, items: list[str], cik: str) -> tuple[str, ...]:
    """Base-rate lines for the alert's first high issuer (ADR 0018). Never stops an alert going out."""
    try:
        lines = [history_line(rate) for rate in base_rates_for(conn, form_type=form_type, items=items, cik=cik)]
        conn.commit()
    except Exception:  # noqa: BLE001 - the alert matters more than its history line
        conn.rollback()
        LOGGER.warning("alert_history_failed form=%s cik=%s", form_type, cik, exc_info=True)
        return ()
    return tuple(line for line in lines if line)


def _record(conn: Connection, channel: str, accession_no: str, run_id: int | None, error: str | None) -> datetime:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            UPDATE alert_deliveries
            SET attempts = attempts + 1, last_attempt_at = NOW(), task_run_id = %(run_id)s,
                last_error = %(error)s,
                status = CASE WHEN %(error)s::text IS NULL THEN 'delivered' ELSE status END,
                delivered_at = CASE WHEN %(error)s::text IS NULL THEN NOW() END
            WHERE accession_no = %(accession_no)s AND channel = %(channel)s AND status = 'pending'
            RETURNING last_attempt_at
            """,
            {"run_id": run_id, "error": error, "accession_no": accession_no, "channel": channel},
        )
        row = cursor.fetchone()
    conn.commit()
    return row[0] if row else datetime.now(UTC)


def deliver_alerts(
    conn: Connection,
    *,
    channel: str,
    make_notifier: Callable[[], Notifier],
    run_id: int | None = None,
    now: datetime | None = None,
) -> DeliveryStats:
    stats = DeliveryStats()
    cutoff = (now or datetime.now(UTC)) - MAX_AGE
    stats.queued, stats.skipped = _queue(conn, channel, cutoff, run_id)
    stats.skipped += _expire(conn, channel, cutoff)
    alerts = _pending(conn, channel)
    stats.pending = len(alerts)
    if not alerts:
        return stats

    try:
        notifier = make_notifier()
    except NotifierConfigError as exc:
        stats.errors.append(f"{len(alerts)} alert(s) waiting: {exc}")
        LOGGER.error("alert_notifier_not_configured channel=%s pending=%d error=%s", channel, len(alerts), exc)
        return stats
    try:
        for alert in alerts:
            try:
                notifier.send(alert)
            except NotifierError as exc:
                _record(conn, channel, alert.accession_no, run_id, str(exc))
                stats.errors.append(f"{alert.accession_no}: {exc}")
                LOGGER.warning(
                    "alert_delivery_failed channel=%s accession_no=%s error=%s", channel, alert.accession_no, exc
                )
                break
            delivered_at = _record(conn, channel, alert.accession_no, run_id, None)
            stats.delivered += 1
            stats.pending -= 1
            LOGGER.info(
                "alert_delivered channel=%s accession_no=%s tickers=%s latency_seconds=%.0f",
                channel,
                alert.accession_no,
                ",".join(alert.tickers),
                (delivered_at - alert.accepted_at).total_seconds(),
            )
    finally:
        notifier.close()
    return stats
