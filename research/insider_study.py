"""The insider-buying study behind ``docs/research/insider-buying.md``.

It runs the base-rate calculator (ADR 0015) on insider purchases and on two control groups, then
compares them. Comparing insider buys with QQQ alone can mislead: the companies whose insiders buy
are mostly small, and small stocks can lag QQQ for reasons that have nothing to do with insiders.
The controls hold that part fixed.

- **Same stocks, other dates:** each stock with an insider buy, entered on random sessions at least
  ``GAP_DAYS`` calendar days away from any of its own insider buys.
- **Same dates, other stocks:** each insider-buy date, with random stocks from the same pool (stocks
  whose insiders bought at some point) that had no insider buy within ``GAP_DAYS`` of that date.

Each control is drawn ``CONTROLS_PER_EVENT`` times per event with a fixed seed, so a rerun on the
same data gives the same answer. Every read goes through one as-of reader.

Run it inside the app image: ``python -m research.insider_study [--as-of YYYY-MM-DD]``.
"""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from ingestion.market_calendar import NEW_YORK
from research.asof import AsOf
from research.base_rates import MAIN, UNFILTERED, Outcome, Settings, evaluate_kinds
from research.events import Event, insider_purchase_events
from research.stats import DEFAULT_REPS, SEED, summarise

GAP_DAYS = 30
CONTROLS_PER_EVENT = 3
SIZE_BANDS: tuple[tuple[str, float, float], ...] = (
    ("under $50k", 0.0, 50_000.0),
    ("$50k to $500k", 50_000.0, 500_000.0),
    ("$500k or more", 500_000.0, float("inf")),
)


def _known_at(session: date) -> datetime:
    """Midnight New York after ``session``: the convention insider filings use (ADR 0014)."""
    return datetime.combine(session + timedelta(days=1), time.min, tzinfo=NEW_YORK)


def _far(day: date, days: Sequence[date], gap: int) -> bool:
    return all(abs((day - other).days) >= gap for other in days)


def same_stock_controls(
    events: Sequence[Event],
    sessions: Sequence[date],
    *,
    per_event: int = CONTROLS_PER_EVENT,
    gap: int = GAP_DAYS,
    seed: int = SEED,
) -> list[Event]:
    """For each event, ``per_event`` sessions of the same stock away from all of its insider buys.

    Sessions are drawn from the span the events cover, so the controls live through the same market.
    """
    rng = random.Random(seed)
    buys: dict[str, list[date]] = defaultdict(list)
    for event in events:
        buys[event.symbol].append(event.event_date)
    if not events:
        return []
    first, last = min(e.event_date for e in events), max(e.event_date for e in events)
    span = [s for s in sessions if first <= s <= last]
    allowed: dict[str, list[date]] = {}
    controls = []
    for event in events:
        if event.symbol not in allowed:
            allowed[event.symbol] = [s for s in span if _far(s, buys[event.symbol], gap)]
        choices = allowed[event.symbol]
        for session in rng.sample(choices, min(per_event, len(choices))):
            controls.append(_control(event.symbol, event.issuer_cik, session))
    return controls


def same_date_controls(
    events: Sequence[Event], *, per_event: int = CONTROLS_PER_EVENT, gap: int = GAP_DAYS, seed: int = SEED
) -> list[Event]:
    """For each event, ``per_event`` other stocks from the pool with no insider buy near its date."""
    rng = random.Random(seed)
    buys: dict[str, list[date]] = defaultdict(list)
    ciks: dict[str, str] = {}
    for event in events:
        buys[event.symbol].append(event.event_date)
        ciks[event.symbol] = event.issuer_cik
    pool = sorted(buys)
    controls = []
    for event in events:
        chosen: list[str] = []
        for _ in range(per_event * 20):  # bounded retries; a crowded date just gets fewer controls
            if len(chosen) == per_event:
                break
            symbol = rng.choice(pool)
            if symbol != event.symbol and symbol not in chosen and _far(event.event_date, buys[symbol], gap):
                chosen.append(symbol)
        controls.extend(_control(symbol, ciks[symbol], event.event_date) for symbol in chosen)
    return controls


def _control(symbol: str, cik: str, session: date) -> Event:
    return Event(
        issuer_cik=cik, symbol=symbol, event_date=session, known_at=_known_at(session), label="control", value=0.0
    )


@dataclass(frozen=True)
class Row:
    group: str
    variant: str
    horizon: int
    stats: dict

    def as_dict(self) -> dict:
        return {"group": self.group, "variant": self.variant, "horizon": self.horizon, **self.stats}


def _month(outcome: Outcome) -> date:
    return outcome.entry_date.replace(day=1)


def _summary(outcomes: Sequence[Outcome], value: Callable[[Outcome], float]) -> dict | None:
    summary = summarise([value(o) for o in outcomes], [_month(o) for o in outcomes])
    return None if summary is None else summary.as_dict()


def difference(
    treated: Sequence[Outcome],
    control: Sequence[Outcome],
    value: Callable[[Outcome], float],
    *,
    reps: int = DEFAULT_REPS,
    seed: int = SEED,
) -> dict:
    """Mean of ``treated`` minus mean of ``control``, with a 95% range from resampling whole months.

    Both groups are resampled by the same months, so the market's month-to-month moves, shared by
    both, cancel out of the range rather than widening it.
    """
    sums: dict[date, list[float]] = defaultdict(lambda: [0.0, 0, 0.0, 0])
    for o in treated:
        sums[_month(o)][0] += value(o)
        sums[_month(o)][1] += 1
    for o in control:
        sums[_month(o)][2] += value(o)
        sums[_month(o)][3] += 1
    months = [m for m in sums.values() if m[1] and m[3]]

    def diff(sample: Sequence[list[float]]) -> float:
        return sum(m[0] for m in sample) / sum(m[1] for m in sample) - sum(m[2] for m in sample) / sum(
            m[3] for m in sample
        )

    rng = random.Random(seed)
    draws = sorted(diff(rng.choices(months, k=len(months))) for _ in range(reps))
    return {
        "diff": diff(months),
        "ci_low": draws[int(0.025 * reps)],
        "ci_high": draws[int(0.975 * reps) - 1],
        "months": len(months),
    }


def _gross_excess(o: Outcome) -> float:
    """Return before costs minus QQQ's: what the stock did, apart from what trading it costs."""
    return o.gross_return - o.benchmark_return


def _net_excess(o: Outcome) -> float:
    return o.excess


def run(reader: AsOf, *, variants: tuple[Settings, ...] = (MAIN, UNFILTERED)) -> dict:
    """The whole study as a JSON-ready dict."""
    events = [e for e in insider_purchase_events(reader) if e.symbol]
    sessions = [bar.trading_date for bar in reader.market_bars(MAIN.benchmark)]
    groups: dict[str, list[Event]] = {
        "insider buys": events,
        "same stocks, other dates": same_stock_controls(events, sessions),
        "same dates, other stocks": same_date_controls(events),
    }
    for label, low, high in SIZE_BANDS:
        groups[f"insider buys, {label}"] = [e for e in events if low <= e.value < high]
    kinds, _ = evaluate_kinds(reader, groups, variants)

    rows: list[Row] = []
    differences: list[dict] = []
    for variant in variants:
        by_group = {g: kinds[g].variants[variant.name].outcomes for g in groups}
        for horizon in variant.horizons:
            at = {g: [o for o in outs if o.horizon == horizon] for g, outs in by_group.items()}
            for group, outs in at.items():
                gross, net = _summary(outs, _gross_excess), _summary(outs, _net_excess)
                if gross is None:
                    continue
                mean_cost = sum(o.cost for o in outs) / len(outs)
                rows.append(Row(group, variant.name, horizon, {"gross": gross, "net": net, "mean_cost": mean_cost}))
            for control in ("same stocks, other dates", "same dates, other stocks"):
                if at["insider buys"] and at[control]:
                    differences.append(
                        {
                            "variant": variant.name,
                            "horizon": horizon,
                            "control": control,
                            **difference(at["insider buys"], at[control], _gross_excess),
                        }
                    )
    return {
        "as_of": reader.as_of.isoformat(),
        "events": {g: kinds[g].events for g in groups},
        "rows": [r.as_dict() for r in rows],
        "differences": differences,
    }


def main(argv: list[str] | None = None) -> int:
    from orchestration.db import connect

    parser = argparse.ArgumentParser(description="Insider purchases against two control groups.")
    parser.add_argument("--as-of", type=date.fromisoformat, help="YYYY-MM-DD; default now")
    args = parser.parse_args(argv)
    as_of = datetime.combine(args.as_of, time.min, tzinfo=NEW_YORK) if args.as_of else datetime.now(UTC)
    with connect(application_name="insider_study") as conn:
        print(json.dumps(run(AsOf(conn, as_of)), indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
