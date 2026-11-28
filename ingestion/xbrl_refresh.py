"""``refresh_xbrl_facts``: load the XBRL facts of each new periodic report from the watchlist (ADR 0021).

Onboarding loads two years of facts once (ADR 0010). After that, a company's new 10-Q, 10-K or 20-F
reaches ``filings`` through the SEC poller, and this hourly task brings its numbers into
``xbrl_facts``. A filing is **due** when it is:

- a periodic report with XBRL (``PERIODIC_FORMS``, amendments included),
- linked to an active watchlist equity whose onboarding is ``ready`` (one still backfilling gets its
  facts from onboarding itself),
- accepted within the last ``LOOKBACK``, and
- not yet the source of any stored fact.

Each run fetches the companyfacts record once per issuer with a due filing and stores every fact
filed since the earliest due one, so restatements and amendments arrive as their own rows (the
financials read the latest-known value). ``known_at`` is the filing's EDGAR acceptance time, which
``filings`` holds for every due filing (ground rule 2).

SEC usually adds a filing to companyfacts within minutes, so a due filing that is still missing is
normally just early and is tried again next hour. One still missing ``STALE_AFTER`` after acceptance
makes the run ``partial``, so the Tasks page shows it; after ``LOOKBACK`` it is no longer tried
(``onboard_tickers`` with ``force_refresh`` reloads everything).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from psycopg2.extensions import connection as Connection

from filings.xbrl import parse_company_facts
from ingestion.onboarding import store_xbrl_facts
from ingestion.sec_client import SecClient, SecError
from orchestration.task_runs import RunResult, TaskRun

LOGGER = logging.getLogger(__name__)

TASK_ID = "refresh_xbrl_facts"
LOOKBACK = timedelta(days=14)
STALE_AFTER = timedelta(days=1)
# Periodic reports that carry financial statements: quarterly and annual, domestic and foreign,
# transition-period reports (after a fiscal-year change) and amendments.
PERIODIC_FORMS = tuple(
    form + suffix for form in ("10-Q", "10-K", "10-QT", "10-KT", "20-F", "40-F") for suffix in ("", "/A")
)


@dataclass(frozen=True)
class DueFiling:
    cik: str
    accession_no: str
    form_type: str
    filed_date: date
    accepted_at: datetime


def due_filings(conn: Connection, *, now: datetime, tickers: list[str]) -> list[DueFiling]:
    """Periodic reports from ready watchlist issuers, accepted within ``LOOKBACK``, with no stored facts."""
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT DISTINCT fi.cik, f.accession_no, f.form_type, f.filed_date, f.accepted_at
            FROM filings f
            JOIN filing_issuers fi ON fi.accession_no = f.accession_no
            JOIN instruments i ON i.cik = fi.cik
            WHERE i.active AND i.asset_type = 'equity' AND i.role = 'watchlist'
              AND i.onboarding_status = 'ready'
              AND (cardinality(%(tickers)s::text[]) = 0 OR i.symbol = ANY(%(tickers)s::text[]))
              AND f.is_xbrl AND f.form_type = ANY(%(forms)s::text[])
              AND f.accepted_at >= %(since)s
              AND NOT EXISTS (
                SELECT 1 FROM xbrl_facts x WHERE x.accession_no = f.accession_no AND x.cik = fi.cik
              )
            ORDER BY fi.cik, f.accepted_at
            """,
            {"tickers": tickers, "forms": list(PERIODIC_FORMS), "since": now - LOOKBACK},
        )
        rows = cursor.fetchall()
    conn.commit()
    return [DueFiling(*row) for row in rows]


def _stored_accessions(conn: Connection, cik: str, accessions: list[str]) -> set[str]:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT accession_no FROM xbrl_facts WHERE cik = %s AND accession_no = ANY(%s::text[])",
            (cik, accessions),
        )
        rows = cursor.fetchall()
    conn.commit()
    return {row[0] for row in rows}


def refresh_xbrl_facts(
    run: TaskRun, conn: Connection, *, sec: SecClient | None = None, now: datetime | None = None
) -> RunResult:
    now = now or datetime.now(UTC)
    due = due_filings(conn, now=now, tickers=run.tickers)
    if not due:
        return RunResult(status="succeeded", meta={"message": "nothing_due"})

    by_cik: dict[str, list[DueFiling]] = defaultdict(list)
    for filing in due:
        by_cik[filing.cik].append(filing)

    meta: dict[str, Any] = {"due": [f.accession_no for f in due], "loaded": [], "waiting": [], "errors": []}
    inserted = fetched = 0
    stale: list[str] = []
    failed_ciks = 0
    client = sec or SecClient.from_env()
    try:
        for cik, filings in by_cik.items():
            try:
                payload = client.company_facts(cik)
            except SecError as exc:
                failed_ciks += 1
                meta["errors"].append(f"{cik}: {exc}")
                LOGGER.warning("xbrl_refresh_failed cik=%s error=%s", cik, exc)
                continue
            facts, invalid = parse_company_facts(payload or {}, filed_since=min(f.filed_date for f in filings))
            if invalid:
                LOGGER.warning("xbrl_facts_invalid cik=%s count=%d", cik, invalid)
            fetched += len(facts)
            inserted += store_xbrl_facts(conn, cik, facts, task_run_id=run.id)

            stored = _stored_accessions(conn, cik, [f.accession_no for f in filings])
            for filing in filings:
                if filing.accession_no in stored:
                    meta["loaded"].append(filing.accession_no)
                    continue
                meta["waiting"].append(filing.accession_no)
                if now - filing.accepted_at >= STALE_AFTER:
                    stale.append(f"{filing.form_type} {filing.accession_no}")
    finally:
        if sec is None:
            client.close()

    LOGGER.info(
        "xbrl_refresh_done issuers=%d loaded=%d waiting=%d inserted=%d",
        len(by_cik),
        len(meta["loaded"]),
        len(meta["waiting"]),
        inserted,
    )
    errors = list(meta["errors"])
    if stale:
        errors.append(f"not in SEC company facts a day after acceptance: {', '.join(stale)}")
    if failed_ciks == len(by_cik):
        status = "failed"
    elif errors:
        status = "partial"
    else:
        status = "succeeded"
    return RunResult(
        status=status,
        fetched_count=fetched,
        inserted_count=inserted,
        deduped_count=fetched - inserted,
        error_message="; ".join(errors) or None,
        meta=meta,
    )
