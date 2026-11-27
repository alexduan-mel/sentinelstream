"""``ingest_insider_datasets``: every Form 3, 4 and 5 transaction from SEC's Insider Transactions Data Sets.

SEC publishes one ZIP per calendar quarter, about six weeks after it ends, holding every ownership
filing made in that quarter as tab-separated files. The base-rate calculator needs past insider
purchases at every company, not only the watchlist, so the job loads:

* ``SUBMISSION.tsv`` → ``insider_filings``: one row per filing, with the issuer's CIK, name and
  trading symbol as filed, the filing date, and whether it reports a 10b5-1 plan;
* ``REPORTINGOWNER.tsv`` → the filers' names, CIKs, relationships and titles, folded into that row;
* ``NONDERIV_TRANS.tsv`` → ``insider_transactions``: one row per non-derivative transaction.

Everything is kept, all forms and transaction codes; which ones count is decided at calculation
time. Each quarter is replaced whole in one transaction and recorded in ``insider_datasets``, so a
rerun never duplicates. Quarters from the one holding the start of the two-year history are loaded
oldest first, at most ``max_files`` per run. A loaded quarter is not fetched again; to re-load one,
delete its ``insider_datasets`` row.

``known_at``: the datasets give the filing date, not the acceptance time. A filing accepted after
17:30 New York time gets the next business day's date, so acceptance falls before 17:30 on the
filing date at the latest; ``known_at`` is the end of the filing date (midnight New York after it),
which is never early. ADR 0014.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import math
import re
import zipfile
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from psycopg2.extensions import connection as Connection
from psycopg2.extras import execute_values

from ingestion.market_bars import history_start
from ingestion.market_calendar import NEW_YORK
from ingestion.sec_client import SecClient, SecError
from ingestion.splits import quarter_start
from orchestration.task_runs import RunResult, TaskRun

LOGGER = logging.getLogger(__name__)

TASK_ID = "ingest_insider_datasets"
DEFAULT_MAX_FILES = 2
# SEC publishes a quarter about six weeks after it ends; one still missing well after that is an error.
PUBLISH_GRACE_DAYS = 100
_MONTHS = {
    m: i for i, m in enumerate(("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1)
}


_ACCESSION = re.compile(r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")
_CIK = re.compile(r"^[0-9]{10}$")


class DatasetError(ValueError):
    """The ZIP is not a readable Insider Transactions Data Set."""


def quarter_name(day: date) -> str:
    return f"{day.year}q{(day.month - 1) // 3 + 1}"


def quarter_end(name: str) -> date:
    year, q = int(name[:4]), int(name[5])
    return date(year + q // 4, 3 * q % 12 + 1, 1) - timedelta(days=1)


def quarter_names(start: date, end: date) -> list[str]:
    names, current = [], quarter_start(start)
    while current <= end:
        names.append(quarter_name(current))
        current = quarter_end(quarter_name(current)) + timedelta(days=1)
    return names


def parse_sec_date(value: str) -> date | None:
    """``31-JUL-2025`` → 2025-07-31; empty → None."""
    value = value.strip()
    if not value:
        return None
    try:
        day, month, year = value.split("-")
        return date(int(year), _MONTHS[month.upper()], int(day))
    except (ValueError, KeyError) as exc:
        raise ValueError(f"not an SEC date: {value!r}") from exc


def _number(value: str) -> float | None:
    value = value.strip()
    if not value:
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"not a finite number: {value!r}")
    return number


def _flag(value: str) -> bool | None:
    value = value.strip().lower()
    if value in ("1", "true"):
        return True
    if value in ("0", "false"):
        return False
    return None


@dataclass
class ParsedDataset:
    filings: list[tuple] = field(default_factory=list)
    transactions: list[tuple] = field(default_factory=list)
    invalid: int = 0


def _tsv(archive: zipfile.ZipFile, name: str) -> Iterator[dict[str, str]]:
    members = {info.filename.rsplit("/", 1)[-1].upper(): info.filename for info in archive.infolist()}
    if name not in members:
        raise DatasetError(f"dataset has no {name}")
    with archive.open(members[name]) as handle:
        text = io.TextIOWrapper(handle, encoding="utf-8", errors="replace", newline="")
        yield from csv.DictReader(text, delimiter="\t", quoting=csv.QUOTE_NONE)


def parse_dataset(content: bytes) -> ParsedDataset:
    """Rows for ``insider_filings`` and ``insider_transactions`` from one quarter's ZIP."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise DatasetError("dataset is not a ZIP file") from exc
    parsed = ParsedDataset()

    owners: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for row in _tsv(archive, "REPORTINGOWNER.TSV"):
        accession = row.get("ACCESSION_NUMBER", "").strip()
        for key, column in (
            ("names", "RPTOWNERNAME"),
            ("ciks", "RPTOWNERCIK"),
            ("roles", "RPTOWNER_RELATIONSHIP"),
            ("titles", "RPTOWNER_TITLE"),
        ):
            value = (row.get(column) or "").strip()
            if value and value not in owners[accession][key]:
                owners[accession][key].append(value)

    accessions: set[str] = set()
    for row in _tsv(archive, "SUBMISSION.TSV"):
        try:
            accession = row["ACCESSION_NUMBER"].strip()
            filing_date = parse_sec_date(row["FILING_DATE"])
            period = parse_sec_date(row.get("PERIOD_OF_REPORT") or "")
            issuer_cik = row["ISSUERCIK"].strip()
            document_type = row["DOCUMENT_TYPE"].strip()
        except (KeyError, ValueError):
            parsed.invalid += 1
            continue
        if (
            filing_date is None
            or not document_type
            or not _CIK.match(issuer_cik)
            or not _ACCESSION.match(accession)
            or accession in accessions
        ):
            parsed.invalid += 1
            continue
        accessions.add(accession)
        who = owners.get(accession, {})
        parsed.filings.append(
            (
                accession,
                document_type,
                filing_date,
                period,
                issuer_cik,
                (row.get("ISSUERNAME") or "").strip() or None,
                (row.get("ISSUERTRADINGSYMBOL") or "").strip() or None,
                "; ".join(who.get("names", [])) or None,
                who.get("ciks", []),
                "; ".join(who.get("roles", [])) or None,
                "; ".join(who.get("titles", [])) or None,
                _flag(row.get("AFF10B5ONE") or ""),
            )
        )

    for row in _tsv(archive, "NONDERIV_TRANS.TSV"):
        try:
            accession = row["ACCESSION_NUMBER"].strip()
            line = int(row["NONDERIV_TRANS_SK"])
            code = row["TRANS_CODE"].strip()
            trans_date = parse_sec_date(row.get("TRANS_DATE") or "")
            shares = _number(row.get("TRANS_SHARES") or "")
            price = _number(row.get("TRANS_PRICEPERSHARE") or "")
            after = _number(row.get("SHRS_OWND_FOLWNG_TRANS") or "")
        except (KeyError, ValueError):
            parsed.invalid += 1
            continue
        acquired = (row.get("TRANS_ACQUIRED_DISP_CD") or "").strip() or None
        direct = (row.get("DIRECT_INDIRECT_OWNERSHIP") or "").strip() or None
        if (
            accession not in accessions
            or not code
            or acquired not in (None, "A", "D")
            or direct not in (None, "D", "I")
        ):
            parsed.invalid += 1
            continue
        parsed.transactions.append(
            (
                accession,
                line,
                (row.get("SECURITY_TITLE") or "").strip() or None,
                trans_date,
                code,
                acquired,
                shares,
                price,
                after,
                direct,
                _flag(row.get("EQUITY_SWAP_INVOLVED") or ""),
            )
        )
    return parsed


def store_dataset(
    conn: Connection,
    quarter: str,
    *,
    url: str,
    content: bytes,
    last_modified: str | None,
    parsed: ParsedDataset,
    task_run_id: int,
) -> None:
    """Replace the quarter's rows and record it, in one transaction."""
    with conn.cursor() as cursor:
        cursor.execute("DELETE FROM insider_filings WHERE dataset = %s", (quarter,))
        execute_values(
            cursor,
            """
            INSERT INTO insider_filings (accession_no, document_type, filing_date, period_of_report, issuer_cik,
                                         issuer_name, issuer_symbol, owner_names, owner_ciks, owner_relationships,
                                         owner_titles, aff10b5one, dataset)
            VALUES %s
            ON CONFLICT (accession_no) DO NOTHING
            """,
            [(*row, quarter) for row in parsed.filings],
            page_size=2000,
        )
        execute_values(
            cursor,
            """
            INSERT INTO insider_transactions (accession_no, line_sk, security_title, transaction_date, transaction_code,
                                              acquired_disposed, shares, price_per_share, shares_owned_after,
                                              direct_indirect, equity_swap)
            VALUES %s
            ON CONFLICT (accession_no, line_sk) DO NOTHING
            """,
            parsed.transactions,
            page_size=2000,
        )
        cursor.execute(
            """
            INSERT INTO insider_datasets (quarter, url, last_modified, sha256, bytes, filing_count,
                                          transaction_count, invalid_count, task_run_id)
            VALUES (%(quarter)s, %(url)s, %(last_modified)s, %(sha256)s, %(bytes)s,
                    (SELECT count(*) FROM insider_filings WHERE dataset = %(quarter)s),
                    (SELECT count(*) FROM insider_transactions t JOIN insider_filings f USING (accession_no)
                     WHERE f.dataset = %(quarter)s),
                    %(invalid)s, %(run)s)
            ON CONFLICT (quarter) DO UPDATE SET
              url = EXCLUDED.url, last_modified = EXCLUDED.last_modified, sha256 = EXCLUDED.sha256,
              bytes = EXCLUDED.bytes, filing_count = EXCLUDED.filing_count,
              transaction_count = EXCLUDED.transaction_count, invalid_count = EXCLUDED.invalid_count,
              task_run_id = EXCLUDED.task_run_id, fetched_at = NOW()
            """,
            {
                "quarter": quarter,
                "url": url,
                "last_modified": last_modified,
                "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
                "invalid": parsed.invalid,
                "run": task_run_id,
            },
        )
    conn.commit()


def _max_files(payload: dict[str, Any]) -> int:
    raw = payload.get("max_files", DEFAULT_MAX_FILES)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        raise ValueError(f"max_files must be a positive integer, got {raw!r}")
    return value


def _loaded_quarters(conn: Connection) -> set[str]:
    with conn.cursor() as cursor:
        cursor.execute("SELECT quarter FROM insider_datasets")
        loaded = {quarter for (quarter,) in cursor.fetchall()}
    conn.commit()
    return loaded


def ingest_insider_datasets(
    run: TaskRun, conn: Connection, *, sec: SecClient | None = None, now: datetime | None = None
) -> RunResult:
    now = now or datetime.now(UTC)
    today = now.astimezone(NEW_YORK).date()
    max_files = _max_files(run.payload)
    # Quarters that have ended, from the one holding the start of the two-year history.
    wanted = [q for q in quarter_names(history_start(today), today) if quarter_end(q) < today]
    loaded = _loaded_quarters(conn)
    meta: dict[str, Any] = {"loaded": [], "not_published": [], "invalid_rows": 0, "errors": []}

    todo_all = [q for q in wanted if q not in loaded]
    if not todo_all:
        return RunResult(status="succeeded", meta={**meta, "remaining": 0, "message": "already_current"})

    client = sec or SecClient.from_env()
    filings = transactions = 0
    try:
        try:
            links = client.insider_dataset_links()
        except SecError as exc:
            return RunResult(status="failed", error_message=str(exc), meta={**meta, "errors": [str(exc)]})
        for quarter in todo_all:
            if quarter not in links:
                late = (today - quarter_end(quarter)).days > PUBLISH_GRACE_DAYS
                meta["not_published"].append(quarter)
                if late:
                    meta["errors"].append(
                        f"{quarter} dataset is not listed {PUBLISH_GRACE_DAYS} days after the quarter"
                    )
        todo = [q for q in todo_all if q in links][:max_files]
        for quarter in todo:
            try:
                response = client.insider_dataset(links[quarter])
                parsed = parse_dataset(response.content)
            except (SecError, DatasetError) as exc:
                meta["errors"].append(f"{quarter}: {exc}")
                LOGGER.warning("insider_dataset_failed run_id=%d quarter=%s error=%s", run.id, quarter, exc)
                break
            store_dataset(
                conn,
                quarter,
                url=links[quarter],
                content=response.content,
                last_modified=response.headers.get("Last-Modified"),
                parsed=parsed,
                task_run_id=run.id,
            )
            filings += len(parsed.filings)
            transactions += len(parsed.transactions)
            meta["invalid_rows"] += parsed.invalid
            meta["loaded"].append(quarter)
            LOGGER.info(
                "insider_dataset_loaded run_id=%d quarter=%s filings=%d transactions=%d invalid=%d",
                run.id,
                quarter,
                len(parsed.filings),
                len(parsed.transactions),
                parsed.invalid,
            )
    finally:
        if sec is None:
            client.close()

    loaded_now = _loaded_quarters(conn)
    meta["remaining"] = len([q for q in wanted if q not in loaded_now])
    meta["filings"], meta["transactions"] = filings, transactions
    if not meta["errors"]:
        status = "succeeded"
    elif meta["loaded"]:
        status = "partial"
    else:
        status = "failed"
    return RunResult(
        status=status,
        fetched_count=transactions,
        inserted_count=transactions,
        error_message="; ".join(meta["errors"]) or None,
        meta=meta,
    )
