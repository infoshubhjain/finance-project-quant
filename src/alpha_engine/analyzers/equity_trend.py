"""US equity trend analyzer. Today it deliberately delegates to the same
dual-MA + momentum read as the crypto analyzer: price structure is price
structure, and inventing a gratuitous difference would just be surface area.

It still gets its own module and its own source name because the plan expects
it to diverge: equities have overnight gaps, earnings dates, and meaningful
volume, none of which apply to 24/7 crypto. When that divergence lands, it
happens here without touching the crypto path, and the backtester scores the
two independently by name.
"""

from __future__ import annotations

from alpha_engine.analyzers.crypto_trend import analyze_trend
from alpha_engine.cache.models import PriceSeries
from alpha_engine.schema.signal import SignalSource


def analyze_equity_trend(
    series: PriceSeries, fast: int = 10, slow: int = 30, mom_lookback: int = 14
) -> SignalSource:
    """Produce one SignalSource from equity price structure. Pure function;
    numbers currently identical to the crypto trend read (pinned by test)."""
    source = analyze_trend(series, fast=fast, slow=slow, mom_lookback=mom_lookback)
    return source.model_copy(update={"name": "equity.trend"})
