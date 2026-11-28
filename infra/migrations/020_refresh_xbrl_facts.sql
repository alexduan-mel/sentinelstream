-- ============================================================
-- 020_refresh_xbrl_facts.sql
--
-- Keep xbrl_facts current after onboarding (ADR 0021). Every hour,
-- refresh_xbrl_facts looks for periodic reports (10-Q, 10-K, 20-F and
-- the like, amendments included) that the SEC poller recorded for a
-- ready watchlist issuer in the last 14 days and that no stored fact
-- comes from yet, and loads them from SEC's companyfacts record: one
-- request per issuer with such a filing. It calls SEC, so it runs on
-- the default queue.
--
-- The index answers "does any fact come from this filing?" without
-- reading every fact the issuer has.
-- ============================================================

CREATE INDEX idx_xbrl_facts_accession ON xbrl_facts (accession_no);

INSERT INTO task_definitions
  (task_id, label, job_type, worker_name, purpose, frequency, endpoint,
   trigger_mode, schedule_type, interval_minutes, scheduled_time, schedule_timezone, schedule_days, next_run_at)
VALUES
  ('refresh_xbrl_facts', 'Refresh XBRL facts', 'refresh_xbrl_facts', 'xbrl_refresh_worker',
   'Load the XBRL facts of each new 10-Q, 10-K or 20-F a watchlist company files, so key financials stay current without re-onboarding.',
   'Every hour', '/api/tasks/refresh_xbrl_facts/run',
   'automatic', 'interval', 60, NULL, 'UTC', 'all', NOW());

INSERT INTO schema_migrations (migration_name, sequence_number)
VALUES ('020_refresh_xbrl_facts.sql', 20);
