"""Task definitions and task runs: read endpoints plus the two manual triggers.

Triggers never run work themselves: they insert a ``pending`` run for the worker and return 202
(ground rule 4). Overlap is handled by the worker's advisory lock, which ends the later run
``skipped``.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from psycopg2.extensions import connection as Connection
from pydantic import BaseModel

from api.auth import AuthUser, CurrentUser
from api.db import DbConn
from orchestration.task_runs import TaskDisabledError, TaskNotFoundError, enqueue_run

RunStatus = Literal["pending", "running", "succeeded", "partial", "skipped", "failed"]

router = APIRouter(prefix="/api", tags=["tasks"])

TIMEZONE_ABBREVIATIONS = {"America/New_York": "ET", "UTC": "UTC"}


class LastRun(BaseModel):
    id: int
    status: RunStatus
    created_at: datetime


class TaskDefinition(BaseModel):
    task_id: str
    label: str
    schedule_label: str
    trigger_mode: Literal["manual", "automatic"]
    enabled: bool
    next_run_at: datetime | None
    last_run: LastRun | None


class TaskRunSummary(BaseModel):
    id: int
    task_id: str
    run_mode: str
    status: RunStatus
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    duration_seconds: float | None
    fetched_count: int
    inserted_count: int
    deduped_count: int
    error_message: str | None


class TaskRunDetail(TaskRunSummary):
    trace_id: str
    requested_by: str | None
    tickers: list[str]
    as_of_date: date | None
    force_refresh: bool
    meta: dict[str, Any]


class TaskRunPage(BaseModel):
    items: list[TaskRunSummary]
    total: int


class QueuedRun(BaseModel):
    run_id: int
    status: Literal["pending"] = "pending"


def schedule_label(
    schedule_type: str,
    interval_minutes: int | None,
    scheduled_time: time | None,
    schedule_timezone: str,
    schedule_days: str,
) -> str:
    """Label a schedule, e.g. ``Every 10 min``, ``Every 2 h``, ``Weekdays 18:30 ET``, ``Daily 06:00 UTC``."""
    if schedule_type == "interval" and interval_minutes is not None:
        if interval_minutes >= 60 and interval_minutes % 60 == 0:
            return f"Every {interval_minutes // 60} h"
        return f"Every {interval_minutes} min"
    if schedule_type == "daily_time" and scheduled_time is not None:
        days = "Weekdays" if schedule_days == "weekdays" else "Daily"
        zone = TIMEZONE_ABBREVIATIONS.get(schedule_timezone, schedule_timezone)
        return f"{days} {scheduled_time:%H:%M} {zone}"
    raise ValueError(f"Unrecognised schedule {schedule_type!r}")


@router.get("/tasks", response_model=list[TaskDefinition])
def list_tasks(conn: DbConn) -> list[TaskDefinition]:
    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT td.task_id, td.label, td.schedule_type, td.interval_minutes, td.scheduled_time,
                   td.schedule_timezone, td.schedule_days, td.trigger_mode, td.enabled, td.next_run_at,
                   last.id, last.status, last.created_at
            FROM task_definitions td
            LEFT JOIN LATERAL (
                SELECT id, status, created_at
                FROM task_runs
                WHERE task_runs.task_id = td.task_id
                ORDER BY created_at DESC, id DESC
                LIMIT 1
            ) last ON TRUE
            ORDER BY td.task_id
            """)
        rows = cursor.fetchall()
    return [
        TaskDefinition(
            task_id=row[0],
            label=row[1],
            # A manual task's schedule columns are placeholders the table requires; it runs on demand.
            schedule_label=(
                schedule_label(row[2], row[3], row[4], row[5], row[6]) if row[7] == "automatic" else "On demand"
            ),
            trigger_mode=row[7],
            enabled=row[8],
            # Manual tasks are never scheduled, whatever the column holds.
            next_run_at=row[9] if row[7] == "automatic" and row[8] else None,
            last_run=None if row[10] is None else LastRun(id=row[10], status=row[11], created_at=row[12]),
        )
        for row in rows
    ]


def _queue(conn: Connection, user: AuthUser, task_id: str, **parameters: Any) -> QueuedRun:
    try:
        run_id = enqueue_run(
            conn,
            task_id,
            trigger_source="api",
            run_mode="manual",
            requested_by=f"web:{user.username}",
            **parameters,
        )
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TaskDisabledError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    conn.commit()
    return QueuedRun(run_id=run_id)


@router.post("/tasks/{task_id}/run", status_code=202, response_model=QueuedRun)
def run_task(task_id: str, conn: DbConn, user: CurrentUser) -> QueuedRun:
    return _queue(conn, user, task_id)


_SUMMARY_COLUMNS = """
    id, task_id, run_mode, status, created_at, started_at, completed_at,
    EXTRACT(EPOCH FROM completed_at - started_at)::float8, fetched_count, inserted_count, deduped_count,
    error_message
"""


def _summary(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "id": row[0],
        "task_id": row[1],
        "run_mode": row[2],
        "status": row[3],
        "created_at": row[4],
        "started_at": row[5],
        "completed_at": row[6],
        "duration_seconds": row[7],
        "fetched_count": row[8],
        "inserted_count": row[9],
        "deduped_count": row[10],
        "error_message": row[11],
    }


@router.get("/task-runs", response_model=TaskRunPage)
def list_task_runs(
    conn: DbConn,
    task_id: str | None = None,
    status: RunStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TaskRunPage:
    """Runs newest first, optionally for one task and one status."""
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT {_SUMMARY_COLUMNS}, COUNT(*) OVER ()
            FROM task_runs
            WHERE (%(task_id)s::text IS NULL OR task_id = %(task_id)s)
              AND (%(status)s::text IS NULL OR status = %(status)s)
            ORDER BY created_at DESC, id DESC
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            {"task_id": task_id, "status": status, "limit": limit, "offset": offset},
        )
        rows = cursor.fetchall()
        if rows:
            total = int(rows[0][-1])
        else:
            # Past the last page the window count is gone; count separately.
            cursor.execute(
                """
                SELECT COUNT(*) FROM task_runs
                WHERE (%(task_id)s::text IS NULL OR task_id = %(task_id)s)
                  AND (%(status)s::text IS NULL OR status = %(status)s)
                """,
                {"task_id": task_id, "status": status},
            )
            total = int(cursor.fetchone()[0])
    return TaskRunPage(items=[TaskRunSummary(**_summary(row)) for row in rows], total=total)


def _fetch_run(conn: Connection, run_id: int) -> TaskRunDetail:
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT {_SUMMARY_COLUMNS}, trace_id::text, requested_by, tickers, as_of_date, force_refresh, meta
            FROM task_runs WHERE id = %s
            """,
            (run_id,),
        )
        row = cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Task run {run_id} not found")
    return TaskRunDetail(
        **_summary(row),
        trace_id=row[12],
        requested_by=row[13],
        tickers=row[14],
        as_of_date=row[15],
        force_refresh=row[16],
        meta=row[17],
    )


@router.get("/task-runs/{run_id}", response_model=TaskRunDetail)
def get_task_run(run_id: int, conn: DbConn) -> TaskRunDetail:
    return _fetch_run(conn, run_id)


@router.post("/task-runs/{run_id}/rerun", status_code=202, response_model=QueuedRun)
def rerun_task_run(run_id: int, conn: DbConn, user: CurrentUser) -> QueuedRun:
    """Queue a new manual run of the same task with the same tickers, as-of date, refresh flag and payload."""
    run = _fetch_run(conn, run_id)
    payload = run.meta.get("dispatch_payload")
    return _queue(
        conn,
        user,
        run.task_id,
        payload=payload if isinstance(payload, dict) else {},
        tickers=run.tickers,
        as_of_date=run.as_of_date,
        force_refresh=run.force_refresh,
    )
