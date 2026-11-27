"""SEC Insider Transactions Data Sets: parsing, ``ingest_insider_datasets`` and as-of reads. No network."""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from ingestion.http import Pacer
from ingestion.insider_datasets import (
    DatasetError,
    ingest_insider_datasets,
    parse_dataset,
    parse_sec_date,
    quarter_end,
    quarter_names,
)
from ingestion.market_calendar import NEW_YORK
from ingestion.sec_client import SecClient, SecConfigurationError, SecError
from orchestration.task_runs import TaskRun, enqueue_run
from research.asof import AsOf

UA = "SentinelStream tests test@example.com"
# Friday 27 Nov 2026: quarters 2024q4 to 2026q3 have ended.
NOW = datetime(2026, 11, 27, 15, 0, tzinfo=UTC)
WANTED = ["2024q4", "2025q1", "2025q2", "2025q3", "2025q4", "2026q1", "2026q2", "2026q3"]

SUBMISSION_COLS = [
    "ACCESSION_NUMBER",
    "FILING_DATE",
    "PERIOD_OF_REPORT",
    "DATE_OF_ORIG_SUB",
    "DOCUMENT_TYPE",
    "ISSUERCIK",
    "ISSUERNAME",
    "ISSUERTRADINGSYMBOL",
    "REMARKS",
    "AFF10B5ONE",
]
OWNER_COLS = ["ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNERNAME", "RPTOWNER_RELATIONSHIP", "RPTOWNER_TITLE"]
TRANS_COLS = [
    "ACCESSION_NUMBER",
    "NONDERIV_TRANS_SK",
    "SECURITY_TITLE",
    "TRANS_DATE",
    "TRANS_CODE",
    "EQUITY_SWAP_INVOLVED",
    "TRANS_SHARES",
    "TRANS_PRICEPERSHARE",
    "TRANS_ACQUIRED_DISP_CD",
    "SHRS_OWND_FOLWNG_TRANS",
    "DIRECT_INDIRECT_OWNERSHIP",
]


def _tsv(cols: list[str], rows: list[dict]) -> str:
    lines = ["\t".join(cols)] + ["\t".join(str(row.get(c, "")) for c in cols) for row in rows]
    return "\n".join(lines) + "\n"


def _zip(submissions: list[dict], owners: list[dict], transactions: list[dict], *, skip: str | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, cols, rows in (
            ("SUBMISSION.tsv", SUBMISSION_COLS, submissions),
            ("REPORTINGOWNER.tsv", OWNER_COLS, owners),
            ("NONDERIV_TRANS.tsv", TRANS_COLS, transactions),
        ):
            if name != skip:
                archive.writestr(name, _tsv(cols, rows))
        archive.writestr("FORM_345_readme.htm", "<html></html>")
    return buffer.getvalue()


def _quarter_zip(quarter: str) -> bytes:
    """One Form 4 purchase dated in the middle of the quarter, with a serial accession per quarter."""
    end = quarter_end(quarter)
    filed = end - timedelta(days=40)
    accession = f"0001045810-{quarter[2:4]}-{int(quarter[5]) * 1000:06d}"
    stamp = filed.strftime("%d-%b-%Y").upper()
    return _zip(
        [
            {
                "ACCESSION_NUMBER": accession,
                "FILING_DATE": stamp,
                "DOCUMENT_TYPE": "4",
                "ISSUERCIK": "0001045810",
                "ISSUERNAME": "NVIDIA CORP",
                "ISSUERTRADINGSYMBOL": "NVDA",
                "AFF10B5ONE": "0",
            }
        ],
        [
            {
                "ACCESSION_NUMBER": accession,
                "RPTOWNERCIK": "0001197649",
                "RPTOWNERNAME": "Doe Jane",
                "RPTOWNER_RELATIONSHIP": "Director",
            }
        ],
        [
            {
                "ACCESSION_NUMBER": accession,
                "NONDERIV_TRANS_SK": "1",
                "SECURITY_TITLE": "Common Stock",
                "TRANS_DATE": stamp,
                "TRANS_CODE": "P",
                "TRANS_SHARES": "1000.0",
                "TRANS_PRICEPERSHARE": "150.5",
                "TRANS_ACQUIRED_DISP_CD": "A",
                "SHRS_OWND_FOLWNG_TRANS": "5000.0",
                "DIRECT_INDIRECT_OWNERSHIP": "D",
            }
        ],
    )


class FakeSec:
    """The datasets index page and one ZIP per listed quarter, in two folders as on sec.gov."""

    def __init__(self, quarters: list[str]) -> None:
        self.zips = {q: _quarter_zip(q) for q in quarters}
        self.requests: list[httpx.Request] = []

    def path(self, quarter: str) -> str:
        folder = "datastandardsinnovation" if quarter >= "2026q2" else "structureddata"
        return f"/files/{folder}/data/insider-transactions-data-sets/{quarter}_form345.zip"

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("insider-transactions-data-sets"):
            links = "".join(f'<a href="{self.path(q)}">{q}</a>' for q in sorted(self.zips))
            return httpx.Response(200, text=f"<html><body>{links}</body></html>")
        for quarter, content in self.zips.items():
            if request.url.path == self.path(quarter):
                return httpx.Response(200, content=content, headers={"Last-Modified": "Tue, 18 Nov 2025 12:45:46 GMT"})
        return httpx.Response(404)

    def client(self) -> SecClient:
        return SecClient(UA, transport=httpx.MockTransport(self.handler), pacer=Pacer(0), base_delay=0)

    def downloads(self) -> list[str]:
        return [r.url.path.rsplit("/", 1)[1][:6] for r in self.requests if r.url.path.endswith(".zip")]


def _run(conn, **payload) -> TaskRun:
    run_id = enqueue_run(
        conn,
        "ingest_insider_datasets",
        trigger_source="test",
        run_mode="manual",
        requested_by="pytest",
        payload=payload,
    )
    conn.commit()
    return TaskRun(run_id, "ingest_insider_datasets", "t", "test", "manual", "pytest", [], None, False, payload)


def _count(conn, table: str) -> int:
    with conn.cursor() as cursor:
        cursor.execute(f"SELECT count(*) FROM {table}")
        (count,) = cursor.fetchone()
    conn.commit()
    return count


# --- helpers and parsing ---------------------------------------------------------------------------


def test_dates_and_quarters() -> None:
    assert parse_sec_date("31-JUL-2025") == date(2025, 7, 31)
    assert parse_sec_date(" ") is None
    with pytest.raises(ValueError):
        parse_sec_date("2025-07-31")
    assert quarter_end("2024q4") == date(2024, 12, 31)
    assert quarter_end("2025q1") == date(2025, 3, 31)
    assert quarter_names(date(2024, 11, 27), date(2025, 4, 1)) == ["2024q4", "2025q1", "2025q2"]


def test_parse_folds_owners_into_the_filing_and_drops_bad_rows() -> None:
    content = _zip(
        [
            {
                "ACCESSION_NUMBER": "0001104659-24-132975",
                "FILING_DATE": "02-JAN-2025",
                "PERIOD_OF_REPORT": "31-DEC-2024",
                "DOCUMENT_TYPE": "4",
                "ISSUERCIK": "0001393311",
                "ISSUERNAME": "Public Storage",
                "ISSUERTRADINGSYMBOL": "NYSE: PSA",
                "AFF10B5ONE": "1",
            },
            {
                "ACCESSION_NUMBER": "0001104659-24-000001",
                "FILING_DATE": "soon",
                "DOCUMENT_TYPE": "4",
                "ISSUERCIK": "0001393311",
            },  # bad date
            {
                "ACCESSION_NUMBER": "0001104659-24-000002",
                "FILING_DATE": "02-JAN-2025",
                "DOCUMENT_TYPE": "4",
                "ISSUERCIK": "1393311",
            },  # CIK not 10 digits
        ],
        [
            {
                "ACCESSION_NUMBER": "0001104659-24-132975",
                "RPTOWNERCIK": "0000000001",
                "RPTOWNERNAME": "Fund A LP",
                "RPTOWNER_RELATIONSHIP": "TenPercentOwner",
            },
            {
                "ACCESSION_NUMBER": "0001104659-24-132975",
                "RPTOWNERCIK": "0000000002",
                "RPTOWNERNAME": "Doe Jane",
                "RPTOWNER_RELATIONSHIP": "Director",
                "RPTOWNER_TITLE": "Chair",
            },
        ],
        [
            {
                "ACCESSION_NUMBER": "0001104659-24-132975",
                "NONDERIV_TRANS_SK": "8129327",
                "SECURITY_TITLE": "Class A",
                "TRANS_DATE": "31-DEC-2024",
                "TRANS_CODE": "P",
                "TRANS_SHARES": "8782.0",
                "TRANS_PRICEPERSHARE": "11.39",
                "TRANS_ACQUIRED_DISP_CD": "A",
                "SHRS_OWND_FOLWNG_TRANS": "1848104.0",
                "DIRECT_INDIRECT_OWNERSHIP": "I",
                "EQUITY_SWAP_INVOLVED": "0",
            },
            {
                "ACCESSION_NUMBER": "0001104659-24-132975",
                "NONDERIV_TRANS_SK": "8129328",
                "TRANS_CODE": "S",
                "TRANS_SHARES": "lots",
            },  # bad number
            {"ACCESSION_NUMBER": "0001104659-24-999999", "NONDERIV_TRANS_SK": "1", "TRANS_CODE": "P"},  # no filing
            {
                "ACCESSION_NUMBER": "0001104659-24-132975",
                "NONDERIV_TRANS_SK": "8129329",
                "TRANS_CODE": "P",
                "TRANS_ACQUIRED_DISP_CD": "X",
            },  # bad code
        ],
    )

    parsed = parse_dataset(content)

    [filing] = parsed.filings
    assert filing == (
        "0001104659-24-132975",
        "4",
        date(2025, 1, 2),
        date(2024, 12, 31),
        "0001393311",
        "Public Storage",
        "NYSE: PSA",
        "Fund A LP; Doe Jane",
        ["0000000001", "0000000002"],
        "TenPercentOwner; Director",
        "Chair",
        True,
    )
    assert parsed.transactions == [
        ("0001104659-24-132975", 8129327, "Class A", date(2024, 12, 31), "P", "A", 8782.0, 11.39, 1848104.0, "I", False)
    ]
    assert parsed.invalid == 5


def test_parse_rejects_what_is_not_a_dataset() -> None:
    with pytest.raises(DatasetError, match="not a ZIP"):
        parse_dataset(b"<html>maintenance</html>")
    with pytest.raises(DatasetError, match="NONDERIV_TRANS"):
        parse_dataset(_zip([], [], [], skip="NONDERIV_TRANS.tsv"))


def test_client_reads_links_from_both_folders_and_refuses_other_urls() -> None:
    fake = FakeSec(["2026q1", "2026q2"])
    links = fake.client().insider_dataset_links()
    assert links == {
        "2026q1": "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/2026q1_form345.zip",
        "2026q2": "https://www.sec.gov/files/datastandardsinnovation/data/insider-transactions-data-sets/"
        "2026q2_form345.zip",
    }
    assert fake.requests[0].headers["User-Agent"] == UA
    with pytest.raises(ValueError):
        fake.client().insider_dataset("https://example.com/files/x/2026q1_form345.zip")
    with pytest.raises(ValueError):
        fake.client().insider_dataset("https://www.sec.gov/files/../x/2026q1_form345.zip")
    with pytest.raises(SecError, match="lists no datasets"):
        FakeSec([]).client().insider_dataset_links()


# --- the job ---------------------------------------------------------------------------------------


def test_runs_load_two_quarters_each_oldest_first_then_stop_asking(connect_app_db) -> None:
    conn = connect_app_db()
    fake = FakeSec(WANTED + ["2024q3"])  # 2024q3 is before the two-year history
    with fake.client() as client:
        first = ingest_insider_datasets(_run(conn), conn, sec=client, now=NOW)
        assert first.status == "succeeded"
        assert first.meta["loaded"] == ["2024q4", "2025q1"]
        assert first.meta["remaining"] == 6
        assert (first.meta["filings"], first.meta["transactions"]) == (2, 2)

        rest = ingest_insider_datasets(_run(conn, max_files=10), conn, sec=client, now=NOW)
        assert rest.meta["loaded"] == WANTED[2:]
        asked = len(fake.requests)

        current = ingest_insider_datasets(_run(conn), conn, sec=client, now=NOW)
    assert current.meta["message"] == "already_current"
    assert len(fake.requests) == asked
    assert fake.downloads() == WANTED
    assert (_count(conn, "insider_filings"), _count(conn, "insider_transactions")) == (8, 8)


def test_an_unpublished_quarter_waits_but_a_long_missing_one_is_an_error(connect_app_db) -> None:
    conn = connect_app_db()
    fake = FakeSec(WANTED[:-2])  # 2026q2 and 2026q3 not listed
    with fake.client() as client:
        result = ingest_insider_datasets(_run(conn, max_files=10), conn, sec=client, now=NOW)
    assert result.meta["not_published"] == ["2026q2", "2026q3"]
    # 2026q3 ended 58 days ago: still waiting. 2026q2 ended 150 days ago: SEC should have published it.
    assert result.status == "partial"
    assert result.error_message == "2026q2 dataset is not listed 100 days after the quarter"


def test_a_deleted_quarter_is_loaded_again_without_duplicates(connect_app_db) -> None:
    conn = connect_app_db()
    fake = FakeSec(WANTED)
    with fake.client() as client:
        ingest_insider_datasets(_run(conn, max_files=10), conn, sec=client, now=NOW)
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM insider_datasets WHERE quarter = '2025q2'")
        conn.commit()
        assert _count(conn, "insider_transactions") == 7  # its rows went with it

        again = ingest_insider_datasets(_run(conn), conn, sec=client, now=NOW)
    assert again.meta["loaded"] == ["2025q2"]
    assert _count(conn, "insider_transactions") == 8
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT filing_count, transaction_count, bytes > 0 FROM insider_datasets WHERE quarter = '2025q2'"
        )
        assert cursor.fetchone() == (1, 1, True)


def test_an_unreadable_file_stops_the_run_and_the_next_run_retries(connect_app_db, monkeypatch) -> None:
    conn = connect_app_db()
    fake = FakeSec(WANTED)
    fake.zips["2024q4"] = b"<html>maintenance</html>"
    with fake.client() as client:
        result = ingest_insider_datasets(_run(conn), conn, sec=client, now=NOW)
        assert result.status == "failed"
        assert "not a ZIP" in result.error_message
        assert _count(conn, "insider_datasets") == 0

        fake.zips["2024q4"] = _quarter_zip("2024q4")
        again = ingest_insider_datasets(_run(conn), conn, sec=client, now=NOW)
    assert again.meta["loaded"] == ["2024q4", "2025q1"]

    with pytest.raises(ValueError, match="max_files"):
        ingest_insider_datasets(_run(conn, max_files=0), conn, sec=fake.client(), now=NOW)
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(SecConfigurationError):
        ingest_insider_datasets(_run(conn), conn, now=NOW)


# --- as-of access ----------------------------------------------------------------------------------


def test_a_transaction_is_public_from_the_end_of_its_filing_date(connect_app_db) -> None:
    conn = connect_app_db()
    fake = FakeSec(["2025q1"])
    with fake.client() as client:
        ingest_insider_datasets(_run(conn, max_files=10), conn, sec=client, now=NOW)
    filed = quarter_end("2025q1") - timedelta(days=40)  # 2025-02-19
    midnight_after = datetime.combine(filed + timedelta(days=1), datetime.min.time(), tzinfo=NEW_YORK)

    assert AsOf(conn, midnight_after - timedelta(minutes=1)).insider_transactions() == []
    assert AsOf(conn, midnight_after).insider_transactions() == []
    [purchase] = AsOf(conn, midnight_after + timedelta(microseconds=1)).insider_transactions(
        issuer_cik="0001045810", codes=["P"], document_types=["4"]
    )
    assert (purchase.issuer_symbol, purchase.shares, purchase.price_per_share) == ("NVDA", 1000.0, 150.5)
    assert (purchase.filing_date, purchase.owner_relationships, purchase.aff10b5one) == (filed, "Director", False)
    assert AsOf(conn, NOW).insider_transactions(codes=["S"]) == []
