# 0016: A separate worker for research

Status: accepted, 27 Nov 2026.

## Context

One worker runs every task, one at a time ([ADR 0003](0003-scheduler-and-worker.md)). Until now each task was kept short, about a minute at most, so the 5-minute SEC poll was never held up for long and alerts stayed inside the 10-minute target.

The base-rate calculator can't be made that short. It reads two years of bars for every stock with an event, about 6 ms per stock locally. Thousands of stocks take minutes on the server, and a base rate on every alert will add more such work.

## Decision

**A second worker service, `research-worker`.** It runs the same image as `worker`, with `--queue research`. It doesn't limit the size of tasks.

- **`task_definitions.queue`** (`default` or `research`) says which worker runs a task. `claim_next_run` only takes pending runs from its own queue. Everything else is unchanged: `SKIP LOCKED` claiming, per-task advisory locks, heartbeats, reconciliation, and the `task_run_failed` line.
- **What goes on `research`:** tasks that read only our own database. They call no data provider, so the two workers never compete for SEC's or Massive's rate limits, whose pacers are per process. Ingestion, alerts and onboarding stay on `default`.
- **First task:** `compute_base_rates`.

## Consequences

- One more container, about 60 MB idle. The instance has about 1 GB available since moving to `t4g.small`.
- A research run and an SEC poll can run at the same moment, each on its own database connection.
- A task that both calls a provider and computes for long would need splitting into two tasks, one per queue.
