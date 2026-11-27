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
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from psycopg2.extensions import connection as Connection

from ingestion.market_calendar import NEW_YORK
from orchestration.task_runs import RunResult, TaskRun
from research.asof import AsOf, MarketBar
from research.events import Event, insider_purchase_events
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
        by_horizon: dict[str, Any] = {}
        for horizon in self.settings.horizons:
            rows = [o for o in self.outcomes if o.horizon == horizon]
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
        return {
            "settings": asdict(self.settings),
            "horizons": by_horizon,
            "skipped": dict(sorted(self.skipped.items())),
        }


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


def evaluate(reader: AsOf, events: list[Event], variants: tuple[Settings, ...] = (MAIN, UNFILTERED)) -> dict[str, Any]:
    """Outcomes and summaries of ``events`` under each of ``variants``, as a JSON-ready report."""
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

    results = {v.name: VariantResult(v) for v in variants}
    by_symbol: dict[str | None, list[Event]] = defaultdict(list)
    for event in events:
        by_symbol[event.symbol].append(event)

    matched = 0
    lookback = max(v.lookback_sessions for v in variants)
    for count, (symbol, symbol_events) in enumerate(sorted(by_symbol.items(), key=lambda kv: kv[0] or ""), 1):
        if symbol is None:
            for result in results.values():
                result.skipped["no_symbol"] += len(symbol_events)
            continue
        bars = reader.market_bars(symbol)
        if not bars:
            for result in results.values():
                result.skipped["no_price_data"] += len(symbol_events)
            continue
        matched += len(symbol_events)
        series, splits = _Series(bars), reader.splits(symbol)
        for event in symbol_events:
            prepared = _prepare(event, series, splits, sessions, closes_at, lookback)
            if isinstance(prepared, str):
                for result in results.values():
                    result.skipped[prepared] += 1
                continue
            for result in results.values():
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

    return {
        "as_of": reader.as_of.isoformat(),
        "events": len(events),
        "matched_to_prices": matched,
        "sessions": [sessions[0].isoformat(), sessions[-1].isoformat()],
        "variants": {name: result.report() for name, result in results.items()},
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


EVENT_KINDS = ("insider_purchases",)


def compute_base_rates(run: TaskRun, conn: Connection, *, now: datetime | None = None) -> RunResult:
    """Task handler: run the calculator for one kind of event and record the report in the run's meta.

    Payload: ``{"event": "insider_purchases", "exclude_10b5_1": false}``. With an ``as_of_date`` the
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
    return RunResult(status="succeeded", fetched_count=report["events"], meta={"event": kind, "report": report})
