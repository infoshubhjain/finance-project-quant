"""Forex analyzer: trend read blended with mean-reversion, tuned for majors.

Major currency pairs behave differently from crypto/equities: they spend most
of their time range-bound (central banks lean against runaway moves), so a
pure trend-follow read overtrades the chop. This analyzer combines:

1. the shared dual-MA trend core (same tested math as crypto/equity), and
2. a mean-reversion read — the z-score of price against its 20-day mean.
   Stretched more than 2 sigma from the mean votes *against* the stretch,
   which in a ranging market is the higher-probability side.

When both agree the weights add; when they conflict the net vote shrinks —
exactly the humility a ranging market deserves.

Honest limitation, stated plainly: PLAN.md also asks for carry-trade signals
(interest-rate differentials) and risk-sentiment correlation (VIX). Both need
per-country rate feeds we don't ingest yet; shipping a fake proxy would
violate the honesty rule, so they are documented future work, not silent
stubs. When a rates source lands in ingestion/, the carry read slots in here.

Cardinal rule compliance: pure function, no network, no LLM, deterministic.
"""

from __future__ import annotations

from alpha_engine.analyzers.crypto_trend import analyze_trend
from alpha_engine.cache.models import PriceSeries
from alpha_engine.schema.signal import Direction, SignalSource

_NAME = "forex.trend"

_Z_WINDOW = 20
_Z_STRETCH = 2.0  # sigmas from the mean that count as "stretched"


def _zscore(closes: list[float], window: int) -> float | None:
    if len(closes) < window:
        return None
    recent = closes[-window:]
    mean = sum(recent) / window
    var = sum((c - mean) ** 2 for c in recent) / window
    std = var**0.5
    if std == 0:
        return 0.0
    return (closes[-1] - mean) / std


def analyze_forex_trend(
    series: PriceSeries, fast: int = 10, slow: int = 30, mom_lookback: int = 14
) -> SignalSource:
    """Produce one SignalSource for a currency pair.

    Direction resolution:
    - trend core neutral -> follow the mean-reversion read (if stretched)
    - trend and reversion agree -> trend direction, weights summed
    - they conflict -> trend direction but weight cut by the reversion weight
      (floored at 0), because a stretched ranging pair fades trend-followers
    """
    trend = analyze_trend(series, fast=fast, slow=slow, mom_lookback=mom_lookback)
    z = _zscore(series.closes(), _Z_WINDOW)

    if z is None:
        return trend.model_copy(update={"name": _NAME})

    # Mean-reversion vote: fade a >2-sigma stretch, weight grows with the excess.
    if z >= _Z_STRETCH:
        rev_dir: Direction | None = Direction.BEARISH
        rev_weight = min((z - _Z_STRETCH) / 2.0 + 0.15, 0.4)
    elif z <= -_Z_STRETCH:
        rev_dir = Direction.BULLISH
        rev_weight = min((-z - _Z_STRETCH) / 2.0 + 0.15, 0.4)
    else:
        rev_dir = None
        rev_weight = 0.0

    detail = f"{trend.detail} z20={z:+.2f}"

    if rev_dir is None:
        return SignalSource(
            name=_NAME, direction=trend.direction, weight=trend.weight, detail=detail
        )
    if trend.direction is Direction.NEUTRAL:
        return SignalSource(
            name=_NAME,
            direction=rev_dir,
            weight=round(rev_weight, 4),
            detail=f"{detail} [reversion vote]",
        )
    if trend.direction is rev_dir:
        return SignalSource(
            name=_NAME,
            direction=trend.direction,
            weight=round(min(trend.weight + rev_weight, 1.0), 4),
            detail=f"{detail} [trend+reversion agree]",
        )
    return SignalSource(
        name=_NAME,
        direction=trend.direction,
        weight=round(max(trend.weight - rev_weight, 0.0), 4),
        detail=f"{detail} [reversion fading trend]",
    )
