-- ============================================================
-- 022_filing_section_diffs.sql
--
-- Year-over-year changes in 10-K risk factors (Item 1A) and MD&A
-- (Item 7), paragraph by paragraph (ADR 0023).
--
-- filing_section_diffs: one row per 10-K and item, compared with the
-- same item of the issuer's previous 10-K (accepted at most 450 days
-- earlier, so a missing year is never skipped over). Counts say how
-- many paragraphs were unchanged, moved, changed (of which only numbers
-- or months), added and removed. extractor_version is the paragraph
-- numbering compared; comparer_version the comparison that ran.
--
-- filing_paragraph_changes: each added, removed or changed paragraph,
-- in this year's reading order (change_no). A change cites
-- (old_accession_no, item, old_paragraph_no) and
-- (new_accession_no, item, new_paragraph_no) in filing_paragraphs.
--
-- known_at is the later 10-K's acceptance time (ground rule 2).
-- Re-extracting either 10-K deletes its sections, and with them these
-- rows, so the next run compares the new paragraphs.
--
-- diff_10k_sections runs every hour on the research queue: it reads
-- only this database.
-- ============================================================

CREATE TABLE filing_section_diffs (
  new_accession_no   TEXT NOT NULL,
  item               TEXT NOT NULL,
  old_accession_no   TEXT NOT NULL,
  cik                TEXT NOT NULL,
  extractor_version  INTEGER NOT NULL,
  comparer_version   INTEGER NOT NULL,
  unchanged_count    INTEGER NOT NULL,
  moved_count        INTEGER NOT NULL,
  changed_count      INTEGER NOT NULL,
  numbers_only_count INTEGER NOT NULL,
  added_count        INTEGER NOT NULL,
  removed_count      INTEGER NOT NULL,
  known_at           TIMESTAMPTZ NOT NULL,
  computed_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  task_run_id        BIGINT REFERENCES task_runs (id) ON DELETE SET NULL,
  PRIMARY KEY (new_accession_no, item),
  FOREIGN KEY (new_accession_no, item) REFERENCES filing_sections (accession_no, item) ON DELETE CASCADE,
  FOREIGN KEY (old_accession_no, item) REFERENCES filing_sections (accession_no, item) ON DELETE CASCADE,
  CONSTRAINT ck_filing_section_diffs_pair CHECK (old_accession_no <> new_accession_no),
  CONSTRAINT ck_filing_section_diffs_cik CHECK (cik ~ '^[0-9]{10}$'),
  CONSTRAINT ck_filing_section_diffs_numbers CHECK (numbers_only_count <= changed_count)
);

CREATE INDEX idx_filing_section_diffs_cik ON filing_section_diffs (cik, known_at DESC);

CREATE TABLE filing_paragraph_changes (
  new_accession_no TEXT NOT NULL,
  item             TEXT NOT NULL,
  change_no        INTEGER NOT NULL,
  kind             TEXT NOT NULL,
  old_paragraph_no INTEGER,
  new_paragraph_no INTEGER,
  similarity       NUMERIC(4, 3),
  numbers_only     BOOLEAN NOT NULL DEFAULT FALSE,
  known_at         TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (new_accession_no, item, change_no),
  FOREIGN KEY (new_accession_no, item) REFERENCES filing_section_diffs (new_accession_no, item) ON DELETE CASCADE,
  CONSTRAINT ck_filing_paragraph_changes_kind CHECK (
    (kind = 'added' AND old_paragraph_no IS NULL AND new_paragraph_no IS NOT NULL AND similarity IS NULL)
    OR (kind = 'removed' AND old_paragraph_no IS NOT NULL AND new_paragraph_no IS NULL AND similarity IS NULL)
    OR (kind = 'changed' AND old_paragraph_no IS NOT NULL AND new_paragraph_no IS NOT NULL AND similarity IS NOT NULL)
  ),
  CONSTRAINT ck_filing_paragraph_changes_numbers CHECK (kind = 'changed' OR NOT numbers_only)
);

INSERT INTO task_definitions
  (task_id, label, job_type, worker_name, purpose, frequency, endpoint,
   trigger_mode, schedule_type, interval_minutes, scheduled_time, schedule_timezone, schedule_days, next_run_at,
   queue)
VALUES
  ('diff_10k_sections', 'Compare 10-K sections', 'diff_10k_sections', 'tenk_diff_worker',
   'Find the paragraphs added, removed and changed in each watchlist 10-K''s risk factors and MD&A since the year before.',
   'Every hour', '/api/tasks/diff_10k_sections/run',
   'automatic', 'interval', 60, NULL, 'UTC', 'all', NOW(), 'research');

INSERT INTO schema_migrations (migration_name, sequence_number)
VALUES ('022_filing_section_diffs.sql', 22);
