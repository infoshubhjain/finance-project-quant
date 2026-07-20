"""Factor ranking: which features actually predict forward returns?

This module answers the core AlphaX question: given any price series, which
factors have measurable predictive power, and how strong is it? Every factor
gets scored by its Information Coefficient (IC) — the correlation between the
factor's value at bar t and the forward return from bar t.

Cardinal rule compliance: all math is deterministic. No optimization loops, no
fitting, no model selection. Just measurement of what happened.

Key concepts:
- Forward return: what the asset did over the next N bars from bar t.
- Rank IC (Spearman): correlation between factor ranks and forward return ranks.
  Robust to outliers and fat tails. Prefer this as the headline metric.
- IC decay: IC at multiple horizons (1, 5, 10, 20 bars). Shows optimal timeframe.
- Hit rate: fraction where factor sign matched forward return sign.
- Coverage: fraction of bars where factor was computable (not None).

The lookahead trap: factor[t] must use only bars [0..t], never the full series.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from alpha_engine.cache.models import PriceSeries


def forward_returns(closes: list[float], horizon: int) -> list[float | None]:
    """Compute forward returns from each bar.

    forward_return[t] = (close[t+horizon] / close[t]) - 1.0

    The last `horizon` entries are None (no future exists yet).
    Never fill them — that would be lookahead.
    """
    n = len(closes)
    fwd: list[float | None] = [None] * n

    for t in range(n - horizon):
        if closes[t] > 0 and closes[t + horizon] > 0:
            fwd[t] = (closes[t + horizon] / closes[t]) - 1.0

    return fwd


def _spearman_rank(values: list[float]) -> list[float]:
    """Convert values to ranks (1-indexed, average for ties)."""
    n = len(values)
    if n == 0:
        return []

    # Sort with original indices
    indexed = [(v, i) for i, v in enumerate(values)]
    indexed.sort(key=lambda x: x[0])

    ranks = [0.0] * n
    i = 0
    while i < n:
        # Find ties
        j = i
        while j < n and indexed[j][0] == indexed[i][0]:
            j += 1
        # Assign average rank (1-indexed)
        avg_rank = (i + j + 1) / 2.0
        for k in range(i, j):
            ranks[indexed[k][1]] = avg_rank
        i = j

    return ranks


def rank_ic(
    factor_values: Sequence[float | None], fwd_returns: Sequence[float | None]
) -> float | None:
    """Spearman rank correlation between factor and forward returns.

    Drops bars where either side is None. Returns None if <10 valid pairs.
    """
    if len(factor_values) != len(fwd_returns):
        return None

    # Collect valid pairs
    pairs = [(f, r) for f, r in zip(factor_values, fwd_returns) if f is not None and r is not None]

    if len(pairs) < 10:  # Need minimum sample size
        return None

    factors, returns = zip(*pairs)
    factors_rank = _spearman_rank(list(factors))
    returns_rank = _spearman_rank(list(returns))

    # Pearson correlation of ranks
    n = len(factors_rank)
    mean_f = sum(factors_rank) / n
    mean_r = sum(returns_rank) / n

    cov = sum((f - mean_f) * (r - mean_r) for f, r in zip(factors_rank, returns_rank)) / n
    std_f = math.sqrt(sum((f - mean_f) ** 2 for f in factors_rank) / n)
    std_r = math.sqrt(sum((r - mean_r) ** 2 for r in returns_rank) / n)

    if std_f == 0 or std_r == 0:
        return None

    return cov / (std_f * std_r)


def hit_rate(
    factor_values: Sequence[float | None], fwd_returns: Sequence[float | None]
) -> float | None:
    """Fraction where factor sign matched forward return sign."""
    if len(factor_values) != len(fwd_returns):
        return None

    matches = 0
    total = 0

    for f, r in zip(factor_values, fwd_returns):
        if f is not None and r is not None and f != 0 and r != 0:
            if (f > 0 and r > 0) or (f < 0 and r < 0):
                matches += 1
            total += 1

    return matches / total if total > 0 else None


def coverage(values: Sequence[float | None]) -> float:
    """Fraction of bars where value was computable (not None)."""
    if not values:
        return 0.0
    return sum(1 for v in values if v is not None) / len(values)


@dataclass
class FactorScore:
    """Measured predictive power of one factor."""

    name: str
    rank_ic: float | None
    hit_rate: float | None
    coverage: float
    t_stat: float | None  # IC × sqrt(n), crude significance check
    ic_by_horizon: dict[int, float | None]  # IC at 1, 5, 10, 20 bars
    n_obs: int = 0  # bars where both factor and forward return existed

    def __repr__(self) -> str:
        ic_str = f"{self.rank_ic:+.4f}" if self.rank_ic is not None else "None"
        t_str = f"{self.t_stat:+.2f}" if self.t_stat is not None else "None"
        hr_str = f"{self.hit_rate:.1%}" if self.hit_rate is not None else "None"
        return (
            f"FactorScore({self.name}: IC={ic_str}, t={t_str}, "
            f"hit={hr_str}, cov={self.coverage:.1%})"
        )


def ic_decay(
    factor_values: Sequence[float | None],
    closes: list[float],
    horizons: tuple[int, ...] = (1, 5, 10, 20),
) -> dict[int, float | None]:
    """IC at multiple horizons. Shows optimal timeframe for the factor."""
    decay = {}
    for h in horizons:
        fwd = forward_returns(closes, h)
        decay[h] = rank_ic(factor_values, fwd)
    return decay


def noise_floor_ic(n_factors: int, n_obs: int) -> float | None:
    """The |IC| you should expect the *best* of `n_factors` useless factors to
    reach, purely by chance, on `n_obs` observations.

    This is the multiple-testing correction the whole ranking layer needs. Score
    500 factors against 30 observations and something will show |IC| ~ 0.6 even
    if every factor is random noise — that is arithmetic, not alpha. A top-ranked
    factor is only interesting if it clears this line.

    The estimate: an IC has standard error ~ 1/sqrt(n), and the maximum of
    `k` standard normals grows like sqrt(2 ln k). So the floor is
    sqrt(2 * ln(k)) / sqrt(n). Crude, deliberately so — it is a sanity line, not
    a p-value, and a precise one would invite exactly the false confidence it
    exists to prevent.
    """
    if n_factors < 2 or n_obs < 3:
        return None
    return math.sqrt(2.0 * math.log(n_factors)) / math.sqrt(n_obs)


def rank_factors(
    series: PriceSeries,
    factor_panel: Mapping[str, Sequence[float | None]],
    horizon: int = 10,
) -> list[FactorScore]:
    """Score and rank all factors by |rank_ic| descending.

    Args:
        series: Price data
        factor_panel: dict[factor_name, values_at_each_bar]
        horizon: Forward return horizon for primary IC

    Returns:
        Sorted list of FactorScore, best (highest |IC|) first.
    """
    closes = [c.close for c in series.candles]
    fwd = forward_returns(closes, horizon)

    scores = []
    for name, values in factor_panel.items():
        if len(values) != len(closes):
            continue  # Skip malformed factors

        ic = rank_ic(values, fwd)
        hr = hit_rate(values, fwd)
        cov = coverage(values)

        # t-stat: IC × sqrt(valid_samples), crude significance test
        valid_pairs = sum(1 for v, f in zip(values, fwd) if v is not None and f is not None)
        t_stat = ic * math.sqrt(valid_pairs) if ic is not None and valid_pairs > 0 else None

        # IC decay at multiple horizons
        decay = ic_decay(values, closes)

        scores.append(
            FactorScore(
                name=name,
                rank_ic=ic,
                hit_rate=hr,
                coverage=cov,
                t_stat=t_stat,
                ic_by_horizon=decay,
                n_obs=valid_pairs,
            )
        )

    # Sort by |IC| descending, with None IC at the end
    def sort_key(s: FactorScore) -> float:
        return abs(s.rank_ic) if s.rank_ic is not None else -1.0

    return sorted(scores, key=sort_key, reverse=True)
