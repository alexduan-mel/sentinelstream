"""``ingest_sec_filings``: poll EDGAR submissions for watchlist issuers and record new filings.

New in this repo (the prototype never stored filings). Each run:

1. Resolves the SEC identity of active watchlist equities that have no CIK yet (``sec_identity``).
   A symbol SEC cannot match is marked ``onboarding_status = 'failed'`` with the reason, and is not
   retried until a run with ``force_refresh``.
2. Fetches each issuer's submissions record once (share classes share a CIK) and upserts the recent
   filings by accession number, so reruns only add what is new.

``known_at`` is EDGAR's acceptance time (ADR 0004). An issuer's first poll records its existing
filings with ``is_backfill = TRUE``, so alerting (M1) can tell history from news.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from psycopg2.extensions import connection as Connection
from psycopg2.extras import execute_values

from ingestion.market_calendar import NEW_YORK
from ingestion.sec_client import SecClient, SecError
from ingestion.sec_identity import IdentityError, Issuer, resolve_issuer
from orchestration.task_runs import RunResult, TaskRun

LOGGER = logging.getLogger(__name__)

TASK_ID = "ingest_sec_filings"
_ACCESSION = re.compile(r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")
# A parsed acceptance time this far past the fetch means the time zone reading is wrong (ADR 0004).
_FUTURE_TOLERANCE = timedelta(minutes=5)


@dataclass(frozen=True)
class FilingRecord:
    accession_no: str
    cik: str
    form_type: str
    items: list[str]
    filed_date: date
    report_date: date | None
    primary_document: str | None
    is_xbrl: bool
    accepted_at: datetime


def parse_acceptance(value: str) -> datetime:
    """EDGAR's ``acceptanceDateTime`` as an aware datetime.

    The submissions API writes New York wall-clock time with a misleading ``Z`` suffix: a 10-K
    accepted at 18:08 shows as ``...T18:08:27.000Z`` and gets the next day's filing date, because
    filings accepted after 17:30 Eastern are dated the next business day. Reading it as UTC would put
    ``known_at`` four or five hours early, which is lookahead. An explicit numeric offset is trusted.
    """
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1]
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is not None:
        return parsed
    # fold=1: in the repeated hour at the end of daylight time, take the later instant.
    return parsed.replace(tzinfo=NEW_YORK, fold=1)


def _optional_date(value: Any) -> date | None:
    text = str(value or "").strip()
    return date.fromisoformat(text) if text else None


def normalise_recent_filings(cik: str, submissions: dict[str, Any]) -> tuple[list[FilingRecord], int]:
    """The filings in a submissions record's ``recent`` block, plus how many rows were invalid."""
    recent = (submissions.get("filings") or {}).get("recent") or {}
    accessions = recent.get("accessionNumber") or []
    columns = ("filingDate", "reportDate", "acceptanceDateTime", "form", "items", "primaryDocument", "isXBRL")
    values = {name: recent.get(name) or [] for name in columns}
    if any(len(column) != len(accessions) for column in values.values()):
        raise SecError(f"SEC submissions for CIK {cik} have filing columns of different lengths")

    records: list[FilingRecord] = []
    invalid = 0
    for index, accession in enumerate(accessions):
        try:
            accession_no = str(accession).strip()
            form_type = str(values["form"][index]).strip()
            if not _ACCESSION.match(accession_no) or not form_type:
                raise ValueError("bad accession number or form")
            items = [item.strip() for item in str(values["items"][index] or "").split(",") if item.strip()]
            records.append(
                FilingRecord(
                    accession_no=accession_no,
                    cik=cik,
                    form_type=form_type,
                    items=items,
                    filed_date=date.fromisoformat(str(values["filingDate"][index])),
                    report_date=_optional_date(values["reportDate"][index]),
                    primary_document=str(values["primaryDocument"][index] or "").strip() or None,
                    is_xbrl=bool(values["isXBRL"][index]),
                    accepted_at=parse_acceptance(str(values["acceptanceDateTime"][index])),
                )
            )
        except (TypeError, ValueError) as exc:
            invalid += 1
            LOGGER.warning("sec_filing_invalid cik=%s index=%d error=%s", cik, index, exc)
    return records, invalid


def _watch_instruments(conn: Connection, tickers: list[str]) -> list[tuple[int, str, str | None, str | None]]:
    """Active watchlist equities (id, symbol, cik, onboarding_error), narrowed to ``tickers`` if given."""
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, symbol, cik, onboarding_error
            FROM instruments
            WHERE active AND asset_type = 'equity' AND role = 'watchlist'
              AND (cardinality(%(tickers)s::text[]) = 0 OR symbol = ANY(%(tickers)s::text[]))
            ORDER BY symbol
            """,
            {"tickers": tickers},
        )
        rows = cursor.fetchall()
    conn.commit()
    return rows


def _record_identity(conn: Connection, instrument_id: int, issuer: Issuer) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            UPDATE instruments
            SET cik = %s, name = %s, exchange = %s, filer_type = %s, onboarding_error = NULL,
                onboarding_status = CASE WHEN onboarding_status = 'failed' THEN 'pending' ELSE onboarding_status END,
                updated_at = NOW()
            WHERE id = %s
            """,
            (issuer.cik, issuer.name or None, issuer.exchange, issuer.filer_type, instrument_id),
        )
    conn.commit()


def _record_identity_failure(conn: Connection, instrument_id: int, message: str) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            "UPDATE instruments SET onboarding_status = 'failed', onboarding_error = %s, updated_at = NOW() "
            "WHERE id = %s",
            (message, instrument_id),
        )
    conn.commit()


def upsert_filings(conn: Connection, records: list[FilingRecord], *, is_backfill: bool, task_run_id: int) -> int:
    """Insert filings not seen before and commit; returns how many were new. Existing rows are kept
    as first recorded, so ``first_seen_at`` stays the time the poller first saw the filing."""
    if not records:
        return 0
    rows = [
        (
            r.accession_no,
            r.cik,
            r.form_type,
            r.items,
            r.filed_date,
            r.report_date,
            r.primary_document,
            r.is_xbrl,
            r.accepted_at,
            r.accepted_at,
            is_backfill,
            task_run_id,
        )
        for r in records
    ]
    with conn.cursor() as cursor:
        inserted = execute_values(
            cursor,
            """
            INSERT INTO filings (accession_no, cik, form_type, items, filed_date, report_date, primary_document,
                                 is_xbrl, accepted_at, known_at, is_backfill, task_run_id)
            VALUES %s
            ON CONFLICT (accession_no) DO NOTHING
            RETURNING id
            """,
            rows,
            fetch=True,
        )
    conn.commit()
    return len(inserted)


def _has_filings(conn: Connection, cik: str) -> bool:
    with conn.cursor() as cursor:
        cursor.execute("SELECT EXISTS (SELECT 1 FROM filings WHERE cik = %s)", (cik,))
        found = bool(cursor.fetchone()[0])
    conn.commit()
    return found


def ingest_sec_filings(run: TaskRun, conn: Connection, *, sec: SecClient | None = None) -> RunResult:
    instruments = _watch_instruments(conn, run.tickers)
    meta: dict[str, Any] = {"resolved": [], "unresolved": [], "errors": [], "issuers": {}, "invalid_rows": 0}
    if run.tickers:
        known = {symbol for _id, symbol, _cik, _error in instruments}
        meta["unknown_tickers"] = sorted(set(run.tickers) - known)
    if not instruments:
        return RunResult(status="succeeded", meta={**meta, "message": "no_watchlist_equities"})

    client = sec or SecClient.from_env()
    attempted = failed = 0
    fetched = inserted = 0
    future_acceptance = 0
    try:
        # 1. Identity for symbols without a CIK.
        submissions_by_cik: dict[str, dict[str, Any]] = {}
        ciks: dict[str, list[str]] = {}
        ticker_map: list[dict[str, Any]] | None = None
        for instrument_id, symbol, cik, onboarding_error in instruments:
            if cik:
                ciks.setdefault(cik, []).append(symbol)
                continue
            if onboarding_error and not run.force_refresh:
                meta["unresolved"].append({"symbol": symbol, "reason": onboarding_error})
                continue
            attempted += 1
            try:
                if ticker_map is None:
                    ticker_map = client.ticker_map()
                issuer, submissions = resolve_issuer(symbol, ticker_map, client)
            except IdentityError as exc:
                failed += 1
                _record_identity_failure(conn, instrument_id, str(exc))
                meta["unresolved"].append({"symbol": symbol, "reason": str(exc)})
                LOGGER.warning("sec_identity_unresolved run_id=%d symbol=%s reason=%s", run.id, symbol, exc)
                continue
            except SecError as exc:  # transient: not recorded on the instrument, retried next run
                failed += 1
                meta["errors"].append(f"{symbol}: {exc}")
                continue
            _record_identity(conn, instrument_id, issuer)
            meta["resolved"].append(
                {"symbol": symbol, "cik": issuer.cik, "name": issuer.name, "filer_type": issuer.filer_type}
            )
            LOGGER.info(
                "sec_identity_resolved run_id=%d symbol=%s cik=%s filer_type=%s",
                run.id,
                symbol,
                issuer.cik,
                issuer.filer_type,
            )
            submissions_by_cik[issuer.cik] = submissions
            ciks.setdefault(issuer.cik, []).append(symbol)

        # 2. Filings, once per issuer.
        for cik, symbols in sorted(ciks.items()):
            attempted += 1
            try:
                submissions = submissions_by_cik.get(cik) or client.submissions(cik)
                fetched_at = datetime.now(UTC)
                records, invalid = normalise_recent_filings(cik, submissions)
            except SecError as exc:
                failed += 1
                meta["errors"].append(f"{'/'.join(symbols)} (CIK {cik}): {exc}")
                continue
            early = [r for r in records if r.accepted_at > fetched_at + _FUTURE_TOLERANCE]
            if early:
                future_acceptance += len(early)
                LOGGER.warning("sec_acceptance_in_future cik=%s count=%d", cik, len(early))
            is_backfill = run.run_mode == "backfill" or not _has_filings(conn, cik)
            new = upsert_filings(conn, records, is_backfill=is_backfill, task_run_id=run.id)
            fetched += len(records)
            inserted += new
            meta["invalid_rows"] += invalid
            meta["issuers"][cik] = {"symbols": symbols, "fetched": len(records), "new": new, "backfill": is_backfill}
    finally:
        if sec is None:
            client.close()

    if future_acceptance:
        meta["future_acceptance"] = future_acceptance
    if failed == 0:
        status = "succeeded"
    elif failed < attempted:
        status = "partial"
    else:
        status = "failed"
    error = "; ".join([*meta["errors"], *(f"{u['symbol']}: {u['reason']}" for u in meta["unresolved"])])
    return RunResult(
        status=status,
        fetched_count=fetched,
        inserted_count=inserted,
        deduped_count=fetched - inserted,
        error_message=(error or None) if status != "succeeded" else None,
        meta=meta,
    )
