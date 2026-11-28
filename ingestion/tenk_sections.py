"""``extract_10k_sections``: risk factors and MD&A from watchlist 10-Ks, as numbered paragraphs (ADR 0022).

A 10-K is **due** when it is one of the two most recent 10-Ks of a ready, active watchlist equity
(this year's and last year's, which the year-over-year diff compares) and has no sections extracted
by the current ``EXTRACTOR_VERSION``. Raising the version re-extracts every 10-K with the new
parser; a run with ``force_refresh`` re-extracts those 10-Ks whatever version they were read with.

For each due 10-K the run fetches its primary document from EDGAR, the filing as accepted, and
records its SHA-256. ``filings.tenk`` finds Item 1A and Item 7 and splits them into paragraphs. The
10-K's sections are replaced in one transaction, so a rerun leaves the same rows. A section that is
not found is recorded as such and makes the run ``partial`` once, not on every run. At most
``MAX_PER_RUN`` 10-Ks are read per run, newest first; the rest wait for the next run.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg2.extensions import connection as Connection
from psycopg2.extras import execute_values

from filings.tenk import SECTIONS, Section, extract_sections
from ingestion.sec_client import SecClient, SecError
from orchestration.task_runs import RunResult, TaskRun

LOGGER = logging.getLogger(__name__)

TASK_ID = "extract_10k_sections"
# Raise when filings.tenk changes what it extracts, so stored 10-Ks are extracted again.
EXTRACTOR_VERSION = 1
LATEST_PER_ISSUER = 2
MAX_PER_RUN = 8


@dataclass(frozen=True)
class DueTenK:
    cik: str
    accession_no: str
    primary_document: str | None
    known_at: datetime


def due_10ks(conn: Connection, *, tickers: list[str], force: bool) -> list[DueTenK]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            WITH ranked AS (
              SELECT fi.cik, f.accession_no, f.primary_document, f.known_at, f.accepted_at,
                     ROW_NUMBER() OVER (PARTITION BY fi.cik ORDER BY f.accepted_at DESC) AS recency
              FROM filings f
              JOIN filing_issuers fi ON fi.accession_no = f.accession_no
              WHERE f.form_type = '10-K'
                AND EXISTS (
                  SELECT 1 FROM instruments i
                  WHERE i.cik = fi.cik AND i.active AND i.asset_type = 'equity' AND i.role = 'watchlist'
                    AND i.onboarding_status = 'ready'
                    AND (cardinality(%(tickers)s::text[]) = 0 OR i.symbol = ANY(%(tickers)s::text[]))
                )
            )
            SELECT cik, accession_no, primary_document, known_at
            FROM ranked r
            WHERE recency <= %(latest)s
              AND (%(force)s OR NOT EXISTS (
                SELECT 1 FROM filing_sections s
                WHERE s.accession_no = r.accession_no AND s.extractor_version = %(version)s
              ))
            ORDER BY accepted_at DESC, accession_no
            """,
            {"tickers": tickers, "latest": LATEST_PER_ISSUER, "force": force, "version": EXTRACTOR_VERSION},
        )
        rows = cursor.fetchall()
    conn.commit()
    return [DueTenK(*row) for row in rows]


def decode_document(raw: bytes) -> str:
    """EDGAR documents are UTF-8 (inline XBRL) or, in older filings, Windows-1252."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def store_sections(
    conn: Connection,
    tenk: DueTenK,
    sections: dict[str, Section | None],
    *,
    sha256: str,
    task_run_id: int,
) -> None:
    """Replace the 10-K's sections and paragraphs with ``sections`` in one transaction."""
    with conn.cursor() as cursor:
        cursor.execute("DELETE FROM filing_sections WHERE accession_no = %s", (tenk.accession_no,))
        for key, section in sections.items():
            item = key.upper()
            cursor.execute(
                """
                INSERT INTO filing_sections (accession_no, item, found, heading, anchor, paragraph_count,
                                             tables_skipped, source_document, source_sha256, extractor_version,
                                             known_at, task_run_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    tenk.accession_no,
                    item,
                    section is not None,
                    section.heading if section else None,
                    section.anchor if section else None,
                    len(section.paragraphs) if section else 0,
                    section.tables_skipped if section else 0,
                    tenk.primary_document,
                    sha256,
                    EXTRACTOR_VERSION,
                    tenk.known_at,
                    task_run_id,
                ),
            )
            if section and section.paragraphs:
                execute_values(
                    cursor,
                    "INSERT INTO filing_paragraphs (accession_no, item, paragraph_no, text, known_at) VALUES %s",
                    [
                        (tenk.accession_no, item, number, text, tenk.known_at)
                        for number, text in enumerate(section.paragraphs, start=1)
                    ],
                    page_size=500,
                )
    conn.commit()


def extract_10k_sections(run: TaskRun, conn: Connection, *, sec: SecClient | None = None) -> RunResult:
    due = due_10ks(conn, tickers=run.tickers, force=run.force_refresh)
    if not due:
        return RunResult(status="succeeded", meta={"message": "nothing_due"})
    batch = due[:MAX_PER_RUN]
    meta: dict[str, Any] = {"extracted": {}, "not_found": [], "errors": [], "remaining": len(due) - len(batch)}
    paragraphs = 0
    client = sec or SecClient.from_env()
    try:
        for tenk in batch:
            if not tenk.primary_document:
                meta["errors"].append(f"{tenk.accession_no}: no primary document recorded")
                continue
            try:
                raw = client.archive_file(tenk.cik, tenk.accession_no, tenk.primary_document)
            except (SecError, ValueError) as exc:
                meta["errors"].append(f"{tenk.accession_no}: {exc}")
                LOGGER.warning("tenk_fetch_failed accession=%s error=%s", tenk.accession_no, exc)
                continue
            sections = extract_sections(decode_document(raw))
            store_sections(conn, tenk, sections, sha256=hashlib.sha256(raw).hexdigest(), task_run_id=run.id)
            counts = {key.upper(): len(s.paragraphs) if s else 0 for key, s in sections.items()}
            meta["extracted"][tenk.accession_no] = counts
            paragraphs += sum(counts.values())
            for key, section in sections.items():
                if section is None:
                    meta["not_found"].append(f"Item {key.upper()} ({SECTIONS[key]}) in 10-K {tenk.accession_no}")
            LOGGER.info("tenk_sections_extracted accession=%s counts=%s", tenk.accession_no, counts)
    finally:
        if sec is None:
            client.close()

    problems = meta["errors"] + [f"not found: {entry}" for entry in meta["not_found"]]
    if not meta["extracted"]:
        status = "failed"
    elif problems:
        status = "partial"
    else:
        status = "succeeded"
    return RunResult(
        status=status,
        fetched_count=len(meta["extracted"]),
        inserted_count=paragraphs,
        error_message="; ".join(problems) or None,
        meta=meta,
    )
