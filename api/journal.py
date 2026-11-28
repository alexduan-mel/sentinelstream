"""The Journal API: list entries, log one, correct or withdraw one (ADR 0025).

Logging is a single row written in the request, not background work. The entry's time is the
database clock, never taken from the request; a real trade may give its own day and price, which are
checked against that day's prices. Nothing is edited in place: a correction is a new row,
and the entry comes back with both the original and the corrected wording.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from api.auth import CurrentUser
from api.db import DbConn
from ledger.journal import REASON_MAX, THESIS_MAX, JournalError, correct_entry, list_entries, log_entry, open_positions

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


class Journal(BaseModel):
    entries: list[Entry]
    open_positions: list[str]


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


def _entry(conn, entry_id: int) -> Entry:
    return Entry.model_validate(list_entries(conn, entry_id=entry_id)[0])


@router.get("/journal")
def get_journal(conn: DbConn, symbol: Annotated[str | None, Query()] = None) -> Journal:
    """Every entry, newest first, withdrawn ones included; or one ticker's."""
    entries = list_entries(conn, symbol=_symbol(symbol) if symbol else None)
    return Journal(entries=[Entry.model_validate(e) for e in entries], open_positions=open_positions(conn))


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
