-- ============================================================
-- 008_alert_deliveries.sql
--
-- One row per filing and notifier channel: the outbox and delivery record
-- for alerts (ADR 0009).
--
-- The primary key is the deduplication: a filing is pushed to a channel at
-- most once, however many watchlist issuers it is linked to and however
-- often the poller reruns. A row is queued when any of the filing's links
-- is high and news (is_backfill = FALSE). status:
--   pending    queued, not delivered yet; attempts and last_error say why
--   delivered  the channel accepted it at delivered_at
--   skipped    never to be sent; skip_reason says why
--
-- delivered_at minus the filing's accepted_at is the alert latency that M1
-- measures (the 10-minute target).
--
-- Highs classified before this migration are history for Slack: they were
-- news when the poller first saw them, but pushing them now would be late.
-- They are recorded as skipped so the notifier's first run sends nothing
-- stale.
-- ============================================================

CREATE TABLE alert_deliveries (
  accession_no    TEXT NOT NULL REFERENCES filings (accession_no) ON DELETE CASCADE,
  channel         TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'pending',
  attempts        INTEGER NOT NULL DEFAULT 0,
  last_error      TEXT,
  skip_reason     TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_attempt_at TIMESTAMPTZ,
  delivered_at    TIMESTAMPTZ,
  task_run_id     BIGINT REFERENCES task_runs (id) ON DELETE SET NULL,
  PRIMARY KEY (accession_no, channel),
  CONSTRAINT ck_alert_deliveries_channel CHECK (channel ~ '^[a-z][a-z0-9_]{0,31}$'),
  CONSTRAINT ck_alert_deliveries_status CHECK (status IN ('pending', 'delivered', 'skipped')),
  CONSTRAINT ck_alert_deliveries_delivered CHECK ((status = 'delivered') = (delivered_at IS NOT NULL)),
  CONSTRAINT ck_alert_deliveries_skipped CHECK ((status = 'skipped') = (skip_reason IS NOT NULL))
);

CREATE INDEX idx_alert_deliveries_pending ON alert_deliveries (channel, created_at)
  WHERE status = 'pending';

INSERT INTO alert_deliveries (accession_no, channel, status, skip_reason)
SELECT DISTINCT accession_no, 'slack', 'skipped', 'classified before the Slack notifier was switched on'
FROM filing_issuers
WHERE materiality = 'high' AND NOT is_backfill;

INSERT INTO schema_migrations (migration_name, sequence_number)
VALUES ('008_alert_deliveries.sql', 8);
