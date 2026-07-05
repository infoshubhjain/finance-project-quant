"""Support/Resistance analyzer. Finds price levels the market has repeatedly
respected and votes on what the current price is doing at them.

A *swing high* is a bar whose high tops its neighbors on both sides; a *swing
low* mirrors that. Levels that collect several swing touches act as memory:
buyers who defended a support before tend to defend it again, and sellers who
capped a resistance tend to reappear there. The analyzer:

1. collects swing highs/lows over the series,
2. clusters them into levels (touches within a small % band are one level),
3. votes BULLISH when price sits near a support and the last bar closed up
   (a bounce), BEARISH near a resistance with the last bar closing down
   (a rejection), NEUTRAL otherwise.

Weight scales with how many touches built the level and how recent the latest
touch is — a level tested five times last week beats one touched twice months
ago. This is a standard technical heuristic, not proprietary alpha.

Cardinal rule compliance: pure function, no network, no LLM, deterministic.
"""

from __future__ import annotations

from alpha_engine.cache.models import Candle, PriceSeries
from alpha_engine.schema.signal import Direction, SignalSource

_NAME = "support_resistance"


def _swing_points(candles: list[Candle], span: int = 2) -> tuple[list[int], list[int]]:
    """Indexes of swing highs and swing lows. A swing high at i means its high
    strictly tops every high within `span` bars on both sides."""
    highs: list[int] = []
    lows: list[int] = []
    for i in range(span, len(candles) - span):
        window = candles[i - span : i + span + 1]
        if candles[i].high == max(c.high for c in window) and all(
            candles[i].high > c.high for j, c in enumerate(window) if j != span
        ):
            highs.append(i)
        if candles[i].low == min(c.low for c in window) and all(
            candles[i].low < c.low for j, c in enumerate(window) if j != span
        ):
            lows.append(i)
    return highs, lows


def _cluster_levels(
    prices: list[tuple[int, float]], tolerance_pct: float
) -> list[dict[str, float]]:
    """Group nearby swing prices into levels.

    Returns one dict per level: its mean price, touch count, and the index of
    the most recent touch. Clustering is greedy over price-sorted points so the
    result is deterministic.
    """
    if not prices:
        return []
    by_price = sorted(prices, key=lambda p: (p[1], p[0]))
    clusters: list[list[tuple[int, float]]] = [[by_price[0]]]
    for idx, price in by_price[1:]:
        anchor = clusters[-1][0][1]
        if anchor and abs(price - anchor) / anchor <= tolerance_pct:
            clusters[-1].append((idx, price))
        else:
            clusters.append([(idx, price)])
    return [
        {
            "level": sum(p for _, p in c) / len(c),
            "touches": float(len(c)),
            "last_touch": float(max(i for i, _ in c)),
        }
        for c in clusters
    ]


def analyze_support_resistance(
    series: PriceSeries,
    span: int = 2,
    tolerance_pct: float = 0.01,
    near_pct: float = 0.02,
) -> SignalSource:
    """Produce one SignalSource from support/resistance structure.

    Direction:
    - price within `near_pct` of a support level and last bar closed up -> BULLISH
    - price within `near_pct` of a resistance level and last bar closed down -> BEARISH
    - otherwise (mid-range, or at a level without the confirming bar) -> NEUTRAL

    Weight grows with the level's touch count (more touches, more memory) and
    decays with how long ago the level was last touched, capped at 0.6 — a
    location read should support a trend read, not out-shout it.
    """
    candles = series.candles
    if len(candles) < 2 * span + 5:
        return SignalSource(
            name=_NAME,
            direction=Direction.NEUTRAL,
            weight=0.0,
            detail=f"insufficient history for swing detection (need {2 * span + 5} bars)",
        )

    high_idx, low_idx = _swing_points(candles, span)
    resistances = _cluster_levels([(i, candles[i].high) for i in high_idx], tolerance_pct)
    supports = _cluster_levels([(i, candles[i].low) for i in low_idx], tolerance_pct)

    last = candles[-1]
    price = last.close
    closed_up = last.close > last.open
    closed_down = last.close < last.open

    def _nearest(levels: list[dict[str, float]]) -> dict[str, float] | None:
        near = [
            lv
            for lv in levels
            if lv["level"] > 0 and abs(price - lv["level"]) / lv["level"] <= near_pct
        ]
        if not near:
            return None
        return max(near, key=lambda lv: (lv["touches"], lv["last_touch"]))

    near_support = _nearest(supports)
    near_resistance = _nearest(resistances)

    def _weight(level: dict[str, float]) -> float:
        touches = min(level["touches"] / 4.0, 1.0)  # saturates at 4 touches
        recency = level["last_touch"] / max(len(candles) - 1, 1)
        return round(min(0.6 * touches * (0.5 + 0.5 * recency), 0.6), 4)

    if near_support and closed_up:
        return SignalSource(
            name=_NAME,
            direction=Direction.BULLISH,
            weight=_weight(near_support),
            detail=(
                f"bounce off support {near_support['level']:.2f} "
                f"({int(near_support['touches'])} touches)"
            ),
        )
    if near_resistance and closed_down:
        return SignalSource(
            name=_NAME,
            direction=Direction.BEARISH,
            weight=_weight(near_resistance),
            detail=(
                f"rejection at resistance {near_resistance['level']:.2f} "
                f"({int(near_resistance['touches'])} touches)"
            ),
        )

    n_levels = len(supports) + len(resistances)
    return SignalSource(
        name=_NAME,
        direction=Direction.NEUTRAL,
        weight=0.0,
        detail=f"price mid-range; {n_levels} mapped level(s), no bounce/rejection at one",
    )
