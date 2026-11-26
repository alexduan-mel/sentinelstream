-- ============================================================
-- 006_materiality.sql
--
-- Materiality rules v0 (ADR 0008).
--
-- Materiality is decided per filing-issuer link, because the answer depends
-- on the issuer: its filer type picks the rule set, and a Form 4 is high
-- only for the company whose shares were bought, not for a watchlist
-- company that happens to be a reporting owner. A NULL materiality means
-- the link has not been classified yet (a Form 4 whose XML has not been
-- fetched); the poller classifies new links first, then works through
-- history a few Form 4s at a time.
--
-- form4_transactions holds the non-derivative rows of each Form 4 fetched,
-- as filed. known_at is the filing's EDGAR acceptance time (ground rule 2).
-- ============================================================

ALTER TABLE filing_issuers
  ADD COLUMN materiality        TEXT,
  ADD COLUMN materiality_reason TEXT,
  ADD COLUMN classified_at      TIMESTAMPTZ,
  ADD CONSTRAINT ck_filing_issuers_materiality CHECK (materiality IS NULL OR materiality IN ('high', 'low')),
  ADD CONSTRAINT ck_filing_issuers_classified
    CHECK ((materiality IS NULL) = (classified_at IS NULL)
           AND (materiality IS NULL) = (materiality_reason IS NULL));

CREATE INDEX idx_filing_issuers_unclassified ON filing_issuers (is_backfill, accession_no)
  WHERE materiality IS NULL;

CREATE TABLE form4_transactions (
  accession_no       TEXT NOT NULL REFERENCES filings (accession_no) ON DELETE CASCADE,
  line_no            INTEGER NOT NULL,
  issuer_cik         TEXT NOT NULL,
  reporting_owners   TEXT NOT NULL,
  owner_roles        TEXT NOT NULL,
  security_title     TEXT,
  transaction_date   DATE,
  transaction_code   TEXT NOT NULL,
  acquired_disposed  TEXT,
  shares             NUMERIC,
  price_per_share    NUMERIC,
  shares_owned_after NUMERIC,
  direct_indirect    TEXT,
  known_at           TIMESTAMPTZ NOT NULL,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (accession_no, line_no),
  CONSTRAINT ck_form4_transactions_issuer_cik CHECK (issuer_cik ~ '^[0-9]{10}$'),
  CONSTRAINT ck_form4_transactions_acquired_disposed CHECK (acquired_disposed IS NULL OR acquired_disposed IN ('A', 'D')),
  CONSTRAINT ck_form4_transactions_direct_indirect CHECK (direct_indirect IS NULL OR direct_indirect IN ('D', 'I'))
);

CREATE INDEX idx_form4_transactions_issuer_code ON form4_transactions (issuer_cik, transaction_code, known_at DESC);

INSERT INTO schema_migrations (migration_name, sequence_number)
VALUES ('006_materiality.sql', 6);
