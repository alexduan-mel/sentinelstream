"""What changed in a watchlist company's latest 10-K since the year before, for one section (ADR 0023).

Serves the newest stored comparison of Item 1A (risk factors) or Item 7 (MD&A): both 10-Ks with
links to the section in each document, the counts, and every added, removed or changed paragraph
with its text and paragraph numbers. Changes are grouped under the heading they fall under, in this
year's reading order. A changed paragraph comes as word runs (kept, deleted, inserted), so the page
can show last year's and this year's wording in one place. When Claude has summarized the changes,
``summary`` carries the checked points, each citing the changes it rests on (ADR 0024).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.db import DbConn
from filings.edgar import sec_document_url, sec_index_url
from filings.tenk import SECTIONS
from filings.tenk_diff import heading_before, word_changes

router = APIRouter(prefix="/api", tags=["filings"])

Item = Literal["1A", "7"]


class TenK(BaseModel):
    accession_no: str
    filed_date: date
    report_date: date | None
    heading: str | None
    # The section in the 10-K document as filed, opened at its heading when the anchor is known.
    section_url: str
    index_url: str


class Counts(BaseModel):
    unchanged: int
    moved: int
    changed: int
    numbers_only: int
    added: int
    removed: int


class WordRun(BaseModel):
    op: Literal["equal", "delete", "insert"]
    text: str


class ParagraphChange(BaseModel):
    change_no: int
    kind: Literal["added", "removed", "changed"]
    old_paragraph_no: int | None
    new_paragraph_no: int | None
    similarity: float | None
    numbers_only: bool
    # This year's text; last year's for a removed paragraph.
    text: str
    # For a changed paragraph: this year's text against last year's, word by word.
    words: list[WordRun] | None = None


class ChangeGroup(BaseModel):
    heading: str | None
    changes: list[ParagraphChange]


class SummaryPoint(BaseModel):
    text: str
    # The changes (change_no) the point rests on; each cites paragraphs in both 10-Ks.
    change_nos: list[int]


class ChangeSummary(BaseModel):
    # ok: points passed every check (none means nothing significant changed); rejected / refused:
    # nothing to show; no_changes: only figures or dates changed (ADR 0024).
    status: Literal["ok", "rejected", "refused", "no_changes"]
    model: str | None
    written_at: datetime
    points: list[SummaryPoint]
    rejected_count: int


class Comparison(BaseModel):
    new: TenK
    old: TenK
    counts: Counts
    computed_at: datetime
    groups: list[ChangeGroup]
    summary: ChangeSummary | None = None


class TenKChanges(BaseModel):
    symbol: str
    cik: str
    filer_type: Literal["domestic", "foreign_private"] | None
    item: Item
    item_label: str
    comparison: Comparison | None


def _tenk(cursor, cik: str, accession_no: str, item: str) -> TenK:
    cursor.execute(
        """
        SELECT f.filed_date, f.report_date, s.heading, s.source_document, s.anchor
        FROM filing_sections s JOIN filings f ON f.accession_no = s.accession_no
        WHERE s.accession_no = %s AND s.item = %s
        """,
        (accession_no, item),
    )
    filed_date, report_date, heading, document, anchor = cursor.fetchone()
    url = sec_document_url(cik, accession_no, document)
    return TenK(
        accession_no=accession_no,
        filed_date=filed_date,
        report_date=report_date,
        heading=heading,
        section_url=f"{url}#{anchor}" if anchor else url,
        index_url=sec_index_url(cik, accession_no),
    )


def _summary(cursor, accession_no: str, item: str) -> ChangeSummary | None:
    cursor.execute(
        "SELECT status, model, known_at, rejected_count FROM filing_change_summaries "
        "WHERE new_accession_no = %s AND item = %s",
        (accession_no, item),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    status, model, written_at, rejected = row
    cursor.execute(
        "SELECT text, change_nos FROM filing_change_summary_points "
        "WHERE new_accession_no = %s AND item = %s ORDER BY point_no",
        (accession_no, item),
    )
    points = [SummaryPoint(text=text, change_nos=list(numbers)) for text, numbers in cursor.fetchall()]
    return ChangeSummary(status=status, model=model, written_at=written_at, points=points, rejected_count=rejected)


def _paragraphs(cursor, accession_no: str, item: str) -> list[str]:
    cursor.execute(
        "SELECT text FROM filing_paragraphs WHERE accession_no = %s AND item = %s ORDER BY paragraph_no",
        (accession_no, item),
    )
    return [row[0] for row in cursor.fetchall()]


@router.get("/instruments/{symbol}/tenk-changes", response_model=TenKChanges)
def get_tenk_changes(symbol: str, conn: DbConn, item: Item = "1A") -> TenKChanges:
    """The latest year-over-year comparison of one 10-K section; ``comparison`` is null when there is
    none yet (no two consecutive 10-Ks stored, or a filer of 20-Fs)."""
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT symbol, cik, filer_type FROM instruments WHERE symbol = %s AND role = 'watchlist'",
            (symbol.strip().upper(),),
        )
        row = cursor.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"{symbol!r} is not on the watchlist")
        found_symbol, cik, filer_type = row
        if cik is None:
            raise HTTPException(status_code=409, detail=f"{found_symbol} has no SEC CIK yet; onboarding sets it")
        result = TenKChanges(
            symbol=found_symbol,
            cik=cik,
            filer_type=filer_type,
            item=item,
            item_label=SECTIONS[item.lower()],
            comparison=None,
        )

        cursor.execute(
            """
            SELECT new_accession_no, old_accession_no, unchanged_count, moved_count, changed_count,
                   numbers_only_count, added_count, removed_count, computed_at
            FROM filing_section_diffs
            WHERE cik = %s AND item = %s
            ORDER BY known_at DESC
            LIMIT 1
            """,
            (cik, item),
        )
        diff = cursor.fetchone()
        if diff is None:
            return result
        new_accession, old_accession, *counts, computed_at = diff
        new_paragraphs = _paragraphs(cursor, new_accession, item)
        old_paragraphs = _paragraphs(cursor, old_accession, item)
        cursor.execute(
            """
            SELECT change_no, kind, old_paragraph_no, new_paragraph_no, similarity, numbers_only
            FROM filing_paragraph_changes
            WHERE new_accession_no = %s AND item = %s
            ORDER BY change_no
            """,
            (new_accession, item),
        )
        rows = cursor.fetchall()

        # Last year's paragraphs that were reworded into this year's, so a removed paragraph whose
        # heading was renamed is grouped under the new name.
        renamed = {old_no: new_no for _, kind, old_no, new_no, _, _ in rows if kind == "changed"}
        groups: list[ChangeGroup] = []
        for change_no, kind, old_no, new_no, similarity, numbers_only in rows:
            if kind == "removed":
                text = old_paragraphs[old_no - 1]
                number = heading_before(old_paragraphs, old_no)
                if number in renamed:
                    heading = new_paragraphs[renamed[number] - 1]
                else:
                    heading = old_paragraphs[number - 1] if number else None
                words = None
            else:
                text = new_paragraphs[new_no - 1]
                number = heading_before(new_paragraphs, new_no)
                heading = new_paragraphs[number - 1] if number else None
                words = (
                    [WordRun(op=op, text=run) for op, run in word_changes(old_paragraphs[old_no - 1], text)]
                    if kind == "changed"
                    else None
                )
            change = ParagraphChange(
                change_no=change_no,
                kind=kind,
                old_paragraph_no=old_no,
                new_paragraph_no=new_no,
                similarity=float(similarity) if similarity is not None else None,
                numbers_only=numbers_only,
                text=text,
                words=words,
            )
            if groups and groups[-1].heading == heading:
                groups[-1].changes.append(change)
            else:
                groups.append(ChangeGroup(heading=heading, changes=[change]))

        result.comparison = Comparison(
            new=_tenk(cursor, cik, new_accession, item),
            old=_tenk(cursor, cik, old_accession, item),
            counts=Counts(
                unchanged=counts[0],
                moved=counts[1],
                changed=counts[2],
                numbers_only=counts[3],
                added=counts[4],
                removed=counts[5],
            ),
            computed_at=computed_at,
            groups=groups,
            summary=_summary(cursor, new_accession, item),
        )
    return result
