"""The Journal API: list entries, log one, correct or withdraw one (ADR 0025).

Logging is a single row written in the request, not background work. The entry's time is the
database clock, never taken from the request; a real trade may give its own day and price, which are
checked against that day's prices. Nothing is edited in place: a correction is a new row,
and the entry comes back with both the original and the corrected wording.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from api.auth import CurrentUser
from api.db import DbConn
from ledger.journal import (
    REASON_MAX,
    THESIS_MAX,
    JournalError,
    correct_entry,
    journal_tickers,
    list_entries,
    log_entry,
    open_positions,
)
from ledger.scoring import journal_report

router = APIRouter(prefix="/api", tags=["journal"])

_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")
_ACCESSION = r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$"

Action = Literal["opened", "added", "trimmed", "closed", "pass"]


class Source(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    accession_no: str
    form_type: str
    accepted_at: datetime
    url: str


class Correction(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: Literal["thesis", "source", "withdrawn"]
    thesis: str | None
    source: Source | None
    reason: str
    known_at: datetime
    recorded_by: str


class Score(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: Literal["scored", "waiting"]
    start_date: date | None
    start_basis: Literal["trade", "next_close"] | None
    start_price: float | None
    price_check: Literal["ok", "out_of_range"] | None
    end_date: date | None
    end_price: float | None
    ends: Literal["closed", "latest"] | None
    closed_by: int | None
    sessions: int
    stock_return: float | None
    qqq_return: float | None
    spy_return: float | None
    vs_qqq: float | None
    vs_spy: float | None
    # ADR 0027: beta from daily returns on QQQ's; market_part = beta x qqq_return; alpha the rest.
    fit: Literal["ok", "too_early"] | None
    days: int
    beta: float | None
    r_squared: float | None
    market_part: float | None
    alpha: float | None


class Combined(BaseModel):
    """Every scored entry in the response together; each counts once (ADR 0027)."""

    model_config = ConfigDict(from_attributes=True)

    scored: int
    fitted: int
    too_early: int
    alpha_of_calls: float | None
    calls_right: int
    beta_of_holdings: float | None
    holdings_days: int


class Position(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    symbol: str
    opened_id: int
    start_date: date | None
    end_date: date | None
    status: Literal["open", "closed"]
    excluded: Literal["no_shares", "waiting"] | None
    bought: float | None
    sold: float | None
    value: float | None
    profit: float | None
    qqq_profit: float | None


class Money(BaseModel):
    """Positions in dollars against the same dollars in QQQ (ADR 0028). Totals cover only positions
    whose every entry gave a share count."""

    model_config = ConfigDict(from_attributes=True)

    positions: list[Position]
    bought: float
    profit: float
    qqq_profit: float
    excluded: int


class Entry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str
    name: str | None
    action: Action
    thesis: str
    source: Source | None
    original_thesis: str
    original_source: Source | None
    traded_on: date | None
    price: float | None
    shares: float | None
    known_at: datetime
    recorded_by: str
    withdrawn: Correction | None
    corrections: list[Correction]
    # How it has done since, against QQQ and SPY (ADR 0026); None for a withdrawn entry.
    score: Score | None = None


class Journal(BaseModel):
    entries: list[Entry]
    open_positions: list[str]
    # Every ticker with an entry, whatever the filter, for the page's ticker select.
    tickers: list[str]
    # Combined and money cover the entries in this response (one ticker's when filtered).
    combined: Combined
    money: Money


class NewEntry(BaseModel):
    symbol: str = Field(min_length=1, max_length=16)
    action: Action
    thesis: str = Field(min_length=1, max_length=THESIS_MAX + 100)
    accession_no: str | None = Field(default=None, pattern=_ACCESSION)
    # A real trade: the day and price per share it was done at, and optionally how many shares.
    traded_on: date | None = None
    price: float | None = Field(default=None, gt=0, lt=1e12)
    shares: float | None = Field(default=None, gt=0, lt=1e14)


class NewCorrection(BaseModel):
    kind: Literal["thesis", "source", "withdrawn"]
    reason: str = Field(min_length=1, max_length=REASON_MAX + 100)
    thesis: str | None = Field(default=None, max_length=THESIS_MAX + 100)
    accession_no: str | None = Field(default=None, pattern=_ACCESSION)


def _symbol(raw: str) -> str:
    symbol = raw.strip().upper()
    if not _SYMBOL.match(symbol):
        raise HTTPException(status_code=422, detail=f"{raw!r} is not a ticker symbol")
    return symbol


def _report(conn, entries) -> tuple[list[Entry], Combined, Money]:
    """Entries with their scores, all together, and in money, from the bars stored before now."""
    report = journal_report(conn, entries, datetime.now(UTC))
    scored = [
        Entry.model_validate(e).model_copy(
            update={"score": Score.model_validate(report.scores[e.id]) if e.id in report.scores else None}
        )
        for e in entries
    ]
    return scored, Combined.model_validate(report.combined), Money.model_validate(report.money)


def _entry(conn, entry_id: int) -> Entry:
    [entry] = list_entries(conn, entry_id=entry_id)
    # A score can depend on the ticker's other entries (a Closed ends an Opened).
    scored, _, _ = _report(conn, list_entries(conn, symbol=entry.symbol))
    return next(e for e in scored if e.id == entry_id)


@router.get("/journal")
def get_journal(conn: DbConn, symbol: Annotated[str | None, Query()] = None) -> Journal:
    """Every entry, newest first, withdrawn ones included; or one ticker's. Each counted entry carries
    its score, worked out from stored bars on each request (ADR 0026)."""
    entries = list_entries(conn, symbol=_symbol(symbol) if symbol else None)
    scored, combined, money = _report(conn, entries)
    return Journal(
        entries=scored,
        open_positions=open_positions(conn),
        tickers=journal_tickers(conn),
        combined=combined,
        money=money,
    )


@router.post("/journal", status_code=201)
def post_entry(body: NewEntry, conn: DbConn, user: CurrentUser) -> Entry:
    try:
        entry_id = log_entry(
            conn,
            symbol=_symbol(body.symbol),
            action=body.action,
            thesis=body.thesis,
            accession_no=body.accession_no,
            user_id=user.id,
            traded_on=body.traded_on,
            price=body.price,
            shares=body.shares,
        )
    except JournalError as exc:
        conn.rollback()
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc
    conn.commit()
    return _entry(conn, entry_id)


@router.post("/journal/{entry_id}/corrections", status_code=201)
def post_correction(entry_id: int, body: NewCorrection, conn: DbConn, user: CurrentUser) -> Entry:
    try:
        correct_entry(
            conn,
            entry_id,
            kind=body.kind,
            reason=body.reason,
            user_id=user.id,
            thesis=body.thesis,
            accession_no=body.accession_no,
        )
    except JournalError as exc:
        conn.rollback()
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc
    conn.commit()
    return _entry(conn, entry_id)
