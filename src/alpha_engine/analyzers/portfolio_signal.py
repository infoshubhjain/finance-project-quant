"""Portfolio-level aggregation: fold many per-asset Signals into one view of
overall positioning.

This stays research output, same as every Signal: "the engine's views lean
72% bullish and they are highly correlated" is a description of the signal
set, not an instruction to allocate capital. The `conviction_weights` are the
relative sizes of the engine's confidence per asset (they sum to 1 across
directional signals) — a comparison device, deliberately not a position-size
recommendation.

What it computes, all deterministic:
- net bias: confidence-weighted average of signal directions, -1..+1
- conviction weights: each directional signal's share of total confidence
- diversification score: 1 minus the average |pairwise return correlation|
  among the directional assets (1 = wiggles unrelated, 0 = one big bet)
- concentration flags: plain-language warnings when the views cluster (all
  one direction, or same-direction assets highly correlated)

Cardinal rule compliance: pure functions, no network, no LLM, deterministic.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from alpha_engine.analyzers.correlation import (
    CONCENTRATION_MIN_CORR,
    CorrelationMatrix,
    correlation_matrix,
)
from alpha_engine.cache.models import PriceSeries
from alpha_engine.schema.signal import Direction, Signal

# Flag "everyone agrees" only when there are enough views for agreement to
# mean something; two signals agreeing is coincidence, not concentration.
_MIN_SIGNALS_FOR_FLAG = 3


class PortfolioView(BaseModel):
    """One aggregate read over the latest signal per asset."""

    signal_count: int
    directional_count: int
    net_bias: float = Field(0.0, ge=-1.0, le=1.0, description="confidence-weighted, -1..+1")
    direction: Direction = Direction.NEUTRAL
    conviction_weights: dict[str, float] = Field(
        default_factory=dict,
        description="share of total confidence per directional asset; sums to ~1",
    )
    diversification_score: float | None = Field(
        None, description="1 - avg |pairwise correlation| among directional assets"
    )
    concentration_flags: list[str] = Field(default_factory=list)
    correlations: CorrelationMatrix | None = None


def _signed(direction: Direction) -> float:
    if direction is Direction.BULLISH:
        return 1.0
    if direction is Direction.BEARISH:
        return -1.0
    return 0.0


def build_portfolio_view(
    signals: list[Signal],
    series_by_asset: dict[str, PriceSeries] | None = None,
    window: int = 30,
) -> PortfolioView:
    """Aggregate the latest signals (one per asset) into a portfolio view.

    `series_by_asset` feeds the correlation reads; assets without a cached
    series simply drop out of the correlation-based numbers (never guessed).
    """
    directional = [s for s in signals if s.direction is not Direction.NEUTRAL]

    view = PortfolioView(
        signal_count=len(signals),
        directional_count=len(directional),
        net_bias=0.0,
        diversification_score=None,
    )
    if not directional:
        return view

    total_conf = sum(s.confidence for s in directional)
    if total_conf > 0:
        view.net_bias = round(
            sum(_signed(s.direction) * s.confidence for s in directional) / total_conf, 4
        )
        view.conviction_weights = {
            s.asset: round(s.confidence / total_conf, 4) for s in directional
        }
    if view.net_bias > 0.15:
        view.direction = Direction.BULLISH
    elif view.net_bias < -0.15:
        view.direction = Direction.BEARISH

    # Correlation-based reads only cover directional assets with cached prices.
    if series_by_asset:
        covered = {
            s.asset: series_by_asset[s.asset] for s in directional if s.asset in series_by_asset
        }
        if len(covered) >= 2:
            matrix = correlation_matrix(covered, window=window)
            view.correlations = matrix
            pair_values = [
                v
                for i in range(len(matrix.assets))
                for j in range(i + 1, len(matrix.assets))
                if (v := matrix.matrix[i][j]) is not None
            ]
            if pair_values:
                avg_abs = sum(abs(v) for v in pair_values) / len(pair_values)
                view.diversification_score = round(1.0 - avg_abs, 4)

    view.concentration_flags = _concentration_flags(directional, view)
    return view


def _concentration_flags(directional: list[Signal], view: PortfolioView) -> list[str]:
    flags: list[str] = []
    if len(directional) >= _MIN_SIGNALS_FOR_FLAG:
        directions = {s.direction for s in directional}
        if directions == {Direction.BULLISH}:
            flags.append(
                f"all {len(directional)} directional views are bullish — one macro "
                f"shock hits every position"
            )
        elif directions == {Direction.BEARISH}:
            flags.append(f"all {len(directional)} directional views are bearish")

    matrix = view.correlations
    if matrix is not None:
        by_asset = {s.asset: s.direction for s in directional}
        for i, a in enumerate(matrix.assets):
            for j in range(i + 1, len(matrix.assets)):
                b = matrix.assets[j]
                corr = matrix.matrix[i][j]
                if corr is None or corr < CONCENTRATION_MIN_CORR:
                    continue
                if by_asset.get(a) == by_asset.get(b):
                    flags.append(
                        f"{a} and {b} point the same way with {corr:+.2f} return "
                        f"correlation — effectively one position"
                    )
    return flags
