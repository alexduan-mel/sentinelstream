"""``ingest_splits``: every US stock split from Massive, and split detection for the watchlist.

**Loading.** Splits are fetched one calendar quarter per request (about 400 in a 2025 quarter), from
the quarter holding the start of the two-year history to the one holding ``UPCOMING_DAYS`` from now
(splits are listed before they happen). A quarter is written whole in one transaction, and splits in
it that Massive no longer lists are removed, so a corrected record replaces the old one. An empty
answer for a quarter that has splits stored is treated as a provider error, not as a correction. A quarter
whose end was more than ``SETTLE_DAYS`` before its last fetch is settled and not asked for again; the
current and upcoming quarters are fetched on every run. Unsettled quarters go first, then the oldest
unsettled backlog, at most ``max_requests`` per run (default 6, about a minute), so the single worker
never holds up the SEC poll for long.

**Detection.** Watchlist and benchmark bars in ``price_bars`` are split-adjusted as of when they were
fetched (ADR 0002). After a split, the bars from before it that were fetched before it took effect
are on the old scale. For each active instrument with such bars, the run queues one ``ingest_prices``
run that re-fetches the instrument's whole stored history, unless one is already pending or running.
A day whose splits cancel out (a reverse split and a forward split together) changes nothing and is
ignored. Detection works from stored splits, so it also runs when a fetch fails.

Market-wide bars are stored as traded and need no re-fetch; research corrects their returns with
these splits. ADR 0013.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

from psycopg2.extensions import connection as Connection
from psycopg2.extras import execute_values

from ingestion.market_bars import history_start
from ingestion.market_calendar import NEW_YORK
from ingestion.massive_client import MassiveClient, MassiveError
from orchestration.task_runs import RunResult, TaskDisabledError, TaskRun, enqueue_run

LOGGER = logging.getLogger(__name__)

TASK_ID = "ingest_splits"
PRICES_TASK_ID = "ingest_prices"
UPCOMING_DAYS = 120
SETTLE_DAYS = 45
DEFAULT_MAX_REQUESTS = 6


def quarter_start(day: date) -> date:
    return date(day.year, 3 * ((day.month - 1) // 3) + 1, 1)


def quarter_windows(start: date, end: date) -> list[tuple[date, date]]:
    """Calendar quarters from the one holding ``start`` to the one holding ``end``."""
    windows = []
    first = quarter_start(start)
    while first <= end:
        following = date(first.year + (first.month + 2) // 12, (first.month + 2) % 12 + 1, 1)
        windows.append((first, following - timedelta(days=1)))
        first = following
    return windows


def _max_requests(payload: dict[str, Any]) -> int:
    raw = payload.get("max_requests", DEFAULT_MAX_REQUESTS)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        raise ValueError(f"max_requests must be a positive integer, got {raw!r}")
    return value


def split_rows(results: list[dict[str, Any]], window: tuple[date, date]) -> tuple[list[tuple], int]:
    """Rows for the splits in ``window``, plus how many records were invalid or repeated."""
    rows: dict[str, tuple] = {}
    invalid = 0
    for record in results:
        try:
            split_id = str(record["id"]).strip()
            symbol = str(record["ticker"]).strip()
            executed = date.fromisoformat(str(record["execution_date"]))
            split_from, split_to = float(record["split_from"]), float(record["split_to"])
        except (KeyError, TypeError, ValueError):
            invalid += 1
            continue
        valid_ratio = all(math.isfinite(v) and v > 0 for v in (split_from, split_to))
        if not split_id or not symbol or not valid_ratio or not window[0] <= executed <= window[1]:
            invalid += 1
            continue
        if split_id in rows:
            invalid += 1
        rows[split_id] = (split_id, symbol, executed, split_from, split_to)
    return list(rows.values()), invalid


def store_window(conn: Connection, window: tuple[date, date], rows: list[tuple], task_run_id: int) -> int:
    """Replace the quarter's splits with ``rows`` and record it, in one transaction; returns how many were removed."""
    start, end = window
    with conn.cursor() as cursor:
        cursor.execute(
            "DELETE FROM stock_splits WHERE execution_date BETWEEN %s AND %s AND NOT (id = ANY(%s))",
            (start, end, [row[0] for row in rows]),
        )
        removed = cursor.rowcount
        if rows:
            execute_values(
                cursor,
                """
                INSERT INTO stock_splits (id, symbol, execution_date, split_from, split_to, task_run_id)
                VALUES %s
                ON CONFLICT (id) DO UPDATE SET
                  symbol = EXCLUDED.symbol, execution_date = EXCLUDED.execution_date,
                  split_from = EXCLUDED.split_from, split_to = EXCLUDED.split_to,
                  task_run_id = EXCLUDED.task_run_id, fetched_at = NOW()
                """,
                [(*row, task_run_id) for row in rows],
                page_size=1000,
            )
        cursor.execute(
            """
            INSERT INTO split_windows (window_start, window_end, split_count, task_run_id)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (window_start) DO UPDATE SET
              window_end = EXCLUDED.window_end, split_count = EXCLUDED.split_count,
              task_run_id = EXCLUDED.task_run_id, fetched_at = NOW()
            """,
            (start, end, len(rows), task_run_id),
        )
    conn.commit()
    return removed


def _stored_count(conn: Connection, window: tuple[date, date]) -> int:
    with conn.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM stock_splits WHERE execution_date BETWEEN %s AND %s", window)
        (count,) = cursor.fetchone()
    conn.commit()
    return count


def _fetched_windows(conn: Connection) -> dict[date, datetime]:
    with conn.cursor() as cursor:
        cursor.execute("SELECT window_start, fetched_at FROM split_windows")
        rows = dict(cursor.fetchall())
    conn.commit()
    return rows


def _settled(window: tuple[date, date], fetched_at: datetime | None) -> bool:
    return fetched_at is not None and fetched_at.astimezone(NEW_YORK).date() > window[1] + timedelta(days=SETTLE_DAYS)


def stale_watchlist_splits(conn: Connection, today: date) -> dict[str, tuple[date, list[date]]]:
    """Instruments whose stored bars predate a split that has taken effect.

    Returns symbol -> (first stored bar date, split dates). A bar is stale for a split when it is
    dated before the split and was fetched before 09:30 New York time on the split day.
    """
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT i.symbol, s.execution_date, s.split_from, s.split_to,
                   (SELECT MIN(trading_date) FROM price_bars WHERE instrument_id = i.id)
            FROM instruments i
            JOIN stock_splits s ON s.symbol = i.symbol AND s.execution_date <= %(today)s
            WHERE i.active
              AND EXISTS (
                SELECT 1 FROM price_bars b
                WHERE b.instrument_id = i.id AND b.trading_date < s.execution_date
                  AND b.fetched_at < (s.execution_date + TIME '09:30') AT TIME ZONE 'America/New_York')
            ORDER BY i.symbol, s.execution_date
            """,
            {"today": today},
        )
        rows = cursor.fetchall()
    conn.commit()
    factors: dict[tuple[str, date], float] = defaultdict(lambda: 1.0)
    first_bar: dict[str, date] = {}
    for symbol, executed, split_from, split_to, first in rows:
        factors[(symbol, executed)] *= split_to / split_from
        first_bar[symbol] = first
    stale: dict[str, tuple[date, list[date]]] = {}
    for (symbol, executed), factor in factors.items():
        if abs(factor - 1.0) > 1e-9:
            stale.setdefault(symbol, (first_bar[symbol], []))[1].append(executed)
    return stale


def _refetch_waiting(conn: Connection, symbol: str) -> bool:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM task_runs WHERE task_id = %s AND status IN ('pending', 'running') AND tickers ? %s LIMIT 1",
            (PRICES_TASK_ID, symbol),
        )
        waiting = cursor.fetchone() is not None
    conn.commit()
    return waiting


def queue_refetches(conn: Connection, today: date, meta: dict[str, Any]) -> None:
    for symbol, (first, split_dates) in sorted(stale_watchlist_splits(conn, today).items()):
        splits = [d.isoformat() for d in split_dates]
        if _refetch_waiting(conn, symbol):
            meta["refetch_waiting"].append(symbol)
            continue
        try:
            run_id = enqueue_run(
                conn,
                PRICES_TASK_ID,
                trigger_source="split_detected",
                run_mode="backfill",
                requested_by=TASK_ID,
                tickers=[symbol],
                payload={"date_from": first.isoformat()},
            )
        except TaskDisabledError as exc:
            conn.rollback()
            meta["errors"].append(f"{symbol} split on {', '.join(splits)}: {exc}")
            continue
        conn.commit()
        meta["refetch_queued"].append({"symbol": symbol, "run_id": run_id, "splits": splits})
        LOGGER.info("split_refetch_queued symbol=%s run_id=%d splits=%s", symbol, run_id, ",".join(splits))


def ingest_splits(
    run: TaskRun, conn: Connection, *, massive: MassiveClient | None = None, now: datetime | None = None
) -> RunResult:
    now = now or datetime.now(UTC)
    today = now.astimezone(NEW_YORK).date()
    max_requests = _max_requests(run.payload)

    windows = quarter_windows(history_start(today), today + timedelta(days=UPCOMING_DAYS))
    fetched = _fetched_windows(conn)
    open_windows = [w for w in windows if not _settled(w, fetched.get(w[0]))]
    unsettled = [w for w in open_windows if w[1] + timedelta(days=SETTLE_DAYS) >= today]
    backlog = [w for w in open_windows if w not in unsettled]
    todo = (unsettled + backlog)[:max_requests]

    meta: dict[str, Any] = {
        "windows": [],
        "splits": 0,
        "removed": 0,
        "invalid": 0,
        "refetch_queued": [],
        "refetch_waiting": [],
        "errors": [],
    }
    stored = 0
    client = massive or MassiveClient.from_env()
    try:
        for window in todo:
            try:
                results = client.splits(*window)
            except MassiveError as exc:
                meta["errors"].append(str(exc))
                LOGGER.warning("splits_fetch_failed run_id=%d window=%s error=%s", run.id, window[0], exc)
                break
            rows, invalid = split_rows(results, window)
            if not rows and _stored_count(conn, window):
                # A glitch, not a correction: every quarter so far has had hundreds of splits.
                meta["errors"].append(f"Massive listed no splits for {window[0]}..{window[1]}, which has some stored")
                break
            meta["removed"] += store_window(conn, window, rows, run.id)
            meta["windows"].append(f"{window[0].isoformat()}..{window[1].isoformat()}")
            meta["splits"] += len(rows)
            meta["invalid"] += invalid
            stored += 1
    finally:
        if massive is None:
            client.close()

    fetched = _fetched_windows(conn)
    meta["remaining_windows"] = sum(
        1 for w in windows if not _settled(w, fetched.get(w[0])) and w[1] + timedelta(days=SETTLE_DAYS) < today
    )
    queue_refetches(conn, today, meta)

    if not meta["errors"]:
        status = "succeeded"
    elif stored or meta["refetch_queued"]:
        status = "partial"
    else:
        status = "failed"
    return RunResult(
        status=status,
        fetched_count=meta["splits"],
        error_message="; ".join(meta["errors"]) or None,
        meta=meta,
    )
