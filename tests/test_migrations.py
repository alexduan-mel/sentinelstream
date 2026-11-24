from __future__ import annotations

import shutil
import threading
import time
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


def test_baseline_applies_once(connect_test_db) -> None:
    conn = connect_test_db()

    assert run_migrations(conn) == ["001_baseline.sql"]
    assert _ledger(conn) == [("001_baseline.sql", 1)]
    for table in ("app_users", "instruments", "task_definitions", "task_runs", "price_bars", "filings"):
        assert _exists(conn, f"sentinel_stream.{table}"), table

    assert run_migrations(conn) == []
    assert _ledger(conn) == [("001_baseline.sql", 1)]


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
