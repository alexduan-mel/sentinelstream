"""Container health check for the scheduler and worker: exit 0 if the heartbeat file is fresh.

python -m orchestration.healthcheck --max-age 120
"""

from __future__ import annotations

import argparse
import os
import sys

from orchestration.service import DEFAULT_HEARTBEAT_FILE, is_fresh


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the service heartbeat file is fresh.")
    parser.add_argument("--max-age", type=float, required=True, help="seconds")
    parser.add_argument("--file", default=os.getenv("HEARTBEAT_FILE", DEFAULT_HEARTBEAT_FILE))
    args = parser.parse_args(argv)
    return 0 if is_fresh(args.file, args.max_age) else 1


if __name__ == "__main__":
    sys.exit(main())
