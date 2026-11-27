"""Read-only list of SEC filings for the Alerts page, with materiality and Slack delivery.

Every stored filing is listed, newest first. A filing is shown under every watchlist issuer whose EDGAR
submissions list it (``filing_issuers``, ADR 0007), with one ticker per issuer.

Materiality is decided per link (ADR 0008); a filing is high if any of its links is high, low once
every link is classified and none is, and unclassified (null) until then. Its Slack delivery is the
``alert_deliveries`` row for the ``slack`` channel, if it was ever queued (ADR 0009).
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from api.db import DbConn
from filings.edgar import sec_document_url, sec_index_url
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


# One row per filing, shared by the list and the detail. Columns 0-12 make a ``Filing``; 13-16 are
# the detail's extras.
_SELECT = """
    SELECT f.accession_no, f.cik, tickers.symbols, f.form_type, f.items, f.accepted_at, f.filed_date,
           level.materiality, level.reason, purchase.shares,
           d.status, d.delivered_at, COALESCE(d.skip_reason, d.last_error),
           f.report_date, f.primary_document, f.first_seen_at, d.attempts
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
"""


def _filing(row: tuple[Any, ...]) -> Filing:
    return Filing(
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
            {_SELECT}
            {_WHERE}
            ORDER BY f.accepted_at DESC, f.id DESC
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            params,
        )
        rows = cursor.fetchall()
    return FilingPage(
        items=[_filing(row) for row in rows],
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


# --- one filing ------------------------------------------------------------------------------------

_ACCESSION = re.compile(r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")


class FilingIssuer(BaseModel):
    """One watchlist issuer EDGAR lists the filing under, with the materiality decided for it."""

    ticker: str | None
    cik: str
    materiality: Literal["high", "low"] | None
    materiality_reason: str | None
    # History: stored when the issuer was first polled or onboarded, so never pushed.
    is_backfill: bool
    first_seen_at: datetime


class Form4Transaction(BaseModel):
    """A non-derivative row of the Form 4, as filed."""

    line_no: int
    reporting_owners: str
    owner_roles: str
    security_title: str | None
    transaction_date: date | None
    transaction_code: str
    acquired_disposed: Literal["A", "D"] | None
    shares: float | None
    price_per_share: float | None
    shares_owned_after: float | None
    direct_indirect: Literal["D", "I"] | None


class FilingDetail(Filing):
    report_date: date | None
    primary_document: str | None
    primary_document_url: str | None
    first_seen_at: datetime
    # How long after EDGAR acceptance the poller saw the filing; None for history, where the
    # filing was loaded long after it was accepted and the gap says nothing about polling.
    detection_delay_seconds: float | None
    slack_attempts: int | None
    # EDGAR acceptance to Slack delivery: the M1 latency target is 10 minutes.
    alert_latency_seconds: float | None
    issuers: list[FilingIssuer]
    form4_transactions: list[Form4Transaction]


def _number(value: Any) -> float | None:
    return None if value is None else float(value)


@router.get("/filings/{accession_no}", response_model=FilingDetail)
def get_filing(accession_no: str, conn: DbConn) -> FilingDetail:
    """One filing with its linked issuers, Slack delivery and Form 4 transactions."""
    if not _ACCESSION.match(accession_no):
        raise HTTPException(status_code=422, detail=f"{accession_no!r} is not an accession number")
    with conn.cursor() as cursor:
        cursor.execute(f"{_SELECT} WHERE f.accession_no = %(accession)s", {"accession": accession_no})
        row = cursor.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"No stored filing {accession_no}")
        cursor.execute(
            """
            SELECT ticker.symbol, l.cik, l.materiality, l.materiality_reason, l.is_backfill, l.first_seen_at
            FROM filing_issuers l
            LEFT JOIN LATERAL (
                SELECT symbol FROM instruments i
                WHERE i.cik = l.cik
                ORDER BY (i.active AND i.role = 'watchlist') DESC, i.symbol
                LIMIT 1
            ) ticker ON TRUE
            WHERE l.accession_no = %s
            ORDER BY ticker.symbol NULLS LAST, l.cik
            """,
            (accession_no,),
        )
        issuers = [FilingIssuer(**dict(zip(FilingIssuer.model_fields, link, strict=True))) for link in cursor]
        cursor.execute(
            """
            SELECT line_no, reporting_owners, owner_roles, security_title, transaction_date, transaction_code,
                   acquired_disposed, shares, price_per_share, shares_owned_after, direct_indirect
            FROM form4_transactions
            WHERE accession_no = %s
            ORDER BY line_no
            """,
            (accession_no,),
        )
        transactions = [
            Form4Transaction(
                line_no=t[0],
                reporting_owners=t[1],
                owner_roles=t[2],
                security_title=t[3],
                transaction_date=t[4],
                transaction_code=t[5],
                acquired_disposed=t[6],
                shares=_number(t[7]),
                price_per_share=_number(t[8]),
                shares_owned_after=_number(t[9]),
                direct_indirect=t[10],
            )
            for t in cursor.fetchall()
        ]
    filing = _filing(row)
    accepted_at: datetime = row[5]
    report_date, primary_document, first_seen_at, attempts = row[13], row[14], row[15], row[16]
    is_news = any(not link.is_backfill for link in issuers)
    delivered_at = filing.slack.delivered_at if filing.slack else None
    return FilingDetail(
        **filing.model_dump(),
        report_date=report_date,
        primary_document=primary_document,
        primary_document_url=(
            sec_document_url(filing.cik, accession_no, primary_document) if primary_document else None
        ),
        first_seen_at=first_seen_at,
        detection_delay_seconds=(first_seen_at - accepted_at).total_seconds() if is_news else None,
        slack_attempts=attempts,
        alert_latency_seconds=(delivered_at - accepted_at).total_seconds() if delivered_at else None,
        issuers=issuers,
        form4_transactions=transactions,
    )
