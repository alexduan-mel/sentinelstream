"""XBRL company facts as SEC's ``companyfacts`` API reports them, flattened to one row per value.

The API nests facts as taxonomy → concept → unit → list of values, each carrying the accession number
of the filing that reported it, the filing date and the period. Values are kept exactly as filed:
mapping companies' different tags to one line item (PRD: "a mapping layer is needed") is M3 work.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

_ACCESSION = re.compile(r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")


@dataclass(frozen=True)
class XbrlFact:
    taxonomy: str
    concept: str
    unit: str
    period_start: date | None
    period_end: date
    value: Decimal
    accession_no: str
    fiscal_year: int | None
    fiscal_period: str | None
    form: str | None
    filed_date: date
    frame: str | None


def _optional_date(value: Any) -> date | None:
    return date.fromisoformat(str(value)) if value not in (None, "") else None


def _value(raw: Any) -> Decimal:
    if isinstance(raw, bool) or not isinstance(raw, int | float | str):
        raise ValueError(f"not a number: {raw!r}")
    number = Decimal(str(raw))
    if not number.is_finite():
        raise ValueError(f"not a finite number: {raw!r}")
    return number


def parse_company_facts(payload: dict[str, Any], *, filed_since: date) -> tuple[list[XbrlFact], int]:
    """Facts filed on or after ``filed_since``, plus how many entries were malformed and skipped."""
    facts: list[XbrlFact] = []
    invalid = 0
    for taxonomy, concepts in (payload.get("facts") or {}).items():
        if not isinstance(concepts, dict):
            continue
        for concept, body in concepts.items():
            units = body.get("units") if isinstance(body, dict) else None
            if not isinstance(units, dict):
                continue
            for unit, entries in units.items():
                for entry in entries if isinstance(entries, list) else []:
                    try:
                        filed = date.fromisoformat(str(entry["filed"]))
                        if filed < filed_since:
                            continue
                        accession = str(entry["accn"]).strip()
                        if not _ACCESSION.match(accession):
                            raise ValueError(f"bad accession number {accession!r}")
                        start = _optional_date(entry.get("start"))
                        end = date.fromisoformat(str(entry["end"]))
                        if start is not None and start > end:
                            raise ValueError("period starts after it ends")
                        fiscal_year = entry.get("fy")
                        facts.append(
                            XbrlFact(
                                taxonomy=str(taxonomy),
                                concept=str(concept),
                                unit=str(unit),
                                period_start=start,
                                period_end=end,
                                value=_value(entry["val"]),
                                accession_no=accession,
                                fiscal_year=int(fiscal_year) if fiscal_year not in (None, "") else None,
                                fiscal_period=(str(entry.get("fp") or "").strip() or None),
                                form=(str(entry.get("form") or "").strip() or None),
                                filed_date=filed,
                                frame=(str(entry.get("frame") or "").strip() or None),
                            )
                        )
                    except (KeyError, TypeError, ValueError, InvalidOperation):
                        invalid += 1
    return facts, invalid
