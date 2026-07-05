"""Tests for the deterministic core. These prove the cardinal rule: given fixed
inputs, the analysis and synthesis layers produce fixed, predictable outputs. No
network, no LLM, no randomness. A reader running `pytest` sees the engine is real.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from alpha_engine.cache.models import Candle, Interval, PriceSeries
from alpha_engine.analyzers.crypto_trend import analyze_trend
from alpha_engine.schema.signal import (
    Direction,
    Market,
    Signal,
    SignalSource,
    Timeframe,
)
from alpha_engine.synthesis.synthesize import synthesize


def _series(closes: list[float]) -> PriceSeries:
    candles = [
        Candle(
            ts=datetime(2024, 1, 1, tzinfo=timezone.utc),
            open=c,
            high=c,
            low=c,
            close=c,
        )
        for c in closes
    ]
    return PriceSeries(asset="BTC", interval=Interval.DAY, candles=candles)


def test_schema_rejects_blank_asset():
    with pytest.raises(ValueError):
        Signal(
            asset="  ",
            market=Market.CRYPTO,
            direction=Direction.NEUTRAL,
            confidence=0.0,
            timeframe=Timeframe.SWING,
        )


def test_schema_requires_utc_timestamp():
    with pytest.raises(ValueError):
        Signal(
            asset="BTC",
            market=Market.CRYPTO,
            direction=Direction.NEUTRAL,
            confidence=0.0,
            timeframe=Timeframe.SWING,
            timestamp=datetime(2024, 1, 1),  # naive
        )


def test_confidence_bounds_enforced():
    with pytest.raises(ValueError):
        Signal(
            asset="BTC",
            market=Market.CRYPTO,
            direction=Direction.BULLISH,
            confidence=1.5,
            timeframe=Timeframe.SWING,
        )


def test_trend_rising_series_is_bullish():
    rising = [float(i) for i in range(1, 60)]
    src = analyze_trend(_series(rising))
    assert src.direction is Direction.BULLISH
    assert src.weight > 0


def test_trend_falling_series_is_bearish():
    falling = [float(i) for i in range(60, 1, -1)]
    src = analyze_trend(_series(falling))
    assert src.direction is Direction.BEARISH


def test_trend_insufficient_history_is_neutral():
    src = analyze_trend(_series([1.0, 2.0, 3.0]))
    assert src.direction is Direction.NEUTRAL
    assert src.weight == 0.0


def test_trend_is_deterministic():
    rising = [float(i) for i in range(1, 60)]
    a = analyze_trend(_series(rising))
    b = analyze_trend(_series(rising))
    assert a.model_dump() == b.model_dump()


def test_synthesis_single_bullish_source():
    src = SignalSource(name="t", direction=Direction.BULLISH, weight=0.8)
    sig = synthesize("BTC", Market.CRYPTO, [src])
    assert sig.direction is Direction.BULLISH
    assert 0.0 <= sig.confidence <= 1.0


def test_synthesis_contradictory_sources_lower_confidence():
    bull = SignalSource(name="a", direction=Direction.BULLISH, weight=0.8)
    bear = SignalSource(name="b", direction=Direction.BEARISH, weight=0.8)
    sig = synthesize("BTC", Market.CRYPTO, [bull, bear])
    # equal and opposite -> neutral, near-zero confidence
    assert sig.direction is Direction.NEUTRAL
    assert sig.confidence < 0.2


def test_synthesis_empty_sources_is_neutral():
    sig = synthesize("BTC", Market.CRYPTO, [])
    assert sig.direction is Direction.NEUTRAL
    assert sig.confidence == 0.0


def test_confidence_lower_with_fewer_sources():
    """One source should produce lower confidence than three agreeing sources,
    even with the same agreement quality. This tests the source-count cap."""
    single = [SignalSource(name="rsi", direction=Direction.BULLISH, weight=0.8)]
    triple = [
        SignalSource(name="rsi", direction=Direction.BULLISH, weight=0.8),
        SignalSource(name="bollinger", direction=Direction.BULLISH, weight=0.7),
        SignalSource(name="crypto.trend", direction=Direction.BULLISH, weight=0.9),
    ]
    sig1 = synthesize("BTC", Market.CRYPTO, single)
    sig3 = synthesize("BTC", Market.CRYPTO, triple)
    assert sig1.confidence < sig3.confidence


def test_confidence_lower_with_disagreement():
    """When sources disagree, confidence should drop even if the net direction
    is clear."""
    agree = [
        SignalSource(name="rsi", direction=Direction.BULLISH, weight=0.8),
        SignalSource(name="bollinger", direction=Direction.BULLISH, weight=0.7),
    ]
    disagree = [
        SignalSource(name="rsi", direction=Direction.BULLISH, weight=0.8),
        SignalSource(name="bollinger", direction=Direction.BEARISH, weight=0.7),
    ]
    sig_agree = synthesize("BTC", Market.CRYPTO, agree)
    sig_disagree = synthesize("BTC", Market.CRYPTO, disagree)
    # Both should resolve (one bullish, one likely bearish or neutral),
    # but the disagreeing pair should have lower confidence in its direction
    if sig_disagree.direction is Direction.NEUTRAL:
        assert sig_disagree.confidence < sig_agree.confidence
    else:
        assert sig_disagree.confidence < sig_agree.confidence


def test_confidence_never_exceeds_source_count_cap():
    """Confidence with 1 source should never exceed 0.45."""
    src = SignalSource(name="rsi", direction=Direction.BULLISH, weight=1.0)
    sig = synthesize("BTC", Market.CRYPTO, [src])
    assert sig.confidence <= 0.45


def test_confidence_is_deterministic():
    """Same inputs must always produce the same confidence."""
    sources = [
        SignalSource(name="rsi", direction=Direction.BULLISH, weight=0.8),
        SignalSource(name="bollinger", direction=Direction.BULLISH, weight=0.7),
    ]
    a = synthesize("BTC", Market.CRYPTO, sources)
    b = synthesize("BTC", Market.CRYPTO, sources)
    assert a.confidence == b.confidence
    assert a.direction == b.direction
