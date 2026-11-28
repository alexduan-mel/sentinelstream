"""Extracting 10-K sections for the watchlist, against Postgres and a fake SEC. No network."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import httpx

from ingestion.tenk_sections import EXTRACTOR_VERSION, extract_10k_sections
from tests.test_onboarding import _count, _onboard, _run
from tests.test_price_ingestion import FakeMassive
from tests.test_sec_ingestion import NVDA_CIK, NVDA_FILINGS, FakeSec, _filing, _submissions
from tests.test_tenk import tenk

TEN_K_2026 = "0001045810-26-000021"
TEN_K_2025 = "0001045810-25-000023"
TEN_K_2024 = "0001045810-24-000029"
TEN_K_A = "0001045810-26-000030"
ACCEPTED_2026 = datetime(2026, 2, 25, 21, 30, tzinfo=UTC)

TEN_KS = [
    _filing(TEN_K_A, "10-K/A", "2026-03-20T20:00:00.000Z", filed="2026-03-20", doc="nvda-10ka.htm"),
    _filing(TEN_K_2026, "10-K", "2026-02-25T21:30:00.000Z", filed="2026-02-25", doc="nvda-20260125.htm"),
    _filing(TEN_K_2025, "10-K", "2025-02-26T21:30:00.000Z", filed="2025-02-26", doc="nvda-20250126.htm"),
    _filing(TEN_K_2024, "10-K", "2024-02-21T21:30:00.000Z", filed="2024-02-21", doc="nvda-20240128.htm"),
]


def _doc_path(accession: str, name: str) -> str:
    return f"/Archives/edgar/data/1045810/{accession.replace('-', '')}/{name}"


def _sec_with_10ks() -> FakeSec:
    sec = FakeSec()
    sec.routes[f"/submissions/CIK{NVDA_CIK}.json"] = _submissions("NVIDIA CORP", ["NVDA"], NVDA_FILINGS + TEN_KS)
    sec.routes[_doc_path(TEN_K_2026, "nvda-20260125.htm")] = httpx.Response(200, content=tenk().encode())
    # Last year's 10-K: Item 7 incorporated by reference, so it is not found.
    sec.routes[_doc_path(TEN_K_2025, "nvda-20250126.htm")] = httpx.Response(
        200, content=tenk(mdna="<div>See the Annual Report to Shareholders\u2019 section.</div>").encode("cp1252")
    )
    return sec


def _onboarded(conn) -> FakeSec:
    sec = _sec_with_10ks()
    _onboard(conn, sec, FakeMassive(), tickers=["NVDA"])
    sec.requests.clear()
    return sec


def _extract(conn, sec: FakeSec, *, force: bool = False, tickers: list[str] | None = None):
    run = _run(conn, task_id="extract_10k_sections", tickers=tickers, force_refresh=force)
    return extract_10k_sections(run, conn, sec=sec.client())


def _sections(conn) -> list[tuple]:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT accession_no, item, found, heading, anchor, paragraph_count, tables_skipped, source_document, "
            "source_sha256, extractor_version, known_at FROM filing_sections ORDER BY accession_no, item"
        )
        rows = cursor.fetchall()
    conn.rollback()
    return rows


def test_the_two_latest_10ks_are_extracted_with_citations(connect_app_db) -> None:
    conn = connect_app_db()
    sec = _onboarded(conn)

    result = _extract(conn, sec)

    # The two latest 10-Ks, newest first; not the 10-K/A, not the one from 2024.
    assert sec.paths() == [_doc_path(TEN_K_2026, "nvda-20260125.htm"), _doc_path(TEN_K_2025, "nvda-20250126.htm")]
    assert result.status == "partial"  # last year's Item 7 is missing, which is worth a look once
    assert result.error_message == f"not found: Item 7 (Management's discussion and analysis) in 10-K {TEN_K_2025}"
    assert list(result.meta["extracted"]) == [TEN_K_2026, TEN_K_2025]

    rows = {(row[0], row[1]): row for row in _sections(conn)}
    digest = hashlib.sha256(tenk().encode()).hexdigest()
    risk = rows[(TEN_K_2026, "1A")]
    assert risk[2:5] == (True, "Item 1A. Risk Factors", "i1a")
    assert risk[7:] == ("nvda-20260125.htm", digest, EXTRACTOR_VERSION, ACCEPTED_2026)
    assert rows[(TEN_K_2026, "7")][4:7] == ("i7", 3, 3)
    assert rows[(TEN_K_2025, "7")][2:6] == (False, None, None, 0)
    assert rows[(TEN_K_2025, "1A")][2] is True  # a Windows-1252 document reads too

    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT paragraph_no, text, known_at FROM filing_paragraphs "
            "WHERE accession_no = %s AND item = '1A' ORDER BY paragraph_no LIMIT 2",
            (TEN_K_2026,),
        )
        paragraphs = cursor.fetchall()
    conn.rollback()
    assert paragraphs == [
        (1, "The following risk factors should be considered in addition to the other information.", ACCEPTED_2026),
        (2, "Risks Related to Our Industry and Markets", ACCEPTED_2026),
    ]
    assert risk[5] == _count(
        conn, "SELECT COUNT(*) FROM filing_paragraphs WHERE accession_no = %s AND item = '1A'", TEN_K_2026
    )


def test_a_rerun_does_nothing_and_a_forced_run_leaves_the_same_rows(connect_app_db) -> None:
    conn = connect_app_db()
    sec = _onboarded(conn)
    _extract(conn, sec)
    before = _sections(conn)
    paragraphs = _count(conn, "SELECT COUNT(*) FROM filing_paragraphs")

    sec.requests.clear()
    assert _extract(conn, sec).meta == {"message": "nothing_due"}
    assert sec.paths() == []

    forced = _extract(conn, sec, force=True)
    assert list(forced.meta["extracted"]) == [TEN_K_2026, TEN_K_2025]
    assert [row[:10] for row in _sections(conn)] == [row[:10] for row in before]
    assert _count(conn, "SELECT COUNT(*) FROM filing_paragraphs") == paragraphs


def test_a_failed_fetch_is_retried_on_the_next_run(connect_app_db) -> None:
    conn = connect_app_db()
    sec = _onboarded(conn)
    sec.routes[_doc_path(TEN_K_2026, "nvda-20260125.htm")] = httpx.Response(404)

    result = _extract(conn, sec)
    assert result.status == "partial"
    assert result.error_message.startswith(f"{TEN_K_2026}: SEC returned 404")
    assert list(result.meta["extracted"]) == [TEN_K_2025]

    sec.routes[_doc_path(TEN_K_2026, "nvda-20260125.htm")] = httpx.Response(200, content=tenk().encode())
    assert list(_extract(conn, sec).meta["extracted"]) == [TEN_K_2026]


def test_only_ready_watchlist_issuers_and_named_tickers(connect_app_db) -> None:
    conn = connect_app_db()
    sec = _onboarded(conn)

    assert _extract(conn, sec, tickers=["NBIS"]).meta == {"message": "nothing_due"}
    with conn.cursor() as cursor:
        cursor.execute("UPDATE instruments SET onboarding_status = 'backfilling' WHERE symbol = 'NVDA'")
    conn.commit()
    assert _extract(conn, sec).meta == {"message": "nothing_due"}
    assert sec.paths() == []
