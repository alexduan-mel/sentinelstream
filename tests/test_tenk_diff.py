"""Lining up two years of 10-K paragraphs: the comparison alone, then the task against Postgres."""

from __future__ import annotations

import httpx

from filings.section_diffs import diff_10k_sections
from filings.tenk_diff import COMPARER_VERSION, Change, compare, similarity
from ingestion.tenk_sections import extract_10k_sections
from tests.test_onboarding import _count, _onboard, _run
from tests.test_price_ingestion import FakeMassive
from tests.test_tenk import LONG, tenk
from tests.test_tenk_sections import ACCEPTED_2026, TEN_K_2025, TEN_K_2026, _doc_path, _sec_with_10ks

# --- the comparison --------------------------------------------------------------------------------


def test_unchanged_moved_changed_added_and_removed() -> None:
    old = [
        "Overview",
        "Global Trade",
        "Revenue was $60.9 billion in fiscal year 2024, up 126% from a year ago.",
        "COVID-19",
        "We depend on a few foundries.",
        "This paragraph moves.",
    ]
    new = [
        "Overview",
        "This paragraph moves.",
        "Global Trade and Tariffs",
        "Revenue was $130.5 billion in fiscal year 2025, up 114% from a year ago.",
        "Demand and Supply",
        "We depend on a few foundries.",
    ]
    comparison = compare(old, new)

    assert (comparison.unchanged, comparison.moved) == (2, 1)
    assert comparison.changes == [
        Change("changed", 2, 3, 0.667, numbers_only=False),
        Change("changed", 3, 4, 0.778, numbers_only=True),
        Change("removed", 4, None),  # where COVID-19 stood last year: after the revenue paragraph
        Change("added", None, 5),
    ]


def test_paragraphs_too_different_are_removed_and_added_not_changed() -> None:
    old = ["Revenue increased $3.8 billion or 7%."]
    new = ["Cost of revenue decreased $1.8 billion or 7% driven by lower hardware sales."]

    assert similarity(old[0], new[0]) < 0.6
    assert compare(old, new).changes == [Change("removed", 1, None), Change("added", None, 1)]


def test_typography_alone_is_no_change() -> None:
    old = ["Our customers’ demand may change — quickly.", "Risks  Related to Our Industry"]
    new = ["Our customers' demand may change - quickly.", "RISKS RELATED TO OUR INDUSTRY"]

    comparison = compare(old, new)
    assert comparison.changes == [] and comparison.unchanged == 2


def test_months_and_figures_alone_are_numbers_only() -> None:
    old = ["On January 15, 2025, we repurchased 12.5 million shares for $1.6 billion."]
    new = ["On February 3, 2026, we repurchased 9.1 million shares for $2.0 billion."]

    [change] = compare(old, new).changes
    assert change.kind == "changed" and change.numbers_only


def test_empty_sections() -> None:
    assert compare([], ["New"]).changes == [Change("added", None, 1)]
    assert compare(["Old"], []).changes == [Change("removed", 1, None)]


# --- the task --------------------------------------------------------------------------------------

OLD_RISKS = f"""
<div>The following risk factors should be considered in addition to the other information.</div>
<div>Risks Related to Our Industry and Markets</div>
<div>{LONG}</div>
<div>The COVID-19 pandemic could reduce demand for our products in ways we cannot predict.</div>
<div>Revenue was $60.9 billion in fiscal year 2024, up 126% from a year ago.</div>
"""
NEW_RISKS = f"""
<div>The following risk factors should be considered in addition to the other information.</div>
<div>Risks Related to Our Industry and Markets</div>
<div>{LONG}</div>
<div>Revenue was $130.5 billion in fiscal year 2025, up 114% from a year ago.</div>
<div>New export licensing requirements now cover more of our data center products.</div>
"""


def _extracted(conn, *, old_risks: str = OLD_RISKS, new_risks: str = NEW_RISKS):
    sec = _sec_with_10ks()
    sec.routes[_doc_path(TEN_K_2026, "nvda-20260125.htm")] = httpx.Response(
        200, content=tenk(risk_factors=new_risks).encode()
    )
    sec.routes[_doc_path(TEN_K_2025, "nvda-20250126.htm")] = httpx.Response(
        200, content=tenk(risk_factors=old_risks).encode()
    )
    _onboard(conn, sec, FakeMassive(), tickers=["NVDA"])
    run = _run(conn, task_id="extract_10k_sections")
    assert extract_10k_sections(run, conn, sec=sec.client()).status == "succeeded"
    return sec


def _diff(conn, *, force: bool = False):
    return diff_10k_sections(_run(conn, task_id="diff_10k_sections", force_refresh=force), conn)


def _changes(conn) -> list[tuple]:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT item, change_no, kind, old_paragraph_no, new_paragraph_no, similarity IS NOT NULL, numbers_only, "
            "known_at FROM filing_paragraph_changes WHERE new_accession_no = %s ORDER BY item, change_no",
            (TEN_K_2026,),
        )
        rows = cursor.fetchall()
    conn.rollback()
    return rows


def test_each_10k_is_compared_with_the_year_before_and_cited(connect_app_db) -> None:
    conn = connect_app_db()
    _extracted(conn)

    result = _diff(conn)

    assert result.status == "succeeded"
    assert sorted(result.meta["compared"]) == [f"{TEN_K_2026} Item 1A", f"{TEN_K_2026} Item 7"]
    # In this year's order; the COVID-19 paragraph came before the revenue one last year.
    assert _changes(conn) == [
        ("1A", 1, "removed", 4, None, False, False, ACCEPTED_2026),
        ("1A", 2, "changed", 5, 4, True, True, ACCEPTED_2026),
        ("1A", 3, "added", None, 5, False, False, ACCEPTED_2026),
    ]
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT item, old_accession_no, cik, comparer_version, unchanged_count, moved_count, changed_count, "
            "numbers_only_count, added_count, removed_count FROM filing_section_diffs ORDER BY item"
        )
        diffs = cursor.fetchall()
    conn.rollback()
    assert diffs == [
        ("1A", TEN_K_2025, "0001045810", COMPARER_VERSION, 3, 0, 1, 1, 1, 1),
        ("7", TEN_K_2025, "0001045810", COMPARER_VERSION, 3, 0, 0, 0, 0, 0),  # MD&A did not change
    ]


def test_reruns_and_reextraction(connect_app_db) -> None:
    conn = connect_app_db()
    sec = _extracted(conn)
    _diff(conn)
    before = _changes(conn)

    assert _diff(conn).meta == {"message": "nothing_due"}
    assert sorted(_diff(conn, force=True).meta["compared"]) == [f"{TEN_K_2026} Item 1A", f"{TEN_K_2026} Item 7"]
    assert _changes(conn) == before

    # Extracting the 10-Ks again replaces their paragraphs, so their diffs go and are computed again.
    run = _run(conn, task_id="extract_10k_sections", force_refresh=True)
    extract_10k_sections(run, conn, sec=sec.client())
    assert _count(conn, "SELECT COUNT(*) FROM filing_section_diffs") == 0
    assert len(_diff(conn).meta["compared"]) == 2
    assert _changes(conn) == before


def test_a_missing_year_is_never_skipped_over(connect_app_db) -> None:
    conn = connect_app_db()
    _extracted(conn)
    with conn.cursor() as cursor:
        cursor.execute(
            "UPDATE filings SET accepted_at = accepted_at - INTERVAL '200 days' WHERE accession_no = %s", (TEN_K_2025,)
        )
    conn.commit()

    assert _diff(conn).meta == {"message": "nothing_due"}
