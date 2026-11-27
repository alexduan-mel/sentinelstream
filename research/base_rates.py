"""The base-rate calculator: what stocks did after past events of one kind (ADR 0015).

For each event it buys at the first close after the event became public and sells 1, 5, 20 and 60
sessions later. It takes trading costs off, and compares the result with QQQ over the same days
(SPY is shown for reference). Then it summarises across events: sample size, average, median, share
that beat QQQ, and a 95% confidence range for the average. Nothing is predicted or learned; it is
an average over history.

Every read goes through one :class:`~research.asof.AsOf` reader, so a calculation dated ``as_of``
uses only what was public then: an event whose 60th session has not closed yet simply has no
60-session result.

**Costs.** Each trade, buying and selling, costs the larger of ``cost_per_trade`` (0.10%) and half the
stock's estimated bid–ask spread over the 20 sessions before entry, so thinly traded stocks are
charged what they really cost to trade.

**Filters** are settings, measured only from data before the entry session. ``MAIN`` keeps stocks
whose last close before entry was at least $5 (as traded, so a later split can't change the
answer), with a median traded value of at least $1M a day over the 20 sessions before entry.
``UNFILTERED`` keeps every stock that traded on the entry day. Both are always reported side by
side.
"""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from psycopg2.extensions import connection as Connection
from psycopg2.extras import execute_values

from alerts.materiality import HIGH_8K_ITEMS
from ingestion.market_calendar import NEW_YORK
from orchestration.task_runs import RunResult, TaskRun
from research.asof import AsOf, MarketBar
from research.events import Event, eight_k_events, insider_purchase_events
from research.returns import abdi_ranaldo_spread, entry_index, hold, median_traded_value
from research.stats import summarise

LOGGER = logging.getLogger(__name__)

TASK_ID = "compute_base_rates"


@dataclass(frozen=True)
class Settings:
    name: str = "main"
    horizons: tuple[int, ...] = (1, 5, 20, 60)
    cost_per_trade: float = 0.001
    use_spread_estimate: bool = True
    min_price: float | None = 5.0
    min_traded_value: float | None = 1_000_000.0
    lookback_sessions: int = 20
    min_lookback_bars: int = 10
    benchmark: str = "QQQ"
    reference: str = "SPY"


MAIN = Settings()
UNFILTERED = Settings(name="none", min_price=None, min_traded_value=None)


@dataclass(frozen=True)
class Outcome:
    event: Event
    horizon: int
    entry_date: date
    exit_date: date
    gross_return: float
    cost: float  # round trip
    benchmark_return: float
    reference_return: float | None
    ended_early: bool

    @property
    def net_return(self) -> float:
        return self.gross_return - self.cost

    @property
    def excess(self) -> float:
        """Return after costs minus QQQ's over the same days."""
        return self.net_return - self.benchmark_return

    @property
    def excess_reference(self) -> float | None:
        return None if self.reference_return is None else self.net_return - self.reference_return


@dataclass
class VariantResult:
    settings: Settings
    outcomes: list[Outcome] = field(default_factory=list)
    skipped: Counter = field(default_factory=Counter)

    def report(self) -> dict[str, Any]:
        return {
            "settings": asdict(self.settings),
            "horizons": horizon_summaries(self.outcomes, self.settings.horizons),
            "skipped": dict(sorted(self.skipped.items())),
        }


def horizon_summaries(outcomes: list[Outcome], horizons: tuple[int, ...]) -> dict[str, Any]:
    """Horizon → summary of the outcomes' excess returns (None for a horizon without any)."""
    by_horizon: dict[str, Any] = {}
    for horizon in horizons:
        rows = [o for o in outcomes if o.horizon == horizon]
        summary = summarise([o.excess for o in rows], [o.entry_date.strftime("%Y-%m") for o in rows])
        if summary is None:
            by_horizon[str(horizon)] = None
            continue
        references = [o.excess_reference for o in rows if o.excess_reference is not None]
        by_horizon[str(horizon)] = {
            **{key: _round(value) for key, value in summary.as_dict().items()},
            "mean_gross_return": _round(sum(o.gross_return for o in rows) / len(rows)),
            "mean_benchmark_return": _round(sum(o.benchmark_return for o in rows) / len(rows)),
            "mean_cost": _round(sum(o.cost for o in rows) / len(rows)),
            "mean_excess_vs_reference": _round(sum(references) / len(references)) if references else None,
            "ended_early": sum(1 for o in rows if o.ended_early),
        }
    return by_horizon


def _round(value: Any) -> Any:
    return round(value, 6) if isinstance(value, float) else value


@dataclass(frozen=True)
class _Prepared:
    """What every variant needs about one event, computed once."""

    event: Event
    entry_idx: int
    pre_close: float | None
    traded_value: float | None
    window_bars: int
    spread: float | None


class _Series:
    def __init__(self, bars: list[MarketBar]) -> None:
        self.by_date = {bar.trading_date: bar for bar in bars}
        self.dates = [bar.trading_date for bar in bars]


@dataclass
class KindResult:
    """One kind of event's outcomes under each variant."""

    events: int
    matched: int = 0
    variants: dict[str, VariantResult] = field(default_factory=dict)


def evaluate_kinds(
    reader: AsOf, events_by_kind: dict[str, list[Event]], variants: tuple[Settings, ...] = (MAIN, UNFILTERED)
) -> tuple[dict[str, KindResult], list[date]]:
    """Outcomes of several kinds of event in one pass: each stock's bars are read once for all kinds.

    Returns each kind's results and the sessions loaded.
    """
    benchmark_symbol = variants[0].benchmark
    if any(v.benchmark != benchmark_symbol for v in variants):
        raise ValueError("all variants must share one benchmark")
    benchmark_bars = reader.market_bars(benchmark_symbol)
    if not benchmark_bars:
        raise ValueError(f"no {benchmark_symbol} bars loaded before {reader.as_of.isoformat()}")
    sessions = [bar.trading_date for bar in benchmark_bars]
    closes_at = [bar.known_at for bar in benchmark_bars]
    benchmark = _Series(benchmark_bars)
    benchmark_splits = reader.splits(benchmark_symbol)
    references = {v.reference: _Series(reader.market_bars(v.reference)) for v in variants if v.reference}
    reference_splits = {symbol: reader.splits(symbol) for symbol in references}

    kinds = {
        kind: KindResult(events=len(events), variants={v.name: VariantResult(v) for v in variants})
        for kind, events in events_by_kind.items()
    }
    by_symbol: dict[str | None, list[tuple[str, Event]]] = defaultdict(list)
    for kind, events in events_by_kind.items():
        for event in events:
            by_symbol[event.symbol].append((kind, event))

    lookback = max(v.lookback_sessions for v in variants)
    for count, (symbol, tagged) in enumerate(sorted(by_symbol.items(), key=lambda kv: kv[0] or ""), 1):
        bars = reader.market_bars(symbol) if symbol is not None else []
        if not bars:
            reason = "no_symbol" if symbol is None else "no_price_data"
            for kind, _event in tagged:
                for result in kinds[kind].variants.values():
                    result.skipped[reason] += 1
            continue
        series, splits = _Series(bars), reader.splits(symbol)
        for kind, event in tagged:
            kinds[kind].matched += 1
            prepared = _prepare(event, series, splits, sessions, closes_at, lookback)
            for result in kinds[kind].variants.values():
                if isinstance(prepared, str):
                    result.skipped[prepared] += 1
                    continue
                _apply(
                    result,
                    prepared,
                    series,
                    splits,
                    sessions,
                    benchmark,
                    benchmark_splits,
                    references,
                    reference_splits,
                )
        if count % 1000 == 0:
            LOGGER.info("base_rates_progress symbols=%d/%d", count, len(by_symbol))
    return kinds, sessions


def evaluate(reader: AsOf, events: list[Event], variants: tuple[Settings, ...] = (MAIN, UNFILTERED)) -> dict[str, Any]:
    """Outcomes and summaries of ``events`` under each of ``variants``, as a JSON-ready report."""
    kinds, sessions = evaluate_kinds(reader, {"events": events}, variants)
    result = kinds["events"]
    return {
        "as_of": reader.as_of.isoformat(),
        "events": result.events,
        "matched_to_prices": result.matched,
        "sessions": [sessions[0].isoformat(), sessions[-1].isoformat()],
        "variants": {name: variant.report() for name, variant in result.variants.items()},
    }


def _prepare(event, series, splits, sessions, closes_at, lookback) -> _Prepared | str:
    idx = entry_index(sessions, closes_at, event.known_at)
    if idx is None:
        return "no_session_after_event"
    entry_date = sessions[idx]
    if entry_date not in series.by_date:
        return "no_entry_bar"
    window_dates = set(sessions[max(0, idx - lookback) : idx])
    window = [series.by_date[d] for d in sorted(window_dates) if d in series.by_date]
    earlier = [d for d in series.dates if d < entry_date]
    pre_close = series.by_date[earlier[-1]].close if earlier else None
    return _Prepared(
        event=event,
        entry_idx=idx,
        pre_close=pre_close,
        traded_value=median_traded_value(window),
        window_bars=len(window),
        spread=abdi_ranaldo_spread(window, splits),
    )


def _apply(result, prepared, series, splits, sessions, benchmark, benchmark_splits, references, reference_splits):
    settings = result.settings
    if settings.min_price is not None and (prepared.pre_close is None or prepared.pre_close < settings.min_price):
        result.skipped["below_min_price"] += 1
        return
    if settings.min_traded_value is not None:
        if prepared.window_bars < settings.min_lookback_bars or prepared.traded_value is None:
            result.skipped["too_little_history"] += 1
            return
        if prepared.traded_value < settings.min_traded_value:
            result.skipped["below_min_traded_value"] += 1
            return
    per_trade = settings.cost_per_trade
    if settings.use_spread_estimate and prepared.spread is not None:
        per_trade = max(per_trade, prepared.spread / 2)
    entry_date = sessions[prepared.entry_idx]
    for horizon in settings.horizons:
        exit_idx = prepared.entry_idx + horizon
        if exit_idx >= len(sessions):
            result.skipped[f"horizon_{horizon}_not_reached"] += 1
            continue
        leg = hold(series.by_date, series.dates, splits, entry_date, sessions[exit_idx])
        if leg is None:
            result.skipped[f"horizon_{horizon}_no_price"] += 1
            continue
        bench = hold(benchmark.by_date, benchmark.dates, benchmark_splits, entry_date, leg.exit_date)
        if bench is None:
            result.skipped[f"horizon_{horizon}_no_benchmark"] += 1
            continue
        reference_return = None
        if settings.reference in references:
            ref_series = references[settings.reference]
            ref = (
                hold(
                    ref_series.by_date,
                    ref_series.dates,
                    reference_splits[settings.reference],
                    entry_date,
                    leg.exit_date,
                )
                if ref_series.dates
                else None
            )
            reference_return = ref.gross_return if ref else None
        result.outcomes.append(
            Outcome(
                event=prepared.event,
                horizon=horizon,
                entry_date=entry_date,
                exit_date=leg.exit_date,
                gross_return=leg.gross_return,
                cost=2 * per_trade,
                benchmark_return=bench.gross_return,
                reference_return=reference_return,
                ended_early=leg.ended_early,
            )
        )


EVENT_KINDS = ("alerts", "insider_purchases", "8k_items")
MARKET = "market"


def industry_scope(sic: str | None) -> str | None:
    """``sic2:36`` for SIC 3674: the two-digit major group, a broad industry."""
    if not sic or not sic[:2].isdigit():
        return None
    return f"sic2:{sic[:2]}"


def alert_events(reader: AsOf) -> dict[tuple[str, str], list[Event]]:
    """Every kind of event that can make an alert high (ADR 0008), keyed by (kind, key)."""
    kinds: dict[tuple[str, str], list[Event]] = {("insider_purchase", ""): insider_purchase_events(reader)}
    for item in HIGH_8K_ITEMS:
        kinds[("8k_item", item)] = eight_k_events(reader, [item])
    return kinds


def alert_base_rate_rows(reader: AsOf) -> tuple[list[tuple], dict[str, Any]]:
    """Rows for ``base_rates``: each alert kind, for the whole market and for each industry.

    Returns the rows and a short summary for the run's meta.
    """
    events = alert_events(reader)
    results, sessions = evaluate_kinds(reader, {f"{kind}:{key}": evs for (kind, key), evs in events.items()})
    industries = reader.issuer_industries()
    rows: list[tuple] = []
    summary: dict[str, Any] = {}
    for (kind, key), name in ((k, f"{k[0]}:{k[1]}") for k in events):
        result = results[name]
        for variant_name, variant in result.variants.items():
            scopes: dict[str, list[Outcome]] = defaultdict(list)
            scopes[MARKET] = variant.outcomes
            for outcome in variant.outcomes:
                scope = industry_scope(industries.get(outcome.event.issuer_cik))
                if scope:
                    scopes[scope].append(outcome)
            for scope, outcomes in scopes.items():
                for horizon, stats in horizon_summaries(outcomes, variant.settings.horizons).items():
                    if stats is not None:
                        rows.append((kind, key, scope, variant_name, int(horizon), stats))
        main_market = horizon_summaries(result.variants[MAIN.name].outcomes, MAIN.horizons)
        summary[name] = {
            "events": result.events,
            "matched": result.matched,
            "main_n": {h: (s["n"] if s else 0) for h, s in main_market.items()},
        }
    return rows, {"sessions": [sessions[0].isoformat(), sessions[-1].isoformat()], "kinds": summary}


_STAT_COLUMNS = (
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


def store_base_rates(conn: Connection, rows: list[tuple], *, as_of: datetime, task_run_id: int) -> None:
    """Replace every stored base rate with ``rows``, in one transaction."""
    with conn.cursor() as cursor:
        cursor.execute("DELETE FROM base_rates")
        if rows:
            execute_values(
                cursor,
                f"""
                INSERT INTO base_rates (event_kind, event_key, scope, variant, horizon, {", ".join(_STAT_COLUMNS)},
                                        as_of, task_run_id)
                VALUES %s
                """,
                [
                    (
                        kind,
                        key,
                        scope,
                        variant,
                        horizon,
                        *(stats[c] for c in _STAT_COLUMNS),
                        as_of,
                        task_run_id,
                    )
                    for kind, key, scope, variant, horizon, stats in rows
                ],
                page_size=1000,
            )
    conn.commit()


_ITEM = re.compile(r"^[1-9]\.[0-9]{2}$")


def compute_base_rates(run: TaskRun, conn: Connection, *, now: datetime | None = None) -> RunResult:
    """Task handler: run the calculator for one kind of event and record the report in the run's meta.

    Payload: ``{"event": "alerts"}`` (the daily run: every alert kind, by market and industry, stored
    in ``base_rates``), ``{"event": "insider_purchases", "exclude_10b5_1": false}`` or
    ``{"event": "8k_items", "items": ["2.02"]}`` (a report in the run's meta only). With an ``as_of_date`` the
    calculation is dated the start of that day in New York, as if run that morning.
    """
    kind = run.payload.get("event", "insider_purchases")
    if kind not in EVENT_KINDS:
        raise ValueError(f"event must be one of {', '.join(EVENT_KINDS)}, got {kind!r}")
    if run.as_of_date is not None:
        as_of = datetime.combine(run.as_of_date, datetime.min.time(), tzinfo=NEW_YORK)
    else:
        as_of = now or datetime.now(UTC)
    reader = AsOf(conn, as_of)
    if kind == "alerts":
        rows, summary = alert_base_rate_rows(reader)
        store_base_rates(conn, rows, as_of=as_of, task_run_id=run.id)
        LOGGER.info("base_rates_stored run_id=%d rows=%d", run.id, len(rows))
        return RunResult(
            status="succeeded",
            fetched_count=sum(k["events"] for k in summary["kinds"].values()),
            inserted_count=len(rows),
            meta={"event": kind, "as_of": as_of.isoformat(), "rows": len(rows), **summary},
        )
    if kind == "8k_items":
        items = run.payload.get("items")
        if not isinstance(items, list) or not items or not all(isinstance(i, str) and _ITEM.match(i) for i in items):
            raise ValueError(f'8k_items needs items like ["2.02"], got {items!r}')
        events = eight_k_events(reader, items)
    else:
        events = insider_purchase_events(reader, exclude_10b5_1=bool(run.payload.get("exclude_10b5_1", False)))
    report = evaluate(reader, events)
    conn.commit()
    LOGGER.info(
        "base_rates_computed run_id=%d event=%s events=%d matched=%d",
        run.id,
        kind,
        report["events"],
        report["matched_to_prices"],
    )
    options = {"items": run.payload.get("items")} if kind == "8k_items" else {}
    return RunResult(
        status="succeeded", fetched_count=report["events"], meta={"event": kind, **options, "report": report}
    )
