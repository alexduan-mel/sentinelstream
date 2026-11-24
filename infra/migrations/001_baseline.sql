-- ============================================================
-- 001_baseline.sql
--
-- Baseline schema for the showcase repo. It replaces the prototype's 35
-- migrations, keeping only the tables M0 and M1 use (see
-- docs/decisions/0002-baseline-data-model.md). Tables for news, sentiment,
-- outcome evaluation and Company Context arrive later as forward migrations,
-- when their code is carried over.
--
-- Conventions:
--   * Facts reference instruments by instrument_id, never by symbol text,
--     and never with ON DELETE CASCADE.
--   * Every fact table carries known_at (when the fact became public).
--     Research code reads facts only where known_at < as_of.
--   * Every ingested row records the task_run_id that wrote it.
--   * Each migration records itself in schema_migrations. The runner
--     (orchestration/migrations.py) runs each file in one transaction that
--     it owns and commits only if that row is there, so files contain no
--     BEGIN or COMMIT. To apply by hand: psql --single-transaction -f FILE.
-- ============================================================

CREATE SCHEMA IF NOT EXISTS sentinel_stream;
SET search_path TO sentinel_stream, public;

-- ------------------------------------------------------------
-- Migration ledger
-- ------------------------------------------------------------
CREATE TABLE schema_migrations (
  migration_name  TEXT PRIMARY KEY,
  sequence_number INTEGER NOT NULL UNIQUE,
  is_baseline     BOOLEAN NOT NULL DEFAULT FALSE,
  applied_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  applied_by      TEXT NOT NULL DEFAULT CURRENT_USER,
  CONSTRAINT ck_schema_migrations_name
    CHECK (migration_name ~ '^[0-9]{3}_[a-z0-9_]+[.]sql$'),
  CONSTRAINT ck_schema_migrations_sequence
    CHECK (sequence_number > 0)
);

COMMENT ON TABLE schema_migrations IS
  'Forward-only migration ledger. 001_baseline.sql collapses the prototype history; every later file uses the next unique sequence number and records itself.';

-- ------------------------------------------------------------
-- Users (login only; sessions are HMAC-signed cookies, so no session table)
-- ------------------------------------------------------------
CREATE TABLE app_users (
  id            BIGSERIAL PRIMARY KEY,
  username      TEXT NOT NULL,
  password_hash TEXT,
  role          TEXT NOT NULL DEFAULT 'user',
  active        BOOLEAN NOT NULL DEFAULT TRUE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT ck_app_users_username
    CHECK (username = LOWER(username) AND username ~ '^[a-z0-9][a-z0-9._-]{2,63}$'),
  CONSTRAINT ck_app_users_role CHECK (role IN ('admin', 'user'))
);

CREATE UNIQUE INDEX uq_app_users_username_lower ON app_users (LOWER(username));

-- The password is never stored in SQL. The first successful admin login
-- installs a hash from AUTH_ADMIN_PASSWORD (SSM Parameter Store on EC2).
INSERT INTO app_users (id, username, password_hash, role, active)
VALUES (1, 'admin', NULL, 'admin', TRUE);

SELECT setval(pg_get_serial_sequence('app_users', 'id'), 1, TRUE);

COMMENT ON TABLE app_users IS
  'Login-only accounts. The bootstrap admin row has no hash until first login.';

-- ------------------------------------------------------------
-- Instruments (replaces the prototype's symbol-keyed tickers table)
-- ------------------------------------------------------------
CREATE TABLE instruments (
  id                BIGSERIAL PRIMARY KEY,
  symbol            TEXT NOT NULL UNIQUE,
  name              TEXT,
  exchange          TEXT,
  asset_type        TEXT NOT NULL,
  role              TEXT NOT NULL DEFAULT 'watchlist',
  cik               TEXT,
  filer_type        TEXT,
  onboarding_status TEXT NOT NULL DEFAULT 'pending',
  onboarding_error  TEXT,
  active            BOOLEAN NOT NULL DEFAULT TRUE,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT ck_instruments_symbol
    CHECK (symbol = UPPER(symbol) AND symbol ~ '^[A-Z][A-Z0-9.-]{0,9}$'),
  CONSTRAINT ck_instruments_asset_type CHECK (asset_type IN ('equity', 'etf')),
  CONSTRAINT ck_instruments_role CHECK (role IN ('watchlist', 'benchmark')),
  CONSTRAINT ck_instruments_cik CHECK (cik IS NULL OR cik ~ '^[0-9]{10}$'),
  CONSTRAINT ck_instruments_filer_type
    CHECK (filer_type IS NULL OR filer_type IN ('domestic', 'foreign_private')),
  CONSTRAINT ck_instruments_onboarding_status
    CHECK (onboarding_status IN ('pending', 'backfilling', 'ready', 'failed')),
  -- An equity is only ready once onboarding has resolved its SEC identity.
  CONSTRAINT ck_instruments_ready_equity_identity
    CHECK (onboarding_status <> 'ready' OR asset_type <> 'equity'
           OR (cik IS NOT NULL AND filer_type IS NOT NULL))
);

-- CIK is not unique: share classes of one issuer (GOOGL, GOOG) share a CIK.
CREATE INDEX idx_instruments_cik ON instruments (cik) WHERE cik IS NOT NULL;

COMMENT ON TABLE instruments IS
  'Instrument registry. Facts reference instruments.id. CIK and filer type are filled by the onboarding job, not by hand.';

-- Seed: identities (name, CIK, filer type) are left to the
-- onboarding job, which resolves them from SEC data.
INSERT INTO instruments (symbol, asset_type, role) VALUES
  ('NVDA',  'equity', 'watchlist'),
  ('SPCX',  'equity', 'watchlist'),
  ('ANT',   'equity', 'watchlist'),
  ('GOOGL', 'equity', 'watchlist'),
  ('AMZN',  'equity', 'watchlist'),
  ('NBIS',  'equity', 'watchlist'),
  ('QQQ',   'etf',    'benchmark'),
  ('SPY',   'etf',    'benchmark');

-- ------------------------------------------------------------
-- Orchestration
-- ------------------------------------------------------------
CREATE TABLE task_definitions (
  task_id           TEXT PRIMARY KEY,
  label             TEXT NOT NULL,
  job_type          TEXT NOT NULL UNIQUE,
  worker_name       TEXT NOT NULL UNIQUE,
  purpose           TEXT NOT NULL,
  frequency         TEXT NOT NULL,
  endpoint          TEXT NOT NULL,
  default_payload   JSONB NOT NULL DEFAULT '{}'::jsonb,
  enabled           BOOLEAN NOT NULL DEFAULT TRUE,
  trigger_mode      TEXT NOT NULL DEFAULT 'manual',
  schedule_type     TEXT NOT NULL DEFAULT 'interval',
  interval_minutes  INTEGER,
  scheduled_time    TIME,
  schedule_timezone TEXT NOT NULL DEFAULT 'UTC',
  schedule_days     TEXT NOT NULL DEFAULT 'all',
  next_run_at       TIMESTAMPTZ,
  last_scheduled_at TIMESTAMPTZ,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT ck_task_definitions_trigger_mode CHECK (trigger_mode IN ('manual', 'automatic')),
  CONSTRAINT ck_task_definitions_schedule_type CHECK (schedule_type IN ('interval', 'daily_time')),
  CONSTRAINT ck_task_definitions_schedule_days CHECK (schedule_days IN ('all', 'weekdays')),
  CONSTRAINT ck_task_definitions_interval_minutes
    CHECK (interval_minutes IS NULL OR interval_minutes > 0),
  CONSTRAINT ck_task_definitions_schedule_shape
    CHECK ((schedule_type = 'interval' AND interval_minutes IS NOT NULL)
        OR (schedule_type = 'daily_time' AND scheduled_time IS NOT NULL))
);

CREATE INDEX idx_task_definitions_scheduler_due
  ON task_definitions (enabled, trigger_mode, next_run_at)
  WHERE enabled = TRUE AND trigger_mode = 'automatic';

-- Both start manual so a fresh deploy does not call providers before its
-- credentials are in Parameter Store; switch to automatic once verified.
INSERT INTO task_definitions
  (task_id, label, job_type, worker_name, purpose, frequency, endpoint,
   trigger_mode, schedule_type, interval_minutes, scheduled_time, schedule_timezone, schedule_days)
VALUES
  ('ingest_prices', 'Ingest daily price bars', 'ingest_prices', 'price_bars_worker',
   'Fetch split-adjusted daily bars from Massive for active instruments.',
   'Weekdays after the US close', '/api/tasks/ingest_prices/run',
   'manual', 'daily_time', NULL, '18:30', 'America/New_York', 'weekdays'),
  ('ingest_sec_filings', 'Ingest SEC filings', 'ingest_sec_filings', 'sec_filings_worker',
   'Poll EDGAR submissions for watchlist issuers and record new filings.',
   'Every 10 minutes', '/api/tasks/ingest_sec_filings/run',
   'manual', 'interval', 10, NULL, 'UTC', 'all');

CREATE TABLE task_runs (
  id             BIGSERIAL PRIMARY KEY,
  task_id        TEXT NOT NULL REFERENCES task_definitions (task_id),
  worker_name    TEXT,
  trace_id       UUID NOT NULL UNIQUE,
  trigger_source TEXT NOT NULL DEFAULT 'direct_worker',
  run_mode       TEXT NOT NULL DEFAULT 'manual',
  status         TEXT NOT NULL DEFAULT 'pending',
  requested_by   TEXT,
  as_of_date     DATE,
  force_refresh  BOOLEAN NOT NULL DEFAULT FALSE,
  tickers        JSONB NOT NULL DEFAULT '[]'::jsonb,
  window_from    TIMESTAMPTZ,
  window_to      TIMESTAMPTZ,
  fetched_count  INTEGER NOT NULL DEFAULT 0,
  inserted_count INTEGER NOT NULL DEFAULT 0,
  deduped_count  INTEGER NOT NULL DEFAULT 0,
  error_message  TEXT,
  meta           JSONB NOT NULL DEFAULT '{}'::jsonb,
  started_at     TIMESTAMPTZ,
  completed_at   TIMESTAMPTZ,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT ck_task_runs_run_mode
    CHECK (run_mode IN ('manual', 'scheduled', 'backfill', 'replay')),
  -- The prototype's legacy 'done' status is gone.
  CONSTRAINT ck_task_runs_status
    CHECK (status IN ('pending', 'running', 'succeeded', 'partial', 'skipped', 'failed')),
  CONSTRAINT ck_task_runs_terminal_has_completed_at
    CHECK (status IN ('pending', 'running') OR completed_at IS NOT NULL)
);

CREATE INDEX idx_task_runs_status_created ON task_runs (status, created_at DESC);
CREATE INDEX idx_task_runs_task_created ON task_runs (task_id, created_at DESC);

-- ------------------------------------------------------------
-- Price bars
-- ------------------------------------------------------------
CREATE TABLE price_bars (
  id            BIGSERIAL PRIMARY KEY,
  instrument_id BIGINT NOT NULL REFERENCES instruments (id) ON DELETE RESTRICT,
  trading_date  DATE NOT NULL,
  open          NUMERIC(18, 6),
  high          NUMERIC(18, 6),
  low           NUMERIC(18, 6),
  close         NUMERIC(18, 6),
  volume        BIGINT,
  provider      TEXT NOT NULL,
  -- Bars are split-adjusted as Massive returns them (adjusted=true). After a
  -- new split the instrument's whole history is re-fetched and overwritten.
  -- A bar is public at the regular close. 16:00 New York time is used for
  -- every session, which is conservative on 13:00 half-days.
  known_at      TIMESTAMPTZ NOT NULL
    GENERATED ALWAYS AS ((trading_date + TIME '16:00') AT TIME ZONE 'America/New_York') STORED,
  fetched_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  data_quality  TEXT NOT NULL DEFAULT 'ok',
  raw_payload   JSONB NOT NULL DEFAULT '{}'::jsonb,
  task_run_id   BIGINT REFERENCES task_runs (id) ON DELETE SET NULL,
  CONSTRAINT uq_price_bars_instrument_date_provider UNIQUE (instrument_id, trading_date, provider),
  CONSTRAINT ck_price_bars_data_quality CHECK (data_quality IN ('ok', 'partial', 'suspect')),
  CONSTRAINT ck_price_bars_ohlc
    CHECK (high IS NULL OR low IS NULL OR high >= low)
);

CREATE INDEX idx_price_bars_instrument_date ON price_bars (instrument_id, trading_date DESC);

COMMENT ON TABLE price_bars IS
  'Split-adjusted daily bars, re-fetched in full after a split. Upsert key: instrument_id + trading_date + provider. A large move with no recorded split is marked suspect.';

-- ------------------------------------------------------------
-- SEC filings (new; the prototype never stored filings)
-- ------------------------------------------------------------
CREATE TABLE filings (
  id               BIGSERIAL PRIMARY KEY,
  accession_no     TEXT NOT NULL UNIQUE,
  -- The filing belongs to an issuer (CIK), not to one ticker. Join to
  -- instruments on cik, so a filing reaches every share class.
  cik              TEXT NOT NULL,
  form_type        TEXT NOT NULL,
  items            TEXT[] NOT NULL DEFAULT '{}',
  filed_date       DATE NOT NULL,
  report_date      DATE,
  primary_document TEXT,
  is_xbrl          BOOLEAN NOT NULL DEFAULT FALSE,
  -- accepted_at: EDGAR acceptance time. known_at: when the fact became
  -- public, set to accepted_at. first_seen_at: when our poller first saw it,
  -- kept apart so polling latency never leaks into research timing.
  accepted_at      TIMESTAMPTZ NOT NULL,
  known_at         TIMESTAMPTZ NOT NULL,
  first_seen_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  is_backfill      BOOLEAN NOT NULL DEFAULT FALSE,
  raw_s3_key       TEXT,
  raw_sha256       TEXT,
  task_run_id      BIGINT REFERENCES task_runs (id) ON DELETE SET NULL,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT ck_filings_accession CHECK (accession_no ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'),
  CONSTRAINT ck_filings_cik CHECK (cik ~ '^[0-9]{10}$'),
  CONSTRAINT ck_filings_known_after_acceptance CHECK (known_at >= accepted_at),
  CONSTRAINT ck_filings_raw_pair CHECK ((raw_s3_key IS NULL) = (raw_sha256 IS NULL)),
  CONSTRAINT ck_filings_raw_sha256 CHECK (raw_sha256 IS NULL OR raw_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE INDEX idx_filings_cik_known ON filings (cik, known_at DESC);
CREATE INDEX idx_filings_form_known ON filings (form_type, known_at DESC);

COMMENT ON TABLE filings IS
  'SEC filing metadata keyed by accession number. Raw documents live in S3; this table stores the key and SHA-256.';

-- ------------------------------------------------------------
INSERT INTO schema_migrations (migration_name, sequence_number, is_baseline)
VALUES ('001_baseline.sql', 1, TRUE);
