"""Synthesis. Takes the SignalSources produced by one or more analyzers and folds
them into a single unified Signal. With one analyzer this is near pass-through, but
the seam exists now so adding markets later is additive, not surgical.

This is deterministic. It weights sources, resolves a net direction, derives a
calibrated confidence, and leaves `thesis` empty for the narrator to fill.
"""

from __future__ import annotations

from alpha_engine.schema.signal import (
    Direction,
    Market,
    Signal,
    SignalSource,
    Timeframe,
)


def _net_direction(sources: list[SignalSource]) -> tuple[Direction, float]:
    """Weighted vote across sources. Returns (direction, net_score) where
    net_score is in [-1, 1]: positive bullish, negative bearish."""
    score = 0.0
    total_weight = 0.0
    for s in sources:
        total_weight += s.weight
        if s.direction is Direction.BULLISH:
            score += s.weight
        elif s.direction is Direction.BEARISH:
            score -= s.weight
    if total_weight == 0:
        return Direction.NEUTRAL, 0.0
    net = score / total_weight
    if net > 0.1:
        return Direction.BULLISH, net
    if net < -0.1:
        return Direction.BEARISH, net
    return Direction.NEUTRAL, net


def synthesize(
    asset: str,
    market: Market,
    sources: list[SignalSource],
    timeframe: Timeframe = Timeframe.SWING,
    invalidation_level: float | None = None,
) -> Signal:
    """Assemble the final Signal. thesis is left blank; the narrator fills it."""
    direction, net = _net_direction(sources)

    # Confidence: magnitude of net vote, scaled by how much total weight was in
    # play. Sparse or contradictory inputs -> low confidence. Honest by design.
    avg_weight = (
        sum(s.weight for s in sources) / len(sources) if sources else 0.0
    )
    confidence = round(min(abs(net) * avg_weight * 1.5, 1.0), 4)

    return Signal(
        asset=asset,
        market=market,
        direction=direction,
        confidence=confidence,
        timeframe=timeframe,
        signal_sources=sources,
        invalidation_level=invalidation_level,
        thesis="",
    )
