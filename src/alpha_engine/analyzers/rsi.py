"""RSI (Relative Strength Index) analyzer. A pure-function momentum oscillator
that measures the speed and magnitude of recent price changes.

RSI ranges from 0-100:
- Above 70 = overbought (bearish signal)
- Below 30 = oversold (bullish signal)
- Between 30-70 = neutral zone

This is a standard technical indicator, not proprietary alpha. Its value in the
engine is as a confirming or contradicting input to the trend analyzers.

Cardinal rule compliance: pure function, no network, no LLM, deterministic.
"""

from __future__ import annotations

from alpha_engine.cache.models import PriceSeries
from alpha_engine.schema.signal import Direction, SignalSource


def _rsi(closes: list[float], period: int = 14) -> float | None:
    """Compute RSI using the standard Wilder smoothing method.

    Returns None if insufficient data (need at least period + 1 bars).
    """
    if len(closes) < period + 1:
        return None

    # Calculate initial average gain/loss from the first `period` changes
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0) for d in deltas]
    losses = [max(-d, 0) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # Wilder smoothing for remaining bars
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def analyze_rsi(
    series: PriceSeries,
    period: int = 14,
    overbought: float = 70.0,
    oversold: float = 30.0,
) -> SignalSource:
    """Produce one SignalSource from RSI momentum.

    Direction:
    - RSI < oversold -> BULLISH (oversold, likely bounce)
    - RSI > overbought -> BEARISH (overbought, likely pullback)
    - Between -> NEUTRAL

    Weight scales with how far RSI is from the midpoint (50), capped to [0, 1].
    """
    closes = series.closes()
    rsi_val = _rsi(closes, period)

    if rsi_val is None:
        return SignalSource(
            name="rsi",
            direction=Direction.NEUTRAL,
            weight=0.0,
            detail=f"insufficient history for RSI({period})",
        )

    midpoint = (overbought + oversold) / 2.0

    if rsi_val < oversold:
        direction = Direction.BULLISH
        # Weight: how deep into oversold (0 at oversold boundary, 1 at 0)
        weight = min((oversold - rsi_val) / oversold, 1.0)
    elif rsi_val > overbought:
        direction = Direction.BEARISH
        # Weight: how deep into overbought (0 at overbought boundary, 1 at 100)
        weight = min((rsi_val - overbought) / (100.0 - overbought), 1.0)
    else:
        direction = Direction.NEUTRAL
        # Small weight proportional to distance from midpoint (weak signal)
        weight = min(abs(rsi_val - midpoint) / (midpoint - oversold) * 0.3, 0.3)

    weight = round(max(weight, 0.0), 4)

    detail = f"rsi({period})={rsi_val:.2f}"
    if rsi_val < oversold:
        detail += " [oversold]"
    elif rsi_val > overbought:
        detail += " [overbought]"
    else:
        detail += " [neutral]"

    return SignalSource(
        name="rsi",
        direction=direction,
        weight=weight,
        detail=detail,
    )
