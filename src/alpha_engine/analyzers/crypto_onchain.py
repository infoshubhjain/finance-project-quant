"""Crypto positioning and on-chain analyzer.

Before this existed, the "crypto agent" was a moving-average crossover wearing a
different name. This is the read the original blueprint actually wanted: what is
the *positioning* underneath the price?

Four transparent votes, each of which can abstain:

1. **Funding rate** (contrarian). Perpetual futures have no expiry, so exchanges
   tether them to spot by making one side pay the other every 8 hours. Strongly
   positive funding means longs are paying to stay long — the crowd is leaning
   one way and paying for it. Crowded trades unwind violently, so extreme
   positive funding votes *bearish*, and extreme negative votes *bullish*.
   This is the only deliberately inverted vote in the codebase, which is why it
   gets this much explanation.

2. **Open-interest build-up** (confirming). Rising OI means new money entering
   rather than existing positions closing. It has no direction of its own, so it
   only sharpens whatever funding already said.

3. **Exchange net flow** (directional). Coins moving onto exchanges are coins
   positioned to sell (bearish); coins moving off are coins going to storage
   (bullish). Needs a Glassnode key; abstains without one.

4. **BTC dominance** (risk appetite). Rising dominance is capital retreating from
   altcoins into BTC — risk-off for everything that is not BTC. For BTC itself
   this vote is skipped, because "BTC is gaining share" says nothing about BTC's
   own direction.

Thresholds are visible constants, coarse on purpose, and unvalidated: the
ranking and backtest harnesses are what decide whether any of this has edge.
"""

from __future__ import annotations

import math

from alpha_engine.cache.models import OnChainObservation
from alpha_engine.schema.signal import Direction, SignalSource

# Positioning is context, not a thesis. Capped below price-structure sources.
MAX_WEIGHT = 0.40

# Funding is quoted per 8-hour period. 0.01% is the exchange's neutral resting
# value; 0.05% per period is ~55% annualized, which is a genuinely crowded long.
FUNDING_CROWDED = 0.0005
FUNDING_CAPITULATED = -0.0003

OI_BUILDUP = 0.10  # +10% open interest over the window = new money arriving
NETFLOW_SIGNIFICANT = 0.15  # net flow of 15% of window mean = a real move
DOMINANCE_MOVE = 1.0  # percentage points of market share over the window

_DEADBAND = 0.15
_RECENT = 9  # funding prints 3x/day, so 9 observations is ~3 days


def _by_metric(obs: list[OnChainObservation], metric: str) -> list[OnChainObservation]:
    """Observations for one metric, oldest first."""
    return sorted((o for o in obs if o.metric == metric), key=lambda o: o.ts)


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def _pct_change(series: list[float]) -> float | None:
    """Change from first to last, as a fraction. None if the base is zero, since
    a percentage change from nothing is undefined, not infinite."""
    if len(series) < 2 or series[0] == 0:
        return None
    return series[-1] / series[0] - 1.0


def analyze_onchain(
    obs: list[OnChainObservation],
    asset: str = "BTC",
) -> SignalSource:
    """Fold available positioning and on-chain metrics into one SignalSource.

    Every vote is optional. With no data at all this returns a zero-weight
    NEUTRAL source, which is the honest answer for an asset with no positioning
    data rather than a fabricated one.
    """
    asset = asset.upper()
    votes: list[float] = []
    notes: list[str] = []

    # --- 1. funding rate (contrarian) ---
    funding = _by_metric(obs, f"funding_rate_{asset}")
    if funding:
        recent = [o.value for o in funding[-_RECENT:]]
        avg = _mean(recent)
        if avg >= FUNDING_CROWDED:
            votes.append(-1.0)  # crowded longs -> contrarian bearish
        elif avg <= FUNDING_CAPITULATED:
            votes.append(1.0)  # shorts paying -> contrarian bullish
        else:
            votes.append(0.0)
        notes.append(f"funding={avg:+.4%}")

    # --- 2. open interest build-up (confirms, never leads) ---
    oi = _by_metric(obs, f"open_interest_{asset}")
    oi_change = _pct_change([o.value for o in oi]) if len(oi) >= 2 else None
    if oi_change is not None:
        notes.append(f"oi={oi_change:+.1%}")

    # --- 3. exchange net flow (directional) ---
    netflow = _by_metric(obs, f"exchange_netflow_{asset}")
    if len(netflow) >= 2:
        values = [o.value for o in netflow]
        recent_sum = sum(values[-7:])
        scale = _mean([abs(v) for v in values]) or 1.0
        normalized = recent_sum / (scale * min(7, len(values)))
        # Positive net flow = coins moving ONTO exchanges = supply to sell
        if normalized >= NETFLOW_SIGNIFICANT:
            votes.append(-1.0)
        elif normalized <= -NETFLOW_SIGNIFICANT:
            votes.append(1.0)
        else:
            votes.append(0.0)
        notes.append(f"netflow={normalized:+.2f}")

    # --- 4. BTC dominance (risk appetite; meaningless for BTC itself) ---
    dominance = _by_metric(obs, "btc_dominance")
    if len(dominance) >= 2 and asset != "BTC":
        delta = dominance[-1].value - dominance[0].value
        if delta >= DOMINANCE_MOVE:
            votes.append(-1.0)  # capital fleeing alts
        elif delta <= -DOMINANCE_MOVE:
            votes.append(1.0)
        else:
            votes.append(0.0)
        notes.append(f"dominance={delta:+.2f}pp")

    if not votes:
        return SignalSource(
            name="crypto.onchain",
            direction=Direction.NEUTRAL,
            weight=0.0,
            detail="no positioning data",
        )

    score = sum(votes) / len(votes)

    if score > _DEADBAND:
        direction = Direction.BULLISH
    elif score < -_DEADBAND:
        direction = Direction.BEARISH
    else:
        direction = Direction.NEUTRAL

    # Breadth: one metric agreeing with itself is not corroboration. A single
    # vote earns ~58% of the available weight, three independent ones earn all
    # of it. Without this, a lone funding read saturates the cap and the
    # open-interest adjustment below becomes invisible.
    breadth = min(1.0, math.sqrt(len(votes)) / math.sqrt(3.0))

    # Open interest sharpens conviction without creating it: new money arriving
    # makes an existing positioning read matter more, but cannot make a signal
    # out of a neutral one.
    conviction = 1.25 if (oi_change is not None and oi_change >= OI_BUILDUP) else 1.0

    weight = round(min(abs(score) * breadth * conviction, 1.0) * MAX_WEIGHT, 4)
    detail = " ".join(notes) + f" score={score:+.2f} breadth={breadth:.2f}"

    return SignalSource(
        name="crypto.onchain",
        direction=direction,
        weight=weight,
        detail=detail,
    )
