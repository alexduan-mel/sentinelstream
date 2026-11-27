"""Summaries of per-event results: sample size, average, median, hit rate and a confidence range.

Events in the same month share the market's moves, so they are not independent. The confidence range
therefore resamples whole calendar months of events (a cluster bootstrap) rather than single events;
resampling single events would make it look narrower than the evidence supports. The random seed
is fixed, so the same data always gives the same range.
"""

from __future__ import annotations

import random
import statistics
from collections import defaultdict
from collections.abc import Hashable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Summary:
    n: int
    mean: float
    median: float
    hit_rate: float  # share above zero
    ci_low: float | None  # 95% range for the mean; None with fewer than MIN_CLUSTERS months
    ci_high: float | None
    clusters: int

    def as_dict(self) -> dict:
        return {
            "n": self.n,
            "mean": self.mean,
            "median": self.median,
            "hit_rate": self.hit_rate,
            "ci_low": self.ci_low,
            "ci_high": self.ci_high,
            "months": self.clusters,
        }


MIN_CLUSTERS = 6
DEFAULT_REPS = 2000
SEED = 20261127


def summarise(
    values: Sequence[float], clusters: Sequence[Hashable], *, reps: int = DEFAULT_REPS, seed: int = SEED
) -> Summary | None:
    """Summary of ``values``, with a 95% cluster-bootstrap range for the mean. None for no values."""
    if len(values) != len(clusters):
        raise ValueError("values and clusters must have the same length")
    if not values:
        return None
    totals: dict[Hashable, list[float]] = defaultdict(lambda: [0.0, 0])
    for value, cluster in zip(values, clusters, strict=True):
        totals[cluster][0] += value
        totals[cluster][1] += 1
    groups = list(totals.values())
    low = high = None
    if len(groups) >= MIN_CLUSTERS:
        rng = random.Random(seed)
        means = []
        for _ in range(reps):
            sample = rng.choices(groups, k=len(groups))
            means.append(sum(s for s, _ in sample) / sum(c for _, c in sample))
        means.sort()
        low, high = means[int(0.025 * reps)], means[int(0.975 * reps) - 1]
    return Summary(
        n=len(values),
        mean=statistics.fmean(values),
        median=statistics.median(values),
        hit_rate=sum(1 for v in values if v > 0) / len(values),
        ci_low=low,
        ci_high=high,
        clusters=len(groups),
    )
