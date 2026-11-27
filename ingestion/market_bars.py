"""``ingest_market_bars``: as-traded daily bars for every US stock, for the base-rate calculator.

One request to Massive's grouped-daily endpoint returns every US stock's bar for one trading day
(about 12,600 symbols, 1.3 MB). The job wants every trading day from two years back (the free plan's
history) to the latest closed session, and fetches the ones not recorded in ``market_bar_days``:

* **Oldest first.** The plan's history drops its oldest day each day, so those go first.
* **A few days per run** (``max_days``, default 5, about a minute at 5 requests a minute). The
  worker runs one job at a time, so a long run would hold up the 5-minute SEC poll. The task runs
  every 10 minutes; once history is complete a run makes no request until a new session closes.
* **A day is stored whole or not at all,** bars and its ``market_bar_days`` row in one transaction.
  A rerun of a stored day overwrites its rows, so reruns never duplicate.

What a fetched day becomes:

* bars → ``loaded``;
* no bars, and the day closed more than ``SETTLE_DAYS`` ago → ``empty`` (an unscheduled closure the
  calendar does not know, such as the national day of mourning on 9 January 2025);
* no bars for a recent day → nothing recorded: Massive has not published it yet, and the next run
  asks again;
* before the plan's history → ``unavailable``, never asked again.

Any other provider error stops the run: the rest would most likely fail the same way, and stopping
spends no more of the rate limit. The run is ``partial`` if it stored something first, ``failed``
otherwise. ADR 0012.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from psycopg2.extensions import connection as Connection
from psycopg2.extras import execute_values

from ingestion.market_calendar import NEW_YORK, is_trading_day, latest_completed_session
from ingestion.massive_client import MassiveClient, MassiveEntitlementError, MassiveError
from orchestration.task_runs import RunResult, TaskRun

LOGGER = logging.getLogger(__name__)

TASK_ID = "ingest_market_bars"
HISTORY_YEARS = 2
DEFAULT_MAX_DAYS = 5
SETTLE_DAYS = 3


def history_start(today: date) -> date:
    """The same calendar day ``HISTORY_YEARS`` back (28 February for 29 February)."""
    try:
        return today.replace(year=today.year - HISTORY_YEARS)
    except ValueError:
        return today.replace(year=today.year - HISTORY_YEARS, day=28)


def trading_days(start: date, end: date) -> list[date]:
    days = []
    current = start
    while current <= end:
        if is_trading_day(current):
            days.append(current)
        current += timedelta(days=1)
    return days


def _max_days(payload: dict[str, Any]) -> int:
    raw = payload.get("max_days", DEFAULT_MAX_DAYS)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        raise ValueError(f"max_days must be a positive integer, got {raw!r}")
    return value


def _recorded_days(conn: Connection, start: date) -> set[date]:
    with conn.cursor() as cursor:
        cursor.execute("SELECT trading_date FROM market_bar_days WHERE trading_date >= %s", (start,))
        days = {day for (day,) in cursor.fetchall()}
    conn.commit()
    return days


def _number(value: Any) -> float | None:
    return None if value is None else float(value)


def market_bar_rows(payload: dict[str, Any], day: date) -> tuple[list[tuple], int]:
    """Rows for ``day`` from a grouped-daily payload, plus how many aggregates were invalid.

    An aggregate is invalid without a symbol or close, with high below low, when stamped on another
    day, or when a symbol repeats.
    """
    rows: list[tuple] = []
    seen: set[str] = set()
    invalid = 0
    for aggregate in payload.get("results") or []:
        try:
            symbol = str(aggregate["T"]).strip()
            # Stamped at the session's New York midnight, in epoch milliseconds.
            stamped = datetime.fromtimestamp(int(aggregate["t"]) / 1000, tz=UTC).astimezone(NEW_YORK).date()
            o, h, low, c = (_number(aggregate.get(k)) for k in ("o", "h", "l", "c"))
            volume, vwap = _number(aggregate.get("v")), _number(aggregate.get("vw"))
            transactions = None if aggregate.get("n") is None else int(aggregate["n"])
        except (KeyError, TypeError, ValueError, OverflowError):
            invalid += 1
            continue
        if not symbol or c is None or stamped != day or symbol in seen:
            invalid += 1
            continue
        if h is not None and low is not None and h < low:
            invalid += 1
            continue
        seen.add(symbol)
        rows.append((symbol, day, o, h, low, c, volume, vwap, transactions))
    return rows, invalid


def store_day(
    conn: Connection,
    day: date,
    *,
    status: str,
    rows: list[tuple],
    invalid: int,
    request_id: str | None,
    task_run_id: int,
) -> tuple[int, int]:
    """Record ``day`` and upsert its bars in one transaction; returns (inserted, updated)."""
    inserted = updated = 0
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO market_bar_days (trading_date, status, symbol_count, invalid_count, request_id, task_run_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (trading_date) DO UPDATE SET
              status = EXCLUDED.status, symbol_count = EXCLUDED.symbol_count,
              invalid_count = EXCLUDED.invalid_count, request_id = EXCLUDED.request_id,
              task_run_id = EXCLUDED.task_run_id, fetched_at = NOW()
            """,
            (day, status, len(rows), invalid, request_id, task_run_id),
        )
        if rows:
            result = execute_values(
                cursor,
                """
                INSERT INTO market_bars (symbol, trading_date, open, high, low, close, volume, vwap, transactions)
                VALUES %s
                ON CONFLICT (symbol, trading_date) DO UPDATE SET
                  open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low, close = EXCLUDED.close,
                  volume = EXCLUDED.volume, vwap = EXCLUDED.vwap, transactions = EXCLUDED.transactions
                RETURNING (xmax = 0)
                """,
                rows,
                page_size=2000,
                fetch=True,
            )
            inserted = sum(1 for (is_new,) in result if is_new)
            updated = len(result) - inserted
    conn.commit()
    return inserted, updated


def ingest_market_bars(
    run: TaskRun, conn: Connection, *, massive: MassiveClient | None = None, now: datetime | None = None
) -> RunResult:
    now = now or datetime.now(UTC)
    target = latest_completed_session(now)
    start = history_start(now.astimezone(NEW_YORK).date())
    max_days = _max_days(run.payload)

    wanted = trading_days(start, target)
    recorded = _recorded_days(conn, start)
    todo = [day for day in wanted if day not in recorded][:max_days]
    meta: dict[str, Any] = {
        "target_session": target.isoformat(),
        "history_start": start.isoformat(),
        "max_days": max_days,
        "loaded": [],
        "empty": [],
        "unavailable": [],
        "waiting": [],
        "invalid_bars": 0,
        "errors": [],
    }
    if not todo:
        return RunResult(status="succeeded", meta={**meta, "remaining_days": 0, "message": "already_current"})

    client = massive or MassiveClient.from_env()
    fetched = inserted = updated = 0
    try:
        for day in todo:
            try:
                payload = client.grouped_daily(day)
            except MassiveEntitlementError:
                store_day(conn, day, status="unavailable", rows=[], invalid=0, request_id=None, task_run_id=run.id)
                meta["unavailable"].append(day.isoformat())
                continue
            except MassiveError as exc:
                meta["errors"].append(str(exc))
                LOGGER.warning("market_bars_fetch_failed run_id=%d day=%s error=%s", run.id, day, exc)
                break
            rows, invalid = market_bar_rows(payload, day)
            meta["invalid_bars"] += invalid
            request_id = payload.get("request_id")
            if rows:
                new, changed = store_day(
                    conn, day, status="loaded", rows=rows, invalid=invalid, request_id=request_id, task_run_id=run.id
                )
                fetched += len(rows)
                inserted += new
                updated += changed
                meta["loaded"].append(day.isoformat())
            elif (target - day).days > SETTLE_DAYS:
                store_day(
                    conn, day, status="empty", rows=[], invalid=invalid, request_id=request_id, task_run_id=run.id
                )
                meta["empty"].append(day.isoformat())
                LOGGER.warning("market_bars_day_empty run_id=%d day=%s", run.id, day)
            else:
                meta["waiting"].append(day.isoformat())
    finally:
        if massive is None:
            client.close()

    meta["remaining_days"] = len([day for day in wanted if day not in _recorded_days(conn, start)])
    stored_any = bool(meta["loaded"] or meta["empty"] or meta["unavailable"])
    if not meta["errors"]:
        status = "succeeded"
    elif stored_any:
        status = "partial"
    else:
        status = "failed"
    return RunResult(
        status=status,
        fetched_count=fetched,
        inserted_count=inserted,
        deduped_count=updated,
        error_message="; ".join(meta["errors"]) or None,
        meta=meta,
    )
