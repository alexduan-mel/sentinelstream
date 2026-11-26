-- ============================================================
-- 011_name_benchmarks.sql
--
-- Onboarding now names ETFs from Massive's ticker details: an ETF has no
-- SEC issuer to take a name from. QQQ and SPY were onboarded before that,
-- so they are sent through onboarding again (prices only for an ETF) to
-- pick up their names for the Watchlist page.
-- ============================================================

UPDATE instruments
SET onboarding_status = 'pending', updated_at = NOW()
WHERE active AND asset_type = 'etf' AND name IS NULL AND onboarding_status = 'ready';

UPDATE task_definitions SET next_run_at = NOW(), updated_at = NOW() WHERE task_id = 'onboard_tickers';

INSERT INTO schema_migrations (migration_name, sequence_number)
VALUES ('011_name_benchmarks.sql', 11);
