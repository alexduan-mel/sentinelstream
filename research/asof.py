"""Point-in-time data access for research (ground rule 2, ADR 0011).

Research code reads stored facts only through :class:`AsOf`. Every query it runs ends in
``known_at < as_of``, so a study dated ``as_of`` sees exactly what was public before that moment:

- a daily bar from its close (16:00 New York time on the trading date),
- a filing, and the Form 4 rows and XBRL values it carries, from EDGAR acceptance.

The comparison is strict. A filing accepted at 16:05:00 is not visible at ``as_of`` 16:05:00.

Other modules in ``research/`` must not query fact tables directly; ``tests/test_asof.py`` scans
them and fails if one does. New fact tables are added here, with a ``known_at`` column and a
lookahead test, before research code uses them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from psycopg2.extensions import connection as Connection

# Fact tables this layer reads. tests/test_asof.py uses this list to check that no other research
# module queries them directly.
FACT_TABLES = ("price_bars", "filings", "filing_issuers", "form4_transactions", "xbrl_facts")


class AsOfError(ValueError):
    """Raised for an invalid as-of time."""


@dataclass(frozen=True)
class Bar:
    symbol: str
    trading_date: date
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    volume: int | None
    data_quality: str
    known_at: datetime


@dataclass(frozen=True)
class Filing:
    accession_no: str
    cik: str
    form_type: str
    items: tuple[str, ...]
    filed_date: date
    accepted_at: datetime
    known_at: datetime


@dataclass(frozen=True)
class Form4Row:
    accession_no: str
    line_no: int
    issuer_cik: str
    reporting_owners: str
    owner_roles: str
    transaction_date: date | None
    transaction_code: str
    acquired_disposed: str | None
    shares: Decimal | None
    price_per_share: Decimal | None
    known_at: datetime


@dataclass(frozen=True)
class XbrlFact:
    cik: str
    taxonomy: str
    concept: str
    unit: str
    period_start: date | None
    period_end: date
    value: Decimal
    accession_no: str
    form: str | None
    filed_date: date
    known_at: datetime


class AsOf:
    """Read facts as they stood just before ``as_of``.

    ``as_of`` must be timezone-aware. One instance holds one moment; make a new one per moment
    rather than changing it.
    """

    def __init__(self, conn: Connection, as_of: datetime) -> None:
        if not isinstance(as_of, datetime):
            raise AsOfError("as_of must be a datetime")
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise AsOfError("as_of must be timezone-aware")
        self._conn = conn
        self._as_of = as_of

    @property
    def as_of(self) -> datetime:
        return self._as_of

    def bars(self, symbol: str, *, start: date | None = None, end: date | None = None) -> list[Bar]:
        """Daily bars for ``symbol`` whose close came before ``as_of``, oldest first."""
        where = ["i.symbol = %(symbol)s"]
        params: dict[str, Any] = {"symbol": symbol.upper()}
        if start is not None:
            where.append("b.trading_date >= %(start)s")
            params["start"] = start
        if end is not None:
            where.append("b.trading_date <= %(end)s")
            params["end"] = end
        rows = self._select(
            "SELECT i.symbol, b.trading_date, b.open, b.high, b.low, b.close, b.volume, b.data_quality, b.known_at "
            "FROM price_bars b JOIN instruments i ON i.id = b.instrument_id",
            "b.known_at",
            where,
            params,
            order_by="b.trading_date",
        )
        return [Bar(*row) for row in rows]

    def filings(
        self,
        *,
        cik: str | None = None,
        form_types: tuple[str, ...] | list[str] | None = None,
        since: datetime | None = None,
    ) -> list[Filing]:
        """Filings accepted before ``as_of``, oldest first.

        With ``cik``, every filing linked to that issuer, including one stored under another issuer
        that shares it (ADR 0007). ``cik`` in each result is then the one asked for.
        """
        where: list[str] = []
        params: dict[str, Any] = {}
        if cik is not None:
            source = (
                "SELECT f.accession_no, fi.cik, f.form_type, f.items, f.filed_date, f.accepted_at, f.known_at "
                "FROM filings f JOIN filing_issuers fi ON fi.accession_no = f.accession_no"
            )
            where.append("fi.cik = %(cik)s")
            params["cik"] = cik
        else:
            source = (
                "SELECT f.accession_no, f.cik, f.form_type, f.items, f.filed_date, f.accepted_at, f.known_at "
                "FROM filings f"
            )
        if form_types is not None:
            where.append("f.form_type = ANY(%(form_types)s)")
            params["form_types"] = list(form_types)
        if since is not None:
            where.append("f.known_at >= %(since)s")
            params["since"] = since
        rows = self._select(source, "f.known_at", where, params, order_by="f.known_at, f.accession_no")
        return [
            Filing(acc, c, form, tuple(items or ()), filed, acc_at, known)
            for acc, c, form, items, filed, acc_at, known in rows
        ]

    def form4_transactions(
        self,
        *,
        issuer_cik: str | None = None,
        codes: tuple[str, ...] | list[str] | None = None,
        since: datetime | None = None,
    ) -> list[Form4Row]:
        """Non-derivative Form 4 rows from filings accepted before ``as_of``, oldest first."""
        where: list[str] = []
        params: dict[str, Any] = {}
        if issuer_cik is not None:
            where.append("t.issuer_cik = %(issuer_cik)s")
            params["issuer_cik"] = issuer_cik
        if codes is not None:
            where.append("t.transaction_code = ANY(%(codes)s)")
            params["codes"] = list(codes)
        if since is not None:
            where.append("t.known_at >= %(since)s")
            params["since"] = since
        rows = self._select(
            "SELECT t.accession_no, t.line_no, t.issuer_cik, t.reporting_owners, t.owner_roles, t.transaction_date, "
            "t.transaction_code, t.acquired_disposed, t.shares, t.price_per_share, t.known_at "
            "FROM form4_transactions t",
            "t.known_at",
            where,
            params,
            order_by="t.known_at, t.accession_no, t.line_no",
        )
        return [Form4Row(*row) for row in rows]

    def xbrl_facts(self, cik: str, concept: str, *, unit: str | None = None) -> list[XbrlFact]:
        """Every value filed for ``concept`` before ``as_of``, restated ones included, oldest first."""
        where = ["x.cik = %(cik)s", "x.concept = %(concept)s"]
        params: dict[str, Any] = {"cik": cik, "concept": concept}
        if unit is not None:
            where.append("x.unit = %(unit)s")
            params["unit"] = unit
        rows = self._select(
            "SELECT x.cik, x.taxonomy, x.concept, x.unit, x.period_start, x.period_end, x.value, x.accession_no, "
            "x.form, x.filed_date, x.known_at FROM xbrl_facts x",
            "x.known_at",
            where,
            params,
            order_by="x.period_end, x.known_at, x.accession_no",
        )
        return [XbrlFact(*row) for row in rows]

    def latest_xbrl_values(self, cik: str, concept: str, *, unit: str | None = None) -> list[XbrlFact]:
        """One value per period: the most recently filed one before ``as_of``, by period end.

        A later restatement replaces the earlier value only once the restating filing is public.
        """
        latest: dict[tuple[str, str, date | None, date], XbrlFact] = {}
        for fact in self.xbrl_facts(cik, concept, unit=unit):
            key = (fact.taxonomy, fact.unit, fact.period_start, fact.period_end)
            held = latest.get(key)
            if held is None or (fact.known_at, fact.accession_no) > (held.known_at, held.accession_no):
                latest[key] = fact
        return sorted(latest.values(), key=lambda f: (f.period_end, f.period_start or date.min, f.unit))

    def _select(
        self, source: str, known_at_column: str, where: list[str], params: dict[str, Any], *, order_by: str
    ) -> list[tuple[Any, ...]]:
        # The point-in-time condition is added here, not by callers, so no query can leave it out.
        conditions = [*where, f"{known_at_column} < %(as_of)s"]
        query = f"{source} WHERE {' AND '.join(conditions)} ORDER BY {order_by}"
        with self._conn.cursor() as cursor:
            cursor.execute(query, {**params, "as_of": self._as_of})
            return cursor.fetchall()
