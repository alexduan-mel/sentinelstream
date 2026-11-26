"""Resolve a watchlist symbol to its SEC issuer: CIK, name, exchange and filer type.

All of it comes from SEC data, never from hand-written seeds. The ticker-onboarding job
(``ingestion.onboarding``) calls these for every new watchlist equity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ingestion.sec_client import SecClient

# Most recent form of each kind decides; the order of the groups is the order of precedence.
# A foreign private issuer files 20-F or 40-F annually and 6-K for current reports, and has no Form 4.
_FILER_TYPE_BY_FORM: tuple[dict[str, str], ...] = (
    {"10-K": "domestic", "10-KT": "domestic", "10-Q": "domestic", "20-F": "foreign_private", "40-F": "foreign_private"},
    {"8-K": "domestic", "6-K": "foreign_private"},
    {"S-1": "domestic", "10-12B": "domestic", "F-1": "foreign_private", "20FR12B": "foreign_private"},
    # Section 16 insider reports: foreign private issuers are exempt, so these mean domestic. This
    # classifies a fresh IPO whose S-1 has already dropped behind a burst of Form 3 and 4 filings.
    {"3": "domestic", "4": "domestic", "5": "domestic"},
)

# Forms only registered funds file (open-end, closed-end, ETFs, unit trusts). A ticker held by an
# issuer that files any of them is not a watchlist company. SEC's ``entityType`` is no help here: it
# says "other" for some operating companies, such as recent IPOs.
FUND_FORMS = frozenset(
    {
        "N-1A",
        "N-2",
        "N-8A",
        "N-CSR",
        "N-CSRS",
        "N-CEN",
        "NPORT-P",
        "N-PX",
        "485APOS",
        "485BPOS",
        "497",
        "497K",
        "24F-2NT",
    }
)


class IdentityError(ValueError):
    """The symbol cannot be tied to exactly one SEC-registered company."""


@dataclass(frozen=True)
class Issuer:
    cik: str
    name: str
    exchange: str | None
    filer_type: str | None


def detect_filer_type(submissions: dict[str, Any]) -> str | None:
    """``domestic`` or ``foreign_private`` from the forms the issuer files; None if nothing tells.

    Looks at the newest periodic report first, then current reports, then registration statements,
    so an issuer that switched (an FPI that now files 10-K) gets the type it files today.
    """
    forms = (submissions.get("filings") or {}).get("recent", {}).get("form") or []
    # ``recent`` is newest first; amendments count as their base form.
    base_forms = [str(form).removesuffix("/A") for form in forms]
    for group in _FILER_TYPE_BY_FORM:
        for form in base_forms:
            if form in group:
                return group[form]
    return None


def resolve_issuer(symbol: str, ticker_map: list[dict[str, Any]], sec: SecClient) -> tuple[Issuer, dict[str, Any]]:
    """Find ``symbol`` in SEC's ticker map and confirm it against the issuer's submissions record.

    Returns the issuer and the submissions record it was confirmed against, so the caller can reuse it.
    """
    matches = [row for row in ticker_map if str(row.get("ticker", "")).upper() == symbol]
    ciks = {int(row["cik"]) for row in matches}
    if not ciks:
        raise IdentityError(f"{symbol} is not in SEC's ticker map")
    if len(ciks) > 1:
        raise IdentityError(f"{symbol} maps to several CIKs in SEC's ticker map: {sorted(ciks)}")
    cik = f"{ciks.pop():010d}"

    submissions = sec.submissions(cik)
    tickers = [str(value).upper() for value in submissions.get("tickers") or []]
    if symbol not in tickers:
        raise IdentityError(f"SEC submissions for CIK {cik} do not list {symbol}")
    forms = {
        str(form).removesuffix("/A") for form in (submissions.get("filings") or {}).get("recent", {}).get("form") or []
    }
    fund_forms = sorted(forms & FUND_FORMS)
    if fund_forms:
        raise IdentityError(f"CIK {cik} for {symbol} files as a fund ({', '.join(fund_forms)}), not a company")

    name = str(submissions.get("name") or matches[0].get("name") or "").strip()
    issuer = Issuer(
        cik=cik,
        name=name,
        exchange=(str(matches[0].get("exchange") or "").strip() or None),
        filer_type=detect_filer_type(submissions),
    )
    return issuer, submissions
