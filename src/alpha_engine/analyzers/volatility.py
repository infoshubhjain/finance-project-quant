"""Volatility regime analyzer (ATR-based).

ATR (Average True Range) measures how far price typically travels in a bar,
including gaps. Comparing the current ATR to its own longer history classifies
the *regime*:

- low       (< 0.75x average)  — compression; often precedes a breakout
- normal                        — nothing remarkable
- high      (> 1.5x average)   — trending/energetic tape
- extreme   (> 2.5x average)   — disorderly; directional reads get unreliable

The analyzer itself always votes NEUTRAL: volatility says how much to trust a
directional read, not which direction to take. It therefore exposes two
things — a contextual SignalSource whose detail names the regime, and
`volatility_scalar()`, a deterministic multiplier the CLI applies to the
*other* analyzers' weights (1.0 normal/low/high, 0.6 extreme). Numbers stay in
tested pure Python; nothing here touches direction.

Cardinal rule compliance: pure function, no network, no LLM, deterministic.
"""

from __future__ import annotations

from alpha_engine.cache.models import PriceSeries
from alpha_engine.schema.signal import Direction, SignalSource

_NAME = "volatility_regime"

LOW_RATIO = 0.75
HIGH_RATIO = 1.5
EXTREME_RATIO = 2.5

# Weight multiplier per regime, applied by the caller to other sources.
# Extreme tape is the only regime that dampens: a wild market genuinely makes
# every directional read less trustworthy. High volatility is left at 1.0 —
# claiming it "boosts trend signals" would be an untested assertion of edge.
_SCALARS = {"low": 1.0, "normal": 1.0, "high": 1.0, "extreme": 0.6}


def _true_ranges(series: PriceSeries) -> list[float]:
    candles = series.candles
    trs: list[float] = []
    for i in range(1, len(candles)):
        c, prev = candles[i], candles[i - 1]
        trs.append(max(c.high - c.low, abs(c.high - prev.close), abs(c.low - prev.close)))
    return trs


def _atr_ratio(series: PriceSeries, period: int, baseline: int) -> float | None:
    """Current ATR(period) divided by the average true range over `baseline`
    bars. None when there isn't enough history for both."""
    trs = _true_ranges(series)
    if len(trs) < baseline or baseline <= period:
        return None
    atr_now = sum(trs[-period:]) / period
    atr_base = sum(trs[-baseline:]) / baseline
    if atr_base <= 0:
        return None
    return atr_now / atr_base


def classify_regime(ratio: float) -> str:
    if ratio >= EXTREME_RATIO:
        return "extreme"
    if ratio >= HIGH_RATIO:
        return "high"
    if ratio <= LOW_RATIO:
        return "low"
    return "normal"


def volatility_scalar(series: PriceSeries, period: int = 14, baseline: int = 60) -> float:
    """Deterministic weight multiplier for the other analyzers' sources.
    Unknown regime (not enough history) leaves weights untouched."""
    ratio = _atr_ratio(series, period, baseline)
    if ratio is None:
        return 1.0
    return _SCALARS[classify_regime(ratio)]


def analyze_volatility(series: PriceSeries, period: int = 14, baseline: int = 60) -> SignalSource:
    """Produce one contextual SignalSource naming the volatility regime.

    Always NEUTRAL with a small fixed weight — it exists so the regime shows
    up in the signal's audit trail, not to push direction.
    """
    ratio = _atr_ratio(series, period, baseline)
    if ratio is None:
        return SignalSource(
            name=_NAME,
            direction=Direction.NEUTRAL,
            weight=0.0,
            detail=f"insufficient history for ATR({period}) vs {baseline}-bar baseline",
        )

    regime = classify_regime(ratio)
    notes = {
        "low": "compression; breakout watch",
        "normal": "unremarkable tape",
        "high": "energetic tape",
        "extreme": "disorderly tape; directional reads dampened",
    }
    return SignalSource(
        name=_NAME,
        direction=Direction.NEUTRAL,
        weight=0.05,
        detail=f"atr({period})/{baseline}-bar avg = {ratio:.2f} [{regime}: {notes[regime]}]",
    )
