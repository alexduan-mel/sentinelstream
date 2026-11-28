"""Key financials for one watchlist ticker, from the XBRL facts its filings carried (ADR 0020).

Values are the latest known before the request (a restatement replaces the original once it is
filed), read through the as-of layer. Each value lists the filings it came from, with EDGAR links, so
any number can be checked against its source (ground rule 6).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from api.db import DbConn
from filings.edgar import sec_index_url
from filings.financials import CONCEPTS, METRICS, Basis, Value, key_financials
from research.asof import AsOf

router = APIRouter(prefix="/api", tags=["financials"])


class MetricInfo(BaseModel):
    key: str
    label: str
    unit: Literal["USD", "USD/shares", "shares", "ratio"]
    definition: str


class SourceFact(BaseModel):
    concept: str
    accession_no: str
    form: str | None
    filed_date: date
    period_start: date | None
    period_end: date
    value: float
    url: str


class MetricValue(BaseModel):
    value: float
    # reported: one filed value; subtracted: a longer period minus the part before (Q4 = year − 9
    # months); computed: worked out from several filed values (gross margin, total debt, FCF).
    method: Literal["reported", "subtracted", "computed"]
    sources: list[SourceFact]


class FinancialPeriod(BaseModel):
    start: date
    end: date
    fiscal_year: int | None
    fiscal_period: str | None
    values: dict[str, MetricValue | None]


class KeyFinancials(BaseModel):
    symbol: str
    cik: str
    name: str | None
    filer_type: Literal["domestic", "foreign_private"] | None
    basis: Basis
    as_of: datetime
    metrics: list[MetricInfo]
    periods: list[FinancialPeriod]


def _value(cik: str, value: Value | None) -> MetricValue | None:
    if value is None:
        return None
    return MetricValue(
        value=float(value.value),
        method=value.method,
        sources=[
            SourceFact(
                concept=s.concept,
                accession_no=s.accession_no,
                form=s.form,
                filed_date=s.filed_date,
                period_start=s.period_start,
                period_end=s.period_end,
                value=float(s.value),
                url=sec_index_url(cik, s.accession_no),
            )
            for s in value.sources
        ],
    )


@router.get("/instruments/{symbol}/financials", response_model=KeyFinancials)
def get_key_financials(
    symbol: str,
    conn: DbConn,
    basis: Basis = "quarterly",
    periods: Annotated[int, Query(ge=1, le=20)] = 8,
) -> KeyFinancials:
    """The latest ``periods`` quarters or fiscal years, newest first. A metric the company did not
    file for a period is null."""
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT symbol, cik, name, filer_type FROM instruments WHERE symbol = %s AND role = 'watchlist'",
            (symbol.strip().upper(),),
        )
        row = cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"{symbol!r} is not on the watchlist")
    found_symbol, cik, name, filer_type = row
    if cik is None:
        raise HTTPException(status_code=409, detail=f"{found_symbol} has no SEC CIK yet; onboarding sets it")

    as_of = datetime.now(UTC)
    facts = AsOf(conn, as_of).xbrl_facts(cik, *CONCEPTS)
    return KeyFinancials(
        symbol=found_symbol,
        cik=cik,
        name=name,
        filer_type=filer_type,
        basis=basis,
        as_of=as_of,
        metrics=[MetricInfo(key=m.key, label=m.label, unit=m.unit, definition=m.definition) for m in METRICS],
        periods=[
            FinancialPeriod(
                start=p.start,
                end=p.end,
                fiscal_year=p.fiscal_year,
                fiscal_period=p.fiscal_period,
                values={key: _value(cik, value) for key, value in p.values.items()},
            )
            for p in key_financials(facts, basis, limit=periods)
        ],
    )
