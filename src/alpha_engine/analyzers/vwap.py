"""VWAP (Volume-Weighted Average Price) analyzer.

VWAP is the average price paid per unit actually traded over a window: each
bar's typical price (high+low+close)/3 weighted by its volume. It approximates
the crowd's average cost basis. Price holding above VWAP means the average
recent buyer is in profit (buying pressure tends to persist); price below
means the average buyer is underwater (overhead supply).

Daily candles give a rolling multi-day VWAP, not the intraday session VWAP a
floor trader watches — documented honestly here rather than pretended away.
Sources without volume data (some forex/macro feeds) degrade to weight 0.

Cardinal rule compliance: pure function, no network, no LLM, deterministic.
"""

from __future__ import annotations

from alpha_engine.cache.models import PriceSeries
from alpha_engine.schema.signal import Direction, SignalSource

_NAME = "vwap"


def _vwap(series: PriceSeries, window: int) -> float | None:
    """Rolling VWAP over the last `window` bars. None when volume is missing
    or zero throughout (a zero-volume VWAP is undefined, not zero)."""
    candles = series.candles[-window:]
    if len(candles) < window:
        return None
    num = 0.0
    den = 0.0
    for c in candles:
        if c.volume is None:
            return None
        typical = (c.high + c.low + c.close) / 3.0
        num += typical * c.volume
        den += c.volume
    if den <= 0:
        return None
    return num / den


def analyze_vwap(series: PriceSeries, window: int = 20) -> SignalSource:
    """Produce one SignalSource from price's position relative to rolling VWAP.

    Direction:
    - close above VWAP -> BULLISH (average recent buyer in profit)
    - close below VWAP -> BEARISH (average recent buyer underwater)

    Weight scales with the distance from VWAP (2% of price saturates it) and
    with how much of the window carried above-average volume, capped at 0.5 —
    a cost-basis read is context, not a trend engine.
    """
    vwap_val = _vwap(series, window)
    if vwap_val is None or vwap_val <= 0:
        return SignalSource(
            name=_NAME,
            direction=Direction.NEUTRAL,
            weight=0.0,
            detail=f"no usable volume data for VWAP({window})",
        )

    close = series.candles[-1].close
    distance = (close - vwap_val) / vwap_val

    # Volume participation: fraction of recent bars trading above the window average.
    volumes = [c.volume or 0.0 for c in series.candles[-window:]]
    avg_vol = sum(volumes) / len(volumes)
    participation = sum(1 for v in volumes[-5:] if v > avg_vol) / 5.0 if avg_vol > 0 else 0.0

    magnitude = min(abs(distance) / 0.02, 1.0)
    weight = round(min(0.5 * magnitude * (0.6 + 0.4 * participation), 0.5), 4)

    if distance > 0:
        direction = Direction.BULLISH
    elif distance < 0:
        direction = Direction.BEARISH
    else:
        direction = Direction.NEUTRAL
        weight = 0.0

    return SignalSource(
        name=_NAME,
        direction=direction,
        weight=weight,
        detail=(
            f"vwap({window})={vwap_val:.2f} close={close:.2f} "
            f"dist={distance * 100:+.2f}% recent_vol_participation={participation:.1f}"
        ),
    )
