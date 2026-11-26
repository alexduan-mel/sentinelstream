"""The watchlist and benchmarks: list instruments, add and remove tickers, retry onboarding.

Adding a ticker stores it as ``pending`` and queues an ``onboard_tickers`` run, returning 202; the
work itself happens in the worker (ground rule 4). Removing one sets ``active = false`` and keeps
its history. Benchmarks are fixed and cannot be added or removed here.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from api.auth import CurrentUser
from api.db import DbConn
from ingestion.onboarding import TASK_ID as ONBOARD_TASK
from orchestration.task_runs import enqueue_run

router = APIRouter(prefix="/api", tags=["instruments"])

# The same rule as the instruments table's check constraint.
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")

OnboardingStatus = Literal["pending", "backfilling", "ready", "failed"]


class LastFiling(BaseModel):
    form_type: str
    accepted_at: datetime


class PriceHistory(BaseModel):
    first: date
    last: date
    bars: int


class Instrument(BaseModel):
    symbol: str
    name: str | None
    asset_type: Literal["equity", "etf"]
    role: Literal["watchlist", "benchmark"]
    cik: str | None
    filer_type: Literal["domestic", "foreign_private"] | None
    onboarding_status: OnboardingStatus
    onboarding_error: str | None
    last_filing: LastFiling | None
    prices: PriceHistory | None


class AddInstrument(BaseModel):
    symbol: str = Field(min_length=1, max_length=16)


class Queued(BaseModel):
    symbol: str
    onboarding_status: OnboardingStatus
    run_id: int


def _symbol(raw: str) -> str:
    symbol = raw.strip().upper()
    if not _SYMBOL.match(symbol):
        raise HTTPException(status_code=422, detail=f"{raw.strip()!r} is not a ticker symbol")
    return symbol


@router.get("/instruments", response_model=list[Instrument])
def list_instruments(conn: DbConn) -> list[Instrument]:
    """Active instruments, watchlist first, with their newest filing and price coverage."""
    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT i.symbol, i.name, i.asset_type, i.role, i.cik, i.filer_type, i.onboarding_status,
                   i.onboarding_error, latest.form_type, latest.accepted_at, bars.first, bars.last, bars.count
            FROM instruments i
            LEFT JOIN LATERAL (
                SELECT f.form_type, f.accepted_at
                FROM filing_issuers l JOIN filings f USING (accession_no)
                WHERE l.cik = i.cik
                ORDER BY f.accepted_at DESC
                LIMIT 1
            ) latest ON i.cik IS NOT NULL
            LEFT JOIN LATERAL (
                SELECT MIN(trading_date) AS first, MAX(trading_date) AS last, COUNT(*) AS count
                FROM price_bars p WHERE p.instrument_id = i.id
            ) bars ON TRUE
            WHERE i.active
            ORDER BY i.role = 'watchlist' DESC, i.symbol
            """)
        rows = cursor.fetchall()
    return [
        Instrument(
            symbol=row[0],
            name=row[1],
            asset_type=row[2],
            role=row[3],
            cik=row[4],
            filer_type=row[5],
            onboarding_status=row[6],
            onboarding_error=row[7],
            last_filing=LastFiling(form_type=row[8], accepted_at=row[9]) if row[8] else None,
            prices=PriceHistory(first=row[10], last=row[11], bars=row[12]) if row[12] else None,
        )
        for row in rows
    ]


def _queue_onboarding(conn, user, symbol: str, *, force_refresh: bool) -> int:
    run_id = enqueue_run(
        conn,
        ONBOARD_TASK,
        trigger_source="api",
        run_mode="manual",
        requested_by=f"web:{user.username}",
        tickers=[symbol],
        force_refresh=force_refresh,
    )
    conn.commit()
    return run_id


@router.post("/instruments", status_code=202, response_model=Queued)
def add_instrument(body: AddInstrument, conn: DbConn, user: CurrentUser) -> Queued:
    """Add a watchlist ticker and queue its onboarding. A removed ticker comes back with its history."""
    symbol = _symbol(body.symbol)
    with conn.cursor() as cursor:
        cursor.execute("SELECT role, active FROM instruments WHERE symbol = %s FOR UPDATE", (symbol,))
        existing = cursor.fetchone()
        if existing is not None and existing[0] == "benchmark":
            conn.rollback()
            raise HTTPException(status_code=409, detail=f"{symbol} is a benchmark")
        if existing is not None and existing[1]:
            conn.rollback()
            raise HTTPException(status_code=409, detail=f"{symbol} is already on the watchlist")
        if existing is None:
            cursor.execute(
                "INSERT INTO instruments (symbol, asset_type, role) VALUES (%s, 'equity', 'watchlist')", (symbol,)
            )
        else:
            # Back from removal: onboarding confirms the identity again and tops up the history.
            cursor.execute(
                """
                UPDATE instruments
                SET active = TRUE, onboarding_status = 'pending', onboarding_error = NULL, updated_at = NOW()
                WHERE symbol = %s
                """,
                (symbol,),
            )
    run_id = _queue_onboarding(conn, user, symbol, force_refresh=existing is not None)
    return Queued(symbol=symbol, onboarding_status="pending", run_id=run_id)


@router.post("/instruments/{symbol}/onboard", status_code=202, response_model=Queued)
def retry_onboarding(symbol: str, conn: DbConn, user: CurrentUser) -> Queued:
    """Run onboarding again for a watchlist ticker, looking its identity up afresh."""
    symbol = _symbol(symbol)
    with conn.cursor() as cursor:
        cursor.execute(
            """
            UPDATE instruments
            SET onboarding_status = CASE WHEN onboarding_status = 'failed' THEN 'pending' ELSE onboarding_status END,
                onboarding_error = NULL, updated_at = NOW()
            WHERE symbol = %s AND active AND role = 'watchlist'
            RETURNING onboarding_status
            """,
            (symbol,),
        )
        row = cursor.fetchone()
    if row is None:
        conn.rollback()
        raise HTTPException(status_code=404, detail=f"{symbol} is not on the watchlist")
    run_id = _queue_onboarding(conn, user, symbol, force_refresh=True)
    return Queued(symbol=symbol, onboarding_status=row[0], run_id=run_id)


@router.delete("/instruments/{symbol}", status_code=204)
def remove_instrument(symbol: str, conn: DbConn) -> Response:
    """Stop tracking a watchlist ticker. Its filings, prices and alerts stay."""
    symbol = _symbol(symbol)
    with conn.cursor() as cursor:
        cursor.execute("SELECT role FROM instruments WHERE symbol = %s AND active", (symbol,))
        row = cursor.fetchone()
        if row is None:
            conn.rollback()
            raise HTTPException(status_code=404, detail=f"{symbol} is not on the watchlist")
        if row[0] == "benchmark":
            conn.rollback()
            raise HTTPException(status_code=409, detail=f"{symbol} is a benchmark and cannot be removed")
        cursor.execute("UPDATE instruments SET active = FALSE, updated_at = NOW() WHERE symbol = %s", (symbol,))
    conn.commit()
    return Response(status_code=204)
