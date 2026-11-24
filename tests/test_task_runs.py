"""The task-run lifecycle against Postgres: enqueue, claim under locks, finish, reconcile."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from orchestration import locks
from orchestration.task_runs import (
    RunResult,
    TaskDisabledError,
    TaskNotFoundError,
    claim_next_run,
    enqueue_run,
    finish_run,
    reconcile_runs,
)

TASK = "ingest_prices"
OTHER_TASK = "ingest_sec_filings"


def _enqueue(conn, task_id: str = TASK, **kwargs) -> int:
    kwargs.setdefault("trigger_source", "test")
    kwargs.setdefault("run_mode", "manual")
    kwargs.setdefault("requested_by", "pytest")
    run_id = enqueue_run(conn, task_id, **kwargs)
    conn.commit()
    return run_id


def _run(conn, run_id: int) -> dict:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT status, error_message, meta, started_at, completed_at, fetched_count, inserted_count "
            "FROM task_runs WHERE id = %s",
            (run_id,),
        )
        status, error, meta, started, completed, fetched, inserted = cursor.fetchone()
    conn.rollback()
    return {
        "status": status,
        "error": error,
        "meta": meta,
        "started_at": started,
        "completed_at": completed,
        "fetched": fetched,
        "inserted": inserted,
    }


def _db_now(conn) -> datetime:
    with conn.cursor() as cursor:
        cursor.execute("SELECT clock_timestamp()")
        now = cursor.fetchone()[0]
    conn.rollback()
    return now


def _lock_is_free(conn, task_id: str) -> bool:
    name = locks.task_lock_name(task_id)
    free = locks.try_lock(conn, name)
    if free:
        locks.unlock(conn, name)
    conn.commit()
    return free


# --- enqueue ---------------------------------------------------------------------------------------


def test_enqueue_creates_pending_run_with_merged_payload(connect_app_db) -> None:
    conn = connect_app_db()
    with conn.cursor() as cursor:
        cursor.execute(
            'UPDATE task_definitions SET default_payload = \'{"lookback_days": 2, "limit": 10}\' WHERE task_id = %s',
            (TASK,),
        )
    conn.commit()

    run_id = _enqueue(conn, payload={"limit": 50}, tickers=[" nvda ", "AMZN", ""])

    with conn.cursor() as cursor:
        cursor.execute("SELECT status, worker_name, tickers, meta FROM task_runs WHERE id = %s", (run_id,))
        status, worker_name, tickers, meta = cursor.fetchone()
    assert status == "pending"
    assert worker_name == "price_bars_worker"
    assert tickers == ["NVDA", "AMZN"]
    assert meta == {"dispatch_payload": {"lookback_days": 2, "limit": 50}}


def test_enqueue_refuses_unknown_and_disabled_tasks(connect_app_db) -> None:
    conn = connect_app_db()
    with pytest.raises(TaskNotFoundError):
        _enqueue(conn, "no_such_task")
    conn.rollback()

    with conn.cursor() as cursor:
        cursor.execute("UPDATE task_definitions SET enabled = FALSE WHERE task_id = %s", (TASK,))
    conn.commit()
    with pytest.raises(TaskDisabledError):
        _enqueue(conn)


# --- claim and finish ------------------------------------------------------------------------------


def test_claim_marks_running_and_hands_over_the_payload(connect_app_db) -> None:
    conn = connect_app_db()
    run_id = _enqueue(conn, payload={"limit": 5}, tickers=["NVDA"])

    claim = claim_next_run(conn, worker_instance="w1")

    assert claim is not None and claim.run is not None
    assert claim.run.id == run_id
    assert claim.run.payload == {"limit": 5}
    assert claim.run.tickers == ["NVDA"]
    row = _run(conn, run_id)
    assert row["status"] == "running"
    assert row["started_at"] is not None
    assert row["meta"]["worker_instance"] == "w1"
    assert claim_next_run(conn, worker_instance="w1") is None


def test_claims_in_creation_order(connect_app_db) -> None:
    conn = connect_app_db()
    first = _enqueue(conn, TASK)
    second = _enqueue(conn, OTHER_TASK)

    assert claim_next_run(conn, worker_instance="w").run_id == first
    assert claim_next_run(conn, worker_instance="w").run_id == second


def test_overlapping_run_of_the_same_task_is_skipped(connect_app_db) -> None:
    worker_a, worker_b = connect_app_db(), connect_app_db()
    first = _enqueue(worker_a)
    claim_a = claim_next_run(worker_a, worker_instance="a")
    second = _enqueue(worker_a)

    claim_b = claim_next_run(worker_b, worker_instance="b")

    assert claim_a.run is not None
    assert claim_b.run_id == second and claim_b.run is None
    assert claim_b.skip_reason == "task_already_running"
    skipped = _run(worker_b, second)
    assert skipped["status"] == "skipped"
    assert skipped["completed_at"] is not None
    assert skipped["meta"]["skip_reason"] == "task_already_running"
    assert _run(worker_b, first)["status"] == "running"


def test_two_workers_run_different_tasks_in_parallel(connect_app_db) -> None:
    worker_a, worker_b = connect_app_db(), connect_app_db()
    _enqueue(worker_a, TASK)
    _enqueue(worker_a, OTHER_TASK)

    claim_a = claim_next_run(worker_a, worker_instance="a")
    claim_b = claim_next_run(worker_b, worker_instance="b")

    assert {claim_a.task_id, claim_b.task_id} == {TASK, OTHER_TASK}
    assert claim_a.run is not None and claim_b.run is not None


def test_run_of_a_task_disabled_after_enqueue_is_skipped(connect_app_db) -> None:
    conn = connect_app_db()
    run_id = _enqueue(conn)
    with conn.cursor() as cursor:
        cursor.execute("UPDATE task_definitions SET enabled = FALSE WHERE task_id = %s", (TASK,))
    conn.commit()

    claim = claim_next_run(conn, worker_instance="w")

    assert claim.skip_reason == "task_disabled"
    assert _run(conn, run_id)["status"] == "skipped"
    assert _lock_is_free(connect_app_db(), TASK)


def test_finish_records_outcome_and_releases_the_lock(connect_app_db) -> None:
    worker, other = connect_app_db(), connect_app_db()
    run_id = _enqueue(worker)
    claim = claim_next_run(worker, worker_instance="w")
    assert not _lock_is_free(other, TASK)

    updated = finish_run(worker, claim.run, RunResult(status="partial", fetched_count=7, inserted_count=3))

    assert updated
    row = _run(worker, run_id)
    assert (row["status"], row["fetched"], row["inserted"]) == ("partial", 7, 3)
    assert row["completed_at"] is not None
    assert _lock_is_free(other, TASK)


def test_finish_rejects_non_terminal_status(connect_app_db) -> None:
    conn = connect_app_db()
    _enqueue(conn)
    claim = claim_next_run(conn, worker_instance="w")

    with pytest.raises(ValueError):
        finish_run(conn, claim.run, RunResult(status="running"))


def test_finish_does_not_overwrite_a_reconciled_run(connect_app_db) -> None:
    conn = connect_app_db()
    run_id = _enqueue(conn)
    claim = claim_next_run(conn, worker_instance="w")
    with conn.cursor() as cursor:
        cursor.execute(
            "UPDATE task_runs SET status = 'failed', error_message = 'reconciled', completed_at = NOW() WHERE id = %s",
            (run_id,),
        )
    conn.commit()

    assert finish_run(conn, claim.run, RunResult(status="succeeded")) is False
    assert _run(conn, run_id)["error"] == "reconciled"
    assert _lock_is_free(connect_app_db(), TASK)


# --- reconcile -------------------------------------------------------------------------------------

PENDING_TIMEOUT = timedelta(hours=1)
GRACE = timedelta(seconds=60)


def test_reconcile_fails_runs_nobody_claimed(connect_app_db) -> None:
    conn = connect_app_db()
    old = _enqueue(conn)
    fresh = _enqueue(conn, OTHER_TASK)
    with conn.cursor() as cursor:
        cursor.execute("UPDATE task_runs SET created_at = NOW() - INTERVAL '2 hours' WHERE id = %s", (old,))
    conn.commit()

    counts = reconcile_runs(conn, _db_now(conn), pending_timeout=PENDING_TIMEOUT, running_grace=GRACE)

    assert counts == {"pending_timed_out": 1, "running_abandoned": 0}
    assert _run(conn, old)["status"] == "failed"
    assert _run(conn, old)["error"] == "not claimed by a worker within 3600 s"
    assert _run(conn, fresh)["status"] == "pending"


def test_reconcile_fails_a_run_whose_worker_died(connect_app_db) -> None:
    scheduler, worker = connect_app_db(), connect_app_db()
    run_id = _enqueue(worker)
    claim_next_run(worker, worker_instance="w")
    worker.close()  # the worker process exits mid-run: Postgres drops its lock

    later = _db_now(scheduler) + timedelta(minutes=5)
    counts = reconcile_runs(scheduler, later, pending_timeout=PENDING_TIMEOUT, running_grace=GRACE)

    assert counts["running_abandoned"] == 1
    row = _run(scheduler, run_id)
    assert row["status"] == "failed"
    assert row["error"] == "worker stopped before the run finished"
    assert _lock_is_free(connect_app_db(), TASK)  # the reconciler released its own hold


def test_reconcile_leaves_live_and_recent_runs_alone(connect_app_db) -> None:
    scheduler, worker, dead_worker = connect_app_db(), connect_app_db(), connect_app_db()
    live = _enqueue(worker, TASK)
    claim_next_run(worker, worker_instance="live")
    recent = _enqueue(dead_worker, OTHER_TASK)
    claim_next_run(dead_worker, worker_instance="dead")
    dead_worker.close()

    # Live: its lock is held. Recent: lock free but started within the grace period.
    counts = reconcile_runs(scheduler, _db_now(scheduler), pending_timeout=PENDING_TIMEOUT, running_grace=GRACE)

    assert counts == {"pending_timed_out": 0, "running_abandoned": 0}
    assert _run(scheduler, live)["status"] == "running"
    assert _run(scheduler, recent)["status"] == "running"
    later = _db_now(scheduler) + timedelta(minutes=5)
    reconcile_runs(scheduler, later, pending_timeout=PENDING_TIMEOUT, running_grace=GRACE)
    assert _run(scheduler, live)["status"] == "running"
    assert _run(scheduler, recent)["status"] == "failed"
