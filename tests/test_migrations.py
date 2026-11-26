from __future__ import annotations

import shutil
import threading
import time
from datetime import time as datetime_time
from pathlib import Path

import pytest

from orchestration.migrations import (
    DEFAULT_DIRECTORY,
    LOCK_KEY,
    MigrationError,
    discover_migrations,
    main,
    run_migrations,
)

BASELINE = DEFAULT_DIRECTORY / "001_baseline.sql"


def _record(name: str, sequence: int) -> str:
    return f"INSERT INTO schema_migrations (migration_name, sequence_number) VALUES ('{name}', {sequence});\n"


def _write(directory: Path, name: str, body: str) -> None:
    (directory / name).write_text(body, encoding="utf-8")


@pytest.fixture
def migrations_dir(tmp_path: Path) -> Path:
    """A migrations directory holding a copy of the real baseline."""
    directory = tmp_path / "migrations"
    directory.mkdir()
    shutil.copy(BASELINE, directory / BASELINE.name)
    return directory


def _ledger(conn) -> list[tuple[str, int]]:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT migration_name, sequence_number FROM sentinel_stream.schema_migrations ORDER BY sequence_number"
        )
        rows = cursor.fetchall()
    conn.rollback()
    return rows


def _exists(conn, relation: str) -> bool:
    with conn.cursor() as cursor:
        cursor.execute("SELECT to_regclass(%s) IS NOT NULL", (relation,))
        result = cursor.fetchone()[0]
    conn.rollback()
    return result


# --- Against Postgres ----------------------------------------------------------------------------


def test_baseline_applies_once(connect_test_db, migrations_dir: Path) -> None:
    conn = connect_test_db()

    assert run_migrations(conn, migrations_dir) == ["001_baseline.sql"]
    assert _ledger(conn) == [("001_baseline.sql", 1)]
    for table in ("app_users", "instruments", "task_definitions", "task_runs", "price_bars", "filings"):
        assert _exists(conn, f"sentinel_stream.{table}"), table

    assert run_migrations(conn, migrations_dir) == []
    assert _ledger(conn) == [("001_baseline.sql", 1)]


def test_repo_migrations_apply_in_order(connect_test_db) -> None:
    conn = connect_test_db()

    applied = run_migrations(conn)

    assert applied == [m.name for m in discover_migrations(DEFAULT_DIRECTORY)]
    assert [sequence for _, sequence in _ledger(conn)] == list(range(1, len(applied) + 1))
    assert run_migrations(conn) == []


def test_002_reads_stored_acceptance_digits_as_utc(connect_test_db, migrations_dir: Path) -> None:
    """Rows written under the old reading (digits taken as New York time) move to the true UTC instant."""
    conn = connect_test_db()
    run_migrations(conn, migrations_dir)
    # (accession, digits SEC sent, how 001-era code stored them, the true instant)
    cases = [
        # Winter: the AMZN Form 4 EDGAR shows as accepted 18:46:00 ET.
        ("0001018724-26-000001", "2026-11-24 23:46:00", "2026-11-24 23:46:00-05", "2026-11-24 18:46:00-05"),
        # Summer: 20:30Z is 16:30 EDT.
        ("0001018724-26-000002", "2026-04-30 20:30:00", "2026-04-30 20:30:00-04", "2026-04-30 16:30:00-04"),
        # The repeated hour on 1 Nov 2026, which the old code stored with fold=1 (EST).
        ("0001018724-26-000003", "2026-11-01 01:30:00", "2026-11-01 01:30:00-05", "2026-10-31 21:30:00-04"),
    ]
    with conn.cursor() as cursor:
        for accession, _digits, stored, _true in cases:
            cursor.execute(
                """
                INSERT INTO sentinel_stream.filings (accession_no, cik, form_type, filed_date, accepted_at, known_at)
                VALUES (%s, '0001018724', '4', '2026-01-01', %s, %s)
                """,
                (accession, stored, stored),
            )
    conn.commit()

    shutil.copy(DEFAULT_DIRECTORY / "002_acceptance_times_utc.sql", migrations_dir)
    assert run_migrations(conn, migrations_dir) == ["002_acceptance_times_utc.sql"]

    with conn.cursor() as cursor:
        for accession, digits, _stored, true in cases:
            cursor.execute(
                """
                SELECT accepted_at = %s::timestamptz, known_at = %s::timestamptz,
                       to_char(accepted_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')
                FROM sentinel_stream.filings WHERE accession_no = %s
                """,
                (true, true, accession),
            )
            assert cursor.fetchone() == (True, True, digits), accession
    conn.rollback()


def test_003_runs_the_sec_poller_automatically_every_5_minutes(connect_test_db, migrations_dir: Path) -> None:
    conn = connect_test_db()
    run_migrations(conn, migrations_dir)
    with conn.cursor() as cursor:
        cursor.execute(
            "UPDATE sentinel_stream.task_definitions SET next_run_at = '2026-11-26T00:00:00Z' "
            "WHERE task_id = 'ingest_sec_filings'"
        )
    conn.commit()

    for name in ("002_acceptance_times_utc.sql", "003_sec_poller_every_5_min.sql"):
        shutil.copy(DEFAULT_DIRECTORY / name, migrations_dir)
    assert run_migrations(conn, migrations_dir) == ["002_acceptance_times_utc.sql", "003_sec_poller_every_5_min.sql"]

    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT task_id, trigger_mode, schedule_type, interval_minutes, frequency, next_run_at
            FROM sentinel_stream.task_definitions ORDER BY task_id
            """)
        rows = cursor.fetchall()
    conn.rollback()
    assert rows[1] == ("ingest_sec_filings", "automatic", "interval", 5, "Every 5 minutes", None)
    assert rows[0][:2] == ("ingest_prices", "manual")  # untouched


def test_004_links_each_stored_filing_to_its_issuer(connect_test_db, migrations_dir: Path) -> None:
    conn = connect_test_db()
    for name in ("002_acceptance_times_utc.sql", "003_sec_poller_every_5_min.sql"):
        shutil.copy(DEFAULT_DIRECTORY / name, migrations_dir)
    run_migrations(conn, migrations_dir)
    with conn.cursor() as cursor:
        cursor.execute("""
            INSERT INTO sentinel_stream.filings
              (accession_no, cik, form_type, filed_date, accepted_at, known_at, is_backfill, first_seen_at)
            VALUES ('0001045810-26-000001', '0001045810', '8-K', '2026-11-20', '2026-11-20 21:05:00+00',
                    '2026-11-20 21:05:00+00', TRUE, '2026-11-21 00:00:00+00'),
                   ('0001045810-26-000002', '0001045810', '4', '2026-11-24', '2026-11-24 23:00:00+00',
                    '2026-11-24 23:00:00+00', FALSE, '2026-11-24 23:05:00+00')
            """)
    conn.commit()

    shutil.copy(DEFAULT_DIRECTORY / "004_filing_issuers.sql", migrations_dir)
    assert run_migrations(conn, migrations_dir) == ["004_filing_issuers.sql"]

    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT accession_no, cik, is_backfill, first_seen_at = (
                SELECT first_seen_at FROM sentinel_stream.filings f WHERE f.accession_no = l.accession_no)
            FROM sentinel_stream.filing_issuers l ORDER BY accession_no
            """)
        rows = cursor.fetchall()
    conn.rollback()
    assert rows == [
        ("0001045810-26-000001", "0001045810", True, True),
        ("0001045810-26-000002", "0001045810", False, True),
    ]


def test_005_schedules_daily_price_bars(connect_test_db, migrations_dir: Path) -> None:
    conn = connect_test_db()
    run_migrations(conn, migrations_dir)
    with conn.cursor() as cursor:
        cursor.execute("UPDATE sentinel_stream.task_definitions SET next_run_at = '2026-11-26T00:00:00Z'")
    conn.commit()

    for name in ("002_acceptance_times_utc.sql", "003_sec_poller_every_5_min.sql", "005_price_bars_automatic.sql"):
        shutil.copy(DEFAULT_DIRECTORY / name, migrations_dir)
    shutil.copy(DEFAULT_DIRECTORY / "004_filing_issuers.sql", migrations_dir)
    run_migrations(conn, migrations_dir)

    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT trigger_mode, schedule_type, scheduled_time, schedule_timezone, schedule_days, next_run_at
            FROM sentinel_stream.task_definitions WHERE task_id = 'ingest_prices'
            """)
        row = cursor.fetchone()
    conn.rollback()
    assert row == ("automatic", "daily_time", datetime_time(18, 30), "America/New_York", "weekdays", None)


def test_007_requeues_only_foreign_form4s_marked_low_without_reading(connect_test_db, migrations_dir: Path) -> None:
    conn = connect_test_db()
    for path in DEFAULT_DIRECTORY.glob("00[2-6]_*.sql"):
        shutil.copy(path, migrations_dir)
    run_migrations(conn, migrations_dir)
    rows = [
        ("0001513845-26-000001", "4", "low", "4: no high rule for foreign private issuers in v0"),
        ("0001513845-26-000002", "6-K", "low", "6-K: no high rule for foreign private issuers in v0"),
        ("0001045810-26-000003", "4", "low", "Form 4 codes S"),
    ]
    with conn.cursor() as cursor:
        for accession, form, level, reason in rows:
            cursor.execute(
                "INSERT INTO sentinel_stream.filings (accession_no, cik, form_type, filed_date, accepted_at, known_at) "
                "VALUES (%s, %s, %s, '2026-11-20', '2026-11-20 15:00+00', '2026-11-20 15:00+00')",
                (accession, accession[:10], form),
            )
            cursor.execute(
                "INSERT INTO sentinel_stream.filing_issuers "
                "(accession_no, cik, is_backfill, materiality, materiality_reason, classified_at) "
                "VALUES (%s, %s, TRUE, %s, %s, NOW())",
                (accession, accession[:10], level, reason),
            )
    conn.commit()

    shutil.copy(DEFAULT_DIRECTORY / "007_reclassify_foreign_form4.sql", migrations_dir)
    assert run_migrations(conn, migrations_dir) == ["007_reclassify_foreign_form4.sql"]

    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT accession_no, materiality, classified_at IS NULL FROM sentinel_stream.filing_issuers "
            "ORDER BY accession_no"
        )
        result = cursor.fetchall()
    conn.rollback()
    assert result == [
        ("0001045810-26-000003", "low", False),
        ("0001513845-26-000001", None, True),
        ("0001513845-26-000002", "low", False),
    ]


def test_008_marks_highs_already_classified_as_skipped_for_slack(connect_test_db, migrations_dir: Path) -> None:
    conn = connect_test_db()
    for path in DEFAULT_DIRECTORY.glob("00[2-7]_*.sql"):
        shutil.copy(path, migrations_dir)
    run_migrations(conn, migrations_dir)
    links = [
        ("0002130357-26-000001", "0002130357", False, "high"),  # news, high: stale for Slack now
        ("0002130357-26-000001", "0001513845", False, "high"),  # same filing, second issuer
        ("0001045810-26-000002", "0001045810", True, "high"),  # history
        ("0001045810-26-000003", "0001045810", False, "low"),
    ]
    with conn.cursor() as cursor:
        for accession in sorted({link[0] for link in links}):
            cursor.execute(
                "INSERT INTO sentinel_stream.filings (accession_no, cik, form_type, filed_date, accepted_at, known_at) "
                "VALUES (%s, %s, '8-K', '2026-11-25', '2026-11-25 21:10+00', '2026-11-25 21:10+00')",
                (accession, accession[:10]),
            )
        for accession, cik, is_backfill, level in links:
            cursor.execute(
                "INSERT INTO sentinel_stream.filing_issuers "
                "(accession_no, cik, is_backfill, materiality, materiality_reason, classified_at) "
                "VALUES (%s, %s, %s, %s, 'test', NOW())",
                (accession, cik, is_backfill, level),
            )
    conn.commit()

    shutil.copy(DEFAULT_DIRECTORY / "008_alert_deliveries.sql", migrations_dir)
    assert run_migrations(conn, migrations_dir) == ["008_alert_deliveries.sql"]

    with conn.cursor() as cursor:
        cursor.execute("SELECT accession_no, channel, status, skip_reason FROM sentinel_stream.alert_deliveries")
        result = cursor.fetchall()
    conn.rollback()
    assert result == [
        ("0002130357-26-000001", "slack", "skipped", "classified before the Slack notifier was switched on")
    ]


def test_009_starts_identified_equities_backfilling_and_schedules_onboarding(
    connect_test_db, migrations_dir: Path
) -> None:
    conn = connect_test_db()
    for path in DEFAULT_DIRECTORY.glob("00[2-8]_*.sql"):
        shutil.copy(path, migrations_dir)
    run_migrations(conn, migrations_dir)
    with conn.cursor() as cursor:
        cursor.execute(
            "UPDATE sentinel_stream.instruments SET cik = '0001045810', filer_type = 'domestic' WHERE symbol = 'NVDA'"
        )
        cursor.execute("UPDATE sentinel_stream.instruments SET cik = '0002130357' WHERE symbol = 'ANT'")
    conn.commit()

    shutil.copy(DEFAULT_DIRECTORY / "009_ticker_onboarding.sql", migrations_dir)
    assert run_migrations(conn, migrations_dir) == ["009_ticker_onboarding.sql"]

    with conn.cursor() as cursor:
        cursor.execute("SELECT symbol, onboarding_status FROM sentinel_stream.instruments ORDER BY symbol")
        statuses = dict(cursor.fetchall())
        cursor.execute(
            "SELECT trigger_mode, schedule_type, scheduled_time::text, next_run_at <= NOW() "
            "FROM sentinel_stream.task_definitions WHERE task_id = 'onboard_tickers'"
        )
        task = cursor.fetchone()
    conn.rollback()
    # Identified (CIK and filer type) and polled already: keeps being polled while it backfills.
    assert statuses["NVDA"] == "backfilling"
    # No filer type, no identity, or not a watchlist equity: onboarding starts from the top.
    assert statuses["ANT"] == statuses["AMZN"] == statuses["QQQ"] == "pending"
    assert task == ("automatic", "daily_time", "19:00:00", True)


def test_010_sends_ready_equities_back_through_onboarding(connect_test_db, migrations_dir: Path) -> None:
    conn = connect_test_db()
    for path in DEFAULT_DIRECTORY.glob("00[2-9]_*.sql"):
        shutil.copy(path, migrations_dir)
    run_migrations(conn, migrations_dir)
    with conn.cursor() as cursor:
        cursor.execute(
            "UPDATE sentinel_stream.instruments SET cik = '0001181412', filer_type = 'domestic', "
            "onboarding_status = 'ready' WHERE symbol = 'SPCX'"
        )
        cursor.execute("UPDATE sentinel_stream.instruments SET onboarding_status = 'ready' WHERE symbol = 'QQQ'")
        cursor.execute(
            "UPDATE sentinel_stream.task_definitions SET next_run_at = NOW() + INTERVAL '1 day' "
            "WHERE task_id = 'onboard_tickers'"
        )
    conn.commit()

    shutil.copy(DEFAULT_DIRECTORY / "010_reonboard_for_listing_dates.sql", migrations_dir)
    assert run_migrations(conn, migrations_dir) == ["010_reonboard_for_listing_dates.sql"]

    with conn.cursor() as cursor:
        cursor.execute("SELECT symbol, onboarding_status FROM sentinel_stream.instruments ORDER BY symbol")
        statuses = dict(cursor.fetchall())
        cursor.execute(
            "SELECT next_run_at <= NOW() FROM sentinel_stream.task_definitions WHERE task_id = 'onboard_tickers'"
        )
        due = cursor.fetchone()[0]
    conn.rollback()
    assert statuses["SPCX"] == "backfilling"  # still polled while prices are reloaded
    assert statuses["QQQ"] == "ready"  # an ETF's listing predates the two-year window
    assert due is True


def test_skips_applied_files(connect_test_db, migrations_dir: Path) -> None:
    # Not idempotent on purpose: running 002 a second time would fail on CREATE TABLE.
    _write(migrations_dir, "002_probe.sql", "CREATE TABLE probe (id INT);\n" + _record("002_probe.sql", 2))
    conn = connect_test_db()
    assert run_migrations(conn, migrations_dir) == ["001_baseline.sql", "002_probe.sql"]

    _write(
        migrations_dir, "003_probe_note.sql", "ALTER TABLE probe ADD note TEXT;\n" + _record("003_probe_note.sql", 3)
    )

    assert run_migrations(conn, migrations_dir) == ["003_probe_note.sql"]
    assert [name for name, _ in _ledger(conn)] == ["001_baseline.sql", "002_probe.sql", "003_probe_note.sql"]


def test_fails_and_rolls_back_a_file_that_does_not_record_itself(connect_test_db, migrations_dir: Path) -> None:
    _write(migrations_dir, "002_forgetful.sql", "CREATE TABLE forgetful (id INT);\n")
    conn = connect_test_db()

    with pytest.raises(MigrationError, match="002_forgetful.sql did not record itself"):
        run_migrations(conn, migrations_dir)

    assert _ledger(conn) == [("001_baseline.sql", 1)]
    assert not _exists(conn, "sentinel_stream.forgetful")


def test_baseline_must_record_itself_too(connect_test_db, tmp_path: Path) -> None:
    body = BASELINE.read_text(encoding="utf-8")
    record = "INSERT INTO schema_migrations (migration_name, sequence_number, is_baseline)"
    assert record in body
    _write(tmp_path, "001_baseline.sql", body[: body.index(record)])
    conn = connect_test_db()

    with pytest.raises(MigrationError, match="001_baseline.sql did not record itself"):
        run_migrations(conn, tmp_path)

    # The whole baseline rolled back, ledger and schema included.
    assert not _exists(conn, "sentinel_stream.schema_migrations")
    assert not _exists(conn, "sentinel_stream.instruments")


def test_fails_if_a_file_records_the_wrong_sequence(connect_test_db, migrations_dir: Path) -> None:
    _write(migrations_dir, "002_typo.sql", "CREATE TABLE typo (id INT);\n" + _record("002_typo.sql", 7))
    conn = connect_test_db()

    with pytest.raises(MigrationError, match="recorded sequence_number 7, expected 2"):
        run_migrations(conn, migrations_dir)

    assert not _exists(conn, "sentinel_stream.typo")


def test_refuses_an_older_file_added_after_a_newer_one(connect_test_db, migrations_dir: Path) -> None:
    _write(migrations_dir, "003_later.sql", _record("003_later.sql", 3))
    conn = connect_test_db()
    run_migrations(conn, migrations_dir)

    _write(migrations_dir, "002_late.sql", _record("002_late.sql", 2))

    with pytest.raises(MigrationError, match="out-of-order migration 002_late.sql"):
        run_migrations(conn, migrations_dir)


def test_unknown_applied_migration_is_only_a_warning(connect_test_db, migrations_dir: Path, caplog) -> None:
    """An older image started by a rollback must still start against a newer database."""
    _write(migrations_dir, "002_newer.sql", _record("002_newer.sql", 2))
    conn = connect_test_db()
    run_migrations(conn, migrations_dir)
    (migrations_dir / "002_newer.sql").unlink()

    assert run_migrations(conn, migrations_dir) == []
    assert "002_newer.sql" in caplog.text


def test_two_concurrent_runners_do_not_both_apply(connect_test_db, migrations_dir: Path) -> None:
    _write(migrations_dir, "002_probe.sql", "CREATE TABLE probe (id INT);\n" + _record("002_probe.sql", 2))

    # Hold the runners' lock so both are provably waiting on it at the same time before either runs.
    holder = connect_test_db()
    holder.autocommit = True
    with holder.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_lock(hashtext(%s))", (LOCK_KEY,))

    results: dict[int, list[str]] = {}
    errors: list[BaseException] = []

    def runner(index: int) -> None:
        try:
            results[index] = run_migrations(connect_test_db(), migrations_dir)
        except BaseException as exc:  # surfaced by the assertions below
            errors.append(exc)

    threads = [threading.Thread(target=runner, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()

    deadline = time.monotonic() + 10
    waiting = 0
    with holder.cursor() as cursor:
        while waiting < 2 and time.monotonic() < deadline:
            cursor.execute("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted")
            waiting = cursor.fetchone()[0]
            time.sleep(0.05)
        assert waiting == 2, "both runners should be waiting on the advisory lock"
        cursor.execute("SELECT pg_advisory_unlock(hashtext(%s))", (LOCK_KEY,))

    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    assert sorted(results.values()) == [[], ["001_baseline.sql", "002_probe.sql"]]
    assert [name for name, _ in _ledger(holder)] == ["001_baseline.sql", "002_probe.sql"]


# --- Discovery (no database) ---------------------------------------------------------------------


def test_repo_migrations_are_valid() -> None:
    assert [m.name for m in discover_migrations(DEFAULT_DIRECTORY)][0] == "001_baseline.sql"


@pytest.mark.parametrize("name", ["1_short.sql", "002-dash.sql", "002_Upper.sql", "000_zero.sql"])
def test_rejects_bad_filenames(tmp_path: Path, name: str) -> None:
    _write(tmp_path, name, "")

    with pytest.raises(MigrationError):
        discover_migrations(tmp_path)


def test_rejects_a_reused_sequence_number(tmp_path: Path) -> None:
    _write(tmp_path, "002_first.sql", "")
    _write(tmp_path, "002_second.sql", "")

    with pytest.raises(MigrationError, match="002 is used by both"):
        discover_migrations(tmp_path)


@pytest.mark.parametrize("statement", ["BEGIN;", "begin transaction;", "COMMIT;", "  commit work ;", "ROLLBACK;"])
def test_rejects_transaction_control(tmp_path: Path, statement: str) -> None:
    _write(tmp_path, "002_tx.sql", f"CREATE TABLE t (id INT);\n{statement}\n")

    with pytest.raises(MigrationError, match="must not begin or end"):
        discover_migrations(tmp_path)


def test_allows_plpgsql_blocks_and_comments(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "002_function.sql",
        "-- COMMIT;\nDO $$\nBEGIN\n  PERFORM 1;\nEND;\n$$;\n" + _record("002_function.sql", 2),
    )

    assert [m.name for m in discover_migrations(tmp_path)] == ["002_function.sql"]


def test_main_fails_clearly_without_database_settings(monkeypatch, caplog) -> None:
    for name in ("POSTGRES_HOST", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"):
        monkeypatch.delenv(name, raising=False)

    assert main(["--connect-attempts", "1"]) == 1
    assert "Missing database settings: POSTGRES_HOST, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD" in caplog.text
