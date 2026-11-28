"""The 10-K changes API, against Postgres: fixtures from the diff tests, then the signed-in client."""

from __future__ import annotations

from filings.tenk_diff import heading_before, is_heading, word_changes
from tests.test_tenk import LONG
from tests.test_tenk_diff import _diff, _extracted
from tests.test_tenk_sections import TEN_K_2025, TEN_K_2026

DOCS = "https://www.sec.gov/Archives/edgar/data/1045810"


def test_word_changes_keep_spacing_and_ignore_quote_style() -> None:
    assert word_changes(
        "Revenue was $60.9 billion in fiscal year 2024.", "Revenue was $130.5 billion in fiscal year 2025."
    ) == [
        ("equal", "Revenue was "),
        ("delete", "$60.9 "),
        ("insert", "$130.5 "),
        ("equal", "billion in fiscal year "),
        ("delete", "2024."),
        ("insert", "2025."),
    ]
    assert word_changes("our customers’ demand", "our customers' demand") == [("equal", "our customers' demand")]


def test_headings_are_short_lines_that_are_not_sentences_or_bullets() -> None:
    assert is_heading("Risks Related to Our Industry and Markets")
    assert is_heading("Overview")
    assert not is_heading("Failure to meet the evolving needs of our industry may harm our results.")
    assert not is_heading("Our success depends on our ability to:")
    assert not is_heading("• new or unexpected end-use cases;")
    assert heading_before(["Overview", "Text.", "Demand", "More text."], 4) == 3
    assert heading_before(["Overview", "Text."], 1) == 1
    assert heading_before(["Text."], 1) is None


def test_tenk_changes_need_a_session(api_client) -> None:
    assert api_client.get("/api/instruments/NVDA/tenk-changes").status_code == 401


def test_the_latest_comparison_with_citations_and_links(signed_in_client, connect_app_db) -> None:
    conn = connect_app_db()
    _extracted(conn)
    _diff(conn)

    body = signed_in_client.get("/api/instruments/nvda/tenk-changes", params={"item": "1A"}).json()

    assert (body["symbol"], body["item"], body["item_label"]) == ("NVDA", "1A", "Risk factors")
    comparison = body["comparison"]
    assert comparison["new"]["accession_no"] == TEN_K_2026
    assert comparison["new"]["section_url"] == f"{DOCS}/000104581026000021/nvda-20260125.htm#i1a"
    assert comparison["old"]["accession_no"] == TEN_K_2025
    assert comparison["old"]["filed_date"] == "2025-02-26"
    assert comparison["counts"] == {
        "unchanged": 3, "moved": 0, "changed": 1, "numbers_only": 1, "added": 1, "removed": 1
    }  # fmt: skip

    [group] = comparison["groups"]
    assert group["heading"] == "Risks Related to Our Industry and Markets"
    removed, changed, added = group["changes"]
    assert (removed["kind"], removed["old_paragraph_no"], removed["new_paragraph_no"]) == ("removed", 4, None)
    assert removed["text"].startswith("The COVID-19 pandemic")
    assert (changed["kind"], changed["old_paragraph_no"], changed["new_paragraph_no"]) == ("changed", 5, 4)
    assert changed["numbers_only"] is True
    assert changed["text"] == "Revenue was $130.5 billion in fiscal year 2025, up 114% from a year ago."
    assert {"op": "delete", "text": "$60.9 "} in changed["words"]
    assert {"op": "insert", "text": "$130.5 "} in changed["words"]
    assert (added["kind"], added["new_paragraph_no"], added["words"]) == ("added", 5, None)

    mdna = signed_in_client.get("/api/instruments/NVDA/tenk-changes", params={"item": "7"}).json()
    assert mdna["item_label"] == "Management's discussion and analysis"
    assert mdna["comparison"]["groups"] == []  # MD&A did not change
    assert mdna["comparison"]["new"]["section_url"].endswith("#i7")


def test_a_removed_paragraph_is_grouped_under_its_heading_as_renamed(signed_in_client, connect_app_db) -> None:
    conn = connect_app_db()
    _extracted(
        conn,
        old_risks=f"<div>{LONG}</div><div>Fiscal Year 2025 Summary</div><div>We opened two new offices in Europe.</div>"
        "<div>Revenue grew strongly across all markets this year.</div>",
        new_risks=f"<div>{LONG}</div><div>Fiscal Year 2026 Summary</div>"
        "<div>Revenue grew strongly across all markets this year.</div>",
    )
    _diff(conn)

    body = signed_in_client.get("/api/instruments/NVDA/tenk-changes").json()
    [group] = body["comparison"]["groups"]
    assert group["heading"] == "Fiscal Year 2026 Summary"
    assert [(c["kind"], c["numbers_only"]) for c in group["changes"]] == [("changed", True), ("removed", False)]


def test_no_comparison_yet_and_bad_requests(signed_in_client, connect_app_db) -> None:
    assert signed_in_client.get("/api/instruments/NBIS/tenk-changes").status_code == 409  # not onboarded
    conn = connect_app_db()
    with conn.cursor() as cursor:
        cursor.execute(
            "UPDATE instruments SET cik = '0001513845', filer_type = 'foreign_private' WHERE symbol = 'NBIS'"
        )
    conn.commit()
    body = signed_in_client.get("/api/instruments/NBIS/tenk-changes").json()
    assert (body["item"], body["filer_type"], body["comparison"]) == ("1A", "foreign_private", None)
    assert signed_in_client.get("/api/instruments/ZZZZ/tenk-changes").status_code == 404
    assert signed_in_client.get("/api/instruments/QQQ/tenk-changes").status_code == 404
    assert signed_in_client.get("/api/instruments/NVDA/tenk-changes", params={"item": "2"}).status_code == 422
