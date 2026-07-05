"""Cross-asset correlation analytics.

Unlike the other analyzers (one asset in, one SignalSource out), this module
reads *several* price series and measures how much they move together.
Correlation is computed on daily returns, not raw prices — two assets that
both drift upward for years are not "correlated" in the risk sense unless
their day-to-day wiggles line up.

What it feeds:
- the portfolio view (diversification score, concentration flags), and
- the dashboard's correlation matrix.

Pearson correlation ranges -1..+1: +1 means the two return streams move in
lockstep, 0 means unrelated, -1 means mirror images. Pairs near 0 diversify a
portfolio; pairs near +1 concentrate its risk.

Cardinal rule compliance: pure functions, no network, no LLM, deterministic.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from alpha_engine.cache.models import PriceSeries

DEFAULT_WINDOW = 30
DIVERSIFIER_MAX_ABS_CORR = 0.3
CONCENTRATION_MIN_CORR = 0.7


class CorrelationMatrix(BaseModel):
    """Pairwise return correlations over a shared window. `matrix[i][j]` pairs
    `assets[i]` with `assets[j]`; None marks pairs with insufficient overlap."""

    assets: list[str]
    window: int
    matrix: list[list[float | None]] = Field(default_factory=list)

    def pair(self, a: str, b: str) -> float | None:
        try:
            return self.matrix[self.assets.index(a)][self.assets.index(b)]
        except ValueError:
            return None


def _returns(closes: list[float]) -> list[float]:
    """Simple daily returns; zero-price bars break the ratio, so they yield 0."""
    out: list[float] = []
    for prev, cur in zip(closes, closes[1:]):
        out.append((cur - prev) / prev if prev else 0.0)
    return out


def pearson(a: list[float], b: list[float]) -> float | None:
    """Pearson correlation of two equal-length return streams. None when a
    stream has zero variance (correlation is undefined, not zero)."""
    n = min(len(a), len(b))
    if n < 2:
        return None
    a, b = a[-n:], b[-n:]
    mean_a = sum(a) / n
    mean_b = sum(b) / n
    var_a = sum((x - mean_a) ** 2 for x in a)
    var_b = sum((x - mean_b) ** 2 for x in b)
    if var_a == 0 or var_b == 0:
        return None
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    return cov / (var_a**0.5 * var_b**0.5)


def rolling_correlation(
    series_a: PriceSeries, series_b: PriceSeries, window: int = DEFAULT_WINDOW
) -> float | None:
    """Correlation of the two assets' returns over the trailing `window` bars.

    Bars are matched positionally from the tail. Daily series from different
    markets can misalign on holidays; a bar or two of slippage barely moves a
    30-bar correlation, so positional matching is an accepted approximation
    (documented, not hidden).
    """
    ra = _returns([c.close for c in series_a.candles])[-window:]
    rb = _returns([c.close for c in series_b.candles])[-window:]
    if len(ra) < window or len(rb) < window:
        return None
    return pearson(ra, rb)


def correlation_matrix(
    series_by_asset: dict[str, PriceSeries], window: int = DEFAULT_WINDOW
) -> CorrelationMatrix:
    """Full pairwise matrix, deterministic order (sorted asset names)."""
    assets = sorted(series_by_asset)
    matrix: list[list[float | None]] = []
    for a in assets:
        row: list[float | None] = []
        for b in assets:
            if a == b:
                row.append(1.0)
            else:
                corr = rolling_correlation(series_by_asset[a], series_by_asset[b], window)
                row.append(round(corr, 4) if corr is not None else None)
        matrix.append(row)
    return CorrelationMatrix(assets=assets, window=window, matrix=matrix)


def diversification_pairs(
    matrix: CorrelationMatrix, max_abs_corr: float = DIVERSIFIER_MAX_ABS_CORR
) -> list[tuple[str, str, float]]:
    """Pairs whose |correlation| is low enough to actually diversify."""
    out: list[tuple[str, str, float]] = []
    for i, a in enumerate(matrix.assets):
        for j in range(i + 1, len(matrix.assets)):
            corr = matrix.matrix[i][j]
            if corr is not None and abs(corr) <= max_abs_corr:
                out.append((a, matrix.assets[j], corr))
    return out
