-- ============================================================
-- 018_alert_base_rates.sql
--
-- Stored base rates for alerts (ADR 0018).
--
-- base_rates holds the calculator's summaries for every kind of event
-- that makes an alert high: insider open-market purchases, and 8-Ks
-- with item 1.01, 2.02, 4.02 or 5.02. Each kind is summarised for the
-- whole market and for each industry (scope 'sic2:NN', the two-digit
-- SIC major group), under both filter settings ('main', 'none'), at
-- each horizon. An alert shows its industry's base rate when that has
-- enough past events, otherwise the market's. The table is replaced
-- whole by each run, so it always reflects one calculation.
--
-- compute_base_rates becomes automatic: daily at 06:00 New York time,
-- before the open, with {"event": "alerts"}, on the research worker. It
-- runs straight after this deploy too. A manual run can still ask for
-- a one-off report ({"event": "insider_purchases"}, ...), which goes
-- to the run's meta only.
-- ============================================================

CREATE TABLE base_rates (
  event_kind               TEXT NOT NULL,
  event_key                TEXT NOT NULL,
  scope                    TEXT NOT NULL,
  variant                  TEXT NOT NULL,
  horizon                  INTEGER NOT NULL,
  n                        INTEGER NOT NULL,
  mean                     DOUBLE PRECISION NOT NULL,
  median                   DOUBLE PRECISION NOT NULL,
  hit_rate                 DOUBLE PRECISION NOT NULL,
  ci_low                   DOUBLE PRECISION,
  ci_high                  DOUBLE PRECISION,
  months                   INTEGER NOT NULL,
  mean_gross_return        DOUBLE PRECISION NOT NULL,
  mean_benchmark_return    DOUBLE PRECISION NOT NULL,
  mean_cost                DOUBLE PRECISION NOT NULL,
  mean_excess_vs_reference DOUBLE PRECISION,
  ended_early              INTEGER NOT NULL,
  as_of                    TIMESTAMPTZ NOT NULL,
  computed_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  task_run_id              BIGINT REFERENCES task_runs (id) ON DELETE SET NULL,
  PRIMARY KEY (event_kind, event_key, scope, variant, horizon),
  CONSTRAINT ck_base_rates_kind CHECK (event_kind IN ('insider_purchase', '8k_item')),
  CONSTRAINT ck_base_rates_scope CHECK (scope = 'market' OR scope ~ '^sic2:[0-9]{2}$'),
  CONSTRAINT ck_base_rates_variant CHECK (variant IN ('main', 'none')),
  CONSTRAINT ck_base_rates_n CHECK (n > 0)
);

UPDATE task_definitions
SET trigger_mode      = 'automatic',
    schedule_type     = 'daily_time',
    interval_minutes  = NULL,
    scheduled_time    = '06:00',
    schedule_timezone = 'America/New_York',
    schedule_days     = 'all',
    frequency         = 'Daily before the US open',
    default_payload   = '{"event": "alerts"}'::jsonb,
    next_run_at       = NOW(),
    updated_at        = NOW()
WHERE task_id = 'compute_base_rates';

INSERT INTO schema_migrations (migration_name, sequence_number)
VALUES ('018_alert_base_rates.sql', 18);
