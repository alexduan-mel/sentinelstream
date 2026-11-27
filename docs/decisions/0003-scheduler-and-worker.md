# 0003: Separate scheduler and worker services

Status: accepted, 25 Nov 2026

## Context

In the prototype, `task_scheduler_worker.py` enqueued due tasks and then ran every pending run itself, each as a blocking subprocess with a 30-minute timeout. That design has three problems:

- **One slow job stalls everything.** While a backfill runs, nothing else is scheduled or run, including the SEC poller that M1 needs every 5 minutes.
- **Abandoned runs linger.** A run whose process died stayed `running` until a one-hour stale threshold failed it. Every deploy restarts the containers, so this would happen often.
- **The worker had no separate identity**, so it could not be scaled, stopped or given its own grace period.

Ground rule 4 also requires that all background work go through `task_runs`, that overlapping runs end `skipped` under advisory locks, and that every run ends in an explicit state.

## Decision

**Two services from the one image.** The `scheduler` only writes: it enqueues due tasks as `pending` runs and reconciles abandoned ones. The `worker` claims pending runs and executes them. Manual triggers (the CLI now, the API in a later PR) insert the same `pending` rows.

**Claiming.** A worker takes the oldest pending run with `FOR UPDATE SKIP LOCKED`, so more workers can be added later without double-claiming. In the same transaction it tries the task's session advisory lock (`sentinel_stream_task:<task_id>`). If another session holds the lock, the run ends `skipped` with `meta.skip_reason = task_already_running`. If the task has been disabled since the run was queued, the run ends `skipped` with `task_disabled`. Otherwise the run becomes `running`, and the worker's control connection holds the lock until `finish_run` records the outcome.

**Handlers run in-process.** The prototype passed each job's payload as subprocess CLI flags, one branch per task. Here a registry (`orchestration/handlers.py`) maps `task_id` to a function that receives the run and its own database connection. This saves starting a fresh Python process for every run (each service measures about 18 MB idle) on a 1 GiB host, and the payload arrives as a dict. An exception, a missing handler or an invalid result ends the run `failed` with the error recorded. The trade-off is that a handler that hangs blocks this worker. There is no per-run timeout, so providers must be called with HTTP timeouts, which ground rule 5 already requires.

**Liveness comes from the lock.** Postgres drops a session's advisory locks when its connection closes. On every tick, the scheduler checks each `running` run older than a 60-second grace period by trying its task lock. If the lock is free, the worker is gone (crash, OOM kill, deploy restart), and the run is failed straight away instead of after an hour. Pending runs that no worker claims within an hour are also failed. The check holds the lock for a moment. A worker that claims the same task at that exact instant skips its run, which is harmless because the next scheduled run follows.

**One active scheduler.** It holds `sentinel_stream_task_scheduler`. A second instance waits on standby instead of exiting, so a restart loop cannot develop. Each due task is enqueued, and its `next_run_at` moved on, in one transaction per task, so one bad definition cannot block the rest. A missed schedule runs once, not once per missed slot. A task newly switched to `automatic` gets its first `next_run_at` and does not run at once.

**Health.** Neither service serves HTTP, so each touches a heartbeat file: the scheduler once per tick, the worker between claims and every 15 seconds during a run. The Compose health check fails when the file is stale, and `docker compose up --wait` in deploys requires all three app services to be healthy.

## Consequences

- One worker runs one task at a time. Once onboarding backfills arrive (M1), a long backfill would delay the 5-minute poller. The fix then is a second worker, or a worker per queue; claiming already supports either.
- Handlers own their transactions and must stay idempotent (ground rule 3), because a run cut off by a deploy is failed and the task runs again later.
- Session advisory locks are re-entrant, so the code unlocks exactly once per successful `try_lock`. Lock names share one `hashtext` key space with the migration runner's lock, so a name collision is possible but very unlikely.
- Rolling back to an image built before this change would fail: the Compose files now start `orchestration.scheduler` and `orchestration.worker`, which older images do not contain. This applies only to the first deploy of this change.

## Update, 27 Nov 2026

A second worker, `research-worker`, now serves a separate queue for long research calculations, so they never hold up the SEC poll ([ADR 0016](0016-research-worker.md)). Claiming, locks and reconciliation are unchanged.
