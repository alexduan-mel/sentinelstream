-- ============================================================
-- 013_stock_splits.sql
--
-- Market-wide stock splits, and split detection for the watchlist
-- (ADR 0013).
--
-- stock_splits holds every split Massive reports, keyed by Massive's own
-- id: one day can carry several splits for one symbol (PMD did a
-- 1-for-5,000 reverse split and a 5,000-for-1 split on 4 Dec 2024;
-- some issuers pay two share dividends at once), so (symbol, day) is
-- not unique. split_from shares became split_to shares: NVIDIA's
-- 10-for-1 in June 2024 is 1 -> 10. The ratios can be fractional.
--
-- known_at is the start of the execution day in New York, the first day
-- the new share count trades. Splits are announced before that, so this
-- is never earlier than public. A return up to any later close can use
-- it.
--
-- split_windows records which calendar quarters have been fetched. A
-- quarter is fetched whole, and splits in it that Massive no longer
-- lists are removed in the same transaction. A quarter that ended more
-- than 45 days before its last fetch is settled and not asked again;
-- the current and upcoming quarters are re-fetched on every run.
--
-- ingest_splits runs straight after this deploy, then daily at 17:00 ET:
-- after the close, before ingest_prices at 18:30. When a watchlist or
-- benchmark symbol has a split whose stored split-adjusted bars were
-- fetched before it took effect, the run queues an ingest_prices
-- re-fetch of that symbol's whole history.
-- ============================================================

CREATE TABLE stock_splits (
  id             TEXT PRIMARY KEY,
  symbol         TEXT NOT NULL,
  execution_date DATE NOT NULL,
  split_from     DOUBLE PRECISION NOT NULL,
  split_to       DOUBLE PRECISION NOT NULL,
  known_at       TIMESTAMPTZ NOT NULL
    GENERATED ALWAYS AS ((execution_date + TIME '00:00') AT TIME ZONE 'America/New_York') STORED,
  fetched_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  task_run_id    BIGINT REFERENCES task_runs (id) ON DELETE SET NULL,
  CONSTRAINT ck_stock_splits_symbol CHECK (symbol <> ''),
  CONSTRAINT ck_stock_splits_ratio CHECK (split_from > 0 AND split_to > 0)
);

CREATE INDEX idx_stock_splits_symbol_date ON stock_splits (symbol, execution_date);
CREATE INDEX idx_stock_splits_date ON stock_splits (execution_date);

CREATE TABLE split_windows (
  window_start DATE PRIMARY KEY,
  window_end   DATE NOT NULL,
  split_count  INTEGER NOT NULL,
  fetched_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  task_run_id  BIGINT REFERENCES task_runs (id) ON DELETE SET NULL,
  CONSTRAINT ck_split_windows_range CHECK (window_start <= window_end),
  CONSTRAINT ck_split_windows_count CHECK (split_count >= 0)
);

INSERT INTO task_definitions
  (task_id, label, job_type, worker_name, purpose, frequency, endpoint,
   trigger_mode, schedule_type, interval_minutes, scheduled_time, schedule_timezone, schedule_days, next_run_at)
VALUES
  ('ingest_splits', 'Ingest stock splits', 'ingest_splits', 'splits_worker',
   'Load every US stock split from Massive, and re-fetch a watchlist ticker''s prices after it splits.',
   'Daily after the US close', '/api/tasks/ingest_splits/run',
   'automatic', 'daily_time', NULL, '17:00', 'America/New_York', 'all', NOW());

INSERT INTO schema_migrations (migration_name, sequence_number)
VALUES ('013_stock_splits.sql', 13);
