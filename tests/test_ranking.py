"""Tests for factor ranking (quant/ranking.py)."""

import math
from datetime import datetime, timezone

import pytest

from alpha_engine.cache.models import Candle, Interval, PriceSeries
from alpha_engine.quant.ranking import (
    coverage,
    forward_returns,
    hit_rate,
    ic_decay,
    rank_factors,
    rank_ic,
)


def _series(closes: list[float]) -> PriceSeries:
    """Build a minimal PriceSeries from closes."""
    base_ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    candles = [
        Candle(
            open=c,
            high=c,
            low=c,
            close=c,
            volume=1000,
            ts=datetime.fromtimestamp(base_ts.timestamp() + i * 86400, tz=timezone.utc),
        )
        for i, c in enumerate(closes)
    ]
    return PriceSeries(asset="TEST", interval=Interval.DAY, candles=candles)


def test_forward_returns_basic():
    closes = [100.0, 102.0, 105.0, 103.0, 108.0]
    fwd = forward_returns(closes, horizon=1)

    assert len(fwd) == 5
    assert fwd[0] == pytest.approx((102 - 100) / 100)
    assert fwd[1] == pytest.approx((105 - 102) / 102)
    assert fwd[2] == pytest.approx((103 - 105) / 105)
    assert fwd[3] == pytest.approx((108 - 103) / 103)
    assert fwd[4] is None  # No future


def test_forward_returns_multi_bar_horizon():
    closes = [100.0, 102.0, 105.0, 103.0, 108.0, 110.0]
    fwd = forward_returns(closes, horizon=3)

    assert len(fwd) == 6
    assert fwd[0] == pytest.approx((103 - 100) / 100)
    assert fwd[1] == pytest.approx((108 - 102) / 102)
    assert fwd[2] == pytest.approx((110 - 105) / 105)
    assert fwd[3] is None
    assert fwd[4] is None
    assert fwd[5] is None


def test_forward_returns_handles_zero_prices():
    closes = [100.0, 0.0, 105.0]
    fwd = forward_returns(closes, horizon=1)

    # Bar 1 has zero close, so fwd[0] and fwd[1] should be None
    assert fwd[0] is None
    assert fwd[1] is None
    assert fwd[2] is None


def test_rank_ic_perfect_correlation():
    """Factor that perfectly predicts returns should have IC near 1.0."""
    # Factor values increasing -> returns increasing
    factor = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    returns = [0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.10]

    ic = rank_ic(factor, returns)
    assert ic is not None
    assert ic == pytest.approx(1.0, abs=0.01)


def test_rank_ic_perfect_negative_correlation():
    """Factor inversely correlated with returns should have IC near -1.0."""
    factor = [10.0, 9.0, 8.0, 7.0, 6.0, 5.0, 4.0, 3.0, 2.0, 1.0]
    returns = [0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.10]

    ic = rank_ic(factor, returns)
    assert ic is not None
    assert ic == pytest.approx(-1.0, abs=0.01)


def test_rank_ic_no_correlation():
    """Random factor should have IC near zero."""
    # Alternating factor, monotonic returns
    factor = [1.0, 10.0, 2.0, 9.0, 3.0, 8.0, 4.0, 7.0, 5.0, 6.0]
    returns = [0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.10]

    ic = rank_ic(factor, returns)
    assert ic is not None
    assert abs(ic) < 0.3  # Should be close to zero


def test_rank_ic_handles_none_values():
    """Should skip bars where factor or return is None."""
    # Need at least 10 valid pairs
    factor = [1.0, None, 3.0, 4.0, None, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0, 13.0, 14.0]
    returns = [0.01, 0.02, None, 0.04, 0.05, None, 0.07, 0.08, 0.09, 0.10, 0.11, 0.12, 0.13, 0.14]

    ic = rank_ic(factor, returns)
    # Should compute on the 12 valid pairs
    assert ic is not None


def test_rank_ic_insufficient_data():
    """Should return None if <10 valid pairs."""
    factor = [1.0, 2.0, 3.0]
    returns = [0.01, 0.02, 0.03]

    assert rank_ic(factor, returns) is None


def test_hit_rate_perfect():
    """All signs match -> 100% hit rate."""
    factor = [1.0, 2.0, -1.0, -2.0, 3.0]
    returns = [0.01, 0.02, -0.01, -0.02, 0.03]

    hr = hit_rate(factor, returns)
    assert hr == 1.0


def test_hit_rate_zero():
    """All signs opposite -> 0% hit rate."""
    factor = [1.0, 2.0, -1.0, -2.0, 3.0]
    returns = [-0.01, -0.02, 0.01, 0.02, -0.03]

    hr = hit_rate(factor, returns)
    assert hr == 0.0


def test_hit_rate_ignores_zeros():
    """Zero values in factor or return should be skipped."""
    factor = [1.0, 0.0, 2.0]
    returns = [0.01, 0.02, 0.03]

    hr = hit_rate(factor, returns)
    # Only two valid pairs: (1,0.01) and (2,0.03), both match
    assert hr == 1.0


def test_coverage():
    """Coverage should be fraction of non-None values."""
    values = [1.0, None, 3.0, None, 5.0]
    assert coverage(values) == 0.6  # 3/5


def test_coverage_all_none():
    values = [None, None, None]
    assert coverage(values) == 0.0


def test_coverage_all_present():
    values = [1.0, 2.0, 3.0]
    assert coverage(values) == 1.0


def test_ic_decay():
    """IC decay should compute IC at multiple horizons."""
    closes = [100.0 + i * 2 for i in range(60)]  # Steadily rising, longer series
    # Simple trend factor: price relative to 10-bar average
    factor: list[float | None] = []
    for i in range(60):
        if i < 10:
            factor.append(None)
        else:
            avg = sum(closes[i - 10 : i]) / 10
            factor.append(closes[i] / avg - 1.0 if avg > 0 else None)

    decay = ic_decay(factor, closes, horizons=(1, 5, 10))

    assert 1 in decay
    assert 5 in decay
    assert 10 in decay
    # At least one horizon should have valid IC
    has_valid = any(ic is not None for ic in decay.values())
    assert has_valid, f"Expected at least one valid IC, got {decay}"


def test_rank_factors_sorts_by_abs_ic():
    """rank_factors should sort by |IC| descending."""
    series = _series([100 + i for i in range(50)])

    # Create a factor panel with known ICs
    factor_panel = {
        "weak_positive": [float(i % 5) for i in range(50)],  # Weak signal
        "strong_positive": [float(i) for i in range(50)],  # Strong positive
        "strong_negative": [float(-i) for i in range(50)],  # Strong negative
        "noise": [float((i * 7) % 11) for i in range(50)],  # Random-ish
    }

    scores = rank_factors(series, factor_panel, horizon=1)

    # Should have 4 scores
    assert len(scores) == 4

    # First should be one of the strong ones
    assert scores[0].name in ("strong_positive", "strong_negative")
    assert scores[0].rank_ic is not None
    assert abs(scores[0].rank_ic) > 0.5


def test_synthetic_signal_test():
    """CRITICAL: Known factor constructed to predict returns should show strong IC.

    This is the canonical test — if this fails, the IC calculation is wrong.
    """
    # Generate a series where returns are derived from a known factor plus noise
    n = 100
    factor_values = [math.sin(i * 0.1) for i in range(n)]  # Oscillating factor

    # Returns = factor / 10 + small noise
    closes = [100.0]
    for i in range(1, n):
        ret = factor_values[i - 1] * 0.05  # Strong relationship
        closes.append(closes[-1] * (1 + ret))

    fwd = forward_returns(closes, horizon=1)

    ic = rank_ic(factor_values, fwd)

    assert ic is not None
    assert ic > 0.3, f"Synthetic signal should have strong IC, got {ic}"


def test_pure_noise_test():
    """CRITICAL: Random walk should produce near-zero ICs for all factors.

    A ranking engine that finds edge in noise is broken.
    """
    # Fixed-seed random walk
    import random

    random.seed(42)

    closes = [100.0]
    for _ in range(100):
        closes.append(closes[-1] * (1 + random.gauss(0, 0.02)))

    # Generate random factors
    factor_panel = {f"noise_factor_{i}": [random.gauss(0, 1) for _ in range(100)] for i in range(5)}

    series = _series(closes)
    scores = rank_factors(series, factor_panel, horizon=5)

    # All ICs should be small
    for score in scores:
        if score.rank_ic is not None:
            assert abs(score.rank_ic) < 0.4, (
                f"Factor {score.name} has suspiciously high IC {score.rank_ic} on pure noise"
            )


def test_lookahead_pin():
    """CRITICAL: factor[t] must equal factor[t] computed on truncated series.

    This is the no-lookahead guarantee at the factor level.
    """
    # We'll test this when we implement compute_factor_panel
    # For now, verify forward_returns has the property
    closes = [100.0, 105.0, 103.0, 108.0, 110.0]

    # Full series forward returns
    fwd_full = forward_returns(closes, horizon=1)

    # Truncated series
    closes_trunc = closes[:3]
    fwd_trunc = forward_returns(closes_trunc, horizon=1)

    # First two entries should match exactly
    assert fwd_full[0] == fwd_trunc[0]
    assert fwd_full[1] == fwd_trunc[1]
