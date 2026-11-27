"""The base rate to show with an alert: what similar past events were followed by (ADR 0018).

The calculator stores base rates by kind of event, scope and filter setting (``base_rates``, written
daily by ``compute_base_rates``). An alert reads them here; nothing is calculated in the alert path.

* **Kind:** a Form 4 open-market purchase → ``insider_purchase``; an 8-K → one ``8k_item`` per high
  item it carries (1.01, 2.02, 4.02, 5.02).
* **Scope:** the company's industry (its two-digit SIC major group, from its SEC record) when that
  has at least ``MIN_INDUSTRY_EVENTS`` past events with a 20-session result under the main filters;
  otherwise the whole market (pooled). The reason is in ``scope_label``.

This is history, not a forecast: the text never says what to do.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg2.extensions import connection as Connection

from alerts.materiality import HIGH_8K_ITEMS
from filings.form_8k import item_title

MIN_INDUSTRY_EVENTS = 30
REFERENCE_HORIZON = 20
_STATS = (
    "n",
    "mean",
    "median",
    "hit_rate",
    "ci_low",
    "ci_high",
    "months",
    "mean_gross_return",
    "mean_benchmark_return",
    "mean_cost",
    "mean_excess_vs_reference",
    "ended_early",
)


@dataclass(frozen=True)
class AlertBaseRate:
    event_kind: str
    event_key: str
    label: str  # "8-K item 2.02 Results of Operations and Financial Condition"
    scope: str  # "industry" or "market"
    scope_label: str
    as_of: datetime
    # variant ("main", "none") → horizon ("1", "5", "20", "60") → stats, or None without results
    variants: dict[str, dict[str, dict[str, Any] | None]]

    def headline(self, variant: str = "main", horizon: int = REFERENCE_HORIZON) -> dict[str, Any] | None:
        return self.variants.get(variant, {}).get(str(horizon))


def alert_kinds(form_type: str, items: list[str]) -> list[tuple[str, str, str]]:
    """(kind, key, label) for each high event in a filing."""
    if form_type == "4":
        return [("insider_purchase", "", "Insider open-market purchase")]
    if form_type == "8-K":
        present = {item.strip() for item in items}
        return [
            ("8k_item", item, f"8-K item {item} {item_title(item) or ''}".strip())
            for item in HIGH_8K_ITEMS
            if item in present
        ]
    return []


def _industry(conn: Connection, cik: str) -> tuple[str, str | None] | None:
    with conn.cursor() as cursor:
        cursor.execute("SELECT sic, sic_description FROM market_issuers WHERE cik = %s AND sic IS NOT NULL", (cik,))
        row = cursor.fetchone()
    if row is None or not row[0][:2].isdigit():
        return None
    return f"sic2:{row[0][:2]}", row[1]


def _rows(conn: Connection, kind: str, key: str, scopes: list[str]) -> dict[str, dict[str, dict[str, dict]]]:
    with conn.cursor() as cursor:
        cursor.execute(
            f"SELECT scope, variant, horizon, as_of, {', '.join(_STATS)} FROM base_rates "
            "WHERE event_kind = %s AND event_key = %s AND scope = ANY(%s)",
            (kind, key, scopes),
        )
        found: dict[str, dict[str, dict[str, dict]]] = {}
        for scope, variant, horizon, as_of, *stats in cursor.fetchall():
            found.setdefault(scope, {}).setdefault(variant, {})[str(horizon)] = {
                **dict(zip(_STATS, stats, strict=True)),
                "as_of": as_of,
            }
    return found


def base_rates_for(conn: Connection, *, form_type: str, items: list[str], cik: str) -> list[AlertBaseRate]:
    """The base rate for each high event in a filing about the company ``cik``; empty if none is stored."""
    industry = _industry(conn, cik)
    results = []
    for kind, key, label in alert_kinds(form_type, items):
        scopes = ["market"] + ([industry[0]] if industry else [])
        found = _rows(conn, kind, key, scopes)
        scope, scope_label = "market", "All US-listed companies"
        if industry and industry[0] in found:
            reference = found[industry[0]].get("main", {}).get(str(REFERENCE_HORIZON))
            if reference and reference["n"] >= MIN_INDUSTRY_EVENTS:
                scope = "industry"
                scope_label = f"Same broad industry: SIC group {industry[0][5:]}" + (
                    f", which includes {industry[1]}" if industry[1] else ""
                )
        if scope == "market" and industry:
            scope_label += f" (fewer than {MIN_INDUSTRY_EVENTS} in its industry, so pooled)"
        chosen = found.get(industry[0] if scope == "industry" else "market")
        if not chosen:
            continue
        as_of = next(stats["as_of"] for horizons in chosen.values() for stats in horizons.values())
        variants = {
            variant: {
                str(h): (
                    {k: v for k, v in chosen[variant][str(h)].items() if k != "as_of"}
                    if str(h) in chosen.get(variant, {})
                    else None
                )
                for h in (1, 5, 20, 60)
            }
            for variant in ("main", "none")
        }
        results.append(AlertBaseRate(kind, key, label, scope, scope_label, as_of, variants))
    return results
