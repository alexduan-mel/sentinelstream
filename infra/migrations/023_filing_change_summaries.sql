-- ============================================================
-- 023_filing_change_summaries.sql
--
-- Short summaries of what changed in a 10-K section since last year,
-- written by Claude from the stored year-over-year changes (ADR 0024).
--
-- filing_change_summaries: one row per diff (10-K and item). status:
--   ok          points passed the checks (or nothing significant changed)
--   rejected    every point failed the checks: nothing is shown
--   refused     the model declined; nothing is shown
--   no_changes  the diff has no change outside figures and dates
-- rejections lists each rejected point with why. model, request_id and
-- token counts trace the call. known_at is when the summary was written,
-- so research as of an earlier date never sees it.
--
-- filing_change_summary_points: the accepted points, in order. change_nos
-- cite filing_paragraph_changes, which give the accession numbers, item
-- and paragraph numbers in both 10-Ks.
--
-- Both cascade from filing_section_diffs: a diff computed again is
-- summarized again.
--
-- summarize_10k_changes runs every hour on the research queue: it calls
-- the Claude API, never SEC, and can take a minute.
-- ============================================================

CREATE TABLE filing_change_summaries (
  new_accession_no TEXT NOT NULL,
  item             TEXT NOT NULL,
  status           TEXT NOT NULL,
  model            TEXT,
  prompt_version   INTEGER NOT NULL,
  changes_read     INTEGER NOT NULL,
  accepted_count   INTEGER NOT NULL,
  rejected_count   INTEGER NOT NULL,
  rejections       TEXT[] NOT NULL DEFAULT '{}',
  request_id       TEXT,
  input_tokens     INTEGER NOT NULL DEFAULT 0,
  output_tokens    INTEGER NOT NULL DEFAULT 0,
  known_at         TIMESTAMPTZ NOT NULL,
  task_run_id      BIGINT REFERENCES task_runs (id) ON DELETE SET NULL,
  PRIMARY KEY (new_accession_no, item),
  FOREIGN KEY (new_accession_no, item) REFERENCES filing_section_diffs (new_accession_no, item) ON DELETE CASCADE,
  CONSTRAINT ck_filing_change_summaries_status CHECK (status IN ('ok', 'rejected', 'refused', 'no_changes')),
  CONSTRAINT ck_filing_change_summaries_shown CHECK (status = 'ok' OR accepted_count = 0)
);

CREATE TABLE filing_change_summary_points (
  new_accession_no TEXT NOT NULL,
  item             TEXT NOT NULL,
  point_no         INTEGER NOT NULL,
  text             TEXT NOT NULL,
  change_nos       INTEGER[] NOT NULL,
  PRIMARY KEY (new_accession_no, item, point_no),
  FOREIGN KEY (new_accession_no, item)
    REFERENCES filing_change_summaries (new_accession_no, item) ON DELETE CASCADE,
  CONSTRAINT ck_filing_change_summary_points_cited CHECK (cardinality(change_nos) >= 1),
  CONSTRAINT ck_filing_change_summary_points_text CHECK (text <> '')
);

INSERT INTO task_definitions
  (task_id, label, job_type, worker_name, purpose, frequency, endpoint,
   trigger_mode, schedule_type, interval_minutes, scheduled_time, schedule_timezone, schedule_days, next_run_at,
   queue)
VALUES
  ('summarize_10k_changes', 'Summarize 10-K changes', 'summarize_10k_changes', 'tenk_summary_worker',
   'Have Claude write a few cited points on what changed in each watchlist 10-K''s risk factors and MD&A; points without a valid citation are rejected.',
   'Every hour', '/api/tasks/summarize_10k_changes/run',
   'automatic', 'interval', 60, NULL, 'UTC', 'all', NOW(), 'research');

INSERT INTO schema_migrations (migration_name, sequence_number)
VALUES ('023_filing_change_summaries.sql', 23);
