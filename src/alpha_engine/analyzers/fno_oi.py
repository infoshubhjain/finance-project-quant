"""F&O open-interest analyzer: PCR, max pain, and OI-shift structure for Indian
index options. This is the depth feature generic tools skip, and it follows the
cardinal rule exactly: pure functions of an OptionsChain, no network, no
randomness, no LLM. Every number is hand-checkable from the chain itself.

Interpretation notes (kept transparent, claimed as heuristics, not edge):

- PCR (put OI / call OI): heavy put open interest usually means participants
  are writing puts they expect to stay above — positioning that acts like
  support. Unusually call-heavy chains read the opposite way.
- Max pain: the expiry price at which option HOLDERS collectively get paid the
  least (equivalently, writers keep the most premium). Price often gravitates
  toward it into expiry. Spot sitting below max pain is a mild upward pull;
  above it, a mild downward pull.
- OI change: where fresh contracts appeared today. Puts being added faster than
  calls reads as support being built underneath, and vice versa. Skipped
  entirely when the source doesn't provide day-over-day change (None != 0).

Whether any of this predicts anything is the validation harness's question to
answer, not this module's to assert. Research output only, never advice —
especially here, in exactly the regulated retail F&O space.
"""

from __future__ import annotations

from alpha_engine.cache.models import OptionsChain
from alpha_engine.schema.signal import Direction, SignalSource

# F&O structure may carry more weight than a contextual tilt (it is the primary
# read for this market) but is still capped below full conviction.
MAX_WEIGHT = 0.6

PCR_BULLISH = 1.2  # put/call OI at or above this reads as a supportive base
PCR_BEARISH = 0.7  # at or below this reads as call-heavy, fragile
MAX_PAIN_PULL = 0.01  # spot must sit >1% away from max pain to count as a pull
OI_SHIFT_RATIO = 1.25  # one side must add OI 25% faster to count as a vote

_DEADBAND = 0.15  # |score| below this is no view at all


def summarize_chain(chain: OptionsChain) -> dict[str, float | None]:
    """Return the chain-level facts that drive the F&O read.

    This is deliberately separate from the vote aggregation so the same
    normalized summary can be surfaced in the CLI, dashboard, and tests.
    """
    ratio = pcr(chain)
    pain = max_pain(chain)
    put_wall = oi_support_resistance(chain, Direction.BULLISH)
    call_wall = oi_support_resistance(chain, Direction.BEARISH)

    call_adds = [q.oi_change for q in chain.calls() if q.oi_change is not None]
    put_adds = [q.oi_change for q in chain.puts() if q.oi_change is not None]
    call_new = sum(c for c in call_adds if c > 0) if call_adds else None
    put_new = sum(p for p in put_adds if p > 0) if put_adds else None

    gap = None
    if pain is not None and chain.spot:
        gap = round((pain - chain.spot) / chain.spot, 4)

    return {
        "pcr": round(ratio, 4) if ratio is not None else None,
        "max_pain": pain,
        "max_pain_gap": gap,
        "put_wall": put_wall,
        "call_wall": call_wall,
        "fresh_put_oi": float(put_new) if put_new is not None else None,
        "fresh_call_oi": float(call_new) if call_new is not None else None,
    }


def _format_summary(summary: dict[str, float | None]) -> str:
    parts: list[str] = []
    if summary["pcr"] is not None:
        parts.append(f"pcr={summary['pcr']:.2f}")
    if summary["max_pain"] is not None:
        gap = summary["max_pain_gap"]
        if gap is not None:
            parts.append(f"max_pain={summary['max_pain']:.0f} gap={gap:+.3f}")
        else:
            parts.append(f"max_pain={summary['max_pain']:.0f}")
    if summary["put_wall"] is not None:
        parts.append(f"put_wall={summary['put_wall']:.0f}")
    if summary["call_wall"] is not None:
        parts.append(f"call_wall={summary['call_wall']:.0f}")
    if summary["fresh_put_oi"] is not None or summary["fresh_call_oi"] is not None:
        parts.append(
            f"oi_new_puts={summary['fresh_put_oi'] or 0:.0f} "
            f"oi_new_calls={summary['fresh_call_oi'] or 0:.0f}"
        )
    return " ".join(parts)


def pcr(chain: OptionsChain) -> float | None:
    """Put-call ratio by open interest. None when there is no call OI to divide
    by (an empty or one-sided chain), never a fake infinity."""
    call_oi = sum(q.oi for q in chain.calls())
    put_oi = sum(q.oi for q in chain.puts())
    if call_oi == 0:
        return None
    return put_oi / call_oi


def max_pain(chain: OptionsChain) -> float | None:
    """The strike minimizing total intrinsic payout to option holders at expiry.

    For each candidate expiry price S (candidates are the chain's own strikes):
    every call at strike K pays holders OI * max(0, S - K); every put pays
    OI * max(0, K - S). The S with the smallest total is max pain. Ties resolve
    to the lowest strike so the result is deterministic.
    """
    strikes = sorted({q.strike for q in chain.quotes})
    if not strikes:
        return None

    best_strike: float | None = None
    best_payout = float("inf")
    for s in strikes:
        payout = sum(q.oi * max(0.0, s - q.strike) for q in chain.calls())
        payout += sum(q.oi * max(0.0, q.strike - s) for q in chain.puts())
        if payout < best_payout:
            best_payout = payout
            best_strike = s
    return best_strike


def oi_support_resistance(chain: OptionsChain, direction: Direction) -> float | None:
    """An honest invalidation level from OI structure: the biggest put wall is
    the market's consensus floor, the biggest call wall its ceiling. A bullish
    view is wrong below the put wall; a bearish view is wrong above the call
    wall. Walls on the far side of spot are ignored (a floor above you is not
    a floor). Ties resolve toward spot — the nearer wall is the binding one."""
    if direction is Direction.NEUTRAL:
        return None

    quotes = chain.puts() if direction is Direction.BULLISH else chain.calls()
    if chain.spot is not None:
        if direction is Direction.BULLISH:
            quotes = [q for q in quotes if q.strike <= chain.spot]
        else:
            quotes = [q for q in quotes if q.strike >= chain.spot]
    if not quotes:
        return None

    best = max(
        quotes,
        key=lambda q: (q.oi, q.strike if direction is Direction.BULLISH else -q.strike),
    )
    return best.strike


def analyze_fno(chain: OptionsChain) -> SignalSource:
    """Fold PCR, max-pain pull, and OI shifts into one SignalSource. Components
    that can't be computed simply don't vote; the source degrades to a weaker,
    honest read instead of guessing."""
    votes: list[float] = []

    summary = summarize_chain(chain)

    ratio = summary["pcr"]
    if ratio is not None:
        vote = 1.0 if ratio >= PCR_BULLISH else -1.0 if ratio <= PCR_BEARISH else 0.0
        votes.append(vote)

    pain = summary["max_pain"]
    if pain is not None and chain.spot:
        gap = summary["max_pain_gap"] or 0.0
        vote = 1.0 if gap > MAX_PAIN_PULL else -1.0 if gap < -MAX_PAIN_PULL else 0.0
        votes.append(vote)

    call_new = summary["fresh_call_oi"]
    put_new = summary["fresh_put_oi"]
    if call_new is not None or put_new is not None:
        # Only additions count as fresh positioning; unwinding is ambiguous.
        call_new_value = call_new or 0.0
        put_new_value = put_new or 0.0
        if put_new_value > call_new_value * OI_SHIFT_RATIO:
            vote = 1.0
        elif call_new_value > put_new_value * OI_SHIFT_RATIO:
            vote = -1.0
        else:
            vote = 0.0
        votes.append(vote)

    if not votes:
        return SignalSource(
            name="fno.oi",
            direction=Direction.NEUTRAL,
            weight=0.0,
            detail="empty or unusable chain",
        )

    score = sum(votes) / len(votes)
    if score > _DEADBAND:
        direction = Direction.BULLISH
    elif score < -_DEADBAND:
        direction = Direction.BEARISH
    else:
        direction = Direction.NEUTRAL

    weight = round(min(abs(score), 1.0) * MAX_WEIGHT, 4)
    detail = f"{_format_summary(summary)} score={score:+.2f}"

    return SignalSource(name="fno.oi", direction=direction, weight=weight, detail=detail)
