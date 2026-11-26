-- ============================================================
-- 005_price_bars_automatic.sql
--
-- Runs ingest_prices on its own schedule, weekdays at 18:30 New York time.
--
-- ADR 0002 seeded it as manual until the Massive key was in Parameter
-- Store; it is. Each run fetches every active instrument's bars since its
-- latest stored day, so a missed day is caught up by the next run. M2's
-- event studies need the daily history to have no gaps.
--
-- next_run_at is cleared so the scheduler sets the next 18:30 on its next
-- tick instead of running at once.
-- ============================================================

UPDATE task_definitions
SET trigger_mode = 'automatic',
    next_run_at  = NULL,
    updated_at   = NOW()
WHERE task_id = 'ingest_prices';

INSERT INTO schema_migrations (migration_name, sequence_number)
VALUES ('005_price_bars_automatic.sql', 5);
