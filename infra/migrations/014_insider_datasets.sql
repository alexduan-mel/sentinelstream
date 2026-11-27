-- ============================================================
-- 014_insider_datasets.sql
--
-- Market-wide insider transactions from SEC's Insider Transactions Data
-- Sets (ADR 0014): one ZIP per calendar quarter of every Form 3, 4 and 5
-- filed in it, published about six weeks after the quarter ends.
--
-- insider_filings: one row per filing. issuer_symbol is the trading
-- symbol exactly as the filer typed it ("NYSE: KRC", "(SIRI)",
-- "HEI, HEI.A" all occur); research cleans it, or falls back to the CIK.
-- aff10b5one is the filing's 10b5-1 box: the trade was pre-planned.
--
-- known_at is midnight New York after the filing date. The datasets
-- carry no acceptance time; a filing accepted after 17:30 gets the next
-- business day's date, so acceptance is before the end of the filing
-- date and this is never early.
--
-- insider_transactions: the filing's non-derivative transactions, keyed
-- by the dataset's own row key. Every form and transaction code is kept;
-- which count (Form 4 open-market purchases, code P) is decided by the
-- calculator.
--
-- insider_datasets records each quarter loaded. A quarter is replaced
-- whole in one transaction; delete its row to load it again.
--
-- ingest_insider_datasets runs straight after this deploy, then hourly.
-- It loads at most 2 quarters a run (about 40 seconds), so the two-year
-- backfill takes four runs; once current, a run makes no request until
-- a quarter ends, and one request an hour until SEC publishes it.
-- ============================================================

CREATE TABLE insider_datasets (
  quarter           TEXT PRIMARY KEY,
  url               TEXT NOT NULL,
  last_modified     TEXT,
  sha256            TEXT NOT NULL,
  bytes             BIGINT NOT NULL,
  filing_count      INTEGER NOT NULL,
  transaction_count INTEGER NOT NULL,
  invalid_count     INTEGER NOT NULL,
  fetched_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  task_run_id       BIGINT REFERENCES task_runs (id) ON DELETE SET NULL,
  CONSTRAINT ck_insider_datasets_quarter CHECK (quarter ~ '^[0-9]{4}q[1-4]$'),
  CONSTRAINT ck_insider_datasets_sha256 CHECK (sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE insider_filings (
  accession_no        TEXT PRIMARY KEY,
  document_type       TEXT NOT NULL,
  filing_date         DATE NOT NULL,
  period_of_report    DATE,
  issuer_cik          TEXT NOT NULL,
  issuer_name         TEXT,
  issuer_symbol       TEXT,
  owner_names         TEXT,
  owner_ciks          TEXT[] NOT NULL DEFAULT '{}',
  owner_relationships TEXT,
  owner_titles        TEXT,
  aff10b5one          BOOLEAN,
  dataset             TEXT NOT NULL REFERENCES insider_datasets (quarter) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
  known_at            TIMESTAMPTZ NOT NULL
    GENERATED ALWAYS AS (((filing_date + 1) + TIME '00:00') AT TIME ZONE 'America/New_York') STORED,
  CONSTRAINT ck_insider_filings_accession CHECK (accession_no ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'),
  CONSTRAINT ck_insider_filings_issuer_cik CHECK (issuer_cik ~ '^[0-9]{10}$')
);

CREATE INDEX idx_insider_filings_dataset ON insider_filings (dataset);
CREATE INDEX idx_insider_filings_issuer_known ON insider_filings (issuer_cik, known_at);
CREATE INDEX idx_insider_filings_known ON insider_filings (known_at);

CREATE TABLE insider_transactions (
  accession_no       TEXT NOT NULL REFERENCES insider_filings (accession_no) ON DELETE CASCADE,
  line_sk            BIGINT NOT NULL,
  security_title     TEXT,
  transaction_date   DATE,
  transaction_code   TEXT NOT NULL,
  acquired_disposed  TEXT,
  shares             DOUBLE PRECISION,
  price_per_share    DOUBLE PRECISION,
  shares_owned_after DOUBLE PRECISION,
  direct_indirect    TEXT,
  equity_swap        BOOLEAN,
  PRIMARY KEY (accession_no, line_sk),
  CONSTRAINT ck_insider_transactions_acquired_disposed
    CHECK (acquired_disposed IS NULL OR acquired_disposed IN ('A', 'D')),
  CONSTRAINT ck_insider_transactions_direct_indirect
    CHECK (direct_indirect IS NULL OR direct_indirect IN ('D', 'I'))
);

CREATE INDEX idx_insider_transactions_code ON insider_transactions (transaction_code);

INSERT INTO task_definitions
  (task_id, label, job_type, worker_name, purpose, frequency, endpoint,
   trigger_mode, schedule_type, interval_minutes, scheduled_time, schedule_timezone, schedule_days, next_run_at)
VALUES
  ('ingest_insider_datasets', 'Ingest insider transactions', 'ingest_insider_datasets', 'insider_datasets_worker',
   'Load every Form 3, 4 and 5 transaction from SEC''s quarterly Insider Transactions Data Sets, two years back.',
   'Every hour', '/api/tasks/ingest_insider_datasets/run',
   'automatic', 'interval', 60, NULL, 'UTC', 'all', NOW());

INSERT INTO schema_migrations (migration_name, sequence_number)
VALUES ('014_insider_datasets.sql', 14);
