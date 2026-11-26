"""Materiality rules v0: which filings are high (pushed to Slack) and which are low (UI only).

The rule set depends on the issuer's filer type (CLAUDE.md, ADR 0008):

* **Domestic filers:** an 8-K with item 1.01, 2.02, 4.02 or 5.02 is high, and so is a Form 4 that
  reports an open-market purchase (transaction code P) of the issuer's own shares.
* **Foreign private issuers:** they file 20-F and 6-K and no Form 4, and v0 has no high rule for
  them. A 6-K has no item numbers, so telling results from routine notices needs its text (M3).

Everything else is low. Amendments (8-K/A, 4/A) are low in v0: they usually add exhibits or fix
details of an event that was already alerted.

Each rule returns a short reason that names what matched, shown in the UI next to the level.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from filings.form_4 import Form4
from filings.form_8k import item_title

Level = Literal["high", "low"]
FilerType = Literal["domestic", "foreign_private"]

HIGH_8K_ITEMS = ("1.01", "2.02", "4.02", "5.02")
FORM_4 = "4"


@dataclass(frozen=True)
class Materiality:
    level: Level
    reason: str


def needs_form4(form_type: str, filer_type: str | None) -> bool:
    """True if classifying this filing needs its Form 4 XML."""
    return form_type == FORM_4 and filer_type == "domestic"


def classify(
    form_type: str, items: list[str], filer_type: str | None, cik: str, form4: Form4 | None = None
) -> Materiality:
    """Materiality of one filing for one linked issuer (``cik``)."""
    if filer_type == "domestic":
        return _domestic(form_type, items, cik, form4)
    if filer_type == "foreign_private":
        return Materiality("low", f"{form_type}: no high rule for foreign private issuers in v0")
    return Materiality("low", f"{form_type}: issuer filer type unknown")


def _domestic(form_type: str, items: list[str], cik: str, form4: Form4 | None) -> Materiality:
    if form_type in ("8-K/A", "4/A"):
        return Materiality("low", f"{form_type}: amendments are not pushed in v0")
    if form_type == "8-K":
        matched = [number for number in HIGH_8K_ITEMS if number in {item.strip() for item in items}]
        if matched:
            return Materiality(
                "high", "; ".join(f"Item {number} {item_title(number) or ''}".strip() for number in matched)
            )
        return Materiality("low", "8-K without a high item" if items else "8-K with no items listed")
    if form_type == FORM_4:
        if form4 is None:
            raise ValueError("a domestic Form 4 needs its parsed XML to be classified")
        if form4.issuer_cik != cik:
            return Materiality("low", f"Form 4 for issuer CIK {form4.issuer_cik}; this company is a reporting owner")
        purchases = form4.open_market_purchases
        if purchases:
            who = ", ".join(form4.reporting_owners) or "reporting owner"
            role = f" ({', '.join(form4.owner_roles)})" if form4.owner_roles else ""
            count = f"{len(purchases)} open-market purchases" if len(purchases) > 1 else "Open-market purchase"
            return Materiality("high", f"{count} by {who}{role}")
        codes = sorted({t.code for t in form4.transactions})
        return Materiality("low", f"Form 4 codes {', '.join(codes)}" if codes else "Form 4 with no share transactions")
    return Materiality("low", f"{form_type}: not pushed in v0")
