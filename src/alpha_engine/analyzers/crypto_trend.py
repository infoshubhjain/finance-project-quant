"""Crypto trend analyzer. The first specialist. Demonstrates the cardinal rule:
this is deterministic, pure-function, unit-testable Python. Given the same
PriceSeries it always returns the same SignalSource. No LLM, no randomness, no
hidden network calls. It reads the cache and computes.

The logic here is intentionally simple and honest: dual moving-average trend plus
a momentum read. It is NOT meant to be a profitable strategy out of the box. It is
the scaffold that proves the architecture end to end. Real edge, if any, comes
later and only after the validation harness measures it.
"""

from __future__ import annotations

from alpha_engine.cache.models import Candle, PriceSeries
from alpha_engine.schema.signal import Direction, SignalSource


def _sma(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def _momentum(values: list[float], lookback: int) -> float | None:
    """Simple rate of change over `lookback` bars, as a fraction."""
    if len(values) <= lookback:
        return None
    past = values[-lookback - 1]
    if past == 0:
        return None
    return (values[-1] - past) / past


def analyze_trend(
    series: PriceSeries, fast: int = 10, slow: int = 30, mom_lookback: int = 14
) -> SignalSource:
    """Produce one SignalSource from price structure.

    Direction: fast SMA above slow SMA is bullish, below is bearish, undefined
    (insufficient data) is neutral. Weight scales with how far apart the SMAs are
    and momentum agreement, capped to [0,1]. This is a transparent, defensible
    starting heuristic, not a claim of alpha.
    """
    closes = series.closes()
    fast_ma = _sma(closes, fast)
    slow_ma = _sma(closes, slow)
    mom = _momentum(closes, mom_lookback)

    if fast_ma is None or slow_ma is None or slow_ma == 0:
        return SignalSource(
            name="crypto.trend",
            direction=Direction.NEUTRAL,
            weight=0.0,
            detail="insufficient history",
        )

    spread = (fast_ma - slow_ma) / slow_ma  # fractional gap between MAs

    if spread > 0:
        direction = Direction.BULLISH
    elif spread < 0:
        direction = Direction.BEARISH
    else:
        direction = Direction.NEUTRAL

    # Weight: magnitude of the MA spread, nudged by whether momentum agrees.
    base = min(abs(spread) * 10, 1.0)  # 10% spread -> full weight
    if mom is not None:
        agrees = (mom > 0 and direction is Direction.BULLISH) or (
            mom < 0 and direction is Direction.BEARISH
        )
        base *= 1.0 if agrees else 0.6
    weight = round(min(base, 1.0), 4)

    detail = f"fast={fast_ma:.2f} slow={slow_ma:.2f} spread={spread:.4f}"
    if mom is not None:
        detail += f" mom={mom:.4f}"

    return SignalSource(name="crypto.trend", direction=direction, weight=weight, detail=detail)


def trend_invalidation(
    candles: list[Candle], direction: Direction, lookback: int = 10
) -> float | None:
    """The price at which the trend read is wrong: the recent swing low for a
    bullish view, the recent swing high for a bearish one. Direction-aware on
    purpose — a bearish thesis is invalidated by strength above it, not by a low
    beneath it. Neutral views have nothing to invalidate.

    Shared by the live `scan` path and the backtester so both judge signals
    against the exact same level.
    """
    if not candles or direction is Direction.NEUTRAL:
        return None
    window = candles[-lookback:]
    if direction is Direction.BULLISH:
        return min(c.low for c in window)
    return max(c.high for c in window)
