-- ============================================================
-- 003_sec_poller_every_5_min.sql
--
-- Runs the SEC poller automatically every 5 minutes (M1).
--
-- 001 seeded ingest_sec_filings as manual, every 10 minutes. M1's target is
-- a watchlist 8-K reaching Slack within 10 minutes of EDGAR acceptance, and a
-- filing accepted just after a 10-minute poll would wait almost the whole
-- budget before it was even seen. Five minutes is the PRD's interval and
-- costs about one submissions request per issuer per poll, far inside SEC's
-- fair-access limit.
--
-- next_run_at is cleared so the scheduler initialises it on its next tick
-- from the new interval rather than from any stale value.
-- ============================================================

UPDATE task_definitions
SET trigger_mode     = 'automatic',
    interval_minutes = 5,
    frequency        = 'Every 5 minutes',
    next_run_at      = NULL,
    updated_at       = NOW()
WHERE task_id = 'ingest_sec_filings';

INSERT INTO schema_migrations (migration_name, sequence_number)
VALUES ('003_sec_poller_every_5_min.sql', 3);
