"""News sentiment: a deterministic, finance-specific lexicon score.

**This is not an LLM and must never become one.** Sentiment feeds a weight, and
weights are numbers, so the cardinal rule applies with full force. A language
model asked "is this headline bullish?" answers differently on different days,
which would make every signal containing news unreproducible and every backtest
a lie.

If a model-based sentiment score is ever wanted, the rule is stated in
FUTURE_WORK and repeated here: it must be a *local, pinned, deterministic*
classifier whose output is cached per headline, never a live API call inside the
analyze path.

How the score works
-------------------
1. Each headline is scored by counting words from a finance lexicon. Positive
   words add, negative words subtract, and the result is normalized by the
   number of matched terms so a long headline does not automatically outscore a
   short one.
2. Negation flips a term's sign when a negator appears within three words before
   it ("not profitable" is not bullish).
3. Each headline's score is weighted by freshness with exponential decay. A
   three-week-old headline is not news; by `HALF_LIFE_DAYS` its influence has
   halved, and the analyzer stops looking entirely past `MAX_AGE_DAYS`.
4. The weighted mean becomes one small `SignalSource`, hard-capped by
   `MAX_WEIGHT`, because a headline is a reason to look, not a reason to act.

The lexicon is short and visible on purpose. Every entry is a word whose
market meaning is unambiguous. Ambiguous words ("change", "report", "update")
are deliberately absent — including them adds noise that looks like signal.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone

from alpha_engine.cache.models import NewsItem
from alpha_engine.schema.signal import Direction, SignalSource

# The tilt cap. News may never outweigh price structure.
MAX_WEIGHT = 0.30

HALF_LIFE_DAYS = 5.0  # a headline's influence halves in five days
MAX_AGE_DAYS = 21.0  # past three weeks it is history, not news
_DEADBAND = 0.10  # |score| below this reads as no tilt

# Finance lexicon. Weights are coarse (1.0 / 1.5) because a finer scale would
# imply a precision this method does not have.
POSITIVE: dict[str, float] = {
    "beat": 1.5,
    "beats": 1.5,
    "surge": 1.5,
    "surges": 1.5,
    "soar": 1.5,
    "soars": 1.5,
    "rally": 1.5,
    "rallies": 1.5,
    "upgrade": 1.5,
    "upgraded": 1.5,
    "outperform": 1.5,
    "record": 1.0,
    "profit": 1.0,
    "profitable": 1.0,
    "growth": 1.0,
    "gain": 1.0,
    "gains": 1.0,
    "rise": 1.0,
    "rises": 1.0,
    "jump": 1.0,
    "jumps": 1.0,
    "approval": 1.0,
    "approved": 1.0,
    "expansion": 1.0,
    "dividend": 1.0,
    "buyback": 1.5,
    "stimulus": 1.0,
    "easing": 1.0,
    "recovery": 1.0,
    "strong": 1.0,
    "boost": 1.0,
    "wins": 1.0,
    "acquire": 1.0,
    "acquisition": 1.0,
}

NEGATIVE: dict[str, float] = {
    "miss": 1.5,
    "misses": 1.5,
    "plunge": 1.5,
    "plunges": 1.5,
    "crash": 1.5,
    "crashes": 1.5,
    "slump": 1.5,
    "slumps": 1.5,
    "downgrade": 1.5,
    "downgraded": 1.5,
    "underperform": 1.5,
    "fraud": 1.5,
    "probe": 1.5,
    "investigation": 1.5,
    "lawsuit": 1.5,
    "bankruptcy": 1.5,
    "default": 1.5,
    "loss": 1.0,
    "losses": 1.0,
    "decline": 1.0,
    "declines": 1.0,
    "fall": 1.0,
    "falls": 1.0,
    "drop": 1.0,
    "drops": 1.0,
    "weak": 1.0,
    "warning": 1.0,
    "warns": 1.0,
    "cut": 1.0,
    "cuts": 1.0,
    "layoff": 1.0,
    "layoffs": 1.0,
    "resign": 1.0,
    "resigns": 1.0,
    "delay": 1.0,
    "delayed": 1.0,
    "penalty": 1.0,
    "fine": 1.0,
    "recall": 1.0,
    "tightening": 1.0,
    "recession": 1.5,
    "inflation": 1.0,
    "halt": 1.0,
    "halted": 1.0,
    "suspended": 1.0,
}

# A negator within this many words *before* a term flips its sign.
NEGATORS = frozenset({"not", "no", "never", "without", "fails", "fail", "denies", "denied"})
_NEGATION_WINDOW = 3

_WORD = re.compile(r"[a-z']+")


def score_headline(headline: str) -> float | None:
    """Score one headline to [-1, 1]. None when no lexicon term matched at all —
    which is the common case and must not be confused with neutral-on-purpose."""
    words = _WORD.findall(headline.lower())
    if not words:
        return None

    total = 0.0
    matched = 0

    for i, word in enumerate(words):
        weight = POSITIVE.get(word)
        sign = 1.0
        if weight is None:
            weight = NEGATIVE.get(word)
            sign = -1.0
        if weight is None:
            continue

        window = words[max(0, i - _NEGATION_WINDOW) : i]
        if any(w in NEGATORS for w in window):
            sign = -sign

        total += sign * weight
        matched += 1

    if matched == 0:
        return None

    # Normalize by the strongest possible score for the matched terms so the
    # result stays in [-1, 1] regardless of headline length.
    return max(-1.0, min(1.0, total / (matched * 1.5)))


def _decay_weight(ts: datetime, now: datetime) -> float:
    """Exponential freshness decay. Returns 0.0 past MAX_AGE_DAYS."""
    age_days = (now - ts).total_seconds() / 86400.0
    if age_days < 0:  # a future-dated headline is a feed bug; treat as fresh
        age_days = 0.0
    if age_days > MAX_AGE_DAYS:
        return 0.0
    return 0.5 ** (age_days / HALF_LIFE_DAYS)


def score_news(items: list[NewsItem]) -> list[NewsItem]:
    """Attach `sentiment_score` to each item. Pure: returns new objects rather
    than mutating, so caching a scored batch is safe.

    Takes no `now`: a headline's *score* does not depend on when you read it,
    only its *weight* does. Freshness decay belongs in `analyze_sentiment`,
    which is what makes a scored item safe to cache indefinitely."""
    return [i.model_copy(update={"sentiment_score": score_headline(i.headline)}) for i in items]


def analyze_sentiment(
    items: list[NewsItem],
    asset: str | None = None,
    now: datetime | None = None,
) -> SignalSource:
    """Fold recent headlines into one small contextual SignalSource.

    When `asset` is given, only headlines tagged with it are considered; an
    untagged headline is macro context, not a read on this ticker.
    """
    now = now or datetime.now(timezone.utc)

    relevant = items if asset is None else [i for i in items if asset.upper() in i.asset_tags]

    weighted_sum = 0.0
    weight_total = 0.0
    counted = 0
    for item in relevant:
        score = item.sentiment_score
        if score is None:
            score = score_headline(item.headline)
        if score is None:
            continue
        decay = _decay_weight(item.ts, now)
        if decay <= 0.0:
            continue
        weighted_sum += score * decay
        weight_total += decay
        counted += 1

    if weight_total == 0.0:
        return SignalSource(
            name="news.sentiment",
            direction=Direction.NEUTRAL,
            weight=0.0,
            detail="no scorable recent headlines",
        )

    score = weighted_sum / weight_total

    if score > _DEADBAND:
        direction = Direction.BULLISH
    elif score < -_DEADBAND:
        direction = Direction.BEARISH
    else:
        direction = Direction.NEUTRAL

    # Confidence in a news read grows with the number of corroborating
    # headlines, but with diminishing returns: ten headlines are not ten times
    # more informative than one, they are about three times.
    breadth = min(1.0, math.sqrt(counted) / 3.0)

    # Freshness must scale the *weight*, not just the average. Decay inside a
    # weighted mean cancels out when there is one headline, which would make a
    # three-week-old story count exactly as hard as this morning's.
    freshness = weight_total / counted

    weight = round(min(abs(score), 1.0) * breadth * freshness * MAX_WEIGHT, 4)

    return SignalSource(
        name="news.sentiment",
        direction=direction,
        weight=weight,
        detail=f"n={counted} score={score:+.2f} breadth={breadth:.2f} fresh={freshness:.2f}",
    )
