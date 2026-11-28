-- ============================================================
-- 019_prices_after_day_ends.sql
--
-- ingest_prices moves from weekdays at 18:30 to every day at 05:00
-- New York time.
--
-- Massive's plan serves a day's bars only once that day has ended in
-- New York. Asked at 18:30 the same evening, it returned no bar, and the
-- run failed (first seen on 27 Nov 2026, the first trading evening the
-- schedule met). The job now aims at the last trading day that has
-- ended, and runs after midnight: at 05:00 it picks up the previous
-- day, an hour before compute_base_rates. It runs every day so that
-- Friday's bars arrive on Saturday; a run with nothing new does nothing.
-- ingest_splits stays at 17:00, still before the price run it can
-- trigger a re-fetch for.
-- ============================================================

UPDATE task_definitions
SET scheduled_time    = '05:00',
    schedule_timezone = 'America/New_York',
    schedule_days     = 'all',
    frequency         = 'Daily, after the previous trading day ends',
    next_run_at       = NULL,
    updated_at        = NOW()
WHERE task_id = 'ingest_prices';

INSERT INTO schema_migrations (migration_name, sequence_number)
VALUES ('019_prices_after_day_ends.sql', 19);
