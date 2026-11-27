-- ============================================================
-- 016_compute_base_rates.sql
--
-- The base-rate calculator as a task (ADR 0015), on the research queue
-- (ADR 0016): a run reads two years of market-wide bars for every
-- event's stock and takes minutes.
--
-- It is manual: it runs when asked, and its report is stored in the
-- run's meta. Its schedule columns are placeholders the table requires;
-- the Tasks page shows it as "On demand".
-- ============================================================

INSERT INTO task_definitions
  (task_id, label, job_type, worker_name, purpose, frequency, endpoint,
   trigger_mode, schedule_type, interval_minutes, scheduled_time, schedule_timezone, schedule_days,
   default_payload, queue)
VALUES
  ('compute_base_rates', 'Compute base rates', 'compute_base_rates', 'base_rates_worker',
   'What stocks did 1, 5, 20 and 60 sessions after past events, compared with QQQ, after trading costs.',
   'On demand', '/api/tasks/compute_base_rates/run',
   'manual', 'interval', 1440, NULL, 'UTC', 'all',
   '{"event": "insider_purchases"}'::jsonb, 'research');

INSERT INTO schema_migrations (migration_name, sequence_number)
VALUES ('016_compute_base_rates.sql', 16);
