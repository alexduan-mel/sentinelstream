"""Read-only list of SEC filings for the Alerts page, with materiality and Slack delivery.

Every stored filing is listed, newest first. A filing is shown under every watchlist issuer whose EDGAR
submissions list it (``filing_issuers``, ADR 0007), with one ticker per issuer.

Materiality is decided per link (ADR 0008); a filing is high if any of its links is high, low once
every link is classified and none is, and unclassified (null) until then. Its Slack delivery is the
``alert_deliveries`` row for the ``slack`` channel, if it was ever queued (ADR 0009).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from api.db import DbConn
from filings.edgar import sec_index_url
from filings.form_8k import item_title

router = APIRouter(prefix="/api", tags=["filings"])

FORMS_WITH_8K_ITEMS = frozenset({"8-K", "8-K/A"})


class FilingItem(BaseModel):
    number: str
    title: str | None


class SlackDelivery(BaseModel):
    status: Literal["pending", "delivered", "skipped"]
    delivered_at: datetime | None
    # Why a pending alert has not gone yet (the last error), or why a skipped one never will.
    detail: str | None


class Filing(BaseModel):
    accession_no: str
    cik: str
    tickers: list[str]
    form_type: str
    items: list[FilingItem]
    accepted_at: datetime
    filed_date: date
    sec_url: str
    materiality: Literal["high", "low"] | None
    materiality_reason: str | None
    # Shares bought in the open market, for a high Form 4.
    purchase_shares: float | None
    slack: SlackDelivery | None


class FilingPage(BaseModel):
    items: list[Filing]
    total: int


class FilingFacets(BaseModel):
    tickers: list[str]
    form_types: list[str]


def filing_items(form_type: str, numbers: list[str]) -> list[FilingItem]:
    """Item numbers with their SEC titles; only 8-K items have titles."""
    titled = form_type in FORMS_WITH_8K_ITEMS
    return [FilingItem(number=number, title=item_title(number) if titled else None) for number in numbers]


# Filters shared by the count and the page. Dates are calendar days in ``tz``, the zone the UI shows.
_WHERE = """
    WHERE (cardinality(%(tickers)s::text[]) = 0 OR f.accession_no IN (
              SELECT l.accession_no FROM filing_issuers l JOIN instruments i ON i.cik = l.cik
              WHERE i.symbol = ANY(%(tickers)s::text[])))
      AND (cardinality(%(form_types)s::text[]) = 0 OR f.form_type = ANY(%(form_types)s::text[]))
      AND (%(accepted_from)s::date IS NULL
           OR f.accepted_at >= %(accepted_from)s::date::timestamp AT TIME ZONE %(zone)s)
      AND (%(accepted_to)s::date IS NULL
           OR f.accepted_at < (%(accepted_to)s::date + 1)::timestamp AT TIME ZONE %(zone)s)
      AND (%(materiality)s::text IS NULL
           OR (%(materiality)s = 'high' AND EXISTS (
                  SELECT 1 FROM filing_issuers l WHERE l.accession_no = f.accession_no AND l.materiality = 'high'))
           OR (%(materiality)s = 'low' AND NOT EXISTS (
                  SELECT 1 FROM filing_issuers l
                  WHERE l.accession_no = f.accession_no AND (l.materiality = 'high' OR l.materiality IS NULL))))
"""


@router.get("/filings", response_model=FilingPage)
def list_filings(
    conn: DbConn,
    ticker: Annotated[list[str] | None, Query()] = None,
    form_type: Annotated[list[str] | None, Query()] = None,
    accepted_from: date | None = None,
    accepted_to: date | None = None,
    materiality: Literal["high", "low"] | None = None,
    tz: Annotated[str, Query(max_length=64)] = "America/New_York",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> FilingPage:
    """Filings newest first by EDGAR acceptance, filtered by ticker, form type, accepted date and
    materiality. Accepted dates are calendar days in ``tz``, an IANA zone (US Eastern by default)."""
    if accepted_from and accepted_to and accepted_from > accepted_to:
        raise HTTPException(status_code=422, detail="accepted_from is after accepted_to")
    try:
        zone = ZoneInfo(tz).key
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(status_code=422, detail=f"tz {tz!r} is not an IANA time zone") from None
    params: dict[str, Any] = {
        "tickers": [symbol.strip().upper() for symbol in ticker or [] if symbol.strip()],
        "form_types": [form.strip() for form in form_type or [] if form.strip()],
        "accepted_from": accepted_from,
        "accepted_to": accepted_to,
        "materiality": materiality,
        "zone": zone,
        "limit": limit,
        "offset": offset,
    }
    with conn.cursor() as cursor:
        cursor.execute(f"SELECT COUNT(*) FROM filings f {_WHERE}", params)
        total = int(cursor.fetchone()[0])
        cursor.execute(
            f"""
            SELECT f.accession_no, f.cik, tickers.symbols, f.form_type, f.items, f.accepted_at, f.filed_date,
                   level.materiality, level.reason, purchase.shares,
                   d.status, d.delivered_at, COALESCE(d.skip_reason, d.last_error)
            FROM filings f
            CROSS JOIN LATERAL (
                -- One ticker per linked issuer: an active watchlist symbol first, then any other.
                SELECT COALESCE(array_agg(ticker.symbol ORDER BY ticker.symbol), ARRAY[]::text[]) AS symbols
                FROM filing_issuers l
                CROSS JOIN LATERAL (
                    SELECT symbol FROM instruments i
                    WHERE i.cik = l.cik
                    ORDER BY (i.active AND i.role = 'watchlist') DESC, i.symbol
                    LIMIT 1
                ) ticker
                WHERE l.accession_no = f.accession_no
            ) tickers
            CROSS JOIN LATERAL (
                -- High if any link is high; unclassified while any link is; otherwise low. The reason
                -- shown is a high link's, else any classified link's.
                SELECT CASE WHEN bool_or(l.materiality = 'high') THEN 'high'
                            WHEN bool_or(l.materiality IS NULL) THEN NULL
                            ELSE 'low' END AS materiality,
                       (array_agg(l.materiality_reason
                                  ORDER BY (l.materiality = 'high') DESC NULLS LAST, l.cik))[1] AS reason
                FROM filing_issuers l
                WHERE l.accession_no = f.accession_no
            ) level
            LEFT JOIN LATERAL (
                -- The rows the Form 4 rule matched: code P, not disposed, for an issuer it was high for.
                SELECT SUM(t.shares) AS shares
                FROM form4_transactions t
                JOIN filing_issuers l
                  ON l.accession_no = t.accession_no AND l.cik = t.issuer_cik AND l.materiality = 'high'
                WHERE t.accession_no = f.accession_no AND t.transaction_code = 'P'
                  AND t.acquired_disposed IS DISTINCT FROM 'D'
            ) purchase ON f.form_type = '4'
            LEFT JOIN alert_deliveries d ON d.accession_no = f.accession_no AND d.channel = 'slack'
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
                tickers=row[2],
                form_type=row[3],
                items=filing_items(row[3], row[4]),
                accepted_at=row[5],
                filed_date=row[6],
                sec_url=sec_index_url(row[1], row[0]),
                materiality=row[7],
                materiality_reason=row[8],
                purchase_shares=None if row[9] is None else float(row[9]),
                slack=None if row[10] is None else SlackDelivery(status=row[10], delivered_at=row[11], detail=row[12]),
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
