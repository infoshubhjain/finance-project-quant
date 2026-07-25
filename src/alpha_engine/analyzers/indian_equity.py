"""Indian equity analyzer. A dedicated analyzer for Indian cash equities that
extends the shared trend core with India-specific context.

This analyzer combines:
1. Price trend — delegates to the shared `analyze_trend` (dual-MA + momentum),
   the exact same tested core crypto and US equity use. No copy of the MA math.
2. Gap analysis (Indian equities often gap on global cues)
3. Intraday range (volatility context)

This is a scaffold for India-specific analysis. Future extensions could include
FII/DII flow data, sector rotation, and Indian volatility index (India VIX).

Cardinal rule compliance: pure function, no network, no LLM, deterministic.
"""

from __future__ import annotations

from alpha_engine.analyzers.crypto_trend import analyze_trend
from alpha_engine.cache.models import PriceSeries
from alpha_engine.schema.signal import SignalSource


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
    1. Price trend — the shared `analyze_trend` core (dual-MA + momentum)
    2. Gap analysis — India-specific overnight risk signal
    3. Intraday range — volatility context

    The trend read is the primary input; direction comes entirely from it. Gap
    and range are contextual modifiers — a large average gap trims the weight
    (more overnight uncertainty) but never flips the direction.
    """
    base = analyze_trend(series, fast=fast, slow=slow, name="in_equity.trend")

    # Same neutral-on-insufficient-history contract as the shared core; nothing
    # India-specific to add when there isn't even a trend read.
    if base.detail == "insufficient history":
        return base

    weight = base.weight
    detail = base.detail

    # Gap modifier: large average gaps mean more overnight risk -> trim weight.
    avg_gap = _gap_analysis(series.candles)
    if avg_gap is not None:
        detail += f" avg_gap={avg_gap:.4f}"
        if avg_gap > 0.02:  # >2% average gap
            weight = round(weight * 0.9, 4)

    # Intraday range: reported as context, does not change the vote.
    avg_range = _intraday_range(series.candles)
    if avg_range is not None:
        detail += f" avg_range={avg_range:.4f}"

    return SignalSource(
        name="in_equity.trend",
        direction=base.direction,
        weight=weight,
        detail=detail,
    )
