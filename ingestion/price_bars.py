"""``ingest_prices``: fetch split-adjusted daily bars from Massive for active instruments.

Carried over from the prototype's ``workers/price_bars_worker.py``, now an in-process handler keyed
by ``instrument_id``. Each run brings every instrument up to the latest completed session:

* Incremental (default): from the day after the instrument's newest bar, or ``lookback_days`` back
  if it has none. ``force_refresh`` re-fetches the whole lookback window, e.g. after a split.
* Explicit: payload ``{"date_from": "YYYY-MM-DD", "date_to": "YYYY-MM-DD"}`` for a manual backfill.

Bars for a session that has not closed yet are never stored. Upserts are keyed by instrument,
trading date and provider, so reruns overwrite rather than duplicate. A run ends ``partial`` if some
instruments failed or still lack the latest session's bar, and ``failed`` if all of them did.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, date, datetime, timedelta
from typing import Any

from psycopg2.extensions import connection as Connection
from psycopg2.extras import Json, execute_values

from ingestion.market_calendar import NEW_YORK, latest_published_session
from ingestion.massive_client import MassiveClient, MassiveError
from orchestration.task_runs import RunResult, TaskRun

LOGGER = logging.getLogger(__name__)

TASK_ID = "ingest_prices"
PROVIDER = "massive"
DEFAULT_LOOKBACK_DAYS = 30


def _lookback_days(payload: dict[str, Any]) -> int:
    raw = payload.get("lookback_days", os.getenv("PRICE_LOOKBACK_DAYS") or DEFAULT_LOOKBACK_DAYS)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        raise ValueError(f"lookback_days must be a positive integer, got {raw!r}")
    return value


def _payload_date(payload: dict[str, Any], key: str) -> date | None:
    value = payload.get(key)
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"{key} must be YYYY-MM-DD, got {value!r}") from exc


def _instruments(conn: Connection, tickers: list[str]) -> list[tuple[int, str]]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, symbol FROM instruments
            WHERE active AND (cardinality(%(tickers)s::text[]) = 0 OR symbol = ANY(%(tickers)s::text[]))
            ORDER BY symbol
            """,
            {"tickers": tickers},
        )
        rows = cursor.fetchall()
    conn.commit()
    return rows


def _latest_bars(conn: Connection, instrument_ids: list[int]) -> dict[int, date]:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT instrument_id, MAX(trading_date) FROM price_bars "
            "WHERE provider = %s AND instrument_id = ANY(%s) GROUP BY instrument_id",
            (PROVIDER, instrument_ids),
        )
        rows = dict(cursor.fetchall())
    conn.commit()
    return rows


def bar_rows(
    instrument_id: int, payload: dict[str, Any], *, date_from: date, date_to: date, task_run_id: int
) -> tuple[list[tuple], int]:
    """Database rows for the aggregates dated ``date_from``..``date_to``, plus how many were invalid."""
    rows: list[tuple] = []
    invalid = 0
    for aggregate in payload.get("results") or []:
        try:
            # A daily aggregate is stamped at the session's New York midnight, in epoch milliseconds.
            trading_date = datetime.fromtimestamp(int(aggregate["t"]) / 1000, tz=UTC).astimezone(NEW_YORK).date()
            o, h, low, c = (None if aggregate.get(k) is None else float(aggregate[k]) for k in ("o", "h", "l", "c"))
            volume = None if aggregate.get("v") is None else int(float(aggregate["v"]))
        except (KeyError, TypeError, ValueError, OverflowError):
            invalid += 1
            continue
        if not date_from <= trading_date <= date_to:
            continue
        if h is not None and low is not None and h < low:
            invalid += 1
            continue
        quality = "ok" if None not in (o, h, low, c) else "partial"
        raw = {"aggregate": aggregate, "request_id": payload.get("request_id"), "adjusted": payload.get("adjusted")}
        rows.append((instrument_id, trading_date, o, h, low, c, volume, PROVIDER, quality, Json(raw), task_run_id))
    return rows, invalid


def upsert_bars(conn: Connection, rows: list[tuple]) -> tuple[int, int]:
    """Upsert and commit; returns (inserted, updated)."""
    if not rows:
        return 0, 0
    with conn.cursor() as cursor:
        result = execute_values(
            cursor,
            """
            INSERT INTO price_bars (instrument_id, trading_date, open, high, low, close, volume, provider,
                                    data_quality, raw_payload, task_run_id)
            VALUES %s
            ON CONFLICT (instrument_id, trading_date, provider) DO UPDATE SET
              open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low, close = EXCLUDED.close,
              volume = EXCLUDED.volume, data_quality = EXCLUDED.data_quality, raw_payload = EXCLUDED.raw_payload,
              task_run_id = EXCLUDED.task_run_id, fetched_at = NOW()
            RETURNING (xmax = 0)
            """,
            rows,
            fetch=True,
        )
    conn.commit()
    inserted = sum(1 for (is_new,) in result if is_new)
    return inserted, len(result) - inserted


def ingest_prices(
    run: TaskRun, conn: Connection, *, massive: MassiveClient | None = None, now: datetime | None = None
) -> RunResult:
    target = latest_published_session(now or datetime.now(UTC))
    lookback = _lookback_days(run.payload)
    explicit_from = _payload_date(run.payload, "date_from")
    explicit_to = _payload_date(run.payload, "date_to")
    explicit = explicit_from is not None or explicit_to is not None

    instruments = _instruments(conn, run.tickers)
    meta: dict[str, Any] = {"target_session": target.isoformat(), "errors": [], "invalid_bars": 0}
    if run.tickers:
        meta["unknown_tickers"] = sorted(set(run.tickers) - {symbol for _id, symbol in instruments})
    if not instruments:
        return RunResult(status="succeeded", meta={**meta, "message": "no_instruments"})

    windows: dict[int, tuple[str, date, date]] = {}
    if explicit:
        date_to = min(explicit_to or target, target)
        date_from = explicit_from or date_to - timedelta(days=lookback)
        if date_from > date_to:
            raise ValueError(f"date_from {date_from} is after date_to {date_to} (capped at the last closed session)")
        windows = {instrument_id: (symbol, date_from, date_to) for instrument_id, symbol in instruments}
    else:
        latest = {} if run.force_refresh else _latest_bars(conn, [instrument_id for instrument_id, _ in instruments])
        for instrument_id, symbol in instruments:
            newest = latest.get(instrument_id)
            if newest is None:
                windows[instrument_id] = (symbol, target - timedelta(days=lookback), target)
            elif newest < target:
                windows[instrument_id] = (symbol, newest + timedelta(days=1), target)
    meta["mode"] = "explicit" if explicit else "incremental"
    meta["attempted"] = [symbol for symbol, _f, _t in windows.values()]
    if not windows:
        return RunResult(status="succeeded", meta={**meta, "message": "already_current"})

    client = massive or MassiveClient.from_env()
    fetched = inserted = updated = 0
    failed: set[str] = set()
    try:
        for instrument_id, (symbol, date_from, date_to) in windows.items():
            try:
                payload = client.daily_bars(symbol, date_from, date_to)
            except MassiveError as exc:
                failed.add(symbol)
                meta["errors"].append(str(exc))
                LOGGER.warning("price_fetch_failed run_id=%d symbol=%s error=%s", run.id, symbol, exc)
                continue
            rows, invalid = bar_rows(instrument_id, payload, date_from=date_from, date_to=date_to, task_run_id=run.id)
            new, changed = upsert_bars(conn, rows)
            fetched += len(rows)
            inserted += new
            updated += changed
            meta["invalid_bars"] += invalid
    finally:
        if massive is None:
            client.close()

    missing: list[str] = []
    if not explicit:
        latest_after = _latest_bars(conn, list(windows))
        missing = [
            symbol for instrument_id, (symbol, _f, _t) in windows.items() if latest_after.get(instrument_id) != target
        ]
        meta["missing_target_session"] = missing
    problems = failed | set(missing)
    if not problems:
        status = "succeeded"
    elif len(problems) < len(windows):
        status = "partial"
    else:
        status = "failed"
    error = None
    if status != "succeeded":
        parts = list(meta["errors"])
        if missing:
            parts.append(f"no {target.isoformat()} bar for: {', '.join(sorted(missing))}")
        error = "; ".join(parts)
    return RunResult(
        status=status,
        fetched_count=fetched,
        inserted_count=inserted,
        deduped_count=updated,
        error_message=error,
        meta=meta,
    )
