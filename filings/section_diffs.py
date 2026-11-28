"""``diff_10k_sections``: what changed in each watchlist 10-K's risk factors and MD&A since last year (ADR 0023).

A pair is **due** when a 10-K's section was found, the same issuer's previous 10-K has the same
section found with the same extractor version, that previous 10-K was accepted at most
``MAX_GAP`` earlier (so a missing year is never skipped over), and no diff by the current
``COMPARER_VERSION`` exists for that pair. ``force_refresh`` recomputes every pair.

Each pair is compared with ``filings.tenk_diff`` and replaced in one transaction, so a rerun leaves
the same rows. The task reads only this database, so it runs on the research queue.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from psycopg2.extensions import connection as Connection
from psycopg2.extras import execute_values

from filings.tenk_diff import COMPARER_VERSION, Comparison, compare
from orchestration.task_runs import RunResult, TaskRun

LOGGER = logging.getLogger(__name__)

TASK_ID = "diff_10k_sections"
MAX_GAP = timedelta(days=450)


@dataclass(frozen=True)
class Pair:
    cik: str
    item: str
    old_accession_no: str
    new_accession_no: str
    extractor_version: int
    known_at: datetime


def due_pairs(conn: Connection, *, tickers: list[str], force: bool) -> list[Pair]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            WITH found AS (
              SELECT fi.cik, s.item, s.accession_no, s.extractor_version, s.known_at, f.accepted_at
              FROM filing_sections s
              JOIN filings f ON f.accession_no = s.accession_no
              JOIN filing_issuers fi ON fi.accession_no = s.accession_no
              WHERE s.found AND f.form_type = '10-K'
                AND (cardinality(%(tickers)s::text[]) = 0 OR EXISTS (
                  SELECT 1 FROM instruments i WHERE i.cik = fi.cik AND i.symbol = ANY(%(tickers)s::text[])
                ))
            ),
            paired AS (
              SELECT cik, item, accession_no, extractor_version, known_at, accepted_at,
                     LAG(accession_no) OVER w AS old_accession_no,
                     LAG(accepted_at) OVER w AS old_accepted_at,
                     LAG(extractor_version) OVER w AS old_extractor_version
              FROM found
              WINDOW w AS (PARTITION BY cik, item ORDER BY accepted_at)
            )
            SELECT p.cik, p.item, p.old_accession_no, p.accession_no, p.extractor_version, p.known_at
            FROM paired p
            WHERE p.old_accession_no IS NOT NULL
              AND p.accepted_at - p.old_accepted_at <= %(max_gap)s
              AND p.old_extractor_version = p.extractor_version
              AND (%(force)s OR NOT EXISTS (
                SELECT 1 FROM filing_section_diffs d
                WHERE d.new_accession_no = p.accession_no AND d.item = p.item
                  AND d.old_accession_no = p.old_accession_no AND d.comparer_version = %(version)s
              ))
            ORDER BY p.accepted_at DESC, p.cik, p.item
            """,
            {"tickers": tickers, "force": force, "version": COMPARER_VERSION, "max_gap": MAX_GAP},
        )
        rows = cursor.fetchall()
    conn.commit()
    return [Pair(*row) for row in rows]


def _paragraphs(conn: Connection, accession_no: str, item: str) -> list[str]:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT text FROM filing_paragraphs WHERE accession_no = %s AND item = %s ORDER BY paragraph_no",
            (accession_no, item),
        )
        return [row[0] for row in cursor.fetchall()]


def store_diff(conn: Connection, pair: Pair, comparison: Comparison, *, task_run_id: int) -> None:
    """Replace the pair's diff and its changes in one transaction."""
    changed = [c for c in comparison.changes if c.kind == "changed"]
    with conn.cursor() as cursor:
        cursor.execute(
            "DELETE FROM filing_section_diffs WHERE new_accession_no = %s AND item = %s",
            (pair.new_accession_no, pair.item),
        )
        cursor.execute(
            """
            INSERT INTO filing_section_diffs (new_accession_no, item, old_accession_no, cik, extractor_version,
                                              comparer_version, unchanged_count, moved_count, changed_count,
                                              numbers_only_count, added_count, removed_count, known_at, task_run_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                pair.new_accession_no,
                pair.item,
                pair.old_accession_no,
                pair.cik,
                pair.extractor_version,
                COMPARER_VERSION,
                comparison.unchanged,
                comparison.moved,
                len(changed),
                sum(1 for c in changed if c.numbers_only),
                comparison.count("added"),
                comparison.count("removed"),
                pair.known_at,
                task_run_id,
            ),
        )
        if comparison.changes:
            execute_values(
                cursor,
                """
                INSERT INTO filing_paragraph_changes (new_accession_no, item, change_no, kind, old_paragraph_no,
                                                      new_paragraph_no, similarity, numbers_only, known_at)
                VALUES %s
                """,
                [
                    (
                        pair.new_accession_no,
                        pair.item,
                        number,
                        change.kind,
                        change.old_no,
                        change.new_no,
                        change.similarity,
                        change.numbers_only,
                        pair.known_at,
                    )
                    for number, change in enumerate(comparison.changes, start=1)
                ],
                page_size=500,
            )
    conn.commit()


def diff_10k_sections(run: TaskRun, conn: Connection) -> RunResult:
    pairs = due_pairs(conn, tickers=run.tickers, force=run.force_refresh)
    if not pairs:
        return RunResult(status="succeeded", meta={"message": "nothing_due"})
    meta: dict[str, Any] = {"compared": {}}
    changes = 0
    for pair in pairs:
        comparison = compare(
            _paragraphs(conn, pair.old_accession_no, pair.item), _paragraphs(conn, pair.new_accession_no, pair.item)
        )
        conn.commit()
        store_diff(conn, pair, comparison, task_run_id=run.id)
        counts = {
            "old": pair.old_accession_no,
            "unchanged": comparison.unchanged,
            "moved": comparison.moved,
            "changed": comparison.count("changed"),
            "added": comparison.count("added"),
            "removed": comparison.count("removed"),
        }
        meta["compared"][f"{pair.new_accession_no} Item {pair.item}"] = counts
        changes += len(comparison.changes)
        LOGGER.info("tenk_diff_stored new=%s item=%s counts=%s", pair.new_accession_no, pair.item, counts)
    return RunResult(status="succeeded", fetched_count=len(pairs), inserted_count=changes, meta=meta)
