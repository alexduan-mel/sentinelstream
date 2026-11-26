from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from alerts.materiality import classify, needs_form4
from filings.form_4 import Form4Error, parse_form4, raw_xml_name
from tests.form4_xml import form4, owner, transaction

AMZN = "0001018724"
NVDA = "0001045810"

# --- Form 4 XML ------------------------------------------------------------------------------------


def test_parse_form4_reads_issuer_owner_and_transactions() -> None:
    parsed = parse_form4(form4(AMZN, transaction("M", "50000", "0"), transaction("S", "1,006", "253.0925", ad="D")))

    assert parsed.issuer_cik == AMZN and parsed.issuer_name == "AMAZON COM INC"
    assert parsed.reporting_owners == ["Jassy Andrew R"]
    assert parsed.owner_roles == ["Director", "President and CEO"]
    first, second = parsed.transactions
    assert (first.line_no, first.code, first.shares, first.price_per_share) == (1, "M", Decimal(50000), Decimal(0))
    assert first.transaction_date == date(2026, 11, 21) and first.direct_indirect == "D"
    assert (second.code, second.acquired_disposed, second.shares) == ("S", "D", Decimal(1006))
    assert parsed.open_market_purchases == []


def test_a_price_given_only_as_a_footnote_is_none() -> None:
    parsed = parse_form4(form4(AMZN, transaction("P", "100", None)))
    assert parsed.transactions[0].price_per_share is None
    assert len(parsed.open_market_purchases) == 1


def test_several_owners_and_a_ten_percent_holder() -> None:
    owners = (owner("Fund A LP", ten_percent=True), owner("Fund A GP LLC", ten_percent=True))
    parsed = parse_form4(form4(AMZN, transaction("P", "10", "1.5"), owners=owners))
    assert parsed.reporting_owners == ["Fund A LP", "Fund A GP LLC"]
    assert parsed.owner_roles == ["10% owner"]


@pytest.mark.parametrize(
    ("document", "message"),
    [
        (b"<ownershipDocument><documentType>4", "not well-formed"),
        (b"<html></html>", "not ownershipDocument"),
        (form4(AMZN, document_type="3"), "documentType is '3'"),
        (form4("ABC"), "issuerCik"),
        (form4(AMZN, transaction("P", "lots", "1")), "not a number"),
        (
            b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]>'
            b"<ownershipDocument>&b;</ownershipDocument>",
            "XML refused",
        ),
    ],
)
def test_unreadable_documents_raise_form4_error(document: bytes, message: str) -> None:
    with pytest.raises(Form4Error, match=message):
        parse_form4(document)


def test_raw_xml_name_drops_the_xsl_rendering_folder() -> None:
    assert raw_xml_name("xslF345X06/wk-form4_1795563957.xml") == "wk-form4_1795563957.xml"
    assert raw_xml_name("form4.xml") == "form4.xml"
    assert raw_xml_name("xslF345X05/form4.htm") is None
    assert raw_xml_name(None) is None


# --- rules -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("item", ["1.01", "2.02", "4.02", "5.02"])
def test_high_8k_items(item: str) -> None:
    result = classify("8-K", [item, "9.01"], "domestic", NVDA)
    assert result.level == "high"
    assert result.reason.startswith(f"Item {item} ")


def test_8k_naming_several_high_items_lists_each() -> None:
    result = classify("8-K", ["2.02", "5.02"], "domestic", NVDA)
    assert result.reason == (
        "Item 2.02 Results of Operations and Financial Condition; Item 5.02 Departure of Directors or Certain "
        "Officers; Election of Directors; Appointment of Certain Officers; Compensatory Arrangements of Certain "
        "Officers"
    )


@pytest.mark.parametrize(
    ("form_type", "items", "reason"),
    [
        ("8-K", ["7.01", "9.01"], "8-K without a high item"),
        ("8-K", [], "8-K with no items listed"),
        ("8-K/A", ["2.02"], "8-K/A: amendments are not pushed in v0"),
        ("4/A", [], "4/A: amendments are not pushed in v0"),
        ("10-Q", [], "10-Q: not pushed in v0"),
        ("SCHEDULE 13G", [], "SCHEDULE 13G: not pushed in v0"),
    ],
)
def test_other_domestic_filings_are_low(form_type: str, items: list[str], reason: str) -> None:
    result = classify(form_type, items, "domestic", NVDA)
    assert (result.level, result.reason) == ("low", reason)


def test_form4_open_market_purchase_by_an_insider_is_high() -> None:
    parsed = parse_form4(form4(AMZN, transaction("P", "1000", "250.10")))
    result = classify("4", [], "domestic", AMZN, parsed)
    assert (result.level, result.reason) == (
        "high",
        "Open-market purchase by Jassy Andrew R (Director, President and CEO)",
    )

    two = parse_form4(form4(AMZN, transaction("P", "1", "1"), transaction("P", "2", "1")))
    assert classify("4", [], "domestic", AMZN, two).reason.startswith("2 open-market purchases by ")


def test_form4_without_a_purchase_is_low_and_names_its_codes() -> None:
    parsed = parse_form4(form4(AMZN, transaction("S", "10", "1", ad="D"), transaction("M", "10", "0")))
    result = classify("4", [], "domestic", AMZN, parsed)
    assert (result.level, result.reason) == ("low", "Form 4 codes M, S")
    empty = parse_form4(form4(AMZN))
    assert classify("4", [], "domestic", AMZN, empty).reason == "Form 4 with no share transactions"


def test_form4_is_low_for_a_watchlist_company_that_is_only_a_reporting_owner() -> None:
    parsed = parse_form4(form4("0001234567", transaction("P", "1000000", "10")))
    result = classify("4", [], "domestic", NVDA, parsed)
    assert result.level == "low"
    assert result.reason == "Form 4 for issuer CIK 0001234567; this company is a reporting owner"


def test_foreign_private_issuers_have_no_high_rule_in_v0() -> None:
    assert classify("6-K", [], "foreign_private", "0001513845").level == "low"
    assert classify("8-K", ["2.02"], "foreign_private", "0001513845").reason == (
        "8-K: no high rule for foreign private issuers in v0"
    )
    assert classify("8-K", ["2.02"], None, NVDA).reason == "8-K: issuer filer type unknown"


def test_only_domestic_form4s_need_their_xml() -> None:
    assert needs_form4("4", "domestic")
    assert not needs_form4("4", "foreign_private")
    assert not needs_form4("4/A", "domestic")
    assert not needs_form4("8-K", "domestic")
    with pytest.raises(ValueError, match="needs its parsed XML"):
        classify("4", [], "domestic", AMZN)
