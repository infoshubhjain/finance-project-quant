"""Tests for Phase 11e: forex carry and the dollar cycle.

The dollar-direction logic is the fragile part. "A strong dollar lifts USDINR
but pushes EURUSD down" is correct and also exactly the kind of statement that
gets inverted during a tidy-up, so both directions are pinned explicitly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from alpha_engine.analyzers.forex_carry import (
    MAX_WEIGHT,
    analyze_forex_carry,
    carry_differential,
    split_pair,
)
from alpha_engine.cache.models import Candle, Interval, MacroObservation, PriceSeries
from alpha_engine.schema.signal import Direction

T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)


def _series(asset: str, closes: list[float]) -> PriceSeries:
    return PriceSeries(
        asset=asset,
        interval=Interval.DAY,
        candles=[
            Candle(ts=T0 + timedelta(days=i), open=c, high=c, low=c, close=c)
            for i, c in enumerate(closes)
        ],
    )


def _rate(series_id: str, value: float) -> list[MacroObservation]:
    return [MacroObservation(series_id=series_id, ts=T0, value=value, source="test")]


def _flat(asset: str, n: int = 30, value: float = 100.0) -> PriceSeries:
    return _series(asset, [value] * n)


def _rising(asset: str, n: int = 30) -> PriceSeries:
    return _series(asset, [100.0 * (1.0 + 0.005 * i) for i in range(n)])


def _falling(asset: str, n: int = 30) -> PriceSeries:
    return _series(asset, [100.0 * (1.0 - 0.005 * i) for i in range(n)])


# ---------------------------------------------------------------------------
# Pair parsing
# ---------------------------------------------------------------------------


def test_splits_plain_pair():
    assert split_pair("EURUSD") == ("EUR", "USD")


def test_splits_slashed_pair():
    assert split_pair("EUR/USD") == ("EUR", "USD")


def test_rejects_malformed_pair():
    """Guessing at a malformed symbol means analyzing the wrong currency."""
    assert split_pair("BTC") is None
    assert split_pair("TOOLONGPAIR") is None
    assert split_pair("EUR123") is None


# ---------------------------------------------------------------------------
# Carry differential
# ---------------------------------------------------------------------------


def test_carry_is_base_minus_quote():
    macro = {"FEDFUNDS": _rate("FEDFUNDS", 5.5), "RBI_REPO_RATE": _rate("RBI_REPO_RATE", 6.5)}
    assert carry_differential("USDINR", macro) == pytest.approx(-1.0)


def test_carry_none_when_a_leg_is_unknown():
    """One known rate plus one assumed rate is a fabricated differential."""
    assert carry_differential("USDINR", {"FEDFUNDS": _rate("FEDFUNDS", 5.5)}) is None


def test_carry_none_for_unmapped_currency():
    macro = {"FEDFUNDS": _rate("FEDFUNDS", 5.5)}
    assert carry_differential("USDJPY", macro) is None


def test_carry_none_for_malformed_pair():
    assert carry_differential("NOTAPAIR", {}) is None


# ---------------------------------------------------------------------------
# The analyzer
# ---------------------------------------------------------------------------


def test_non_pair_asset_is_zero_weight():
    src = analyze_forex_carry(_flat("BTC"))
    assert src.weight == 0.0
    assert "not a recognizable currency pair" in src.detail


def test_no_data_is_zero_weight():
    src = analyze_forex_carry(_flat("EURUSD"))
    assert src.direction is Direction.NEUTRAL
    assert src.weight == 0.0


def test_positive_carry_is_bullish_for_the_pair():
    """Base currency pays more, so being long the pair earns carry."""
    macro = {"FEDFUNDS": _rate("FEDFUNDS", 8.0), "RBI_REPO_RATE": _rate("RBI_REPO_RATE", 4.0)}
    assert analyze_forex_carry(_flat("USDINR"), macro=macro).direction is Direction.BULLISH


def test_negative_carry_is_bearish_for_the_pair():
    macro = {"FEDFUNDS": _rate("FEDFUNDS", 4.0), "RBI_REPO_RATE": _rate("RBI_REPO_RATE", 8.0)}
    assert analyze_forex_carry(_flat("USDINR"), macro=macro).direction is Direction.BEARISH


def test_small_differential_abstains():
    macro = {"FEDFUNDS": _rate("FEDFUNDS", 6.0), "RBI_REPO_RATE": _rate("RBI_REPO_RATE", 6.2)}
    assert analyze_forex_carry(_flat("USDINR"), macro=macro).direction is Direction.NEUTRAL


def test_strong_dollar_lifts_pairs_with_usd_as_base():
    """USDINR up when the dollar is strong. If this flips, the sign is wrong."""
    src = analyze_forex_carry(_flat("USDINR"), dxy=_rising("DXY"))
    assert src.direction is Direction.BULLISH


def test_strong_dollar_pushes_down_pairs_with_usd_as_quote():
    """EURUSD down when the dollar is strong — the mirror of the test above."""
    src = analyze_forex_carry(_flat("EURUSD"), dxy=_rising("DXY"))
    assert src.direction is Direction.BEARISH


def test_weak_dollar_reverses_both():
    assert analyze_forex_carry(_flat("USDINR"), dxy=_falling("DXY")).direction is Direction.BEARISH
    assert analyze_forex_carry(_flat("EURUSD"), dxy=_falling("DXY")).direction is Direction.BULLISH


def test_flat_dollar_abstains():
    assert analyze_forex_carry(_flat("EURUSD"), dxy=_flat("DXY")).direction is Direction.NEUTRAL


def test_inr_band_fades_a_sharp_move():
    """A managed currency moving hard attracts intervention, so a large move is
    more likely to fade than extend."""
    src = analyze_forex_carry(_rising("USDINR"))
    assert src.direction is Direction.BEARISH
    assert "inr_band" in src.detail


def test_band_logic_does_not_apply_to_free_floating_pairs():
    src = analyze_forex_carry(_rising("EURUSD"))
    assert "inr_band" not in src.detail


def test_weight_respects_the_cap():
    macro = {"FEDFUNDS": _rate("FEDFUNDS", 20.0), "RBI_REPO_RATE": _rate("RBI_REPO_RATE", 0.1)}
    src = analyze_forex_carry(_rising("USDINR"), macro=macro, dxy=_rising("DXY"))
    assert src.weight <= MAX_WEIGHT


def test_more_agreeing_votes_gives_more_weight():
    macro = {"FEDFUNDS": _rate("FEDFUNDS", 8.0), "RBI_REPO_RATE": _rate("RBI_REPO_RATE", 4.0)}
    carry_only = analyze_forex_carry(_flat("USDINR"), macro=macro)
    both = analyze_forex_carry(_flat("USDINR"), macro=macro, dxy=_rising("DXY"))
    assert both.weight > carry_only.weight


def test_short_series_does_not_crash():
    assert (
        analyze_forex_carry(_series("EURUSD", [1.0, 1.1]), dxy=_series("DXY", [1.0])).weight == 0.0
    )


def test_analyzer_is_deterministic():
    macro = {"FEDFUNDS": _rate("FEDFUNDS", 8.0), "RBI_REPO_RATE": _rate("RBI_REPO_RATE", 4.0)}
    a = analyze_forex_carry(_flat("USDINR"), macro=macro)
    b = analyze_forex_carry(_flat("USDINR"), macro=macro)
    assert (a.direction, a.weight, a.detail) == (b.direction, b.weight, b.detail)
