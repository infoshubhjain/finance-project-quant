"""Synthesis. Takes the SignalSources produced by one or more analyzers and folds
them into a single unified Signal. With one analyzer this is near pass-through, but
the seam exists now so adding markets later is additive, not surgical.

This is deterministic. It weights sources, resolves a net direction, derives a
calibrated confidence, and leaves `thesis` empty for the narrator to fill.

Confidence calibration:
    The formula computes confidence from three components:
    1. Agreement quality — what fraction of total weight agrees with the final direction
    2. Source reliability — historical accuracy of each analyzer type (calibrated against backtests)
    3. Source diversity — more independent sources slightly raise the confidence ceiling

    The result is a confidence that better reflects actual signal reliability, not just
    how strongly the current analyzers agree. High confidence now requires both strong
    agreement AND historically reliable source types.
"""

from __future__ import annotations

import math

from alpha_engine.schema.signal import (
    Direction,
    Market,
    Signal,
    SignalSource,
    Timeframe,
)

# Historical reliability factors for each analyzer type, calibrated against
# backtest results. These are conservative starting points: a 50% hit-rate
# scaffold analyzer gets ~0.5, a genuinely predictive analyzer would get >0.7.
# Updated as the validation layer accumulates more outcome data.
SOURCE_RELIABILITY: dict[str, float] = {
    "crypto.trend": 0.50,
    "equity.trend": 0.50,
    "indian_equity": 0.50,
    "rsi": 0.52,
    "bollinger": 0.51,
    "volume": 0.50,
    "macro": 0.55,
    "fno.pcr": 0.53,
    "fno.max_pain": 0.52,
    "fno.oi_shift": 0.51,
    "fno.wall": 0.50,
}

# Default reliability for unknown analyzer types
_DEFAULT_RELIABILITY = 0.50

# Maximum confidence achievable with a given number of independent sources.
# With 1 source: capped at 0.45 (honestly uncertain).
# With 2 sources: capped at 0.60.
# With 3+ sources: capped at 0.75 (still not 1.0 — no signal is certain).
_SOURCE_COUNT_CAP = {1: 0.45, 2: 0.60, 3: 0.70, 4: 0.75, 5: 0.78}


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


def _agreement_quality(sources: list[SignalSource], direction: Direction) -> float:
    """What fraction of total weight agrees with the final direction.
    Returns a value in [0, 1]. If all weight agrees, returns 1.0.
    If sources are split 50/50, returns 0.5. Neutral sources don't count."""
    if direction is Direction.NEUTRAL or not sources:
        return 0.0
    total_weight = sum(s.weight for s in sources)
    if total_weight == 0:
        return 0.0
    agreeing_weight = sum(s.weight for s in sources if s.direction is direction)
    return agreeing_weight / total_weight


def _reliability_score(sources: list[SignalSource], direction: Direction) -> float:
    """Weighted average reliability of the sources that agree with the final
    direction. Sources that disagree don't contribute — their low reliability
    is already captured by the agreement quality penalty."""
    if direction is Direction.NEUTRAL or not sources:
        return _DEFAULT_RELIABILITY
    agreeing = [s for s in sources if s.direction is direction]
    if not agreeing:
        return 0.0
    total_weight = sum(s.weight for s in agreeing)
    if total_weight == 0:
        return 0.0
    weighted_reliability = sum(
        s.weight * SOURCE_RELIABILITY.get(s.name, _DEFAULT_RELIABILITY) for s in agreeing
    )
    return weighted_reliability / total_weight


def _source_count_cap(n_sources: int) -> float:
    """Return the maximum confidence allowed given the number of independent
    sources. More sources raise the ceiling, but it never reaches 1.0."""
    if n_sources <= 0:
        return 0.0
    return _SOURCE_COUNT_CAP[min(n_sources, max(_SOURCE_COUNT_CAP))]


def _calibrate_confidence(
    sources: list[SignalSource],
    direction: Direction,
    net: float,
) -> float:
    """Compute calibrated confidence from agreement quality, source reliability,
    and source diversity.

    The formula:
        raw = sigmoid(agreement * reliability * net_magnitude)
        confidence = raw * source_count_cap

    This ensures:
    - High agreement + high reliability -> higher confidence
    - Contradictory sources -> lower confidence (even if net direction is clear)
    - Few sources -> lower confidence ceiling (honest uncertainty)
    - The result is always in [0, 1]
    """
    if direction is Direction.NEUTRAL or not sources:
        return 0.0

    agreement = _agreement_quality(sources, direction)
    reliability = _reliability_score(sources, direction)
    n_sources = len([s for s in sources if s.weight > 0])

    # Sigmoid-like scaling: maps agreement * reliability * |net| to [0, 1)
    # using a smooth curve that saturates. The 4.0 constant controls how fast
    # confidence grows with agreement; higher = more aggressive.
    product = agreement * reliability * abs(net)
    raw = 2.0 / (1.0 + math.exp(-4.0 * product)) - 1.0  # maps to [0, 1)

    cap = _source_count_cap(n_sources)
    confidence = round(min(raw * cap, 1.0), 4)
    return confidence


def synthesize(
    asset: str,
    market: Market,
    sources: list[SignalSource],
    timeframe: Timeframe = Timeframe.SWING,
    invalidation_level: float | None = None,
) -> Signal:
    """Assemble the final Signal. thesis is left blank; the narrator fills it."""
    direction, net = _net_direction(sources)
    confidence = _calibrate_confidence(sources, direction, net)

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
