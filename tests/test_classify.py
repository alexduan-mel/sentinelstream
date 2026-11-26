"""Materiality classification inside ``ingest_sec_filings``, against Postgres and a fake SEC."""

from __future__ import annotations

from decimal import Decimal

import httpx

from alerts import classify as classify_module
from ingestion.sec_filings import ingest_sec_filings
from tests.form4_xml import form4, transaction
from tests.test_sec_ingestion import NBIS_CIK, NVDA_CIK, NVDA_FILINGS, FakeSec, _filing, _run, _submissions


def _links(conn) -> dict[str, tuple]:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT accession_no, cik, materiality, materiality_reason, classified_at IS NOT NULL "
            "FROM filing_issuers ORDER BY accession_no, cik"
        )
        rows = {(row[0], row[1]): row[2:] for row in cursor.fetchall()}
    conn.rollback()
    return rows


def _archive(accession: str, name: str = "form4.xml") -> str:
    return f"/Archives/edgar/data/1045810/{accession.replace('-', '')}/{name}"


def _nvda_lists(fake: FakeSec, *extra: dict) -> None:
    fake.routes[f"/submissions/CIK{NVDA_CIK}.json"] = _submissions("NVIDIA CORP", ["NVDA"], [*extra, *NVDA_FILINGS])


def _form4_filing(accession: str, accepted: str = "2026-11-25T21:30:00.000Z") -> dict:
    return _filing(accession, "4", accepted, filed="2026-11-25", doc="xslF345X06/form4.xml")


def test_first_poll_classifies_history_from_items_and_form4_xml(connect_app_db) -> None:
    conn = connect_app_db()
    result = ingest_sec_filings(_run(conn), conn, sec=FakeSec().client())

    links = _links(conn)
    assert links[("0001045810-26-000101", NVDA_CIK)] == (
        "high",
        "Item 2.02 Results of Operations and Financial Condition",
        True,
    )
    assert links[("0001045810-26-000100", NVDA_CIK)][:2] == ("low", "10-Q: not pushed in v0")
    assert links[("0001045810-26-000099", NVDA_CIK)][:2] == ("low", "Form 4 codes S")
    assert links[("0001513845-26-000040", NBIS_CIK)][:2] == (
        "low",
        "6-K: no high rule for foreign private issuers in v0",
    )
    assert result.meta["materiality"] == {
        "classified": 5,
        "high": 1,
        "form4_fetched": 1,
        "form4_unreadable": 0,
        "form4_waiting": 0,
    }


def test_a_new_insider_purchase_is_high_and_its_transactions_are_stored(connect_app_db) -> None:
    conn = connect_app_db()
    fake = FakeSec()
    ingest_sec_filings(_run(conn), conn, sec=fake.client())

    accession = "0001045810-26-000300"
    _nvda_lists(fake, _form4_filing(accession))
    fake.routes[_archive(accession)] = httpx.Response(
        200, content=form4(NVDA_CIK, transaction("P", "2,000", "181.25"), transaction("S", "5", "1", ad="D"))
    )
    result = ingest_sec_filings(_run(conn), conn, sec=fake.client())

    assert result.status == "succeeded"
    assert _links(conn)[(accession, NVDA_CIK)] == (
        "high",
        "Open-market purchase by Jassy Andrew R (Director, President and CEO)",
        True,
    )
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT line_no, issuer_cik, transaction_code, shares, price_per_share, acquired_disposed, "
            "known_at = (SELECT known_at FROM filings WHERE accession_no = %s) "
            "FROM form4_transactions WHERE accession_no = %s ORDER BY line_no",
            (accession, accession),
        )
        rows = cursor.fetchall()
    conn.rollback()
    assert rows == [
        (1, NVDA_CIK, "P", Decimal("2000"), Decimal("181.25"), "A", True),
        (2, NVDA_CIK, "S", Decimal("5"), Decimal("1"), "D", True),
    ]

    # Classified once: the next poll fetches nothing more.
    fake.requests.clear()
    ingest_sec_filings(_run(conn), conn, sec=fake.client())
    assert not [path for path in fake.paths() if path.startswith("/Archives/")]


def test_a_new_form4_that_cannot_be_fetched_is_retried_and_the_run_is_partial(connect_app_db) -> None:
    conn = connect_app_db()
    fake = FakeSec()
    ingest_sec_filings(_run(conn), conn, sec=fake.client())

    accession = "0001045810-26-000301"
    _nvda_lists(fake, _form4_filing(accession))
    fake.routes[_archive(accession)] = httpx.Response(500)
    failed = ingest_sec_filings(_run(conn), conn, sec=fake.client())

    assert failed.status == "partial"
    assert "1 new Form 4(s) could not be fetched" in failed.error_message
    assert _links(conn)[(accession, NVDA_CIK)] == (None, None, False)

    fake.routes[_archive(accession)] = httpx.Response(200, content=form4(NVDA_CIK, transaction("P", "1", "1")))
    retried = ingest_sec_filings(_run(conn), conn, sec=fake.client())
    assert retried.status == "succeeded"
    assert _links(conn)[(accession, NVDA_CIK)][0] == "high"


def test_an_unreadable_form4_is_low_with_the_reason(connect_app_db) -> None:
    conn = connect_app_db()
    fake = FakeSec()
    ingest_sec_filings(_run(conn), conn, sec=fake.client())

    accession = "0001045810-26-000302"
    _nvda_lists(fake, _form4_filing(accession))
    fake.routes[_archive(accession)] = httpx.Response(200, content=b"<html>maintenance</html>")
    result = ingest_sec_filings(_run(conn), conn, sec=fake.client())

    assert result.status == "succeeded"
    assert result.meta["materiality"]["form4_unreadable"] == 1
    assert _links(conn)[(accession, NVDA_CIK)][:2] == (
        "low",
        "Form 4 XML unreadable: root element is 'html', not ownershipDocument",
    )


def test_history_form4s_are_fetched_a_few_per_run(connect_app_db, monkeypatch) -> None:
    monkeypatch.setattr(classify_module, "BACKFILL_FORM4_PER_RUN", 2)
    conn = connect_app_db()
    fake = FakeSec()
    history = [_form4_filing(f"0001045810-26-00040{n}", f"2026-11-0{n}T15:00:00.000Z") for n in range(1, 6)]
    _nvda_lists(fake, *history)
    for filing in history:
        fake.routes[_archive(filing["accessionNumber"])] = httpx.Response(
            200, content=form4(NVDA_CIK, transaction("A", "10", "0"))
        )

    first = ingest_sec_filings(_run(conn, tickers=["NVDA"]), conn, sec=fake.client())
    # NVDA_FILINGS' own Form 4 is history too: six to fetch, two per run.
    assert (first.meta["materiality"]["form4_fetched"], first.meta["materiality"]["form4_waiting"]) == (2, 4)
    assert first.status == "succeeded"
    unclassified = [key for key, value in _links(conn).items() if value[0] is None]
    assert len(unclassified) == 4
    # Newest first.
    assert ("0001045810-26-000405", NVDA_CIK) not in unclassified

    for _ in range(2):
        ingest_sec_filings(_run(conn, tickers=["NVDA"]), conn, sec=fake.client())
    assert all(value[0] is not None for value in _links(conn).values())
