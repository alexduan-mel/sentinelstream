-- ============================================================
-- 010_reonboard_for_listing_dates.sql
--
-- Re-runs onboarding for every ready equity, so price history starts at
-- each security's listing date (ADR 0010, amended).
--
-- The first onboarding loaded two years of bars for whatever traded under
-- each symbol. A reused symbol therefore mixed two securities: SPCX's bars
-- before SpaceX listed on 12 Jun 2026 belong to an ETF that used the symbol
-- earlier. Onboarding now reads the listing date from Massive and deletes
-- bars from before it. Moving the equities back to backfilling keeps them
-- in the poller's watch set; the onboarding run made due here takes them
-- to ready again within a few minutes of the deploy.
-- ============================================================

UPDATE instruments
SET onboarding_status = 'backfilling', updated_at = NOW()
WHERE active AND asset_type = 'equity' AND onboarding_status = 'ready';

UPDATE task_definitions SET next_run_at = NOW(), updated_at = NOW() WHERE task_id = 'onboard_tickers';

INSERT INTO schema_migrations (migration_name, sequence_number)
VALUES ('010_reonboard_for_listing_dates.sql', 10);
