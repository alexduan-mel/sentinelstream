"""Queue a manual run from the command line; the worker picks it up. Prints the new run's id.

    python -m orchestration.enqueue ingest_prices --ticker NVDA --ticker AMZN
    python -m orchestration.enqueue ingest_sec_filings --payload '{"lookback_days": 3}'

On EC2, in an SSM session: ``sudo docker exec sentinelstream-worker-1 python -m orchestration.enqueue <task_id>``.
(A bare ``docker compose exec`` fails there: the prod Compose file needs variables only deploy.sh sets.)
The API's manual-trigger endpoint inserts the same kind of row.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from datetime import date

from orchestration import db
from orchestration.task_runs import TaskDisabledError, TaskNotFoundError, enqueue_run


def _payload(raw: str) -> dict[str, object]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise argparse.ArgumentTypeError("must be a JSON object")
    return value


def _requested_by() -> str:
    try:
        return f"cli:{getpass.getuser()}"
    except (KeyError, OSError):  # no passwd entry for the container's uid
        return "cli"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Queue a manual task run.")
    parser.add_argument("task_id")
    parser.add_argument("--ticker", action="append", default=[], help="repeat for several tickers")
    parser.add_argument("--as-of", type=date.fromisoformat, help="YYYY-MM-DD")
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--payload", type=_payload, default={}, help="JSON object merged over default_payload")
    args = parser.parse_args(argv)

    try:
        conn = db.connect(application_name="sentinelstream-enqueue")
    except db.DatabaseConfigurationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    try:
        run_id = enqueue_run(
            conn,
            args.task_id,
            trigger_source="cli",
            run_mode="manual",
            requested_by=_requested_by(),
            payload=args.payload,
            tickers=args.ticker,
            as_of_date=args.as_of,
            force_refresh=args.force_refresh,
        )
        conn.commit()
    except (TaskNotFoundError, TaskDisabledError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    print(run_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
