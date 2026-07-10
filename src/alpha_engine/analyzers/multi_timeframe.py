"""Multi-timeframe trend alignment analyzer.

The idea: a move is more trustworthy when the short-, medium-, and long-term
trends all point the same way, and suspect when they disagree.

Honest limitation, stated up front: the engine's cache holds daily candles
only, so this analyzer cannot inspect true intraday (4h/1h) frames. Instead it
reads three *lookback horizons* on the daily series — short (10 bars), medium
(20) and long (40) — each scored by the slope of its own moving average. That
captures the same alignment intuition ("is the week, the month, and the
quarter agreeing?") without pretending to data we don't have. If intraday
ingestion lands later, the horizons can become real timeframes without
changing the contract.

Cardinal rule compliance: pure function, no network, no LLM, deterministic.
"""

from __future__ import annotations

from alpha_engine.analyzers.crypto_trend import _sma
from alpha_engine.cache.models import PriceSeries
from alpha_engine.schema.signal import Direction, SignalSource

_NAME = "multi_timeframe"

# (label, moving-average window). Slope is measured over the last `window` bars.
_HORIZONS: tuple[tuple[str, int], ...] = (("short", 10), ("medium", 20), ("long", 40))

# A horizon must move at least this much (fraction of price) to count as a
# trend; smaller drift is flat noise, not agreement.
_FLAT_BAND = 0.005


def _horizon_direction(closes: list[float], window: int) -> Direction | None:
    """Trend of one horizon: sign of the SMA's change across the last `window`
    bars, with a flat band so tiny drift reads NEUTRAL. None = not enough data."""
    if len(closes) < 2 * window:
        return None
    now = _sma(closes, window)
    then = _sma(closes[:-window], window)
    if now is None or then is None or then == 0:
        return None
    change = (now - then) / then
    if change > _FLAT_BAND:
        return Direction.BULLISH
    if change < -_FLAT_BAND:
        return Direction.BEARISH
    return Direction.NEUTRAL


def analyze_multi_timeframe(series: PriceSeries) -> SignalSource:
    """Produce one SignalSource from cross-horizon trend alignment.

    Direction: majority direction across the readable horizons; NEUTRAL when
    bullish and bearish horizons tie or everything is flat.

    Weight: alignment strength. All three horizons agreeing earns the cap
    (0.7); a 2-1 split earns much less; a tie or all-flat earns 0. Fewer than
    two readable horizons degrades to weight 0 rather than guessing.
    """
    closes = series.closes()
    reads: list[tuple[str, Direction]] = []
    for label, window in _HORIZONS:
        direction = _horizon_direction(closes, window)
        if direction is not None:
            reads.append((label, direction))

    if len(reads) < 2:
        return SignalSource(
            name=_NAME,
            direction=Direction.NEUTRAL,
            weight=0.0,
            detail=f"insufficient history ({len(closes)} bars) for horizon comparison",
        )

    bulls = sum(1 for _, d in reads if d is Direction.BULLISH)
    bears = sum(1 for _, d in reads if d is Direction.BEARISH)
    total = len(reads)
    summary = " ".join(f"{label}={d.value}" for label, d in reads)

    if bulls == bears:
        return SignalSource(
            name=_NAME,
            direction=Direction.NEUTRAL,
            weight=0.0,
            detail=f"horizons conflict or flat: {summary}",
        )

    direction = Direction.BULLISH if bulls > bears else Direction.BEARISH
    winners = max(bulls, bears)
    losers = min(bulls, bears)
    # Full agreement (3-0) -> 1.0; a 2-1 split -> ~0.33; scaled into the cap.
    alignment = (winners - losers) / total
    weight = round(0.7 * alignment, 4)

    return SignalSource(
        name=_NAME,
        direction=direction,
        weight=weight,
        detail=f"{winners}/{total} horizons {direction.value}: {summary}",
    )
