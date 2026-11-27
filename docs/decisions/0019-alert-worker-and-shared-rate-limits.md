# 0019: An alert-only worker, and rate limits shared through Postgres

Status: proposed, 27 Nov 2026.

## Context

**Alerts wait behind other work.** Every task that calls SEC or Massive runs on one worker, one run at a time, in the order runs were queued ([ADR 0003](0003-scheduler-and-worker.md), [ADR 0016](0016-research-worker.md)):

- the 5-minute SEC poll that produces alerts;
- watchlist prices, market-wide bars, splits, insider datasets, market-wide 8-Ks and ticker onboarding.

The poll waits until whatever is running finishes. Alert latency, whose target is 10 minutes from EDGAR acceptance, has been protected only by keeping every other task short: at most about a minute a run. That makes backfills crawl. Two years of market-wide bars at 5 days per 10-minute run take about 17 hours, against about 100 minutes at Massive's actual limit. The alert path is protected by a habit, not by the design.

**Rate limits are counted per process, or per run.** Each limiter only knows about its own requests:

| Provider | Limit | Counted today by | Gap |
| --- | --- | --- | --- |
| SEC (fair access) | 10 requests a second per user | One `Pacer` per process, at 5 a second | The API process also calls SEC (the ticker check when one is added), with its own pacer, so the API and the worker together can reach 10 a second. A second worker would add another 5. |
| Massive (free plan) | 5 requests a minute | One `Pacer` per `MassiveClient`, which each run creates | Two runs back to back (market bars, then splits) each start with a fresh count and can together exceed 5 a minute. Only retries on HTTP 429 cover this. |
| Slack webhook | about 1 message a second | One `Pacer` per process | Only the poll sends, so this is fine. |

`Retry-After` is honoured by holding off the pacer of the process that received it. Other processes keep sending.

## Decision

### 1. A worker only for alerts

A third queue, `alerts`, next to `default` and `research`. The `ingest_sec_filings` task moves to it. That task polls EDGAR, classifies new links (reading Form 4 XML) and delivers to Slack. A new Compose service, `alert-worker`, runs the same image with `--queue alerts`. Nothing else is ever queued there, so a poll never waits behind another task: only behind the previous poll, which the per-task lock already handles.

| Worker | Queue | Tasks |
| --- | --- | --- |
| `alert-worker` (new) | `alerts` | `ingest_sec_filings` |
| `worker` | `default` | prices, market-wide bars, splits, insider datasets, market-wide 8-Ks, onboarding |
| `research-worker` | `research` | `compute_base_rates` |

Claiming, advisory locks, heartbeats, reconciliation and the `task_run_failed` line are unchanged. Only the queue a task belongs to moves.

### 2. One token bucket per provider, shared through Postgres

**Table.** A new table, `rate_buckets`, has one row per provider:

| Column | Meaning |
| --- | --- |
| `name` | `sec`, `massive` |
| `capacity` | most tokens the bucket holds (the largest burst) |
| `refill_per_second` | how fast tokens come back |
| `tokens` | tokens left at `updated_at` (fractional, see below) |
| `updated_at` | when `tokens` was last written, by the database clock |
| `blocked_until` | no request before this time (a shared `Retry-After`) |

**Taking a token is one statement.** Every process, including api, workers and CLI, takes a token before every request to that provider, retries included:

```sql
UPDATE rate_buckets
SET tokens     = LEAST(capacity, tokens + EXTRACT(EPOCH FROM clock_timestamp() - updated_at) * refill_per_second) - 1,
    updated_at = clock_timestamp()
WHERE name = %(name)s
  AND clock_timestamp() >= blocked_until
  AND LEAST(capacity, tokens + EXTRACT(EPOCH FROM clock_timestamp() - updated_at) * refill_per_second) >= 1
RETURNING tokens;
```

- **The refill is lazy.** No timer or background job tops the bucket up. Each take works out what has grown back since `updated_at` (elapsed seconds × rate, capped at `capacity`), and saves the result.
- **Fractions are kept.** A request needs a whole token (≥ 1). The fraction is refill time already earned but not yet a whole token. Rounding it away would reset that progress on every update, so a busy bucket would never reach 1 again.
- **No row back means wait.** Without a row, the process reads the row and sleeps until `blocked_until`, or until one token exists: `(1 − tokens) ÷ refill_per_second`. Then it tries again.
- **No double-spend.** The update locks the row for an instant, so two processes never spend the same token.
- **One clock for everyone.** `clock_timestamp()` is the database's clock at that moment. `NOW()` would be wrong, because it is frozen at the start of a transaction.

**Shared backing off.** A `Retry-After`, or a backoff after an error, sets `blocked_until = GREATEST(blocked_until, clock_timestamp() + delay)`. Every process then holds off, not only the one that was told.

**Settings.** A bucket can't exceed the provider's limit in any window if `capacity + refill_per_second × window ≤ limit`:

| Bucket | Capacity | Refill | Most in any window | Provider limit |
| --- | --- | --- | --- | --- |
| `sec` | 5 | 5 a second | 10 in any second | 10 a second |
| `massive` | 1 | 1 every 12.5 s | 5 in any minute | 5 a minute |

The averages are what we use today, now enforced across every process. They are stored in the table, so a plan change is a migration, not a code change.

**Its own connection.** Each process takes tokens on its own autocommit connection, opened on first use, never on a job's connection. A long job's open transaction must never hold the bucket's row. If the database can't be reached, the request fails like any other database failure, and the run ends `failed` with the error.

**Same interface.** The shared bucket keeps the `Pacer` interface: `wait()` to take a token, `hold_off(seconds)` to back off. So `send_with_retries`, the SEC and Massive clients and their tests change only in which limiter is passed in. The Slack pacer stays per process, because only the alert worker sends.

**Fairness.** The alert worker and the background worker compete for SEC tokens on equal terms. At 5 a second, one request waits at most about 0.2 seconds for a token. A poll makes about 10 requests, so it gains at most about 2 seconds, well inside the 10-minute target. Nothing is reserved for alerts. If that ever proved too little, the alert worker could be given first claim without changing the design.

### What this frees up

With alerts on their own worker, background tasks no longer need to stay short:

- **Market-wide bars:** more days per run, held to 5 a minute by the shared bucket.
- **Market-wide 8-Ks:** more companies per run.

The new sizes are set in the last PR, after the bucket is live.

## Alternatives considered

- **A fixed share of SEC's limit per worker** (for example 2 a second for alerts, 6 for the rest). Simpler, with no database round trip, but every new worker means re-dividing the shares by hand, and a quiet worker's share can't be used by a busy one.
- **Redis** for the shared bucket. Built for this, but another service to run, monitor and fit into memory on a 2 GiB instance, for about 10 updates a second that Postgres does in a millisecond each. The prototype's Redis was left behind on purpose (CLAUDE.md, carry-over table).
- **One worker per provider** (an SEC worker, a Massive worker). Cleaner for limits, but alerts would still share a worker with the SEC backfills (insider datasets, 8-Ks), and onboarding calls both providers.
- **Alerts first in the queue.** A worker can't interrupt a run in progress, so a long backfill would still delay the poll.

## Consequences

- **One database round trip before every SEC and Massive request**, about 1 ms on the same host, against 100–300 ms for the request itself.
- **One more container**, about 60 MB idle; the instance has about 1 GB free.
- **The API's SEC calls are counted with everyone else's**, closing today's gap.
- **Retry-After is respected by every process**, not just the one that received it.
- **`rate_buckets.updated_at` shows when each provider was last called**, from any process.
- **Testing:**
  - the bucket's arithmetic: refill, cap, fractions, wait time;
  - two connections taking tokens at once: the total taken over a time span never exceeds `capacity + rate × span`;
  - `blocked_until` makes the other connection wait;
  - the alert worker claims only `ingest_sec_filings`;
  - a long run on `default` doesn't delay a poll on `alerts`.

## Rollout

Each step is one PR and leaves the app working:

1. **Shared buckets.** Migration for `rate_buckets` (with the settings above), the Postgres limiter behind the `Pacer` interface, and the SEC and Massive clients in every process switched to it. Behaviour stays at today's rates.
2. **Alert worker.** Migration adding the `alerts` queue and moving `ingest_sec_filings` to it; the `alert-worker` service in both Compose files; README, CLAUDE.md architecture and ADR 0003 and 0016 notes.
3. **Bigger background runs.** Raise the per-run sizes of market-wide bars and 8-Ks, now that nothing waits behind them.
