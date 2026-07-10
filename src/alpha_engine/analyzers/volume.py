"""Volume Profile analyzer. Uses On-Balance Volume (OBV) to confirm price
trends with volume participation.

OBV is a cumulative volume indicator:
- If close > previous close: OBV += volume
- If close < previous close: OBV -= volume
- If close == previous close: OBV unchanged

When price rises but OBV falls (or vice versa), it signals a divergence —
the trend may be weakening due to lack of volume confirmation.

This analyzer also looks at volume trend (is volume increasing with price?)
as a secondary confirmation signal.

Cardinal rule compliance: pure function, no network, no LLM, deterministic.
"""

from __future__ import annotations

from alpha_engine.analyzers.crypto_trend import _sma
from alpha_engine.cache.models import PriceSeries
from alpha_engine.schema.signal import Direction, SignalSource


def _obv(closes: list[float], volumes: list[float]) -> list[float]:
    """Compute On-Balance Volume series."""
    if len(closes) < 2 or len(volumes) < 2:
        return []

    obv_series = [0.0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            obv_series.append(obv_series[-1] + volumes[i])
        elif closes[i] < closes[i - 1]:
            obv_series.append(obv_series[-1] - volumes[i])
        else:
            obv_series.append(obv_series[-1])
    return obv_series


def analyze_volume(
    series: PriceSeries,
    obv_sma_period: int = 20,
    vol_lookback: int = 10,
) -> SignalSource:
    """Produce one SignalSource from volume analysis.

    The signal is based on two readings:
    1. OBV trend: is volume confirming price direction?
    2. Volume momentum: is recent volume above or below average?

    Direction:
    - OBV rising + price rising -> BULLISH (volume confirms uptrend)
    - OBV falling + price falling -> BEARISH (volume confirms downtrend)
    - OBV diverging from price -> counter-trend signal

    Weight: based on the strength of the OBV trend and volume confirmation.
    """
    closes = series.closes()
    volumes = [c.volume for c in series.candles]

    # Check for missing volume data
    if not any(v is not None and v > 0 for v in volumes):
        return SignalSource(
            name="volume",
            direction=Direction.NEUTRAL,
            weight=0.0,
            detail="no volume data available",
        )

    # Replace None volumes with 0 for computation
    safe_volumes = [v if v is not None else 0.0 for v in volumes]

    if len(closes) < 3:
        return SignalSource(
            name="volume",
            direction=Direction.NEUTRAL,
            weight=0.0,
            detail="insufficient history for volume analysis",
        )

    # OBV computation
    obv_series = _obv(closes, safe_volumes)
    if not obv_series:
        return SignalSource(
            name="volume",
            direction=Direction.NEUTRAL,
            weight=0.0,
            detail="insufficient history for OBV",
        )

    # OBV trend: compare recent OBV to its SMA
    obv_sma = _sma(obv_series, obv_sma_period)
    if obv_sma is None:
        # Use simpler comparison
        obv_trend = obv_series[-1] - obv_series[0]
    else:
        obv_trend = obv_series[-1] - obv_sma

    # Price trend
    price_trend = (
        closes[-1] - closes[-vol_lookback] if len(closes) > vol_lookback else closes[-1] - closes[0]
    )

    # Volume momentum: recent volume vs average
    recent_window = safe_volumes[-vol_lookback:]
    recent_vol = sum(recent_window) / len(recent_window) if recent_window else 0.0
    avg_vol = sum(safe_volumes) / len(safe_volumes) if safe_volumes else 1.0
    vol_ratio = recent_vol / avg_vol if avg_vol > 0 else 1.0

    # Direction: OBV trend aligned with price trend
    if obv_trend > 0 and price_trend > 0:
        direction = Direction.BULLISH
        strength = min(abs(obv_trend) / (abs(obv_series[-1]) + 1.0), 1.0)
    elif obv_trend < 0 and price_trend < 0:
        direction = Direction.BEARISH
        strength = min(abs(obv_trend) / (abs(obv_series[-1]) + 1.0), 1.0)
    elif obv_trend > 0 and price_trend < 0:
        # Bullish divergence: OBV rising while price falling
        direction = Direction.BULLISH
        strength = min(abs(obv_trend) / (abs(obv_series[-1]) + 1.0) * 0.6, 0.6)
    elif obv_trend < 0 and price_trend > 0:
        # Bearish divergence: OBV falling while price rising
        direction = Direction.BEARISH
        strength = min(abs(obv_trend) / (abs(obv_series[-1]) + 1.0) * 0.6, 0.6)
    else:
        direction = Direction.NEUTRAL
        strength = 0.0

    # Boost weight if volume is above average (confirms the signal)
    if vol_ratio > 1.2:
        strength = min(strength * 1.2, 1.0)
    elif vol_ratio < 0.8:
        strength = strength * 0.8

    weight = round(max(min(strength, 1.0), 0.0), 4)

    detail = f"obv_trend={'+' if obv_trend > 0 else ''}{obv_trend:.0f} vol_ratio={vol_ratio:.2f}"
    if abs(obv_trend) > 0 and price_trend > 0 and obv_trend > 0:
        detail += " [confirmed_uptrend]"
    elif abs(obv_trend) > 0 and price_trend < 0 and obv_trend < 0:
        detail += " [confirmed_downtrend]"
    elif obv_trend > 0 and price_trend < 0:
        detail += " [bullish_divergence]"
    elif obv_trend < 0 and price_trend > 0:
        detail += " [bearish_divergence]"

    return SignalSource(
        name="volume",
        direction=direction,
        weight=weight,
        detail=detail,
    )
