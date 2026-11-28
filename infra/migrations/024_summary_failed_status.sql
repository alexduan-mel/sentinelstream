-- ============================================================
-- 024_summary_failed_status.sql
--
-- A summary whose call failed in a way that may have been billed (an
-- answer cut off or not matching the schema) is stored as 'failed' and
-- not retried every hour, so a billed failure can never repeat hourly
-- (ADR 0024). Failures that cost nothing (network, rate limit, overload,
-- a spend limit reached) still leave the diff due for the next run.
-- The error is kept in rejections.
-- ============================================================

ALTER TABLE filing_change_summaries
  DROP CONSTRAINT ck_filing_change_summaries_status,
  ADD CONSTRAINT ck_filing_change_summaries_status
    CHECK (status IN ('ok', 'rejected', 'refused', 'no_changes', 'failed'));

INSERT INTO schema_migrations (migration_name, sequence_number)
VALUES ('024_summary_failed_status.sql', 24);
