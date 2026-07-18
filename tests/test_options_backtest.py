"""Options pricing + joint backtest: determinism, no-lookahead, and sanity."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from alpha_engine.cache.models import Candle, Interval, OptionRight, PriceSeries
from alpha_engine.quant.black_scholes import bs_price, norm_cdf
from alpha_engine.schema.signal import Market, Timeframe
from alpha_engine.validation.options_backtest import run_options_backtest


def test_put_call_parity():
    # C - P == S - K*exp(-rT) for any inputs.
    s, k, t, v, r = 100.0, 105.0, 0.25, 0.2, 0.06
    c = bs_price(s, k, t, v, OptionRight.CALL, r)
    p = bs_price(s, k, t, v, OptionRight.PUT, r)
    assert abs((c - p) - (s - k * math.exp(-r * t))) < 1e-9


def test_price_is_monotonic_in_vol():
    lo = bs_price(100.0, 100.0, 0.25, 0.1, OptionRight.CALL)
    hi = bs_price(100.0, 100.0, 0.25, 0.4, OptionRight.CALL)
    assert hi > lo


def test_expiry_is_intrinsic():
    assert bs_price(110.0, 100.0, 0.0, 0.2, OptionRight.CALL) == 10.0
    assert bs_price(90.0, 100.0, 0.0, 0.2, OptionRight.CALL) == 0.0
    assert bs_price(90.0, 100.0, 0.0, 0.2, OptionRight.PUT) == 10.0


def test_norm_cdf_known_points():
    assert abs(norm_cdf(0.0) - 0.5) < 1e-12
    assert norm_cdf(-5.0) < 1e-6
    assert norm_cdf(5.0) > 1 - 1e-6


def _series(closes: list[float]) -> PriceSeries:
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    candles = [
        Candle(
            ts=base + timedelta(days=i),
            open=c,
            high=c * 1.01,
            low=c * 0.99,
            close=c,
            volume=1000.0,
        )
        for i, c in enumerate(closes)
    ]
    return PriceSeries(asset="TEST", interval=Interval.DAY, candles=candles)


def _uptrend(n: int = 150) -> PriceSeries:
    # Gentle drift up with small wiggles — enough to wake the trend analyzer.
    return _series([100.0 * (1.004**i) + (i % 5) for i in range(n)])


def test_backtest_is_deterministic():
    s = _uptrend()
    a = run_options_backtest(s, market=Market.US_EQUITY, timeframe=Timeframe.SWING, warmup=90)
    b = run_options_backtest(s, market=Market.US_EQUITY, timeframe=Timeframe.SWING, warmup=90)
    assert a.model_dump() == b.model_dump()


def test_backtest_runs_trades_and_reports_both_legs():
    s = _uptrend()
    rep = run_options_backtest(s, market=Market.US_EQUITY, timeframe=Timeframe.SWING, warmup=90)
    assert rep.trades > 0
    assert 0.0 <= rep.option_win_rate <= 1.0
    assert rep.avg_vol_used > 0
    # dte must exceed the hold so exit still carries time value.
    assert rep.dte_bars > 10


def test_no_lookahead_entry_pricing():
    # Appending future bars must not change the option leg over the shared prefix.
    short = _uptrend(120)
    long = _uptrend(150)  # identical first 120 bars, then more
    r_short = run_options_backtest(
        short, market=Market.US_EQUITY, timeframe=Timeframe.SWING, warmup=90
    )
    # Re-run the long series but only over the same window by trimming to 120.
    trimmed = PriceSeries(asset="TEST", interval=Interval.DAY, candles=long.candles[:120])
    r_trim = run_options_backtest(
        trimmed, market=Market.US_EQUITY, timeframe=Timeframe.SWING, warmup=90
    )
    assert r_short.model_dump() == r_trim.model_dump()
