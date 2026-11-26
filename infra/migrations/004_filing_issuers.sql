-- ============================================================
-- 004_filing_issuers.sql
--
-- Links each filing to every watchlist issuer whose EDGAR submissions list
-- it (ADR 0007).
--
-- filings.cik holds one CIK, and the poller's first sighting wins, so a
-- filing listed under two watchlist issuers was stored under whichever was
-- polled first and the other never saw it. Alerts have to reach every
-- issuer a filing belongs to, so the link is its own table.
--
-- is_backfill is per link, not per filing: the same filing can be history
-- for an issuer polled for the first time and news for one already watched.
-- Alerts (M1) fire only for links with is_backfill = FALSE.
--
-- Existing filings get the one link the poller recorded them under. A
-- filing shared with a second watchlist issuer gains that link on the next
-- poll, marked as backfill because the filing was already stored.
-- ============================================================

CREATE TABLE filing_issuers (
  accession_no  TEXT NOT NULL REFERENCES filings (accession_no) ON DELETE CASCADE,
  cik           TEXT NOT NULL,
  is_backfill   BOOLEAN NOT NULL,
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  task_run_id   BIGINT REFERENCES task_runs (id) ON DELETE SET NULL,
  PRIMARY KEY (accession_no, cik),
  CONSTRAINT ck_filing_issuers_cik CHECK (cik ~ '^[0-9]{10}$')
);

CREATE INDEX idx_filing_issuers_cik ON filing_issuers (cik);

INSERT INTO filing_issuers (accession_no, cik, is_backfill, first_seen_at, task_run_id)
SELECT accession_no, cik, is_backfill, first_seen_at, task_run_id
FROM filings;

INSERT INTO schema_migrations (migration_name, sequence_number)
VALUES ('004_filing_issuers.sql', 4);
