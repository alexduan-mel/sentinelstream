-- ============================================================
-- 025_journal.sql
--
-- The Journal: the owner's own decisions, kept as an append-only ledger
-- (ADR 0025). Each entry counts once in the record of calls, whatever its
-- size; a share count, when given, also feeds a "my money" view.
--
-- journal_entries: one row per decision. action is what was done:
--   opened, added, trimmed, closed, or pass ("looked, chose not to act").
-- accession_no is the alert (filing) the decision was logged from, if any.
-- known_at is when the entry was logged, by the database clock; it is
-- never typed in.
--
-- traded_on and price are optional, for a real trade: the day and the
-- price per share it was done at, as traded (not split-adjusted). The
-- API checks the price against that day's low-high range. Without them,
-- and always for a pass, the entry is scored from the first daily close
-- after known_at, never a price already known when it was logged
-- (ground rule 2). shares is optional too, as traded on that day; it
-- feeds the "my money" view and every entry still counts once in the
-- record of calls.
--
-- journal_corrections: later rows that fix an entry without changing it.
--   thesis     new thesis wording (the original stays visible)
--   source     the alert link changed, or removed (accession_no NULL)
--   withdrawn  the entry no longer counts; it stays listed with the reason
-- The action, ticker, trade and time of an entry can't be corrected:
-- withdraw it and log a new one (with the trade day, if it was a trade).
--
-- Both tables refuse UPDATE and DELETE.
-- ============================================================

CREATE TABLE journal_entries (
  id            BIGSERIAL PRIMARY KEY,
  instrument_id BIGINT NOT NULL REFERENCES instruments (id) ON DELETE RESTRICT,
  action        TEXT NOT NULL,
  thesis        TEXT NOT NULL,
  accession_no  TEXT REFERENCES filings (accession_no) ON DELETE RESTRICT,
  traded_on     DATE,
  price         NUMERIC(18, 6),
  shares        NUMERIC(20, 6),
  known_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  recorded_by   BIGINT NOT NULL REFERENCES app_users (id) ON DELETE RESTRICT,
  CONSTRAINT ck_journal_entries_action CHECK (action IN ('opened', 'added', 'trimmed', 'closed', 'pass')),
  CONSTRAINT ck_journal_entries_thesis CHECK (btrim(thesis) <> '' AND char_length(thesis) <= 4000),
  CONSTRAINT ck_journal_entries_trade CHECK ((traded_on IS NULL) = (price IS NULL)),
  CONSTRAINT ck_journal_entries_pass_no_trade
    CHECK (action <> 'pass' OR (traded_on IS NULL AND price IS NULL AND shares IS NULL)),
  CONSTRAINT ck_journal_entries_price CHECK (price IS NULL OR price > 0),
  CONSTRAINT ck_journal_entries_shares CHECK (shares IS NULL OR shares > 0),
  CONSTRAINT ck_journal_entries_traded_before_logged
    CHECK (traded_on IS NULL OR traded_on <= (known_at AT TIME ZONE 'America/New_York')::date)
);

CREATE INDEX idx_journal_entries_instrument ON journal_entries (instrument_id, known_at, id);

CREATE TABLE journal_corrections (
  id           BIGSERIAL PRIMARY KEY,
  entry_id     BIGINT NOT NULL REFERENCES journal_entries (id) ON DELETE RESTRICT,
  kind         TEXT NOT NULL,
  thesis       TEXT,
  accession_no TEXT REFERENCES filings (accession_no) ON DELETE RESTRICT,
  reason       TEXT NOT NULL,
  known_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  recorded_by  BIGINT NOT NULL REFERENCES app_users (id) ON DELETE RESTRICT,
  CONSTRAINT ck_journal_corrections_kind CHECK (kind IN ('thesis', 'source', 'withdrawn')),
  CONSTRAINT ck_journal_corrections_shape CHECK (
    (kind = 'thesis' AND thesis IS NOT NULL AND accession_no IS NULL)
    OR (kind = 'source' AND thesis IS NULL)
    OR (kind = 'withdrawn' AND thesis IS NULL AND accession_no IS NULL)),
  CONSTRAINT ck_journal_corrections_thesis
    CHECK (thesis IS NULL OR (btrim(thesis) <> '' AND char_length(thesis) <= 4000)),
  CONSTRAINT ck_journal_corrections_reason CHECK (btrim(reason) <> '' AND char_length(reason) <= 1000)
);

CREATE INDEX idx_journal_corrections_entry ON journal_corrections (entry_id, id);

-- An entry is withdrawn at most once.
CREATE UNIQUE INDEX uq_journal_corrections_withdrawn ON journal_corrections (entry_id) WHERE kind = 'withdrawn';

CREATE FUNCTION journal_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% is append-only: add a correction instead', TG_TABLE_NAME;
END
$$;

CREATE TRIGGER trg_journal_entries_append_only
  BEFORE UPDATE OR DELETE ON journal_entries
  FOR EACH ROW EXECUTE FUNCTION journal_append_only();

CREATE TRIGGER trg_journal_corrections_append_only
  BEFORE UPDATE OR DELETE ON journal_corrections
  FOR EACH ROW EXECUTE FUNCTION journal_append_only();

COMMENT ON TABLE journal_entries IS
  'The owner''s decisions (ADR 0025). Append-only; corrections go in journal_corrections.';

INSERT INTO schema_migrations (migration_name, sequence_number)
VALUES ('025_journal.sql', 25);
