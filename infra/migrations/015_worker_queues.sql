-- ============================================================
-- 015_worker_queues.sql
--
-- Worker queues (ADR 0016). task_definitions.queue says which worker
-- runs a task: 'default' (ingestion, alerts, onboarding) or 'research'
-- (long calculations that read only our own database). Each worker
-- claims only its queue's runs, so research never holds up the 5-minute
-- SEC poll. Every existing task stays on 'default'.
-- ============================================================

ALTER TABLE task_definitions
  ADD COLUMN queue TEXT NOT NULL DEFAULT 'default',
  ADD CONSTRAINT ck_task_definitions_queue CHECK (queue IN ('default', 'research'));

INSERT INTO schema_migrations (migration_name, sequence_number)
VALUES ('015_worker_queues.sql', 15);
