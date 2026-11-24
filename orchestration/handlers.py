"""Which code runs for each ``task_id``. Replaces the prototype's subprocess dispatch (task_dispatch.py).

A handler is called in the worker process with the claimed run and its own database connection,
which is closed afterwards. It must commit its own writes (uncommitted work is rolled back), tag
ingested rows with ``run.id``, and either return a ``RunResult`` or raise; an exception ends the
run ``failed`` with the error recorded. A run whose task has no handler here also ends ``failed``.

The ingestion handlers (``ingest_sec_filings``, ``ingest_prices``) are registered when their code is
carried over.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from psycopg2.extensions import connection as Connection

from orchestration.task_runs import RunResult, TaskRun

Handler = Callable[[TaskRun, Connection], RunResult]

HANDLERS: Mapping[str, Handler] = {}
