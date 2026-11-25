"""Read-only list of SEC filings for the Alerts page.

Materiality and Slack delivery arrive with M1; until then the page lists every stored filing, newest
first. A filing belongs to an issuer (CIK), and is shown under one ticker for that issuer until M1
adds a filing-to-issuer link for filings that name several issuers.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from api.db import DbConn
from filings.form_8k import item_title

router = APIRouter(prefix="/api", tags=["filings"])

FORMS_WITH_8K_ITEMS = frozenset({"8-K", "8-K/A"})
SEC_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"


class FilingItem(BaseModel):
    number: str
    title: str | None


class Filing(BaseModel):
    accession_no: str
    cik: str
    ticker: str | None
    form_type: str
    items: list[FilingItem]
    accepted_at: datetime
    filed_date: date
    sec_url: str


class FilingPage(BaseModel):
    items: list[Filing]
    total: int


class FilingFacets(BaseModel):
    tickers: list[str]
    form_types: list[str]


def sec_index_url(cik: str, accession_no: str) -> str:
    """EDGAR's index page for a filing, which links the primary document and every exhibit."""
    return f"{SEC_ARCHIVES}/{int(cik)}/{accession_no.replace('-', '')}/{accession_no}-index.htm"


def filing_items(form_type: str, numbers: list[str]) -> list[FilingItem]:
    """Item numbers with their SEC titles; only 8-K items have titles."""
    titled = form_type in FORMS_WITH_8K_ITEMS
    return [FilingItem(number=number, title=item_title(number) if titled else None) for number in numbers]


# Filters shared by the count and the page. Dates are US Eastern calendar days, as the UI shows them.
_WHERE = """
    WHERE (cardinality(%(tickers)s::text[]) = 0 OR f.cik IN (
              SELECT cik FROM instruments WHERE cik IS NOT NULL AND symbol = ANY(%(tickers)s::text[])))
      AND (cardinality(%(form_types)s::text[]) = 0 OR f.form_type = ANY(%(form_types)s::text[]))
      AND (%(accepted_from)s::date IS NULL
           OR f.accepted_at >= %(accepted_from)s::date::timestamp AT TIME ZONE 'America/New_York')
      AND (%(accepted_to)s::date IS NULL
           OR f.accepted_at < (%(accepted_to)s::date + 1)::timestamp AT TIME ZONE 'America/New_York')
"""


@router.get("/filings", response_model=FilingPage)
def list_filings(
    conn: DbConn,
    ticker: Annotated[list[str] | None, Query()] = None,
    form_type: Annotated[list[str] | None, Query()] = None,
    accepted_from: date | None = None,
    accepted_to: date | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> FilingPage:
    """Filings newest first by EDGAR acceptance, filtered by ticker, form type and accepted date (ET)."""
    if accepted_from and accepted_to and accepted_from > accepted_to:
        raise HTTPException(status_code=422, detail="accepted_from is after accepted_to")
    params: dict[str, Any] = {
        "tickers": [symbol.strip().upper() for symbol in ticker or [] if symbol.strip()],
        "form_types": [form.strip() for form in form_type or [] if form.strip()],
        "accepted_from": accepted_from,
        "accepted_to": accepted_to,
        "limit": limit,
        "offset": offset,
    }
    with conn.cursor() as cursor:
        cursor.execute(f"SELECT COUNT(*) FROM filings f {_WHERE}", params)
        total = int(cursor.fetchone()[0])
        cursor.execute(
            f"""
            SELECT f.accession_no, f.cik, ticker.symbol, f.form_type, f.items, f.accepted_at, f.filed_date
            FROM filings f
            LEFT JOIN LATERAL (
                -- One ticker per issuer for now: an active watchlist symbol first, then any other.
                SELECT symbol FROM instruments i
                WHERE i.cik = f.cik
                ORDER BY (i.active AND i.role = 'watchlist') DESC, i.symbol
                LIMIT 1
            ) ticker ON TRUE
            {_WHERE}
            ORDER BY f.accepted_at DESC, f.id DESC
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            params,
        )
        rows = cursor.fetchall()
    return FilingPage(
        items=[
            Filing(
                accession_no=row[0],
                cik=row[1],
                ticker=row[2],
                form_type=row[3],
                items=filing_items(row[3], row[4]),
                accepted_at=row[5],
                filed_date=row[6],
                sec_url=sec_index_url(row[1], row[0]),
            )
            for row in rows
        ],
        total=total,
    )


@router.get("/filings/facets", response_model=FilingFacets)
def filing_facets(conn: DbConn) -> FilingFacets:
    """Filter options: watchlist tickers with a CIK, and the form types that have been stored."""
    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT symbol FROM instruments
            WHERE active AND role = 'watchlist' AND cik IS NOT NULL
            ORDER BY symbol
            """)
        tickers = [row[0] for row in cursor.fetchall()]
        cursor.execute("SELECT DISTINCT form_type FROM filings ORDER BY form_type")
        form_types = [row[0] for row in cursor.fetchall()]
    return FilingFacets(tickers=tickers, form_types=form_types)
