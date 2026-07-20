"""Forex carry and dollar-cycle analyzer.

The existing `forex_trend` analyzer is trend plus a z-score — the same thing the
equity path does, pointed at a currency pair. What the blueprint actually wanted
was the two things that are specific to FX:

**1. Carry.** A currency pair is two interest rates. Holding a high-yield
currency against a low-yield one pays you the difference every day you hold it,
and that differential is one of the oldest documented return sources in FX. Once
FRED (US) and the RBI scraper (India) are both feeding the cache, carry is not a
model — it is subtraction.

The honest caveat, stated because it matters: carry works until it doesn't.
High-yield currencies pay you steadily and then gap against you, which is why
this analyzer caps its own weight and why the invalidation level on an FX signal
is doing real work.

**2. The dollar cycle.** DXY strength transmits to almost everything: emerging
market currencies, commodities, and Indian equities in particular. A pair
containing USD inherits the dollar's own trend as a first-order effect.

**INR specifics.** The RBI manages the rupee inside an informal band rather than
letting it float freely. A USDINR move that would be unremarkable in EURUSD is
large for a managed currency, so INR pairs get tighter thresholds — that is a
property of the market, not a tuned parameter.

Rates come from `MacroObservation` series already in the cache. With no rate
data the carry vote abstains and this degrades to the dollar read alone.
"""

from __future__ import annotations

import math

from alpha_engine.cache.models import MacroObservation, PriceSeries
from alpha_engine.schema.signal import Direction, SignalSource

MAX_WEIGHT = 0.40

# Which cached macro series carries each currency's policy rate.
POLICY_RATE_SERIES: dict[str, str] = {
    "USD": "FEDFUNDS",
    "INR": "RBI_REPO_RATE",
}

# Annualized carry, in percentage points, that counts as a real differential.
CARRY_SIGNIFICANT = 1.5

# Trend thresholds. INR is managed, so the same move means more.
DXY_TREND_MOVE = 0.02
INR_TREND_MOVE = 0.01

_DEADBAND = 0.15


def split_pair(pair: str) -> tuple[str, str] | None:
    """'EURUSD' or 'EUR/USD' -> ('EUR', 'USD'). None if it is not a 6-letter
    pair, because guessing at a malformed symbol is how you analyze the wrong
    currency."""
    cleaned = pair.upper().replace("/", "").replace("_", "").strip()
    if len(cleaned) != 6 or not cleaned.isalpha():
        return None
    return cleaned[:3], cleaned[3:]


def _latest(obs: list[MacroObservation]) -> float | None:
    return max(obs, key=lambda o: o.ts).value if obs else None


def carry_differential(
    pair: str,
    macro: dict[str, list[MacroObservation]],
) -> float | None:
    """Base-currency rate minus quote-currency rate, in percentage points.

    For USDINR: India's repo rate is the base... no — USD is the base and INR
    the quote, so the differential is `US rate - India rate`, which is normally
    negative. A negative differential means holding USDINR long *costs* carry,
    which is exactly the information wanted.

    None when either leg's rate is unknown. A differential computed from one
    known rate and one assumed rate is a fabricated number.
    """
    legs = split_pair(pair)
    if legs is None:
        return None
    base, quote = legs

    base_series = POLICY_RATE_SERIES.get(base)
    quote_series = POLICY_RATE_SERIES.get(quote)
    if base_series is None or quote_series is None:
        return None

    base_rate = _latest(macro.get(base_series) or [])
    quote_rate = _latest(macro.get(quote_series) or [])
    if base_rate is None or quote_rate is None:
        return None

    return base_rate - quote_rate


def _trend(series: PriceSeries, lookback: int = 20) -> float | None:
    closes = [c.close for c in series.candles]
    if len(closes) <= lookback or closes[-1 - lookback] <= 0:
        return None
    return closes[-1] / closes[-1 - lookback] - 1.0


def analyze_forex_carry(
    series: PriceSeries,
    macro: dict[str, list[MacroObservation]] | None = None,
    dxy: PriceSeries | None = None,
) -> SignalSource:
    """Fold carry and the dollar cycle into one SignalSource for an FX pair."""
    macro = macro or {}
    pair = series.asset.upper()
    legs = split_pair(pair)

    if legs is None:
        return SignalSource(
            name="forex.carry",
            direction=Direction.NEUTRAL,
            weight=0.0,
            detail=f"{pair} is not a recognizable currency pair",
        )

    base, quote = legs
    votes: list[float] = []
    notes: list[str] = []

    # --- 1. carry differential ---
    carry = carry_differential(pair, macro)
    if carry is not None:
        # Positive differential = the base currency pays more = long the pair
        # earns carry.
        vote = 1.0 if carry >= CARRY_SIGNIFICANT else -1.0 if carry <= -CARRY_SIGNIFICANT else 0.0
        votes.append(vote)
        notes.append(f"carry={carry:+.2f}pp")

    # --- 2. dollar cycle ---
    if dxy is not None:
        dxy_trend = _trend(dxy)
        if dxy_trend is not None:
            if abs(dxy_trend) >= DXY_TREND_MOVE:
                strong_dollar = dxy_trend > 0
                # A rising dollar lifts pairs where USD is the base (USDINR up)
                # and pushes down pairs where USD is the quote (EURUSD down).
                if base == "USD":
                    votes.append(1.0 if strong_dollar else -1.0)
                elif quote == "USD":
                    votes.append(-1.0 if strong_dollar else 1.0)
                else:
                    votes.append(0.0)
            else:
                votes.append(0.0)
            notes.append(f"dxy_20d={dxy_trend:+.2%}")

    # --- 3. INR managed-band read ---
    if "INR" in (base, quote):
        own_trend = _trend(series)
        if own_trend is not None:
            # A managed currency moving hard tends to attract intervention,
            # which makes a large move more likely to fade than to extend.
            if own_trend >= INR_TREND_MOVE:
                votes.append(-1.0)
            elif own_trend <= -INR_TREND_MOVE:
                votes.append(1.0)
            else:
                votes.append(0.0)
            notes.append(f"inr_band={own_trend:+.2%}")

    if not votes:
        return SignalSource(
            name="forex.carry",
            direction=Direction.NEUTRAL,
            weight=0.0,
            detail=f"{pair}: no rate or dollar data",
        )

    score = sum(votes) / len(votes)

    if score > _DEADBAND:
        direction = Direction.BULLISH
    elif score < -_DEADBAND:
        direction = Direction.BEARISH
    else:
        direction = Direction.NEUTRAL

    breadth = min(1.0, math.sqrt(len(votes)) / math.sqrt(3.0))
    weight = round(min(abs(score), 1.0) * breadth * MAX_WEIGHT, 4)

    return SignalSource(
        name="forex.carry",
        direction=direction,
        weight=weight,
        detail=" ".join(notes) + f" score={score:+.2f}",
    )
