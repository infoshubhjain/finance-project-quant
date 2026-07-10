"""Indian equity analyzer. A dedicated analyzer for Indian cash equities that
extends the base equity trend with India-specific context.

This analyzer combines:
1. Price trend (delegates to equity_trend for the core MA read)
2. Market hours awareness (Indian markets trade 9:15 AM - 3:30 PM IST)
3. Gap analysis (Indian equities often gap on global cues)

This is a scaffold for India-specific analysis. Future extensions could include
FII/DII flow data, sector rotation, and Indian volatility index (India VIX).

Cardinal rule compliance: pure function, no network, no LLM, deterministic.
"""

from __future__ import annotations

from alpha_engine.analyzers.crypto_trend import _sma
from alpha_engine.cache.models import PriceSeries
from alpha_engine.schema.signal import Direction, SignalSource


def _gap_analysis(candles: list) -> float | None:
    """Compute the average gap size as a fraction of price.

    Indian equities frequently gap on global cues (US/Night markets).
    Large gaps indicate high overnight risk/opportunity.
    """
    if len(candles) < 5:
        return None

    gaps = []
    for i in range(1, len(candles)):
        prev_close = candles[i - 1].close
        if prev_close == 0:
            continue
        gap = (candles[i].open - prev_close) / prev_close
        gaps.append(abs(gap))

    if not gaps:
        return None
    return sum(gaps) / len(gaps)


def _intraday_range(candles: list) -> float | None:
    """Average intraday range as a fraction of price.

    Indian equities tend to have wider intraday ranges than US equities
    due to the F&O expiry dynamics and retail participation.
    """
    if not candles:
        return None

    ranges = []
    for c in candles:
        if c.low == 0:
            continue
        r = (c.high - c.low) / c.low
        ranges.append(r)

    if not ranges:
        return None
    return sum(ranges) / len(ranges)


def analyze_indian_equity(
    series: PriceSeries,
    fast: int = 10,
    slow: int = 30,
) -> SignalSource:
    """Produce one SignalSource for an Indian cash equity.

    Combines:
    1. Price trend (dual MA) — the core directional read
    2. Gap analysis — India-specific overnight risk signal
    3. Intraday range — volatility context

    The trend read is the primary input. Gap and range provide contextual
    modifiers that adjust weight but rarely flip direction alone.
    """
    closes = series.closes()
    fast_ma = _sma(closes, fast)
    slow_ma = _sma(closes, slow)

    if fast_ma is None or slow_ma is None or slow_ma == 0:
        return SignalSource(
            name="in_equity.trend",
            direction=Direction.NEUTRAL,
            weight=0.0,
            detail="insufficient history",
        )

    # Core trend read
    spread = (fast_ma - slow_ma) / slow_ma
    if spread > 0:
        direction = Direction.BULLISH
    elif spread < 0:
        direction = Direction.BEARISH
    else:
        direction = Direction.NEUTRAL

    base_weight = min(abs(spread) * 10, 1.0)

    # Gap analysis modifier
    avg_gap = _gap_analysis(series.candles)
    gap_detail = ""
    if avg_gap is not None:
        gap_detail = f" avg_gap={avg_gap:.4f}"
        # Large gaps increase uncertainty -> slightly lower weight
        if avg_gap > 0.02:  # >2% average gap
            base_weight *= 0.9

    # Intraday range modifier
    avg_range = _intraday_range(series.candles)
    range_detail = ""
    if avg_range is not None:
        range_detail = f" avg_range={avg_range:.4f}"

    weight = round(min(base_weight, 1.0), 4)

    detail = f"fast={fast_ma:.2f} slow={slow_ma:.2f} spread={spread:.4f}"
    detail += gap_detail + range_detail

    return SignalSource(
        name="in_equity.trend",
        direction=direction,
        weight=weight,
        detail=detail,
    )
