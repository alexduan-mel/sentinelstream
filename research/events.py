"""Past events for the base-rate calculator, read through the as-of layer (ADR 0015).

An event is one company on one day: several insiders buying the same stock on the same day, or one
insider filing several Form 4s, count once, because they are one piece of news. Its ``known_at`` is
the earliest of its filings' ``known_at``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from research.asof import AsOf

# "NYSE: KRC", "NASDAQ:RMR", "ASX:LNW": an exchange prefix before the symbol.
_EXCHANGE_PREFIX = re.compile(r"^[A-Za-z ]{2,20}:\s*")
_SEPARATORS = re.compile(r"[,;/\s]+")
_PLACEHOLDERS = {"", "N/A", "NA", "NONE", "[NONE]", "-", "--", "NULL"}
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9]{0,5}(\.[A-Z]{1,2})?$")
_EXCHANGES = {"NYSE", "NASDAQ", "AMEX", "NYSEAMERICAN", "OTC", "OTCQX", "OTCQB", "TSX", "ASX", "CBOE"}


def clean_symbol(raw: str | None) -> str | None:
    """The trading symbol in a filer-typed issuer symbol, or None when there is none to read.

    ``NYSE: KRC`` → ``KRC``; ``(SIRI)`` → ``SIRI``; ``HEI, HEI.A`` → ``HEI``; ``BRK-B`` → ``BRK.B``;
    ``N O G`` → ``NOG``; ``NYSE/TRN`` → ``TRN``; ``utgn`` → ``UTGN``; ``N/A`` → None.
    """
    if raw is None:
        return None
    text = raw.strip()
    if text.upper() in _PLACEHOLDERS:
        return None
    text = _EXCHANGE_PREFIX.sub("", text)
    text = text.strip("()[]{}\"' ")
    tokens = [token.strip("()[]{}\"'") for token in _SEPARATORS.split(text)]
    tokens = [token for token in tokens if token]
    if len(tokens) > 1 and all(len(token) == 1 for token in tokens):
        tokens = ["".join(tokens)]  # letters typed with spaces between them
    if len(tokens) > 1 and tokens[0].upper() in _EXCHANGES:
        tokens = tokens[1:]
    symbol = (tokens[0] if tokens else "").upper().replace("-", ".")
    if symbol in _PLACEHOLDERS or not _SYMBOL.match(symbol):
        return None
    return symbol


@dataclass(frozen=True)
class Event:
    """One company on one day."""

    issuer_cik: str
    symbol: str | None  # as traded, if one could be read
    event_date: date
    known_at: datetime
    label: str
    value: float  # dollars bought, summed over the day's transactions


# Exchanges whose stocks are in Massive's market-wide bars; over-the-counter ones are not.
_LISTED_EXCHANGES = {"NYSE", "Nasdaq", "NYSE American", "NYSE Arca", "CBOE", "BATS"}


def listed_symbol(tickers: tuple[str, ...], exchanges: tuple[str, ...]) -> str | None:
    """The first ticker SEC lists on a US exchange; None if all are over the counter or unknown."""
    for ticker, exchange in zip(tickers, exchanges, strict=False):
        if exchange in _LISTED_EXCHANGES:
            return clean_symbol(ticker)
    return None


def eight_k_events(reader: AsOf, items: list[str] | tuple[str, ...]) -> list[Event]:
    """Original 8-Ks carrying any of ``items``, one event per company and filing date.

    The symbol is the company's listed ticker as SEC lists it now: SEC's submissions give no
    history of tickers. A company that changed ticker since is unmatched, not mismatched, unless
    its old ticker went to another company; the report's match counts show how many.
    """
    days: dict[tuple[str, date], Event] = {}
    for filing in reader.market_8k_filings(items=list(items)):
        key = (filing.cik, filing.filed_date)
        if key in days:
            continue  # the earliest is kept: filings come oldest first
        days[key] = Event(
            issuer_cik=filing.cik,
            symbol=listed_symbol(filing.tickers, filing.exchanges),
            event_date=filing.filed_date,
            known_at=filing.known_at,
            label=f"{filing.name or filing.cik} 8-K {','.join(i for i in filing.items if i in items)}",
            value=0.0,
        )
    return sorted(days.values(), key=lambda e: (e.known_at, e.issuer_cik))


def insider_purchase_events(
    reader: AsOf,
    *,
    document_types: tuple[str, ...] = ("4",),
    exclude_10b5_1: bool = False,
    min_value: float = 0.0,
) -> list[Event]:
    """Open-market purchases (code P, acquired) from SEC's datasets, one event per issuer and filing date.

    Only original Form 4s by default; amendments repeat earlier filings. A transaction needs a
    positive share count and price. ``exclude_10b5_1`` drops filings that report a pre-planned trade.
    """
    days: dict[tuple[str, date], dict] = {}
    for row in reader.insider_transactions(codes=["P"], document_types=list(document_types)):
        if row.acquired_disposed != "A" or not row.shares or not row.price_per_share:
            continue
        if row.shares <= 0 or row.price_per_share <= 0:
            continue
        if exclude_10b5_1 and row.aff10b5one:
            continue
        key = (row.issuer_cik, row.filing_date)
        day = days.setdefault(key, {"known_at": row.known_at, "symbols": [], "value": 0.0, "name": row.issuer_name})
        day["known_at"] = min(day["known_at"], row.known_at)
        day["value"] += row.shares * row.price_per_share
        symbol = clean_symbol(row.issuer_symbol)
        if symbol and symbol not in day["symbols"]:
            day["symbols"].append(symbol)

    events = [
        Event(
            issuer_cik=cik,
            symbol=day["symbols"][0] if day["symbols"] else None,
            event_date=filed,
            known_at=day["known_at"],
            label=f"{day['name'] or cik} insider purchase",
            value=day["value"],
        )
        for (cik, filed), day in days.items()
        if day["value"] >= min_value
    ]
    return sorted(events, key=lambda e: (e.known_at, e.issuer_cik))
