"""MACD (Moving Average Convergence Divergence) crossover analyzer.

MACD is the gap between a fast and a slow exponential moving average (EMA);
the *signal line* is an EMA of that gap, and the *histogram* is MACD minus
signal. When the MACD line crosses above its signal line, short-term momentum
has turned up relative to the longer trend (bullish); crossing below is the
bearish mirror. The standard (12, 26, 9) parameters are used.

The vote is strongest right at a fresh crossover and fades to a weaker
"alignment" read when the lines are merely on one side of each other, because
a months-old crossover says little. This is a standard indicator, not
proprietary alpha; its value here is as a momentum input to synthesis.

Cardinal rule compliance: pure function, no network, no LLM, deterministic.
"""

from __future__ import annotations

from alpha_engine.cache.models import PriceSeries
from alpha_engine.schema.signal import Direction, SignalSource

_NAME = "macd"


def _ema(values: list[float], period: int) -> list[float]:
    """Standard EMA seeded with the SMA of the first `period` values."""
    if len(values) < period:
        return []
    k = 2.0 / (period + 1.0)
    seed = sum(values[:period]) / period
    out = [seed]
    for v in values[period:]:
        out.append(v * k + out[-1] * (1.0 - k))
    return out


def _macd_lines(
    closes: list[float], fast: int, slow: int, signal: int
) -> tuple[list[float], list[float]] | None:
    """Return (macd_line, signal_line), tail-aligned. None if too little data."""
    if len(closes) < slow + signal:
        return None
    fast_ema = _ema(closes, fast)
    slow_ema = _ema(closes, slow)
    # Align tails: slow EMA starts later, so trim the fast series to match.
    macd_line = [f - s for f, s in zip(fast_ema[-len(slow_ema) :], slow_ema)]
    signal_line = _ema(macd_line, signal)
    if not signal_line:
        return None
    return macd_line[-len(signal_line) :], signal_line


def analyze_macd(
    series: PriceSeries,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> SignalSource:
    """Produce one SignalSource from MACD momentum.

    Direction:
    - MACD crossed above the signal line on the last bar -> BULLISH
    - MACD crossed below the signal line on the last bar -> BEARISH
    - No fresh crossover -> the current side of the signal line, at reduced weight

    Weight scales with the histogram magnitude relative to price (so a $60k
    asset and a $2 asset are comparable), boosted 2x on a fresh crossover,
    capped at 0.8.
    """
    closes = series.closes()
    lines = _macd_lines(closes, fast, slow, signal)
    if lines is None or len(lines[0]) < 2:
        return SignalSource(
            name=_NAME,
            direction=Direction.NEUTRAL,
            weight=0.0,
            detail=f"insufficient history for MACD({fast},{slow},{signal})",
        )

    macd_line, signal_line = lines
    hist_now = macd_line[-1] - signal_line[-1]
    hist_prev = macd_line[-2] - signal_line[-2]
    price = closes[-1]

    crossed_up = hist_prev <= 0 < hist_now
    crossed_down = hist_prev >= 0 > hist_now

    # Histogram as a fraction of price; 0.5% of price saturates the base weight.
    base = min(abs(hist_now) / price / 0.005, 1.0) * 0.4 if price > 0 else 0.0

    if crossed_up or crossed_down:
        direction = Direction.BULLISH if crossed_up else Direction.BEARISH
        weight = min(base * 2.0, 0.8)
        note = "fresh crossover"
    elif hist_now > 0:
        direction = Direction.BULLISH
        weight = base
        note = "above signal line"
    elif hist_now < 0:
        direction = Direction.BEARISH
        weight = base
        note = "below signal line"
    else:
        direction = Direction.NEUTRAL
        weight = 0.0
        note = "on signal line"

    return SignalSource(
        name=_NAME,
        direction=direction,
        weight=round(weight, 4),
        detail=(f"macd({fast},{slow},{signal}) hist={hist_now:.4f} prev={hist_prev:.4f} [{note}]"),
    )
