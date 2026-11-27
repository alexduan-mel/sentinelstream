-- ============================================================
-- 017_market_8k.sql
--
-- Market-wide 8-K item history (ADR 0017): every 8-K and 8-K/A filed in
-- the last two years, with its items and EDGAR acceptance time, so base
-- rates can cover 8-K events at every company, not only the watchlist.
--
-- Two sources, because neither has everything:
--   EDGAR's quarterly full index (form.idx) lists every 8-K with its
--   filer's CIK and date, but not its items;
--   each filer's submissions record gives items and acceptance times.
--
-- edgar_index_quarters records each quarter's index read. A quarter is
-- complete once read more than 3 days after it ended; until then it is
-- read again daily.
--
-- market_issuers: every company the index shows filing an 8-K, with the
-- date of its latest one, and what its submissions record said when last
-- read (name, current tickers and exchanges, industry code). covered_through
-- is the day before that read: every filing dated up to then had been
-- accepted before it. A company is read again once the index shows an 8-K
-- dated after covered_through and before today. tickers are as of the
-- read, not as of each filing.
--
-- market_8k_filings: one row per 8-K, known_at = EDGAR acceptance
-- (ADR 0006). Kept apart from filings and filing_issuers, which drive
-- watchlist alerts: nothing here can reach Slack.
--
-- ingest_market_8k runs every 10 minutes on the default queue (it calls
-- SEC): at most one index file and 150 submissions records a run, about
-- 40 seconds at SEC's pace.
-- ============================================================

CREATE TABLE edgar_index_quarters (
  quarter     TEXT PRIMARY KEY,
  eight_k_count INTEGER NOT NULL,
  complete    BOOLEAN NOT NULL,
  fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  task_run_id BIGINT REFERENCES task_runs (id) ON DELETE SET NULL,
  CONSTRAINT ck_edgar_index_quarters_quarter CHECK (quarter ~ '^[0-9]{4}q[1-4]$')
);

CREATE TABLE market_issuers (
  cik                    TEXT PRIMARY KEY,
  name                   TEXT,
  latest_8k_date         DATE NOT NULL,
  tickers                TEXT[] NOT NULL DEFAULT '{}',
  exchanges              TEXT[] NOT NULL DEFAULT '{}',
  sic                    TEXT,
  sic_description        TEXT,
  entity_type            TEXT,
  covered_through        DATE,
  submissions_fetched_at TIMESTAMPTZ,
  fetch_error            TEXT,
  task_run_id            BIGINT REFERENCES task_runs (id) ON DELETE SET NULL,
  CONSTRAINT ck_market_issuers_cik CHECK (cik ~ '^[0-9]{10}$')
);

CREATE INDEX idx_market_issuers_due ON market_issuers (covered_through NULLS FIRST, cik);

CREATE TABLE market_8k_filings (
  accession_no TEXT PRIMARY KEY,
  cik          TEXT NOT NULL REFERENCES market_issuers (cik),
  form_type    TEXT NOT NULL,
  items        TEXT[] NOT NULL DEFAULT '{}',
  filed_date   DATE NOT NULL,
  accepted_at  TIMESTAMPTZ NOT NULL,
  known_at     TIMESTAMPTZ NOT NULL,
  task_run_id  BIGINT REFERENCES task_runs (id) ON DELETE SET NULL,
  CONSTRAINT ck_market_8k_filings_accession CHECK (accession_no ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'),
  CONSTRAINT ck_market_8k_filings_form CHECK (form_type IN ('8-K', '8-K/A')),
  CONSTRAINT ck_market_8k_filings_known CHECK (known_at >= accepted_at)
);

CREATE INDEX idx_market_8k_filings_known ON market_8k_filings (known_at);
CREATE INDEX idx_market_8k_filings_items ON market_8k_filings USING GIN (items);

INSERT INTO task_definitions
  (task_id, label, job_type, worker_name, purpose, frequency, endpoint,
   trigger_mode, schedule_type, interval_minutes, scheduled_time, schedule_timezone, schedule_days, next_run_at)
VALUES
  ('ingest_market_8k', 'Ingest market-wide 8-Ks', 'ingest_market_8k', 'market_8k_worker',
   'Load every 8-K filed in the last two years, with its items and acceptance time, from EDGAR''s index and each filer''s submissions.',
   'Every 10 minutes', '/api/tasks/ingest_market_8k/run',
   'automatic', 'interval', 10, NULL, 'UTC', 'all', NOW());

INSERT INTO schema_migrations (migration_name, sequence_number)
VALUES ('017_market_8k.sql', 17);
