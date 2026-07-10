"""Tests for the quant layer: feature table, models, and the scored report.

Everything runs on synthetic candles built from closed-form math (or a seeded
random walk), so the tests are network-free and the expected behavior is
knowable in advance: an uptrend fixture must score bullish, a downtrend
bearish, and every function must be deterministic call-to-call.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone

import pytest

from alpha_engine.cache.models import Candle, Interval, PriceSeries
from alpha_engine.quant.features import (
    FEATURE_GROUPS,
    INTRADAY_FACTORS,
    compute_features,
    hurst_exponent,
    max_drawdown,
)
from alpha_engine.quant.models import (
    fit_garch,
    fit_hmm,
    kalman_fair_value,
    rolling_trend_strength,
)
from alpha_engine.quant.report import (
    adx,
    build_report,
    render_text,
    volume_profile,
)

_T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _series(closes: list[float], volumes: list[float] | None = None) -> PriceSeries:
    """Wrap a close path in plausible OHLCV candles."""
    candles = []
    prev = closes[0]
    for i, c in enumerate(closes):
        o = prev
        hi = max(o, c) * 1.004
        lo = min(o, c) * 0.996
        vol = volumes[i] if volumes is not None else 1000.0 + 3.0 * i
        candles.append(
            Candle(ts=_T0 + timedelta(days=i), open=o, high=hi, low=lo, close=c, volume=vol)
        )
        prev = c
    return PriceSeries(asset="TEST", interval=Interval.DAY, candles=candles)


def _trend_closes(n: int = 150, drift: float = 0.004) -> list[float]:
    return [100.0 * math.exp(drift * i) + 0.6 * math.sin(i * 0.7) for i in range(n)]


def _range_closes(n: int = 150) -> list[float]:
    return [100.0 + 4.0 * math.sin(i / 4.0) for i in range(n)]


@pytest.fixture()
def up_series() -> PriceSeries:
    closes = _trend_closes()
    # volume expands on up days: participation confirms the trend
    vols = []
    prev = closes[0]
    for c in closes:
        vols.append(1500.0 if c > prev else 900.0)
        prev = c
    return _series(closes, vols)


@pytest.fixture()
def down_series() -> PriceSeries:
    return _series([100.0 * math.exp(-0.004 * i) + 0.6 * math.sin(i * 0.7) for i in range(150)])


@pytest.fixture()
def flat_series() -> PriceSeries:
    return _series(_range_closes())


# ---------------------------------------------------------------------------
# features
# ---------------------------------------------------------------------------


def test_all_fifty_features_present(up_series: PriceSeries) -> None:
    f = compute_features(up_series)
    expected = {k for keys in FEATURE_GROUPS.values() for k in keys}
    assert set(f) == expected
    # 50 requested + the vwap_slope intraday extra
    assert len(expected) == 51


def test_features_deterministic(up_series: PriceSeries) -> None:
    assert compute_features(up_series) == compute_features(up_series)


def test_trend_features_read_the_uptrend(up_series: PriceSeries) -> None:
    f = compute_features(up_series)
    assert f["lr_slope"] is not None and f["lr_slope"] > 0
    assert f["lr_r2"] is not None and f["lr_r2"] > 0.5
    assert f["cum_return_20d"] is not None and f["cum_return_20d"] > 0
    assert f["trend_persistence"] is not None and f["trend_persistence"] > 0.5
    assert f["dist_ema50"] is not None and f["dist_ema50"] > 0


def test_range_features_read_the_chop(flat_series: PriceSeries) -> None:
    f = compute_features(flat_series)
    assert f["kaufman_er"] is not None and f["kaufman_er"] < 0.6
    # a bounded oscillation must show pull-back-to-mean with a finite half-life
    assert f["mr_half_life"] is not None and f["mr_half_life"] > 0


def test_bounded_features_stay_in_bounds(up_series: PriceSeries, flat_series: PriceSeries) -> None:
    for series in (up_series, flat_series):
        f = compute_features(series)
        for key in (
            "trend_persistence",
            "kaufman_er",
            "vol_percentile",
            "price_percentile",
            "entropy",
            "hurst",
            "body_pct",
            "upper_wick_pct",
            "lower_wick_pct",
        ):
            v = f[key]
            assert v is None or 0.0 <= v <= 1.0, f"{key}={v}"
        assert f["clv"] is None or -1.0 <= f["clv"] <= 1.0
        assert f["max_drawdown"] is None or f["max_drawdown"] <= 0.0


def test_missing_volume_yields_none_not_zero() -> None:
    closes = _trend_closes()
    candles = [
        Candle(ts=_T0 + timedelta(days=i), open=c, high=c * 1.01, low=c * 0.99, close=c)
        for i, c in enumerate(closes)
    ]
    series = PriceSeries(asset="NOVOL", interval=Interval.DAY, candles=candles)
    f = compute_features(series)
    for key in ("dist_vwap", "relative_volume", "volume_z", "obv_slope", "price_volume_corr"):
        assert f[key] is None


def test_intraday_factor_names_are_a_subset_of_the_table() -> None:
    table = {k for keys in FEATURE_GROUPS.values() for k in keys}
    assert set(INTRADAY_FACTORS) <= table


def test_hurst_near_half_on_seeded_random_walk() -> None:
    rng = random.Random(42)  # seeded: deterministic test, no randomness in prod code
    closes = [100.0]
    for _ in range(400):
        closes.append(closes[-1] * math.exp(rng.gauss(0.0, 0.01)))
    h = hurst_exponent(closes)
    assert h is not None and 0.3 < h < 0.7


def test_max_drawdown_matches_hand_calc() -> None:
    assert max_drawdown([100.0, 120.0, 90.0, 110.0]) == pytest.approx(90.0 / 120.0 - 1.0)


def test_dist_median_uses_true_median_on_even_window() -> None:
    # 20-bar window of 10 lows and 10 highs: the median must sit between the
    # halves (150), not on the upper half (200) as a naive [n//2] pick would
    closes = [100.0] * 100 + [100.0] * 10 + [200.0] * 10
    f = compute_features(_series(closes))
    assert f["dist_median"] == pytest.approx(200.0 / 150.0 - 1.0)


def test_corr_refuses_misaligned_returns() -> None:
    # one non-positive close drops a return; volumes would pair with the
    # wrong bars, so the correlation must be None rather than silently wrong
    closes = _trend_closes()
    closes[50] = 0.0
    f = compute_features(_series(closes, [1000.0] * len(closes)))
    assert f["price_volume_corr"] is None


def test_report_refuses_non_positive_closes() -> None:
    closes = _trend_closes()
    closes[50] = 0.0
    with pytest.raises(ValueError, match="non-positive"):
        build_report(_series(closes), market="crypto")


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------


def test_kalman_tracks_the_trend(up_series: PriceSeries) -> None:
    closes = up_series.closes()
    res = kalman_fair_value(closes)
    assert res is not None
    assert abs(res.distance) < 0.05  # fair value hugs price on clean data
    assert res.slope > 0


def test_garch_fit_is_valid_and_deterministic(up_series: PriceSeries) -> None:
    from alpha_engine.quant.features import log_returns

    rets = log_returns(up_series.closes())
    a = fit_garch(rets)
    b = fit_garch(rets)
    assert a is not None and a == b
    assert 0.0 < a.alpha < 1.0 and 0.0 < a.beta < 1.0
    assert a.alpha + a.beta < 1.0
    assert a.forecast_vol_daily > 0.0


def test_hmm_flags_the_current_block() -> None:
    # 60 bars drifting up then 60 drifting down: latest state must read bearish
    up_block = [0.01 + 0.002 * math.sin(i) for i in range(60)]
    down_block = [-0.01 + 0.002 * math.sin(i) for i in range(60)]
    res_bear = fit_hmm(up_block + down_block)
    res_bull = fit_hmm(down_block + up_block)
    assert res_bear is not None and res_bear.bull_prob < 0.4
    assert res_bull is not None and res_bull.bull_prob > 0.6
    assert res_bull.bull_mean > res_bull.bear_mean


def test_rolling_trend_strength_signs(up_series: PriceSeries, down_series: PriceSeries) -> None:
    up = rolling_trend_strength(up_series.closes())
    down = rolling_trend_strength(down_series.closes())
    assert up is not None and up.slope > 0 and up.stability == 1.0
    assert down is not None and down.slope < 0
    assert 0.0 <= up.r2 <= 1.0


# ---------------------------------------------------------------------------
# indicators
# ---------------------------------------------------------------------------


def test_adx_higher_in_trend_than_range(up_series: PriceSeries, flat_series: PriceSeries) -> None:
    a_trend = adx(up_series)
    a_flat = adx(flat_series)
    assert a_trend is not None and a_flat is not None
    assert 0.0 <= a_flat <= 100.0 and 0.0 <= a_trend <= 100.0
    assert a_trend > a_flat


def test_volume_profile_poc_within_price_range(up_series: PriceSeries) -> None:
    prof = volume_profile(up_series)
    assert prof is not None
    closes = up_series.closes()[-60:]
    poc = prof["poc"]
    assert isinstance(poc, float)
    assert min(closes) * 0.99 <= poc <= max(closes) * 1.01
    assert isinstance(prof["levels"], list) and len(prof["levels"]) == 3


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


def test_report_reads_bullish_on_uptrend(up_series: PriceSeries) -> None:
    r = build_report(up_series, market="crypto")
    assert "Bull" in r.regime_label
    assert r.trend_score is not None and r.trend_score >= 55
    assert r.momentum_score is not None and r.momentum_score >= 50
    for score in (r.trend_score, r.momentum_score, r.volume_score, r.overall_score):
        assert score is None or 0 <= score <= 100
    assert r.forecast_vol_daily is not None and r.forecast_vol_daily > 0
    assert r.disclaimer  # the honesty line must survive any refactor


def test_report_reads_bearish_on_downtrend(down_series: PriceSeries) -> None:
    r = build_report(down_series, market="crypto")
    assert "Bear" in r.regime_label
    assert r.trend_score is not None and r.trend_score <= 45


def test_report_is_deterministic(up_series: PriceSeries) -> None:
    a = build_report(up_series, market="crypto")
    b = build_report(up_series, market="crypto")
    assert a.model_dump() == b.model_dump()


def test_report_needs_sixty_bars() -> None:
    short = _series(_trend_closes(40))
    with pytest.raises(ValueError, match="60"):
        build_report(short, market="crypto")


def test_render_text_contains_the_headline_metrics(up_series: PriceSeries) -> None:
    text = render_text(build_report(up_series, market="crypto"))
    for token in (
        "Regime",
        "Trend Score",
        "Momentum Score",
        "Forecast Volatility",
        "Volatility Percentile",
        "Fair Value (Kalman)",
        "Overall Asset Score",
        "RSI 14",
        "ADX 14",
        "not investment advice",
    ):
        assert token in text, f"missing {token!r} in rendered report"
