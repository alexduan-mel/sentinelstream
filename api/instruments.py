"""The watchlist and benchmarks: list instruments, add and remove tickers, retry onboarding.

Adding a ticker first checks it with SEC, so a symbol that is not a US-listed operating company is
refused with a clear message and never stored. That check is a quick read (SEC's ticker map, cached
here for a few hours, plus the issuer's submissions record), not background work. A valid ticker is
stored as ``pending`` with its identity and an ``onboard_tickers`` run is queued, returning 202; the
loading happens in the worker (ground rule 4). Removing one sets ``active = false`` and keeps its
history. Benchmarks are fixed and cannot be added or removed here.
"""

from __future__ import annotations

import re
import threading
import time
from datetime import date, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from api.auth import CurrentUser
from api.db import DbConn
from ingestion.onboarding import TASK_ID as ONBOARD_TASK
from ingestion.sec_client import SecClient, SecConfigurationError, SecError
from ingestion.sec_identity import IdentityError, Issuer, resolve_issuer
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


TICKER_MAP_TTL_SECONDS = 6 * 3600


class _TickerMapCache:
    """SEC's ticker map, fetched at most once per ``TICKER_MAP_TTL_SECONDS`` by this process.

    SEC refreshes the file about daily, and it only decides whether a symbol exists: the issuer's own
    submissions record, fetched fresh for every add, confirms the rest.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: list[dict[str, Any]] | None = None
        self._fetched = 0.0

    def get(self, sec: SecClient) -> list[dict[str, Any]]:
        with self._lock:
            if self._rows is None or time.monotonic() - self._fetched > TICKER_MAP_TTL_SECONDS:
                self._rows = sec.ticker_map()
                self._fetched = time.monotonic()
            return self._rows

    def clear(self) -> None:
        with self._lock:
            self._rows = None


TICKER_MAP = _TickerMapCache()


def sec_client() -> SecClient:
    return SecClient.from_env()


def check_with_sec(symbol: str) -> Issuer:
    """The SEC issuer behind ``symbol``, or an HTTP error saying why it cannot be added."""
    try:
        with sec_client() as sec:
            ticker_map = TICKER_MAP.get(sec)
            if not any(str(row.get("ticker", "")).upper() == symbol for row in ticker_map):
                raise HTTPException(status_code=422, detail=f"{symbol}: not found on any US exchange")
            issuer, _submissions = resolve_issuer(symbol, ticker_map, sec)
    except SecConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except IdentityError as exc:
        raise HTTPException(status_code=422, detail=f"{symbol}: {exc}") from None
    except SecError:
        raise HTTPException(
            status_code=503, detail=f"Could not reach SEC to check {symbol}. Try again in a minute."
        ) from None
    if issuer.filer_type is None:
        raise HTTPException(
            status_code=422,
            detail=f"{symbol}: SEC filings do not show whether it is a domestic filer or a foreign private issuer",
        )
    return issuer


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
    """Check a ticker with SEC, then add it to the watchlist and queue its onboarding.

    A symbol SEC does not list, or one that belongs to a fund, is refused (422) and nothing is stored.
    A removed ticker comes back with its history.
    """
    symbol = _symbol(body.symbol)
    with conn.cursor() as cursor:
        cursor.execute("SELECT role, active FROM instruments WHERE symbol = %s", (symbol,))
        existing = cursor.fetchone()
    conn.rollback()
    if existing is not None and existing[0] == "benchmark":
        raise HTTPException(status_code=409, detail=f"{symbol} is a benchmark")
    if existing is not None and existing[1]:
        raise HTTPException(status_code=409, detail=f"{symbol} is already on the watchlist")

    issuer = check_with_sec(symbol)  # outside any transaction: no row is locked while SEC answers
    identity = {
        "symbol": symbol,
        "cik": issuer.cik,
        "name": issuer.name or None,
        "exchange": issuer.exchange,
        "filer_type": issuer.filer_type,
    }
    with conn.cursor() as cursor:
        # ON CONFLICT and the NOT active guard cover a second add while SEC was answering.
        cursor.execute(
            """
            INSERT INTO instruments (symbol, asset_type, role, cik, name, exchange, filer_type)
            VALUES (%(symbol)s, 'equity', 'watchlist', %(cik)s, %(name)s, %(exchange)s, %(filer_type)s)
            ON CONFLICT (symbol) DO UPDATE
            -- Back from removal: identity as SEC gives it today; onboarding tops up the history.
            SET active = TRUE, cik = EXCLUDED.cik, name = EXCLUDED.name, exchange = EXCLUDED.exchange,
                filer_type = EXCLUDED.filer_type, onboarding_status = 'pending', onboarding_error = NULL,
                updated_at = NOW()
            WHERE NOT instruments.active AND instruments.role = 'watchlist'
            """,
            identity,
        )
        stored = cursor.rowcount == 1
    if not stored:
        conn.rollback()
        raise HTTPException(status_code=409, detail=f"{symbol} is already on the watchlist")
    run_id = _queue_onboarding(conn, user, symbol, force_refresh=False)
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
