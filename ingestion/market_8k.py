"""``ingest_market_8k``: every 8-K filed in the last two years, with items and acceptance time (ADR 0017).

Each run does two steps, small enough that the default worker is free again within about 40 seconds:

1. **Index.** It reads at most one quarter of EDGAR's full index (``form.idx``): the oldest quarter
   not read yet, otherwise one not yet complete whose last read is over ``INDEX_REFRESH_HOURS`` old.
   Every 8-K and 8-K/A line adds its filer to ``market_issuers``, or moves the filer's
   ``latest_8k_date`` forward. A quarter is complete once read more than ``INDEX_SETTLE_DAYS`` after
   it ended.
2. **Submissions.** For up to ``max_issuers`` companies (default 150) whose latest 8-K is dated after
   ``covered_through`` and before today, it reads the submissions record, plus older pages when the
   recent block does not reach back two years. It stores every 8-K and 8-K/A dated in the window
   and the company's name, tickers, exchanges and industry code. ``covered_through`` becomes the day
   before the read: every filing dated up to then was accepted before it (EDGAR dates a filing
   accepted after 17:30 with the next business day). Waiting until a filing's date has passed means
   a company is never read twice in a day.

A company SEC has no record for is marked with the error and not asked again until a new 8-K
appears. Any other SEC error stops the run, which is then ``partial`` or ``failed``.
"""

from __future__ import annotations

import io
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from psycopg2.extensions import connection as Connection
from psycopg2.extras import execute_values

from ingestion.insider_datasets import quarter_end, quarter_names
from ingestion.market_bars import history_start
from ingestion.market_calendar import NEW_YORK
from ingestion.sec_client import SecClient, SecError, SecNotFoundError
from ingestion.sec_filings import FilingRecord, normalise_filing_columns, normalise_recent_filings
from orchestration.task_runs import RunResult, TaskRun

LOGGER = logging.getLogger(__name__)

TASK_ID = "ingest_market_8k"
FORMS = ("8-K", "8-K/A")
DEFAULT_MAX_ISSUERS = 150
INDEX_REFRESH_HOURS = 20
INDEX_SETTLE_DAYS = 3


def parse_form_index(text: str, since: date) -> dict[str, tuple[str, date]]:
    """CIK → (company name, latest 8-K date) for 8-K and 8-K/A lines dated on or after ``since``.

    Lines are fixed width, but company names hold spaces, so the CIK, date and file name are read
    from the right.
    """
    latest: dict[str, tuple[str, date]] = {}
    for line in io.StringIO(text):
        form = line[:12].strip()
        if form not in FORMS:
            continue
        tokens = line[12:].split()
        if len(tokens) < 4:
            continue
        try:
            cik, filed = f"{int(tokens[-3]):010d}", date.fromisoformat(tokens[-2])
        except ValueError:
            continue
        if filed < since:
            continue
        name = " ".join(tokens[:-3])
        held = latest.get(cik)
        if held is None or filed > held[1]:
            latest[cik] = (name, filed)
    return latest


def _max_issuers(payload: dict[str, Any]) -> int:
    raw = payload.get("max_issuers", DEFAULT_MAX_ISSUERS)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        raise ValueError(f"max_issuers must be a positive integer, got {raw!r}")
    return value


def _index_to_read(conn: Connection, quarters: list[str], now: datetime) -> str | None:
    with conn.cursor() as cursor:
        cursor.execute("SELECT quarter, complete, fetched_at FROM edgar_index_quarters")
        state = {quarter: (complete, fetched_at) for quarter, complete, fetched_at in cursor.fetchall()}
    conn.commit()
    for quarter in quarters:
        if quarter not in state:
            return quarter
    for quarter in quarters:
        complete, fetched_at = state[quarter]
        if not complete and now - fetched_at > timedelta(hours=INDEX_REFRESH_HOURS):
            return quarter
    return None


def store_index(
    conn: Connection, quarter: str, latest: dict[str, tuple[str, date]], *, complete: bool, task_run_id: int
) -> None:
    with conn.cursor() as cursor:
        if latest:
            execute_values(
                cursor,
                """
                INSERT INTO market_issuers (cik, name, latest_8k_date, task_run_id) VALUES %s
                ON CONFLICT (cik) DO UPDATE SET
                  latest_8k_date = GREATEST(market_issuers.latest_8k_date, EXCLUDED.latest_8k_date),
                  name = COALESCE(market_issuers.name, EXCLUDED.name)
                """,
                [(cik, name, filed, task_run_id) for cik, (name, filed) in latest.items()],
                page_size=2000,
            )
        cursor.execute(
            """
            INSERT INTO edgar_index_quarters (quarter, eight_k_count, complete, task_run_id) VALUES (%s, %s, %s, %s)
            ON CONFLICT (quarter) DO UPDATE SET
              eight_k_count = EXCLUDED.eight_k_count, complete = EXCLUDED.complete,
              task_run_id = EXCLUDED.task_run_id, fetched_at = NOW()
            """,
            (quarter, len(latest), complete, task_run_id),
        )
    conn.commit()


def _due_issuers(conn: Connection, today: date, limit: int) -> list[str]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT cik FROM market_issuers
            WHERE latest_8k_date < %(today)s AND (covered_through IS NULL OR latest_8k_date > covered_through)
            ORDER BY covered_through NULLS FIRST, cik
            LIMIT %(limit)s
            """,
            {"today": today, "limit": limit},
        )
        ciks = [cik for (cik,) in cursor.fetchall()]
    conn.commit()
    return ciks


def _remaining(conn: Connection, today: date) -> int:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM market_issuers "
            "WHERE latest_8k_date < %s AND (covered_through IS NULL OR latest_8k_date > covered_through)",
            (today,),
        )
        (count,) = cursor.fetchone()
    conn.commit()
    return count


def eight_k_records(sec: SecClient, cik: str, submissions: dict[str, Any], since: date) -> list[FilingRecord]:
    """The company's 8-Ks dated on or after ``since``, reading older pages only when needed."""
    records, _invalid = normalise_recent_filings(cik, submissions)
    oldest = min((record.filed_date for record in records), default=None)
    if oldest is not None and oldest > since:
        for page in (submissions.get("filings") or {}).get("files") or []:
            if str(page.get("filingTo") or "") < since.isoformat():
                continue
            older, _invalid = normalise_filing_columns(cik, sec.submissions_page(str(page["name"])))
            records.extend(older)
    return [r for r in records if r.form_type in FORMS and r.filed_date >= since]


def _text_list(values: Any) -> list[str]:
    return [str(v).strip() for v in values or [] if v is not None and str(v).strip()]


def store_issuer(
    conn: Connection,
    cik: str,
    submissions: dict[str, Any] | None,
    records: list[FilingRecord],
    *,
    covered_through: date,
    error: str | None,
    task_run_id: int,
) -> int:
    """Upsert the company's 8-Ks and what its record says, in one transaction; returns 8-Ks stored."""
    info = submissions or {}
    with conn.cursor() as cursor:
        cursor.execute(
            """
            UPDATE market_issuers SET
              name = COALESCE(%(name)s, name), tickers = %(tickers)s, exchanges = %(exchanges)s,
              sic = %(sic)s, sic_description = %(sic_description)s, entity_type = %(entity_type)s,
              covered_through = %(covered)s, submissions_fetched_at = NOW(), fetch_error = %(error)s,
              task_run_id = %(run)s
            WHERE cik = %(cik)s
            """,
            {
                "cik": cik,
                "name": (str(info.get("name") or "").strip() or None),
                "tickers": _text_list(info.get("tickers")),
                "exchanges": _text_list(info.get("exchanges")),
                "sic": (str(info.get("sic") or "").strip() or None),
                "sic_description": (str(info.get("sicDescription") or "").strip() or None),
                "entity_type": (str(info.get("entityType") or "").strip() or None),
                "covered": covered_through,
                "error": error,
                "run": task_run_id,
            },
        )
        if records:
            execute_values(
                cursor,
                """
                INSERT INTO market_8k_filings (accession_no, cik, form_type, items, filed_date, accepted_at, known_at,
                                               task_run_id)
                VALUES %s
                ON CONFLICT (accession_no) DO UPDATE SET
                  items = EXCLUDED.items, accepted_at = EXCLUDED.accepted_at, known_at = EXCLUDED.known_at,
                  task_run_id = EXCLUDED.task_run_id
                """,
                [
                    (r.accession_no, cik, r.form_type, r.items, r.filed_date, r.accepted_at, r.accepted_at, task_run_id)
                    for r in {r.accession_no: r for r in records}.values()
                ],
                page_size=1000,
            )
    conn.commit()
    return len({r.accession_no for r in records})


def ingest_market_8k(
    run: TaskRun, conn: Connection, *, sec: SecClient | None = None, now: datetime | None = None
) -> RunResult:
    now = now or datetime.now(UTC)
    today = now.astimezone(NEW_YORK).date()
    since = history_start(today)
    max_issuers = _max_issuers(run.payload)
    meta: dict[str, Any] = {"index_quarter": None, "index_companies": 0, "issuers": 0, "filings": 0, "errors": []}

    quarters = quarter_names(since, today)
    to_read = _index_to_read(conn, quarters, now)
    due = _due_issuers(conn, today, max_issuers)
    if to_read is None and not due:
        return RunResult(status="succeeded", meta={**meta, "remaining_issuers": 0, "message": "already_current"})

    client = sec or SecClient.from_env()
    covered = today - timedelta(days=1)
    try:
        if to_read is not None:
            try:
                text = client.full_index(int(to_read[:4]), int(to_read[5]))
            except SecError as exc:
                meta["errors"].append(f"index {to_read}: {exc}")
            else:
                latest = parse_form_index(text, since)
                complete = today > quarter_end(to_read) + timedelta(days=INDEX_SETTLE_DAYS)
                store_index(conn, to_read, latest, complete=complete, task_run_id=run.id)
                meta["index_quarter"], meta["index_companies"] = to_read, len(latest)
                due = _due_issuers(conn, today, max_issuers)
        for cik in due if not meta["errors"] else []:
            try:
                submissions = client.submissions(cik)
                records = eight_k_records(client, cik, submissions, since)
            except SecNotFoundError as exc:
                store_issuer(conn, cik, None, [], covered_through=covered, error=str(exc), task_run_id=run.id)
                meta["issuers"] += 1
                continue
            except (SecError, KeyError, ValueError) as exc:
                meta["errors"].append(f"CIK {cik}: {exc}")
                LOGGER.warning("market_8k_fetch_failed run_id=%d cik=%s error=%s", run.id, cik, exc)
                break
            meta["filings"] += store_issuer(
                conn, cik, submissions, records, covered_through=covered, error=None, task_run_id=run.id
            )
            meta["issuers"] += 1
    finally:
        if sec is None:
            client.close()

    meta["remaining_issuers"] = _remaining(conn, today)
    stored = meta["index_quarter"] is not None or meta["issuers"] > 0
    status = "succeeded" if not meta["errors"] else ("partial" if stored else "failed")
    return RunResult(
        status=status,
        fetched_count=meta["filings"],
        inserted_count=meta["filings"],
        error_message="; ".join(meta["errors"]) or None,
        meta=meta,
    )
