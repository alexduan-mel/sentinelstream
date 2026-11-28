"""Finding Item 1A and Item 7 in a 10-K and splitting them into paragraphs. Pure functions, no I/O.

The synthetic 10-K copies the layouts seen in real watchlist 10-Ks (NVDA, AMZN, GOOGL, MSFT, Nov 2026):
a table of contents linking to each item, page breaks with a "Table of Contents" link and a page
number, sections starting at the top of a page, bullets as two-cell table rows or as one text, and
paragraphs cut by page breaks.
"""

from __future__ import annotations

from filings.tenk import extract_sections, find_section, read_blocks

LONG = "This risk could materially harm our business, results of operations and financial condition. " * 24


def page_break(number: int, target: str = "") -> str:
    """The end of a page and the top of the next, as Workiva-made 10-Ks print it."""
    anchor = f'<div id="{target}"></div>' if target else ""
    return (
        f'<div style="text-align:center"><span>{number}</span></div>{anchor}<hr style="page-break-after:always"/>'
        '<div><a href="#toc">Table of Contents</a></div>'
    )


def tenk(*, risk_factors: str | None = None, mdna: str | None = None) -> str:
    risk_factors = risk_factors if risk_factors is not None else default_risk_factors()
    mdna = mdna if mdna is not None else default_mdna()
    return f"""<html><head><title>nvda-20260125</title></head><body>
<div style="display:none"><ix:header><ix:hidden>Item 1A. Risk Factors hidden fact</ix:hidden></ix:header></div>
<div id="toc"></div>
<table>
  <tr><td><a href="#i1a">Item 1A.</a></td><td><a href="#i1a">Risk Factors</a></td><td>9</td></tr>
  <tr><td><a href="#i1b">Item 1B.</a></td><td>Unresolved Staff Comments</td><td>30</td></tr>
  <tr><td><a href="#i7">Item 7.</a></td><td>Management's Discussion and Analysis</td><td>35</td></tr>
  <tr><td>Item 7A.</td><td>Quantitative and Qualitative Disclosures About Market Risk</td><td>50</td></tr>
</table>
{page_break(8, "i1a")}
<div><span style="font-weight:700">Item 1A. Risk Factors</span></div>
{risk_factors}
{page_break(29, "i1b")}
<div><span>Item 1B. Unresolved Staff Comments</span></div><div>None.</div>
{page_break(34, "i7")}
<div><span style="font-weight:700">Item 7. Management's Discussion and Analysis of Financial Condition</span></div>
{mdna}
<div><span>Item 7A. Quantitative and Qualitative Disclosures About Market Risk</span></div>
<div>{LONG}</div>
</body></html>"""


def default_risk_factors() -> str:
    return f"""
<div>The following risk factors should be considered in addition to the other information.</div>
<div><span>Risks Related to Our Industry and Markets</span></div>
<div>Failure to meet the evolving needs of our industry may adversely affect our financial results.</div>
<div>{LONG} Our success depends on our ability to:</div>
<table><tr><td>•</td><td>timely identify industry changes;</td></tr>
<tr><td>•</td><td>develop new products.</td></tr></table>
<div>•new or unexpected end-use cases;</div>
<div>{LONG} Demand could fall for reasons we cannot</div>
{page_break(12)}
<div>control, such as export rules.</div>
<div>{LONG}</div>
"""


def default_mdna() -> str:
    return f"""
<div>The following discussion should be read with our consolidated financial statements.</div>
<div><span>Fiscal Year 2026 Summary</span></div>
<table>
  <tr><td></td><td>Year Ended</td><td></td></tr>
  <tr><td>Revenue</td><td>$</td><td>130,497</td></tr>
  <tr><td>Gross margin</td><td>75.0</td><td>%</td></tr>
</table>
<div>{LONG}</div>
"""


def test_the_section_is_the_heading_with_the_most_text_not_the_table_of_contents() -> None:
    sections = extract_sections(tenk())

    risk = sections["1a"]
    assert risk is not None
    assert risk.heading == "Item 1A. Risk Factors"
    assert risk.paragraphs[0] == "The following risk factors should be considered in addition to the other information."
    assert risk.paragraphs[1] == "Risks Related to Our Industry and Markets"
    assert sections["7"] is not None
    assert sections["7"].heading.startswith("Item 7. Management's Discussion")
    # Item 7A ends Item 7; the hidden inline-XBRL header is never read.
    assert all("hidden fact" not in p for s in sections.values() for p in s.paragraphs)
    assert sections["7"].paragraphs[-1] == LONG.strip()


def test_bullets_page_furniture_and_page_breaks() -> None:
    risk = extract_sections(tenk())["1a"]

    assert "• timely identify industry changes;" in risk.paragraphs
    assert "• develop new products." in risk.paragraphs
    assert "• new or unexpected end-use cases;" in risk.paragraphs  # a bullet glued to its text
    # The paragraph cut by the page break is one paragraph, without the page number or the header.
    assert f"{LONG.strip()} Demand could fall for reasons we cannot control, such as export rules." in risk.paragraphs
    assert not any(p in ("12", "Table of Contents") for p in risk.paragraphs)


def test_figure_tables_are_left_out_and_counted() -> None:
    mdna = extract_sections(tenk())["7"]

    assert mdna.paragraphs == [
        "The following discussion should be read with our consolidated financial statements.",
        "Fiscal Year 2026 Summary",
        LONG.strip(),
    ]
    assert mdna.tables_skipped == 3


def test_each_section_links_where_the_table_of_contents_does() -> None:
    sections = extract_sections(tenk())

    # Both start at the top of a page: the link target sits above the page's header line.
    assert sections["1a"].anchor == "i1a"
    assert sections["7"].anchor == "i7"


def test_a_heading_split_in_two_or_under_a_running_header() -> None:
    html = tenk().replace(
        '<div><span style="font-weight:700">Item 1A. Risk Factors</span></div>',
        '<div id="own">Item 1A</div><div><span>ITEM 1A.</span></div><div>RISK FACTORS</div>',
    )
    risk = extract_sections(html)["1a"]

    assert risk.heading == "ITEM 1A. RISK FACTORS"
    assert risk.anchor == "own"
    assert risk.paragraphs[0].startswith("The following risk factors")


def test_a_section_too_short_or_missing_is_not_found() -> None:
    assert extract_sections(tenk(risk_factors="<div>See Exhibit 99.1.</div>"))["1a"] is None
    assert find_section(read_blocks("<html><body><p>No items here.</p></body></html>"), "7") is None


def test_numbering_is_stable_when_the_same_document_is_read_again() -> None:
    assert extract_sections(tenk()) == extract_sections(tenk())
