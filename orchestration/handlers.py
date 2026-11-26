"""Which code runs for each ``task_id``. Replaces the prototype's subprocess dispatch (task_dispatch.py).

A handler is called in the worker process with the claimed run and its own database connection,
which is closed afterwards. It must commit its own writes (uncommitted work is rolled back), tag
ingested rows with ``run.id``, and either return a ``RunResult`` or raise; an exception ends the
run ``failed`` with the error recorded. A run whose task has no handler here also ends ``failed``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from psycopg2.extensions import connection as Connection

from ingestion import onboarding, price_bars, sec_filings
from orchestration.task_runs import RunResult, TaskRun

Handler = Callable[[TaskRun, Connection], RunResult]

HANDLERS: Mapping[str, Handler] = {
    onboarding.TASK_ID: onboarding.onboard_tickers,
    price_bars.TASK_ID: price_bars.ingest_prices,
    sec_filings.TASK_ID: sec_filings.ingest_sec_filings,
}
