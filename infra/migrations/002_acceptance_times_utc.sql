-- ============================================================
-- 002_acceptance_times_utc.sql
--
-- Corrects filings.accepted_at and filings.known_at (ADR 0006).
--
-- The poller read EDGAR's acceptanceDateTime ("2026-11-24T23:46:00.000Z")
-- as New York wall-clock time, but the value is UTC: EDGAR's index page
-- for that filing shows it accepted at 18:46:00 Eastern. Every stored
-- acceptance time is therefore four or five hours late. Recovering the
-- digits SEC sent (the stored instant as New York wall-clock time) and
-- reading them as UTC gives the true instant.
--
-- The repeated hour at the end of daylight time round-trips exactly. Digits
-- inside the skipped hour at the start of daylight time (02:00 to 02:59 on
-- the second Sunday in March) would not, but in UTC those are Saturday
-- evening Eastern, when EDGAR is closed.
-- ============================================================

UPDATE filings
SET accepted_at = (accepted_at AT TIME ZONE 'America/New_York') AT TIME ZONE 'UTC',
    known_at    = (known_at    AT TIME ZONE 'America/New_York') AT TIME ZONE 'UTC',
    updated_at  = NOW();

INSERT INTO schema_migrations (migration_name, sequence_number)
VALUES ('002_acceptance_times_utc.sql', 2);
