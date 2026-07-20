"""The macro calendar: a purely defensive analyzer.

Every other analyzer in this engine tries to tell you which way something is
going. This one only ever tells you to be less sure.

The logic is the entire point: a signal fired the day before an FOMC decision or
an RBI policy meeting is a signal about to be overwritten by an event nobody in
the model can see. The honest response is not to predict the event — it is to
lower confidence, deterministically, because the calendar is *known in advance*.

This returns a **scalar**, not a `SignalSource`, because it is not a view. It
multiplies the weights of every other source, the same way `volatility_scalar`
already does. A calendar entry can never make a signal more confident, only
less: the scalar is bounded to (0, 1].

Calendar data comes from `cache/models.py::EventItem`. With no calendar cached,
the scalar is exactly 1.0 and the engine behaves as it always did — no calendar
is not the same as no events, and this module does not pretend otherwise.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from alpha_engine.cache.models import EventItem

# How much a looming event dampens conviction, by importance. A high-importance
# event within a day cuts weights by 40%.
DAMPEN: dict[str, float] = {"high": 0.60, "medium": 0.80, "low": 0.95}

# Events further out than this do not affect a swing-horizon signal.
HORIZON_DAYS = 3.0

# Which regions' events matter to which markets. Indian equities care about RBI
# *and* the Fed (DXY and US policy transmit directly); US equities do not need
# to care about an RBI meeting.
MARKET_REGIONS: dict[str, tuple[str, ...]] = {
    "in_equity": ("in", "us", "global"),
    "in_fno": ("in", "us", "global"),
    "us_equity": ("us", "global"),
    "crypto": ("us", "global"),
    "forex": ("us", "in", "global"),
}


def upcoming_events(
    events: list[EventItem],
    market: str,
    now: datetime | None = None,
    horizon_days: float = HORIZON_DAYS,
) -> list[EventItem]:
    """Events relevant to `market` that fall within the horizon, soonest first.

    Past events are excluded: an event that already happened is priced in, and
    dampening for it would be permanent timidity rather than caution.
    """
    now = now or datetime.now(timezone.utc)
    regions = MARKET_REGIONS.get(market, ("global",))
    cutoff = now + timedelta(days=horizon_days)

    return sorted(
        (e for e in events if e.region in regions and now <= e.ts <= cutoff),
        key=lambda e: e.ts,
    )


def calendar_scalar(
    events: list[EventItem],
    market: str,
    now: datetime | None = None,
) -> float:
    """A multiplier in (0, 1] to apply to every source weight.

    Only the single most dampening upcoming event counts. Stacking multipliers
    across several events would compound into near-zero confidence during a busy
    week, which would be a bug wearing the costume of prudence.
    """
    relevant = upcoming_events(events, market, now=now)
    if not relevant:
        return 1.0
    return min(DAMPEN.get(e.importance, 1.0) for e in relevant)


def calendar_note(
    events: list[EventItem],
    market: str,
    now: datetime | None = None,
) -> str:
    """A short human-readable reason for the dampening, for the audit trail."""
    relevant = upcoming_events(events, market, now=now)
    if not relevant:
        return ""
    now = now or datetime.now(timezone.utc)
    soonest = min(relevant, key=lambda e: DAMPEN.get(e.importance, 1.0))
    days = (soonest.ts - now).total_seconds() / 86400.0
    return f"{soonest.name} in {days:.1f}d ({soonest.importance})"
