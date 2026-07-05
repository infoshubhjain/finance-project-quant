"""Macro context analyzer. Reads the US policy/inflation/labor posture and
emits a deliberately small SignalSource: a contextual tilt, never the driver of
a signal. Its weight is hard-capped well below what a price-structure source
can carry, because "the Fed is easing" is a climate, not a trade.

Three monthly FRED series, three transparent votes:

- FEDFUNDS: policy rate falling over ~6 months = easing (supportive, +1);
  rising = tightening (restrictive, -1).
- CPIAUCSL: CPI year-over-year at or under ~2.5% = inflation tamed (+1);
  4% or hotter = pressure for restriction (-1).
- UNRATE: unemployment rising ~0.3pp over 6 months = labor cracking (-1);
  falling by the same = strength (+1).

Votes average into a score in [-1, 1]. Missing series simply don't vote — the
analyzer degrades to a weaker, honest read instead of guessing. Same inputs,
same output, always; thresholds are visible constants, not tuned parameters
(nothing here has been validated for edge — the harness will judge it).
"""

from __future__ import annotations

from alpha_engine.cache.models import MacroObservation
from alpha_engine.schema.signal import Direction, SignalSource

# The tilt cap: macro context may never carry more weight than this.
MAX_WEIGHT = 0.35

# Vote thresholds. Deliberately coarse; the point is posture, not precision.
FEDFUNDS_LOOKBACK = 6  # monthly observations ~ half a year
FEDFUNDS_MOVE = 0.25  # percentage points; one standard policy step
CPI_HOT_YOY = 0.04
CPI_TAMED_YOY = 0.025
UNRATE_LOOKBACK = 6
UNRATE_MOVE = 0.3  # percentage points

_DEADBAND = 0.15  # |score| below this reads as no tilt at all


def _latest_delta(obs: list[MacroObservation], back: int) -> float | None:
    """Change from `back` observations ago to the latest. None if too short."""
    if len(obs) <= back:
        return None
    ordered = sorted(obs, key=lambda o: o.ts)
    return ordered[-1].value - ordered[-1 - back].value


def _yoy(obs: list[MacroObservation]) -> float | None:
    """Year-over-year fractional change of an index series (12 monthly obs)."""
    if len(obs) <= 12:
        return None
    ordered = sorted(obs, key=lambda o: o.ts)
    past = ordered[-13].value
    if past == 0:
        return None
    return (ordered[-1].value - past) / past


def analyze_macro(data: dict[str, list[MacroObservation]]) -> SignalSource:
    """Fold available macro series into one small contextual SignalSource."""
    votes: list[float] = []
    notes: list[str] = []

    ff = data.get("FEDFUNDS") or []
    ff_delta = _latest_delta(ff, FEDFUNDS_LOOKBACK)
    if ff_delta is not None:
        vote = 1.0 if ff_delta <= -FEDFUNDS_MOVE else -1.0 if ff_delta >= FEDFUNDS_MOVE else 0.0
        votes.append(vote)
        notes.append(f"ff_6m={ff_delta:+.2f}")

    cpi = data.get("CPIAUCSL") or []
    cpi_yoy = _yoy(cpi)
    if cpi_yoy is not None:
        vote = -1.0 if cpi_yoy >= CPI_HOT_YOY else 1.0 if cpi_yoy <= CPI_TAMED_YOY else 0.0
        votes.append(vote)
        notes.append(f"cpi_yoy={cpi_yoy:+.3f}")

    un = data.get("UNRATE") or []
    un_delta = _latest_delta(un, UNRATE_LOOKBACK)
    if un_delta is not None:
        vote = -1.0 if un_delta >= UNRATE_MOVE else 1.0 if un_delta <= -UNRATE_MOVE else 0.0
        votes.append(vote)
        notes.append(f"unrate_6m={un_delta:+.2f}")

    if not votes:
        return SignalSource(
            name="macro.context",
            direction=Direction.NEUTRAL,
            weight=0.0,
            detail="no macro data",
        )

    score = sum(votes) / len(votes)
    if score > _DEADBAND:
        direction = Direction.BULLISH
    elif score < -_DEADBAND:
        direction = Direction.BEARISH
    else:
        direction = Direction.NEUTRAL

    weight = round(min(abs(score), 1.0) * MAX_WEIGHT, 4)
    detail = " ".join(notes) + f" score={score:+.2f}"

    return SignalSource(name="macro.context", direction=direction, weight=weight, detail=detail)
