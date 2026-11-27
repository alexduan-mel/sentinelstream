-- ============================================================
-- 012_market_bars.sql
--
-- Market-wide daily bars for the base-rate calculator (ADR 0012).
--
-- market_bars holds one row per symbol and trading day for every US
-- stock Massive reports, from its grouped-daily endpoint, as traded:
-- NOT split-adjusted. An as-traded price never changes after the fact,
-- so a split needs no re-fetch and a price-level filter ("$5 or more")
-- sees the price people actually paid. Returns across a split are
-- corrected with split data at calculation time.
--
-- Rows are keyed by the symbol as it traded that day, not by company. A
-- symbol can later pass to another company, and a company can change
-- symbol; research ties an event to the symbol it had at the time.
--
-- market_bar_days records each trading day fetched, in one transaction
-- with its bars, so a day is loaded whole or not at all:
--   loaded       bars stored
--   empty        Massive has no bars for a past weekday the calendar
--                calls a trading day (an unscheduled closure)
--   unavailable  before the start of the plan's history
-- Deleting a day's row deletes its bars, and the next run re-fetches it.
--
-- ingest_market_bars runs every 10 minutes from straight after this
-- deploy. It loads at most a few days per run, oldest first (the plan's
-- two-year history drops its oldest day each day), so the single worker
-- is never held for long and the 5-minute SEC poll keeps its timing.
-- Once the two years are in, a run makes no request until a new session
-- closes.
-- ============================================================

CREATE TABLE market_bar_days (
  trading_date  DATE PRIMARY KEY,
  status        TEXT NOT NULL,
  symbol_count  INTEGER NOT NULL DEFAULT 0,
  invalid_count INTEGER NOT NULL DEFAULT 0,
  request_id    TEXT,
  fetched_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  task_run_id   BIGINT REFERENCES task_runs (id) ON DELETE SET NULL,
  CONSTRAINT ck_market_bar_days_status CHECK (status IN ('loaded', 'empty', 'unavailable')),
  CONSTRAINT ck_market_bar_days_counts CHECK (symbol_count >= 0 AND invalid_count >= 0)
);

CREATE TABLE market_bars (
  symbol       TEXT NOT NULL,
  trading_date DATE NOT NULL REFERENCES market_bar_days (trading_date) ON DELETE CASCADE,
  open         DOUBLE PRECISION,
  high         DOUBLE PRECISION,
  low          DOUBLE PRECISION,
  close        DOUBLE PRECISION NOT NULL,
  -- Shares traded; fractional because Massive counts fractional-share trades.
  volume       DOUBLE PRECISION,
  -- Volume-weighted average price, so volume * vwap is the day's traded value.
  vwap         DOUBLE PRECISION,
  transactions INTEGER,
  -- Public at the regular close, as for price_bars (conservative on half-days).
  known_at     TIMESTAMPTZ NOT NULL
    GENERATED ALWAYS AS ((trading_date + TIME '16:00') AT TIME ZONE 'America/New_York') STORED,
  PRIMARY KEY (symbol, trading_date),
  CONSTRAINT ck_market_bars_symbol CHECK (symbol <> ''),
  CONSTRAINT ck_market_bars_ohlc CHECK (high IS NULL OR low IS NULL OR high >= low)
);

COMMENT ON TABLE market_bars IS
  'As-traded (not split-adjusted) daily bars for every US stock Massive reports, keyed by symbol as traded that day. Lineage is per day, in market_bar_days.';

INSERT INTO task_definitions
  (task_id, label, job_type, worker_name, purpose, frequency, endpoint,
   trigger_mode, schedule_type, interval_minutes, scheduled_time, schedule_timezone, schedule_days, next_run_at)
VALUES
  ('ingest_market_bars', 'Ingest market-wide daily bars', 'ingest_market_bars', 'market_bars_worker',
   'Load as-traded daily bars for every US stock from Massive: two years back, a few days per run, then each new session.',
   'Every 10 minutes', '/api/tasks/ingest_market_bars/run',
   'automatic', 'interval', 10, NULL, 'UTC', 'all', NOW());

INSERT INTO schema_migrations (migration_name, sequence_number)
VALUES ('012_market_bars.sql', 12);
