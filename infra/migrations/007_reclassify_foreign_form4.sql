-- ============================================================
-- 007_reclassify_foreign_form4.sql
--
-- Re-queues Form 4s of foreign private issuers for classification.
--
-- 006's rules assumed foreign private issuers file no Form 4 and marked
-- theirs low without reading them. Nebius (NBIS) still files 20-F and 6-K,
-- but its insiders have filed Forms 3 and 4 since March 2026, so a purchase
-- there would have been missed. The Form 4 rule now applies to every filer
-- type (ADR 0008); clearing these links lets the poller fetch and classify
-- them like any other Form 4.
-- ============================================================

UPDATE filing_issuers l
SET materiality = NULL, materiality_reason = NULL, classified_at = NULL
FROM filings f
WHERE f.accession_no = l.accession_no
  AND f.form_type = '4'
  AND l.materiality_reason LIKE '%no high rule for foreign private issuers%';

INSERT INTO schema_migrations (migration_name, sequence_number)
VALUES ('007_reclassify_foreign_form4.sql', 7);
