"""The Journal: an append-only ledger of the owner's decisions (ADR 0025).

An entry records one decision on a watchlist ticker: its action, the thesis behind it and, optionally,
the alert it was logged from. Its time is the database clock at insert, never typed in. A real trade
can also give its day and price per share, checked against that day's low-high range, and a share
count. Without a price, an entry is scored from the first close after it was logged, so it never
gets credit for a price already known (ground rule 2). Nothing is ever updated or
deleted (the tables refuse it): a thesis or alert link is corrected, or an entry withdrawn, by adding
a correction row, and the original stays visible.

Actions follow a position, in the order the trades happened: Opened starts one, Added and Trimmed need
one, Closed ends it, and Pass ("looked, chose not to act") is allowed at any time. A withdrawn entry
no longer counts, so it can't be withdrawn if that would leave a later entry with no position to act
on.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Sequence
from datetime import date, datetime
from typing import Literal

from psycopg2.extensions import connection as Connection

from filings.edgar import sec_index_url
from ingestion.market_calendar import is_trading_day

Action = Literal["opened", "added", "trimmed", "closed", "pass"]
CorrectionKind = Literal["thesis", "source", "withdrawn"]

ACTIONS: tuple[Action, ...] = ("opened", "added", "trimmed", "closed", "pass")
ACTION_LABELS: dict[str, str] = {
    "opened": "Opened",
    "added": "Added",
    "trimmed": "Trimmed",
    "closed": "Closed",
    "pass": "Pass",
}
THESIS_MAX = 4000
REASON_MAX = 1000
# A typed trade price may sit this far outside the day's regular-session low-high range (a fill just
# before the open or after the close).
PRICE_TOLERANCE = 0.01
SHARE_TOLERANCE = 1e-6


class JournalError(Exception):
    """A request the ledger refuses. ``status`` is the HTTP status the API answers with."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclasses.dataclass(frozen=True)
class Source:
    accession_no: str
    form_type: str
    accepted_at: datetime
    url: str


@dataclasses.dataclass(frozen=True)
class Correction:
    kind: CorrectionKind
    thesis: str | None
    source: Source | None
    reason: str
    known_at: datetime
    recorded_by: str


@dataclasses.dataclass(frozen=True)
class Entry:
    id: int
    symbol: str
    name: str | None
    action: Action
    # The thesis and source as corrected; the originals are kept alongside.
    thesis: str
    source: Source | None
    original_thesis: str
    original_source: Source | None
    # A real trade's day and price per share, as traded; shares as traded that day. All optional.
    traded_on: date | None
    price: float | None
    shares: float | None
    known_at: datetime
    recorded_by: str
    withdrawn: Correction | None
    corrections: list[Correction]


@dataclasses.dataclass(frozen=True)
class Step:
    """One counted entry in a ticker's position, in trade order. ``shares`` is in today's share basis
    (after any later splits), so counts from either side of a split add up."""

    action: str
    day: date | None = None
    shares: float | None = None


def sequence_error(symbol: str, steps: Sequence[Step]) -> str | None:
    """Why this run of entries (oldest first) can't happen, or None if it can.

    The share count is followed only while every entry since Opened gave one; then Trimmed must leave
    some shares and Closed, if it gives a count, must be the whole position.
    """
    holding = False
    held: float | None = None
    for step in steps:
        action, shares = step.action, step.shares
        if action == "opened":
            if holding:
                return f"{symbol} already has an open position; log Added, Trimmed, Closed or Pass instead"
            holding, held = True, shares
        elif action in ("added", "trimmed", "closed"):
            if not holding:
                return f"{symbol} has no open position to mark {ACTION_LABELS[action]}; log Opened first"
            if action == "added":
                held = None if held is None or shares is None else held + shares
            elif action == "trimmed":
                if held is not None and shares is not None and shares >= held - SHARE_TOLERANCE:
                    return (
                        f"{symbol}'s position is {held:,.6g} shares; Trimmed must leave some, "
                        "so log Closed to sell all of it"
                    )
                held = None if held is None or shares is None else held - shares
            else:
                if held is not None and shares is not None and abs(shares - held) > SHARE_TOLERANCE:
                    return (
                        f"{symbol}'s position is {held:,.6g} shares; Closed sells all of it, "
                        "so log Trimmed to sell part"
                    )
                holding, held = False, None
    return None


def _clean(text: str, field: str, limit: int) -> str:
    text = text.strip()
    if not text:
        raise JournalError(422, f"The {field} can't be empty")
    if len(text) > limit:
        raise JournalError(422, f"The {field} is {len(text):,} characters; the limit is {limit:,}")
    return text


def _instrument(conn: Connection, symbol: str) -> tuple[int, str | None]:
    """The watchlist ticker an entry can be logged for, locked so entries on it are logged one at a time."""
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, cik, role, asset_type, onboarding_status, active
            FROM instruments WHERE symbol = %s
            FOR UPDATE
            """,
            (symbol,),
        )
        row = cursor.fetchone()
    if row is None or not row[5]:
        raise JournalError(404, f"{symbol} is not on the watchlist")
    instrument_id, cik, role, asset_type, status, _ = row
    if role != "watchlist" or asset_type != "equity":
        raise JournalError(409, f"{symbol} is a benchmark; the Journal is for watchlist companies")
    if status != "ready":
        raise JournalError(409, f"{symbol} is still onboarding; log decisions once it is Ready")
    return instrument_id, cik


def _check_source(conn: Connection, symbol: str, cik: str | None, accession_no: str) -> None:
    """The alert must be a stored filing listed under this ticker's company."""
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM filing_issuers WHERE accession_no = %s AND cik = %s",
            (accession_no, cik),
        )
        if cursor.fetchone() is None:
            raise JournalError(422, f"Filing {accession_no} is not an alert for {symbol}")


def _today(conn: Connection) -> date:
    """Today in New York, by the database clock (the same clock that stamps ``known_at``)."""
    with conn.cursor() as cursor:
        cursor.execute("SELECT (NOW() AT TIME ZONE 'America/New_York')::date")
        return cursor.fetchone()[0]


class _Splits:
    """A ticker's splits up to today, to move a price or share count as traded on one day into
    today's share basis, which the stored split-adjusted bars use (ADR 0013)."""

    def __init__(self, conn: Connection, symbol: str, today: date) -> None:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT execution_date, split_to / split_from FROM stock_splits
                WHERE symbol = %s AND execution_date <= %s
                """,
                (symbol, today),
            )
            self._splits = cursor.fetchall()

    def factor_after(self, day: date) -> float:
        """Shares today per share held on ``day``: 10.0 after one later 10-for-1 split."""
        return math.prod(ratio for executed, ratio in self._splits if executed > day)


def _check_price(conn: Connection, instrument_id: int, symbol: str, traded_on: date, price: float,
                 today: date, splits: _Splits) -> None:  # fmt: skip
    """A typed trade price must fall inside that day's low-high range, give or take ``PRICE_TOLERANCE``.

    Today's bar is stored only after the close, so a trade logged today is accepted as typed and
    checked by scoring once the bar arrives (ADR 0025).
    """
    if traded_on > today:
        raise JournalError(422, f"The trade day {traded_on:%d %b %Y} is in the future")
    if not is_trading_day(traded_on):
        raise JournalError(422, f"{traded_on:%a %d %b %Y} was not a trading day")
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT low, high FROM price_bars
            WHERE instrument_id = %s AND trading_date = %s AND low IS NOT NULL AND high IS NOT NULL
            ORDER BY provider LIMIT 1
            """,
            (instrument_id, traded_on),
        )
        bar = cursor.fetchone()
    if bar is None:
        if traded_on == today:
            return
        raise JournalError(422, f"No stored price for {symbol} on {traded_on:%d %b %Y}, so the trade can't be checked")
    factor = splits.factor_after(traded_on)
    low, high = float(bar[0]) * factor, float(bar[1]) * factor
    if not low * (1 - PRICE_TOLERANCE) <= price <= high * (1 + PRICE_TOLERANCE):
        raise JournalError(
            422,
            f"${price:,.2f} is outside {symbol}'s range on {traded_on:%d %b %Y} (${low:,.2f} to ${high:,.2f}); "
            "enter the price per share the trade was done at",
        )


def _counted_steps(conn: Connection, instrument_id: int, splits: _Splits, *, without: int | None = None) -> list[Step]:
    """The ticker's entries that count (not withdrawn), in trade order: a trade's day, else the day it
    was logged, in New York."""
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT e.action, COALESCE(e.traded_on, (e.known_at AT TIME ZONE 'America/New_York')::date) AS day,
                   e.shares
            FROM journal_entries e
            WHERE e.instrument_id = %s
              AND e.id IS DISTINCT FROM %s
              AND NOT EXISTS (SELECT 1 FROM journal_corrections c
                              WHERE c.entry_id = e.id AND c.kind = 'withdrawn')
            ORDER BY day, e.known_at, e.id
            """,
            (instrument_id, without),
        )
        return [
            Step(action, day, None if shares is None else float(shares) * splits.factor_after(day))
            for action, day, shares in cursor.fetchall()
        ]


def log_entry(
    conn: Connection,
    *,
    symbol: str,
    action: str,
    thesis: str,
    accession_no: str | None,
    user_id: int,
    traded_on: date | None = None,
    price: float | None = None,
    shares: float | None = None,
) -> int:
    """Store a new entry and return its id. The caller commits."""
    if action not in ACTIONS:
        raise JournalError(422, f"Unknown action {action!r}")
    if action == "pass" and (traded_on or price or shares):
        raise JournalError(422, "A Pass is not a trade, so it takes no trade day, price or shares")
    if (traded_on is None) != (price is None):
        raise JournalError(422, "A trade needs both the day and the price per share")
    if (price is not None and price <= 0) or (shares is not None and shares <= 0):
        raise JournalError(422, "The price and shares must be above zero")
    thesis = _clean(thesis, "thesis", THESIS_MAX)
    instrument_id, cik = _instrument(conn, symbol)
    if accession_no is not None:
        _check_source(conn, symbol, cik, accession_no)
    today = _today(conn)
    splits = _Splits(conn, symbol, today)
    if traded_on is not None:
        _check_price(conn, instrument_id, symbol, traded_on, price, today, splits)
    day = traded_on or today
    steps = _counted_steps(conn, instrument_id, splits)
    if steps and steps[-1].day > day:
        raise JournalError(
            409,
            f"{symbol}'s latest entry is for {steps[-1].day:%d %b %Y}, after {day:%d %b %Y}; "
            "log entries in the order they happened",
        )
    new = Step(action, day, None if shares is None else shares * splits.factor_after(day))
    error = sequence_error(symbol, [*steps, new])
    if error:
        raise JournalError(409, error)
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO journal_entries
              (instrument_id, action, thesis, accession_no, traded_on, price, shares, recorded_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (instrument_id, action, thesis, accession_no, traded_on, price, shares, user_id),
        )
        return int(cursor.fetchone()[0])


def correct_entry(
    conn: Connection,
    entry_id: int,
    *,
    kind: str,
    reason: str,
    user_id: int,
    thesis: str | None = None,
    accession_no: str | None = None,
) -> None:
    """Add a correction to an entry. The caller commits."""
    if kind not in ("thesis", "source", "withdrawn"):
        raise JournalError(422, f"Unknown correction {kind!r}")
    reason = _clean(reason, "reason", REASON_MAX)
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT i.symbol, e.instrument_id,
                   EXISTS (SELECT 1 FROM journal_corrections c WHERE c.entry_id = e.id AND c.kind = 'withdrawn')
            FROM journal_entries e JOIN instruments i ON i.id = e.instrument_id
            WHERE e.id = %s
            """,
            (entry_id,),
        )
        row = cursor.fetchone()
    if row is None:
        raise JournalError(404, f"Journal entry {entry_id} does not exist")
    symbol, instrument_id, withdrawn = row
    if withdrawn:
        raise JournalError(409, f"Journal entry {entry_id} was withdrawn and can't be corrected")
    with conn.cursor() as cursor:
        # Lock the ticker, as logging does, so a withdrawal and a new entry can't cross.
        cursor.execute("SELECT cik FROM instruments WHERE id = %s FOR UPDATE", (instrument_id,))
        cik = cursor.fetchone()[0]

    if kind == "thesis":
        if thesis is None:
            raise JournalError(422, "A thesis correction needs the new thesis")
        thesis = _clean(thesis, "thesis", THESIS_MAX)
        accession_no = None
    elif kind == "source":
        thesis = None
        if accession_no is not None:
            _check_source(conn, symbol, cik, accession_no)
    else:
        thesis = accession_no = None
        splits = _Splits(conn, symbol, _today(conn))
        error = sequence_error(symbol, _counted_steps(conn, instrument_id, splits, without=entry_id))
        if error:
            raise JournalError(409, f"Withdrawing entry {entry_id} would break a later entry: {error}")

    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO journal_corrections (entry_id, kind, thesis, accession_no, reason, recorded_by)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (entry_id, kind, thesis, accession_no, reason, user_id),
        )


def _source(
    cik: str | None, accession_no: str | None, form_type: str | None, accepted_at: datetime | None
) -> Source | None:
    if accession_no is None or cik is None:
        return None
    return Source(accession_no, form_type, accepted_at, sec_index_url(cik, accession_no))


def list_entries(conn: Connection, *, symbol: str | None = None, entry_id: int | None = None) -> list[Entry]:
    """Entries newest first, each with its corrections oldest first."""
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT e.id, i.symbol, i.name, i.cik, e.action, e.thesis,
                   e.accession_no, f.form_type, f.accepted_at, e.traded_on, e.price, e.shares,
                   e.known_at, u.username
            FROM journal_entries e
            JOIN instruments i ON i.id = e.instrument_id
            JOIN app_users u ON u.id = e.recorded_by
            LEFT JOIN filings f ON f.accession_no = e.accession_no
            WHERE (%(symbol)s::text IS NULL OR i.symbol = %(symbol)s)
              AND (%(entry_id)s::bigint IS NULL OR e.id = %(entry_id)s)
            ORDER BY e.known_at DESC, e.id DESC
            """,
            {"symbol": symbol, "entry_id": entry_id},
        )
        rows = cursor.fetchall()
        ids = [row[0] for row in rows]
        corrections: dict[int, list[Correction]] = {entry: [] for entry in ids}
        if ids:
            cursor.execute(
                """
                SELECT c.entry_id, c.kind, c.thesis, c.accession_no, f.form_type, f.accepted_at,
                       c.reason, c.known_at, u.username, i.cik
                FROM journal_corrections c
                JOIN journal_entries e ON e.id = c.entry_id
                JOIN instruments i ON i.id = e.instrument_id
                JOIN app_users u ON u.id = c.recorded_by
                LEFT JOIN filings f ON f.accession_no = c.accession_no
                WHERE c.entry_id = ANY(%s)
                ORDER BY c.entry_id, c.known_at, c.id
                """,
                (ids,),
            )
            for entry, kind, thesis, acc, form, accepted, reason, known_at, user, cik in cursor.fetchall():
                corrections[entry].append(
                    Correction(kind, thesis, _source(cik, acc, form, accepted), reason, known_at, user)
                )

    entries = []
    for entry, sym, name, cik, action, thesis, acc, form, accepted, traded_on, price, shares, known_at, user in rows:
        original_source = _source(cik, acc, form, accepted)
        current_thesis, current_source, withdrawn = thesis, original_source, None
        for correction in corrections[entry]:
            if correction.kind == "thesis":
                current_thesis = correction.thesis
            elif correction.kind == "source":
                current_source = correction.source
            else:
                withdrawn = correction
        entries.append(
            Entry(
                id=entry,
                symbol=sym,
                name=name,
                action=action,
                thesis=current_thesis,
                source=current_source,
                original_thesis=thesis,
                original_source=original_source,
                traded_on=traded_on,
                price=None if price is None else float(price),
                shares=None if shares is None else float(shares),
                known_at=known_at,
                recorded_by=user,
                withdrawn=withdrawn,
                corrections=corrections[entry],
            )
        )
    return entries


def journal_tickers(conn: Connection) -> list[str]:
    """Every ticker with at least one entry, alphabetically."""
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT i.symbol FROM journal_entries e JOIN instruments i ON i.id = e.instrument_id "
            "ORDER BY i.symbol"
        )
        return [row[0] for row in cursor.fetchall()]


def open_positions(conn: Connection) -> list[str]:
    """Tickers whose counted entries leave a position open, alphabetically."""
    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT DISTINCT ON (i.symbol) i.symbol, e.action
            FROM journal_entries e
            JOIN instruments i ON i.id = e.instrument_id
            WHERE e.action IN ('opened', 'closed')
              AND NOT EXISTS (SELECT 1 FROM journal_corrections c
                              WHERE c.entry_id = e.id AND c.kind = 'withdrawn')
            ORDER BY i.symbol, e.known_at DESC, e.id DESC
            """)
        return [symbol for symbol, action in cursor.fetchall() if action == "opened"]
