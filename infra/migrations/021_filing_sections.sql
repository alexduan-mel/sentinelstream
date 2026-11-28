-- ============================================================
-- 021_filing_sections.sql
--
-- Risk factors (Item 1A) and MD&A (Item 7) from watchlist 10-Ks, as
-- numbered paragraphs (ADR 0022), so a year-over-year diff can cite
-- accession number, item and paragraph.
--
-- filing_sections: one row per 10-K and item, found or not. heading is
-- the section's heading as printed; anchor is the element id the 10-K's
-- own table of contents links to, so a link can open the document at
-- the section. source_document and source_sha256 identify the exact
-- file read from EDGAR (the filing as accepted). extractor_version says
-- which parser produced the rows: raising it re-extracts every 10-K.
--
-- filing_paragraphs: the section's paragraphs in reading order, numbered
-- from 1. Figure tables are left out (tables_skipped counts their rows).
--
-- known_at on both is the filing's EDGAR acceptance time (ground rule 2).
--
-- extract_10k_sections runs every hour on the default queue (it calls
-- SEC) and reads the two most recent 10-Ks of each ready watchlist
-- issuer that are not extracted with the current version yet.
-- ============================================================

CREATE TABLE filing_sections (
  accession_no      TEXT NOT NULL REFERENCES filings (accession_no) ON DELETE CASCADE,
  item              TEXT NOT NULL,
  found             BOOLEAN NOT NULL,
  heading           TEXT,
  anchor            TEXT,
  paragraph_count   INTEGER NOT NULL DEFAULT 0,
  tables_skipped    INTEGER NOT NULL DEFAULT 0,
  source_document   TEXT NOT NULL,
  source_sha256     TEXT NOT NULL,
  extractor_version INTEGER NOT NULL,
  known_at          TIMESTAMPTZ NOT NULL,
  extracted_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  task_run_id       BIGINT REFERENCES task_runs (id) ON DELETE SET NULL,
  PRIMARY KEY (accession_no, item),
  CONSTRAINT ck_filing_sections_item CHECK (item IN ('1A', '7')),
  CONSTRAINT ck_filing_sections_sha256 CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT ck_filing_sections_found CHECK (found OR (paragraph_count = 0 AND heading IS NULL))
);

CREATE TABLE filing_paragraphs (
  accession_no TEXT NOT NULL,
  item         TEXT NOT NULL,
  paragraph_no INTEGER NOT NULL,
  text         TEXT NOT NULL,
  known_at     TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (accession_no, item, paragraph_no),
  FOREIGN KEY (accession_no, item) REFERENCES filing_sections (accession_no, item) ON DELETE CASCADE,
  CONSTRAINT ck_filing_paragraphs_number CHECK (paragraph_no >= 1),
  CONSTRAINT ck_filing_paragraphs_text CHECK (text <> '')
);

INSERT INTO task_definitions
  (task_id, label, job_type, worker_name, purpose, frequency, endpoint,
   trigger_mode, schedule_type, interval_minutes, scheduled_time, schedule_timezone, schedule_days, next_run_at)
VALUES
  ('extract_10k_sections', 'Extract 10-K sections', 'extract_10k_sections', 'tenk_sections_worker',
   'Read risk factors (Item 1A) and MD&A (Item 7) from the two latest 10-Ks of each watchlist company, as numbered paragraphs.',
   'Every hour', '/api/tasks/extract_10k_sections/run',
   'automatic', 'interval', 60, NULL, 'UTC', 'all', NOW());

INSERT INTO schema_migrations (migration_name, sequence_number)
VALUES ('021_filing_sections.sql', 21);
