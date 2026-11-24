"""Forward-only SQL migration runner.

Adapted from the prototype's runner (services/python-ai/app/migrations.py). Rules:

* Files are named ``NNN_name.sql`` and applied in sequence order. A sequence number is used once.
* The whole run holds a Postgres session advisory lock, so concurrent runners (every app container
  runs this at start) apply each file once: the others wait, then find nothing pending.
* Each file runs in one transaction owned by the runner, so a file must not contain ``BEGIN``,
  ``COMMIT`` or ``ROLLBACK``. Every file, the baseline included, must insert its own row into
  ``schema_migrations``. The runner checks that row inside the same transaction and rolls the file
  back if it is missing, so a file is either fully applied and recorded or not applied at all.
* A pending file numbered below the newest applied file is refused (out of order).
* A recorded migration with no file on disk is only a warning: an older image started by a rollback
  must still run against a database that newer forward migrations have already touched.

Run with ``python -m orchestration.migrations``; settings come from ``POSTGRES_*`` (see ``.env.example``).
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import psycopg2
from psycopg2.extensions import TRANSACTION_STATUS_INTRANS
from psycopg2.extensions import connection as Connection

from orchestration import db

LOGGER = logging.getLogger(__name__)

DEFAULT_DIRECTORY = Path(__file__).resolve().parent.parent / "infra" / "migrations"
SCHEMA = "sentinel_stream"
LEDGER = f"{SCHEMA}.schema_migrations"
LOCK_KEY = "sentinel_stream_schema_migrations"

NAME_PATTERN = re.compile(r"^(?P<sequence>[0-9]{3})_[a-z0-9_]+[.]sql$")
# Top-level transaction control. Anchored to a line start and a semicolon so that the BEGIN/END of
# PL/pgSQL bodies and commented-out lines do not match.
TRANSACTION_CONTROL = re.compile(
    r"^[ \t]*(?:BEGIN|START[ \t]+TRANSACTION|COMMIT|ROLLBACK|ABORT)(?:[ \t]+(?:WORK|TRANSACTION))?[ \t]*;",
    re.IGNORECASE | re.MULTILINE,
)


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True, order=True)
class Migration:
    sequence: int
    name: str
    path: Path


def discover_migrations(directory: Path) -> list[Migration]:
    """List migration files in sequence order, rejecting bad names, reused numbers and transaction control."""
    if not directory.is_dir():
        raise MigrationError(f"Migration directory does not exist: {directory}")

    by_sequence: dict[int, Migration] = {}
    for path in sorted(directory.glob("*.sql")):
        match = NAME_PATTERN.fullmatch(path.name)
        if match is None:
            raise MigrationError(f"Invalid migration filename: {path.name} (expected NNN_lower_snake.sql)")
        sequence = int(match.group("sequence"))
        if sequence == 0:
            raise MigrationError(f"Migration sequence must start at 001: {path.name}")
        if sequence in by_sequence:
            raise MigrationError(
                f"Sequence {sequence:03d} is used by both {by_sequence[sequence].name} and {path.name}"
            )
        control = TRANSACTION_CONTROL.search(path.read_text(encoding="utf-8"))
        if control is not None:
            raise MigrationError(
                f"{path.name} contains {control.group(0).strip()!r}; the runner owns the transaction, "
                "so migration files must not begin or end one"
            )
        by_sequence[sequence] = Migration(sequence, path.name, path)

    if not by_sequence:
        raise MigrationError(f"No migration files in {directory}")
    return [by_sequence[sequence] for sequence in sorted(by_sequence)]


def pending_migrations(migrations: list[Migration], applied: dict[str, int]) -> list[Migration]:
    """Files not yet recorded in the ledger. ``applied`` maps migration name to its sequence number."""
    on_disk = {migration.name for migration in migrations}
    unknown = sorted(set(applied) - on_disk)
    if unknown:
        LOGGER.warning("migration_unknown_applied names=%s (database is ahead of this build)", ",".join(unknown))

    newest_applied = max(applied.values(), default=0)
    pending = [migration for migration in migrations if migration.name not in applied]
    for migration in pending:
        if migration.sequence <= newest_applied:
            raise MigrationError(
                f"Refusing out-of-order migration {migration.name}: sequence {newest_applied:03d} is already applied"
            )
    return pending


def _applied(conn: Connection) -> dict[str, int]:
    with conn.cursor() as cursor:
        cursor.execute("SELECT to_regclass(%s) IS NOT NULL", (LEDGER,))
        if not cursor.fetchone()[0]:
            return {}
        cursor.execute(f"SELECT migration_name, sequence_number FROM {LEDGER}")
        return {str(name): int(sequence) for name, sequence in cursor.fetchall()}


def _recorded_sequence(conn: Connection, name: str) -> int | None:
    with conn.cursor() as cursor:
        cursor.execute("SELECT to_regclass(%s) IS NOT NULL", (LEDGER,))
        if not cursor.fetchone()[0]:
            return None
        cursor.execute(f"SELECT sequence_number FROM {LEDGER} WHERE migration_name = %s", (name,))
        row = cursor.fetchone()
        return None if row is None else int(row[0])


def _apply(conn: Connection, migration: Migration) -> None:
    """Run one file and check it recorded itself, all in one transaction."""
    LOGGER.info("migration_start name=%s", migration.name)
    started = time.monotonic()
    try:
        with conn.cursor() as cursor:
            cursor.execute(f"SET LOCAL search_path TO {SCHEMA}, public")
            cursor.execute(migration.path.read_text(encoding="utf-8"))
        if conn.get_transaction_status() != TRANSACTION_STATUS_INTRANS:
            # Backstop for transaction control the filename scan missed: the file has already
            # committed, so this cannot be undone here. Stop before anything else runs.
            raise MigrationError(f"{migration.name} ended the runner's transaction; it may be partly applied")
        recorded = _recorded_sequence(conn, migration.name)
        if recorded is None:
            raise MigrationError(
                f"{migration.name} did not record itself in schema_migrations; "
                f"add: INSERT INTO schema_migrations (migration_name, sequence_number) "
                f"VALUES ('{migration.name}', {migration.sequence});"
            )
        if recorded != migration.sequence:
            raise MigrationError(f"{migration.name} recorded sequence_number {recorded}, expected {migration.sequence}")
        conn.commit()
    except Exception:
        conn.rollback()
        LOGGER.error("migration_failed name=%s", migration.name)
        raise
    LOGGER.info("migration_complete name=%s duration_ms=%d", migration.name, (time.monotonic() - started) * 1000)


def run_migrations(conn: Connection, directory: Path = DEFAULT_DIRECTORY) -> list[str]:
    """Apply pending migrations under the advisory lock and return the names applied by this call."""
    migrations = discover_migrations(directory)
    conn.autocommit = True
    with conn.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_lock(hashtext(%s))", (LOCK_KEY,))
    try:
        conn.autocommit = False
        # Read the ledger only after taking the lock, so a runner that waited sees what the
        # previous holder applied.
        pending = pending_migrations(migrations, _applied(conn))
        conn.commit()
        applied_now: list[str] = []
        for migration in pending:
            _apply(conn, migration)
            applied_now.append(migration.name)
        return applied_now
    finally:
        if not conn.closed:
            conn.rollback()
            conn.autocommit = True
            with conn.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(hashtext(%s))", (LOCK_KEY,))


def _connect_with_retry(attempts: int, delay_seconds: float) -> Connection:
    """Connect, retrying while Postgres is still starting. Configuration errors are not retried."""
    for attempt in range(1, attempts + 1):
        try:
            return db.connect()
        except psycopg2.OperationalError as exc:
            if attempt == attempts:
                raise
            LOGGER.info("database_not_ready attempt=%d/%d error=%s", attempt, attempts, str(exc).strip())
            time.sleep(delay_seconds)
    raise AssertionError("unreachable")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply pending SentinelStream SQL migrations.")
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument("--connect-attempts", type=int, default=15)
    args = parser.parse_args(argv)
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")

    try:
        conn = _connect_with_retry(args.connect_attempts, delay_seconds=2)
    except (db.DatabaseConfigurationError, psycopg2.OperationalError) as exc:
        LOGGER.error("migration_runner_failed error=%s", str(exc).strip())
        return 1
    try:
        applied = run_migrations(conn, args.directory)
    except MigrationError as exc:
        LOGGER.error("migration_runner_failed error=%s", exc)
        return 1
    finally:
        conn.close()
    LOGGER.info("migration_runner_complete applied_count=%d applied=%s", len(applied), ",".join(applied) or "-")
    return 0


if __name__ == "__main__":
    sys.exit(main())
