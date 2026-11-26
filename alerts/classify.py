"""Classify unclassified filing-issuer links with the materiality rules (ADR 0008).

Runs inside ``ingest_sec_filings`` right after new filings are stored, so a high filing is known
within the same poll. Every link that is news (``is_backfill = FALSE``) is classified on every run.
History is classified too, so the Alerts page shows a level for every row, but its Form 4s need one
SEC request each, so at most ``BACKFILL_FORM4_PER_RUN`` of them are fetched per run.

A Form 4 whose XML cannot be fetched is left unclassified and retried next run. One whose XML is
fetched but cannot be read is classified low with the reason, and logged, rather than retried
forever.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from psycopg2.extensions import connection as Connection
from psycopg2.extras import execute_values

from alerts.materiality import Materiality, classify, needs_form4
from filings.form_4 import Form4, Form4Error, parse_form4, raw_xml_name
from ingestion.sec_client import SecClient, SecError

LOGGER = logging.getLogger(__name__)

BACKFILL_FORM4_PER_RUN = 25


@dataclass
class ClassifyStats:
    classified: int = 0
    high: int = 0
    form4_fetched: int = 0
    form4_unreadable: int = 0
    form4_waiting: int = 0
    errors: list[str] = field(default_factory=list)
    news_failed: int = 0

    def as_meta(self) -> dict[str, Any]:
        return {
            "classified": self.classified,
            "high": self.high,
            "form4_fetched": self.form4_fetched,
            "form4_unreadable": self.form4_unreadable,
            "form4_waiting": self.form4_waiting,
        }


@dataclass(frozen=True)
class _Link:
    cik: str
    is_backfill: bool
    filer_type: str | None


@dataclass
class _Filing:
    accession_no: str
    form_type: str
    items: list[str]
    filing_cik: str
    primary_document: str | None
    known_at: datetime
    links: list[_Link] = field(default_factory=list)

    @property
    def is_news(self) -> bool:
        return any(not link.is_backfill for link in self.links)

    @property
    def needs_form4(self) -> bool:
        return needs_form4(self.form_type)


def _unclassified(conn: Connection) -> list[_Filing]:
    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT f.accession_no, f.form_type, f.items, f.cik, f.primary_document, f.known_at,
                   l.cik, l.is_backfill, issuer.filer_type
            FROM filing_issuers l
            JOIN filings f USING (accession_no)
            LEFT JOIN LATERAL (
                SELECT filer_type FROM instruments i
                WHERE i.cik = l.cik AND i.filer_type IS NOT NULL
                ORDER BY i.active DESC, i.symbol
                LIMIT 1
            ) issuer ON TRUE
            WHERE l.materiality IS NULL
            ORDER BY l.is_backfill, f.known_at DESC, f.accession_no, l.cik
            """)
        rows = cursor.fetchall()
    conn.commit()
    filings: dict[str, _Filing] = {}
    for accession_no, form_type, items, filing_cik, primary_document, known_at, cik, is_backfill, filer_type in rows:
        filing = filings.setdefault(
            accession_no, _Filing(accession_no, form_type, list(items), filing_cik, primary_document, known_at)
        )
        filing.links.append(_Link(cik, is_backfill, filer_type))
    news = [f for f in filings.values() if f.is_news]
    history = [f for f in filings.values() if not f.is_news]
    return news + history


def _store(conn: Connection, filing: _Filing, results: dict[str, Materiality], form4: Form4 | None) -> None:
    with conn.cursor() as cursor:
        if form4 is not None and form4.transactions:
            execute_values(
                cursor,
                """
                INSERT INTO form4_transactions
                  (accession_no, line_no, issuer_cik, reporting_owners, owner_roles, security_title,
                   transaction_date, transaction_code, acquired_disposed, shares, price_per_share,
                   shares_owned_after, direct_indirect, known_at)
                VALUES %s
                ON CONFLICT (accession_no, line_no) DO NOTHING
                """,
                [
                    (
                        filing.accession_no,
                        t.line_no,
                        form4.issuer_cik,
                        "; ".join(form4.reporting_owners),
                        "; ".join(form4.owner_roles),
                        t.security_title,
                        t.transaction_date,
                        t.code,
                        t.acquired_disposed if t.acquired_disposed in ("A", "D") else None,
                        t.shares,
                        t.price_per_share,
                        t.shares_owned_after,
                        t.direct_indirect if t.direct_indirect in ("D", "I") else None,
                        filing.known_at,
                    )
                    for t in form4.transactions
                ],
            )
        for cik, result in results.items():
            cursor.execute(
                """
                UPDATE filing_issuers
                SET materiality = %s, materiality_reason = %s, classified_at = NOW()
                WHERE accession_no = %s AND cik = %s AND materiality IS NULL
                """,
                (result.level, result.reason, filing.accession_no, cik),
            )
    conn.commit()


def _fetch_form4(client: SecClient, filing: _Filing) -> Form4:
    """The parsed Form 4. Raises SecError if it could not be fetched, Form4Error if unreadable."""
    name = raw_xml_name(filing.primary_document)
    if name is None:
        raise Form4Error(f"primary document {filing.primary_document!r} is not an XML file")
    return parse_form4(client.archive_file(filing.filing_cik, filing.accession_no, name))


def classify_links(conn: Connection, client: SecClient, *, backfill_form4_budget: int | None = None) -> ClassifyStats:
    stats = ClassifyStats()
    budget = BACKFILL_FORM4_PER_RUN if backfill_form4_budget is None else backfill_form4_budget
    for filing in _unclassified(conn):
        form4: Form4 | None = None
        unreadable: str | None = None
        if filing.needs_form4:
            if not filing.is_news:
                if budget <= 0:
                    stats.form4_waiting += 1
                    continue
                budget -= 1
            try:
                form4 = _fetch_form4(client, filing)
                stats.form4_fetched += 1
            except SecError as exc:
                stats.errors.append(f"Form 4 {filing.accession_no}: {exc}")
                if filing.is_news:
                    stats.news_failed += 1
                continue
            except Form4Error as exc:
                stats.form4_fetched += 1
                stats.form4_unreadable += 1
                unreadable = str(exc)
                LOGGER.warning("form4_unreadable accession_no=%s reason=%s", filing.accession_no, exc)

        results: dict[str, Materiality] = {}
        for link in filing.links:
            if unreadable is not None:
                results[link.cik] = Materiality("low", f"Form 4 XML unreadable: {unreadable}")
            else:
                results[link.cik] = classify(filing.form_type, filing.items, link.filer_type, link.cik, form4)
        _store(conn, filing, results, form4)

        stats.classified += len(results)
        for cik, result in results.items():
            if result.level == "high":
                stats.high += 1
                link = next(link for link in filing.links if link.cik == cik)
                if link.is_backfill:
                    continue  # history: counted, not logged one line each
                LOGGER.info(
                    "filing_high accession_no=%s cik=%s form_type=%s reason=%s",
                    filing.accession_no,
                    cik,
                    filing.form_type,
                    result.reason,
                )
    return stats
