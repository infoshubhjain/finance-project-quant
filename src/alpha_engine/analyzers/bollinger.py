"""Bollinger Bands analyzer. Measures volatility and price position relative to
a moving average envelope.

Bollinger Bands consist of:
- Middle band: SMA(20)
- Upper band: SMA(20) + 2 * StdDev(20)
- Lower band: SMA(20) - 2 * StdDev(20)

Interpretation:
- Price near upper band = potentially overbought (bearish)
- Price near lower band = potentially oversold (bullish)
- Band squeeze (narrow bands) = low volatility, breakout imminent
- %B indicator shows where price sits within the bands (0 = lower, 1 = upper)

Cardinal rule compliance: pure function, no network, no LLM, deterministic.
"""

from __future__ import annotations

import math

from alpha_engine.cache.models import PriceSeries
from alpha_engine.schema.signal import Direction, SignalSource


def _stddev(values: list[float], mean: float) -> float:
    """Population standard deviation."""
    if len(values) < 2:
        return 0.0
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(variance)


def _bollinger_bands(
    closes: list[float], period: int = 20, num_std: float = 2.0
) -> tuple[float, float, float] | None:
    """Returns (lower, middle, upper) or None if insufficient data."""
    if len(closes) < period:
        return None
    window = closes[-period:]
    middle = sum(window) / period
    std = _stddev(window, middle)
    lower = middle - num_std * std
    upper = middle + num_std * std
    return lower, middle, upper


def _pct_b(price: float, lower: float, upper: float) -> float:
    """%B: where price sits within the bands. 0 = at lower, 1 = at upper."""
    band_width = upper - lower
    if band_width == 0:
        return 0.5
    return (price - lower) / band_width


def analyze_bollinger(
    series: PriceSeries,
    period: int = 20,
    num_std: float = 2.0,
    squeeze_threshold: float = 0.05,
) -> SignalSource:
    """Produce one SignalSource from Bollinger Band position.

    Direction:
    - %B < 0.0 (below lower band) -> BULLISH (oversold bounce expected)
    - %B > 1.0 (above upper band) -> BEARISH (overbought pullback expected)
    - %B near 0.5 -> NEUTRAL

    Weight scales with distance from midpoint, with a bonus for squeeze setups.
    """
    closes = series.closes()
    current_price = closes[-1] if closes else 0.0
    bands = _bollinger_bands(closes, period, num_std)

    if bands is None:
        return SignalSource(
            name="bollinger",
            direction=Direction.NEUTRAL,
            weight=0.0,
            detail=f"insufficient history for BB({period})",
        )

    lower, middle, upper = bands
    pct_b = _pct_b(current_price, lower, upper)
    band_width = (upper - lower) / middle if middle != 0 else 0.0

    if pct_b < 0.0:
        direction = Direction.BULLISH
        weight = min(abs(pct_b) * 0.8, 1.0)
    elif pct_b > 1.0:
        direction = Direction.BEARISH
        weight = min((pct_b - 1.0) * 0.8, 1.0)
    elif pct_b < 0.3:
        direction = Direction.BULLISH
        weight = min((0.3 - pct_b) * 1.5, 0.5)
    elif pct_b > 0.7:
        direction = Direction.BEARISH
        weight = min((pct_b - 0.7) * 1.5, 0.5)
    else:
        direction = Direction.NEUTRAL
        weight = 0.1

    # Squeeze detection: narrow bands suggest upcoming volatility
    is_squeeze = band_width < squeeze_threshold
    if is_squeeze and direction is not Direction.NEUTRAL:
        weight = min(weight * 1.2, 1.0)  # boost signal during squeeze

    weight = round(max(weight, 0.0), 4)

    detail = f"bb({period},{num_std:.0f}) %b={pct_b:.3f} band_w={band_width:.4f}"
    if is_squeeze:
        detail += " [squeeze]"
    if pct_b < 0:
        detail += " [below_lower]"
    elif pct_b > 1:
        detail += " [above_upper]"

    return SignalSource(
        name="bollinger",
        direction=direction,
        weight=weight,
        detail=detail,
    )
