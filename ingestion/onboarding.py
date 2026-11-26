"""``onboard_tickers``: tie new tickers to their SEC issuer and load two years of history (ADR 0010).

An instrument moves pending → backfilling → ready, or to failed when SEC cannot tie the symbol to one
company. For a watchlist equity each run:

1. **Identity** (pending only, or any status with ``force_refresh``): finds the symbol in SEC's
   ticker map, confirms it against the issuer's submissions record, rejects funds, and detects the
   filer type (``sec_identity``). The instrument then becomes ``backfilling``, which puts it in the
   SEC poller's watch set at once, so nothing filed during the backfill is missed.
2. **Filings**: stores the submissions record's recent filings, plus older pages back two years, as
   history (``is_backfill = TRUE``), so none of it is pushed to Slack.
3. **Prices**: Massive's details for the symbol first. If Massive ties it to a different CIK than SEC
   does, no bars are loaded. Otherwise two years of split-adjusted daily bars, starting no earlier
   than the security's listing date: a symbol can be reused (SPCX traded as an ETF before SpaceX
   listed under it on 12 Jun 2026), so any stored bars from before the listing are deleted.
4. **XBRL facts**: every company fact filed in the last two years (``filings.xbrl``).
5. **Ready.**

A benchmark ETF only needs step 3. Every step upserts on stable keys, so a rerun repeats nothing and
a run cut off part-way resumes where it stopped. A transient error (SEC or Massive unreachable) leaves
the instrument in its current status with the error recorded, and the run ends partial or failed; a
symbol SEC rejects is marked failed, which is the user's input being wrong rather than the system
failing, so the run still succeeds.

Which instruments: the run's tickers if it names any, then every other active instrument still
pending or backfilling. The run keeps looking until none is left, so a ticker added while it was
running (whose own run was skipped under the task lock) is onboarded too. Each instrument is tried
once per run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from psycopg2.extensions import connection as Connection
from psycopg2.extras import execute_values

from filings.xbrl import XbrlFact, parse_company_facts
from ingestion.market_calendar import latest_completed_session
from ingestion.massive_client import MassiveClient, MassiveError
from ingestion.price_bars import bar_rows, upsert_bars
from ingestion.sec_client import SecClient, SecError
from ingestion.sec_filings import normalise_filing_columns, normalise_recent_filings, upsert_filings
from ingestion.sec_identity import IdentityError, resolve_issuer
from orchestration.task_runs import RunResult, TaskRun

LOGGER = logging.getLogger(__name__)

TASK_ID = "onboard_tickers"
HISTORY = timedelta(days=730)


@dataclass(frozen=True)
class _Instrument:
    id: int
    symbol: str
    asset_type: str
    role: str
    cik: str | None
    status: str

    @property
    def is_equity(self) -> bool:
        return self.asset_type == "equity"


class _StepError(RuntimeError):
    """A transient failure in one step: the instrument keeps its status and is retried later."""

    def __init__(self, step: str, error: Exception) -> None:
        super().__init__(f"{step}: {error}")
        self.step = step


def _candidates(conn: Connection, tickers: list[str], tried: set[int]) -> list[_Instrument]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, symbol, asset_type, role, cik, onboarding_status
            FROM instruments
            WHERE active
              AND (symbol = ANY(%(tickers)s::text[]) OR onboarding_status IN ('pending', 'backfilling'))
              AND NOT id = ANY(%(tried)s::bigint[])
            ORDER BY symbol = ANY(%(tickers)s::text[]) DESC, symbol
            """,
            {"tickers": tickers, "tried": sorted(tried)},
        )
        rows = cursor.fetchall()
    conn.commit()
    return [_Instrument(*row) for row in rows]


def _set_status(conn: Connection, instrument: _Instrument, status: str, error: str | None = None) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            "UPDATE instruments SET onboarding_status = %s, onboarding_error = %s, updated_at = NOW() WHERE id = %s",
            (status, error, instrument.id),
        )
    conn.commit()
    LOGGER.info("onboarding_status symbol=%s status=%s error=%s", instrument.symbol, status, error or "-")


def _record_error(conn: Connection, instrument: _Instrument, error: str) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            "UPDATE instruments SET onboarding_error = %s, updated_at = NOW() WHERE id = %s", (error, instrument.id)
        )
    conn.commit()


class _Run:
    """One run's state: lazily created clients and the ticker map, fetched at most once."""

    def __init__(
        self, run: TaskRun, conn: Connection, sec: SecClient | None, massive: MassiveClient | None, now: datetime
    ) -> None:
        self.run = run
        self.conn = conn
        self._sec = sec
        self._massive = massive
        self._owns_sec = sec is None
        self._owns_massive = massive is None
        self._ticker_map: list[dict[str, Any]] | None = None
        self.target = latest_completed_session(now)
        self.since = (now - HISTORY).date()
        self.meta: dict[str, Any] = {"ready": [], "rejected": [], "errors": [], "loaded": {}}

    @property
    def sec(self) -> SecClient:
        if self._sec is None:
            self._sec = SecClient.from_env()
        return self._sec

    @property
    def massive(self) -> MassiveClient:
        if self._massive is None:
            self._massive = MassiveClient.from_env()
        return self._massive

    def ticker_map(self) -> list[dict[str, Any]]:
        if self._ticker_map is None:
            self._ticker_map = self.sec.ticker_map()
        return self._ticker_map

    def close(self) -> None:
        if self._owns_sec and self._sec is not None:
            self._sec.close()
        if self._owns_massive and self._massive is not None:
            self._massive.close()

    # --- steps -------------------------------------------------------------------------------------

    def identify(self, instrument: _Instrument) -> tuple[str, dict[str, Any]] | None:
        """(cik, submissions) once the instrument is backfilling; None if SEC rejected the symbol."""
        conn = self.conn
        if instrument.cik and not self.run.force_refresh:
            try:
                submissions = self.sec.submissions(instrument.cik)
            except SecError as exc:
                raise _StepError("filings", exc) from None
            if instrument.status == "pending":
                _set_status(conn, instrument, "backfilling")
            return instrument.cik, submissions
        try:
            issuer, submissions = resolve_issuer(instrument.symbol, self.ticker_map(), self.sec)
            if issuer.filer_type is None:
                raise IdentityError(
                    f"SEC filings for CIK {issuer.cik} do not show whether {instrument.symbol} is a domestic "
                    "filer or a foreign private issuer"
                )
        except IdentityError as exc:
            _set_status(conn, instrument, "failed", str(exc))
            self.meta["rejected"].append({"symbol": instrument.symbol, "reason": str(exc)})
            return None
        except SecError as exc:
            raise _StepError("identity", exc) from None
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE instruments
                SET cik = %s, name = %s, exchange = %s, filer_type = %s, onboarding_status = 'backfilling',
                    onboarding_error = NULL, updated_at = NOW()
                WHERE id = %s
                """,
                (issuer.cik, issuer.name or None, issuer.exchange, issuer.filer_type, instrument.id),
            )
        conn.commit()
        LOGGER.info(
            "onboarding_status symbol=%s status=backfilling cik=%s filer_type=%s",
            instrument.symbol,
            issuer.cik,
            issuer.filer_type,
        )
        return issuer.cik, submissions

    def filings(self, cik: str, submissions: dict[str, Any]) -> int:
        """Recent filings plus older pages back to ``since``, all stored as history."""
        records, _invalid = normalise_recent_filings(cik, submissions)
        oldest = min((record.filed_date for record in records), default=None)
        if oldest is None or oldest > self.since:
            for page in (submissions.get("filings") or {}).get("files") or []:
                if str(page.get("filingTo") or "") < self.since.isoformat():
                    continue
                try:
                    older, _invalid = normalise_filing_columns(cik, self.sec.submissions_page(str(page["name"])))
                except (SecError, KeyError, ValueError) as exc:
                    raise _StepError("filings", exc) from None
                records.extend(record for record in older if record.filed_date >= self.since)
        _new, linked = upsert_filings(self.conn, records, is_backfill=True, task_run_id=self.run.id)
        return linked

    def prices(self, instrument: _Instrument, cik: str | None) -> int:
        date_from = self.target - HISTORY
        try:
            details = self.massive.ticker_details(instrument.symbol)
        except MassiveError as exc:
            raise _StepError("prices", exc) from None
        if details is not None:
            if cik and details.cik and details.cik != cik:
                raise _StepError(
                    "prices",
                    RuntimeError(
                        f"Massive lists {instrument.symbol} under CIK {details.cik} ({details.name}), "
                        f"but SEC under {cik}"
                    ),
                )
            if details.list_date is not None:
                self._drop_bars_before(instrument, details.list_date)
                date_from = max(date_from, details.list_date)
        try:
            payload = self.massive.daily_bars(instrument.symbol, date_from, self.target)
        except MassiveError as exc:
            raise _StepError("prices", exc) from None
        rows, _invalid = bar_rows(
            instrument.id, payload, date_from=date_from, date_to=self.target, task_run_id=self.run.id
        )
        inserted, updated = upsert_bars(self.conn, rows)
        return inserted + updated

    def _drop_bars_before(self, instrument: _Instrument, list_date: date) -> None:
        """Bars dated before the listing came from an earlier security that used the symbol."""
        with self.conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM price_bars WHERE instrument_id = %s AND trading_date < %s", (instrument.id, list_date)
            )
            dropped = cursor.rowcount
        self.conn.commit()
        if dropped:
            LOGGER.warning(
                "price_bars_before_listing_dropped symbol=%s list_date=%s count=%d",
                instrument.symbol,
                list_date.isoformat(),
                dropped,
            )
            self.meta.setdefault("bars_before_listing_dropped", {})[instrument.symbol] = dropped

    def xbrl(self, cik: str) -> int:
        try:
            payload = self.sec.company_facts(cik)
        except SecError as exc:
            raise _StepError("xbrl", exc) from None
        if payload is None:
            return 0
        facts, invalid = parse_company_facts(payload, filed_since=self.since)
        if invalid:
            LOGGER.warning("xbrl_facts_invalid cik=%s count=%d", cik, invalid)
        return store_xbrl_facts(self.conn, cik, facts, task_run_id=self.run.id)


def store_xbrl_facts(conn: Connection, cik: str, facts: list[XbrlFact], *, task_run_id: int) -> int:
    """Insert facts not stored yet and commit; returns how many were new.

    ``known_at`` is the filing's EDGAR acceptance time when the filing is stored. Otherwise it is
    midnight New York time after the filing date: EDGAR accepts filings until 22:00, so that is never
    earlier than the real acceptance (ground rule 2).
    """
    if not facts:
        return 0
    with conn.cursor() as cursor:
        inserted = execute_values(
            cursor,
            """
            INSERT INTO xbrl_facts (cik, taxonomy, concept, unit, period_start, period_end, value, accession_no,
                                    fiscal_year, fiscal_period, form, filed_date, frame, known_at, task_run_id)
            SELECT v.cik, v.taxonomy, v.concept, v.unit, v.period_start, v.period_end, v.value, v.accession_no,
                   v.fiscal_year, v.fiscal_period, v.form, v.filed_date, v.frame,
                   COALESCE(f.accepted_at, (v.filed_date + 1)::timestamp AT TIME ZONE 'America/New_York'),
                   v.task_run_id
            FROM (VALUES %s) AS v (cik, taxonomy, concept, unit, period_start, period_end, value, accession_no,
                                   fiscal_year, fiscal_period, form, filed_date, frame, task_run_id)
            LEFT JOIN filings f ON f.accession_no = v.accession_no
            ON CONFLICT (cik, taxonomy, concept, unit, accession_no, period_end,
                         (COALESCE(period_start, DATE '0001-01-01')))
            DO NOTHING
            RETURNING 1
            """,
            [
                (
                    cik,
                    fact.taxonomy,
                    fact.concept,
                    fact.unit,
                    fact.period_start,
                    fact.period_end,
                    fact.value,
                    fact.accession_no,
                    fact.fiscal_year,
                    fact.fiscal_period,
                    fact.form,
                    fact.filed_date,
                    fact.frame,
                    task_run_id,
                )
                for fact in facts
            ],
            template=(
                "(%s, %s, %s, %s, %s::date, %s::date, %s::numeric, %s, %s::integer, %s, %s, %s::date, %s, %s::bigint)"
            ),
            page_size=1000,
            fetch=True,
        )
    conn.commit()
    return len(inserted)


def _onboard(state: _Run, instrument: _Instrument) -> None:
    loaded: dict[str, int] = {}
    cik: str | None = None
    if instrument.is_equity:
        identified = state.identify(instrument)
        if identified is None:
            return
        cik, submissions = identified
        loaded["filings"] = state.filings(cik, submissions)
    loaded["price_bars"] = state.prices(instrument, cik)
    if instrument.is_equity:
        loaded["xbrl_facts"] = state.xbrl(cik)
    _set_status(state.conn, instrument, "ready")
    state.meta["ready"].append(instrument.symbol)
    state.meta["loaded"][instrument.symbol] = loaded


def onboard_tickers(
    run: TaskRun,
    conn: Connection,
    *,
    sec: SecClient | None = None,
    massive: MassiveClient | None = None,
    now: datetime | None = None,
) -> RunResult:
    state = _Run(run, conn, sec, massive, now or datetime.now(UTC))
    tried: set[int] = set()
    tried_symbols: set[str] = set()
    failed = 0
    try:
        while batch := _candidates(conn, run.tickers, tried):
            for instrument in batch:
                tried.add(instrument.id)
                tried_symbols.add(instrument.symbol)
                try:
                    _onboard(state, instrument)
                except _StepError as exc:
                    failed += 1
                    message = str(exc)
                    _record_error(conn, instrument, message)
                    state.meta["errors"].append(f"{instrument.symbol}: {message}")
                    LOGGER.warning(
                        "onboarding_step_failed symbol=%s step=%s error=%s", instrument.symbol, exc.step, exc
                    )
    finally:
        state.close()

    meta = state.meta
    meta["since"] = state.since.isoformat()
    if run.tickers:
        meta["unknown_tickers"] = sorted(set(run.tickers) - tried_symbols)
    if not tried:
        return RunResult(status="succeeded", meta={**meta, "message": "nothing_to_onboard"})
    if failed == 0:
        status = "succeeded"
    elif failed < len(tried):
        status = "partial"
    else:
        status = "failed"
    return RunResult(
        status=status,
        # Rows loaded across filings links, price bars and XBRL facts (reruns count again).
        fetched_count=sum(sum(counts.values()) for counts in meta["loaded"].values()),
        error_message="; ".join(meta["errors"]) or None,
        meta=meta,
    )
