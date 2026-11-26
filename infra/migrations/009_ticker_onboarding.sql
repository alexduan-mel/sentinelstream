-- ============================================================
-- 009_ticker_onboarding.sql
--
-- Ticker onboarding (ADR 0010): the onboard_tickers task, the status
-- each instrument moves through, and a table for XBRL company facts.
--
-- pending      added, not identified yet
-- backfilling  tied to its SEC issuer and in the poller's watch set;
--              filings, prices and XBRL facts are being loaded
-- ready        two years of history loaded
-- failed       SEC cannot tie the symbol to one company
--
-- The seeded watchlist equities were identified by the poller before
-- onboarding existed, so they start as backfilling: they stay watched,
-- and onboarding loads the two years of prices and XBRL facts they lack.
-- The task runs once straight after this deploy (next_run_at = NOW()),
-- then daily to pick up anything a skipped run left behind. Adding a
-- ticker in the UI queues a run at once.
-- ============================================================

INSERT INTO task_definitions
  (task_id, label, job_type, worker_name, purpose, frequency, endpoint,
   trigger_mode, schedule_type, interval_minutes, scheduled_time, schedule_timezone, schedule_days, next_run_at)
VALUES
  ('onboard_tickers', 'Onboard tickers', 'onboard_tickers', 'onboarding_worker',
   'Tie new tickers to their SEC issuer and load two years of filings, daily prices and XBRL facts.',
   'Daily, and when a ticker is added', '/api/tasks/onboard_tickers/run',
   'automatic', 'daily_time', NULL, '19:00', 'America/New_York', 'all', NOW());

UPDATE instruments
SET onboarding_status = 'backfilling', updated_at = NOW()
WHERE active AND asset_type = 'equity' AND role = 'watchlist'
  AND cik IS NOT NULL AND filer_type IS NOT NULL AND onboarding_status = 'pending';

-- The poller watches backfilling equities, so they need an identity too.
ALTER TABLE instruments
  ADD CONSTRAINT ck_instruments_backfilling_equity_identity
    CHECK (onboarding_status <> 'backfilling' OR asset_type <> 'equity'
           OR (cik IS NOT NULL AND filer_type IS NOT NULL));

-- XBRL facts as SEC's company-facts API reports them: one row per value an
-- issuer filed for a concept, unit and period. Numbers in Explain (M3) come
-- from here (ground rule 6). known_at is the filing's EDGAR acceptance time
-- when the filing is stored, otherwise the end of its filing date in New
-- York, which is never earlier than acceptance (ground rule 2).
CREATE TABLE xbrl_facts (
  cik           TEXT NOT NULL,
  taxonomy      TEXT NOT NULL,
  concept       TEXT NOT NULL,
  unit          TEXT NOT NULL,
  period_start  DATE,
  period_end    DATE NOT NULL,
  value         NUMERIC NOT NULL,
  accession_no  TEXT NOT NULL,
  fiscal_year   INTEGER,
  fiscal_period TEXT,
  form          TEXT,
  filed_date    DATE NOT NULL,
  frame         TEXT,
  known_at      TIMESTAMPTZ NOT NULL,
  task_run_id   BIGINT REFERENCES task_runs (id) ON DELETE SET NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT ck_xbrl_facts_cik CHECK (cik ~ '^[0-9]{10}$'),
  CONSTRAINT ck_xbrl_facts_accession CHECK (accession_no ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'),
  CONSTRAINT ck_xbrl_facts_period CHECK (period_start IS NULL OR period_start <= period_end)
);

-- One row per filed value; a rerun or an overlapping pull adds nothing.
CREATE UNIQUE INDEX uq_xbrl_facts_value
  ON xbrl_facts (cik, taxonomy, concept, unit, accession_no, period_end, (COALESCE(period_start, DATE '0001-01-01')));
CREATE INDEX idx_xbrl_facts_concept_known ON xbrl_facts (cik, concept, known_at DESC);

INSERT INTO schema_migrations (migration_name, sequence_number)
VALUES ('009_ticker_onboarding.sql', 9);
