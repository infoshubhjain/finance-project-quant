"""Tests for the new analyzers: RSI, Bollinger Bands, Volume, and Indian Equity.

Each analyzer is a pure function from PriceSeries to SignalSource. These tests
verify determinism, boundary conditions, and expected behavior on crafted inputs.
No network, no LLM, no randomness.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

from alpha_engine.analyzers.bollinger import _bollinger_bands, _pct_b, analyze_bollinger
from alpha_engine.analyzers.indian_equity import analyze_indian_equity
from alpha_engine.analyzers.macd import analyze_macd
from alpha_engine.analyzers.multi_timeframe import analyze_multi_timeframe
from alpha_engine.analyzers.rsi import _rsi, analyze_rsi
from alpha_engine.analyzers.support_resistance import analyze_support_resistance
from alpha_engine.analyzers.volatility import (
    analyze_volatility,
    classify_regime,
    volatility_scalar,
)
from alpha_engine.analyzers.volume import _obv, analyze_volume
from alpha_engine.analyzers.vwap import analyze_vwap
from alpha_engine.cache.models import Candle, Interval, PriceSeries
from alpha_engine.schema.signal import Direction

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _series(
    closes: list[float],
    volumes: list[float] | None = None,
    asset: str = "BTC",
) -> PriceSeries:
    candles = []
    for i, c in enumerate(closes):
        vol = volumes[i] if volumes and i < len(volumes) else 1000.0
        candles.append(
            Candle(
                ts=T0 + timedelta(days=i),
                open=c,
                high=c * 1.01,
                low=c * 0.99,
                close=c,
                volume=vol,
            )
        )
    return PriceSeries(asset=asset, interval=Interval.DAY, candles=candles)


# --- RSI tests ----------------------------------------------------------------


def test_rsi_returns_none_for_insufficient_data():
    closes = [100.0] * 10
    assert _rsi(closes, period=14) is None


def test_rsi_identical_prices_returns_50():
    closes = [100.0] * 20
    rsi = _rsi(closes, period=14)
    assert rsi == 50.0  # no movement = neutral RSI


def test_rsi_strong_uptrend_high():
    closes = [100.0 + i * 2 for i in range(20)]
    rsi = _rsi(closes, period=14)
    assert rsi is not None
    assert rsi > 70  # should be overbought


def test_rsi_strong_downtrend_low():
    closes = [200.0 - i * 2 for i in range(20)]
    rsi = _rsi(closes, period=14)
    assert rsi is not None
    assert rsi < 30  # should be oversold


def test_rsi_is_deterministic():
    closes = [100.0 + ((i * 7) % 13) - 6 for i in range(30)]
    a = _rsi(closes, period=14)
    b = _rsi(closes, period=14)
    assert a == b


def test_analyze_rsi_oversold_is_bullish():
    # Strong downtrend to trigger oversold
    closes = [100.0 - i * 3 for i in range(20)]
    src = analyze_rsi(_series(closes))
    assert src.direction is Direction.BULLISH
    assert src.weight > 0
    assert "oversold" in src.detail


def test_analyze_rsi_overbought_is_bearish():
    # Strong uptrend to trigger overbought
    closes = [50.0 + i * 3 for i in range(20)]
    src = analyze_rsi(_series(closes))
    assert src.direction is Direction.BEARISH
    assert src.weight > 0
    assert "overbought" in src.detail


def test_analyze_rsi_neutral_zone():
    # Sideways market, RSI stays in middle
    closes = [100.0 + (i % 3) * 0.1 for i in range(20)]
    src = analyze_rsi(_series(closes))
    assert src.name == "rsi"
    assert "neutral" in src.detail


def test_analyze_rsi_insufficient_data():
    closes = [100.0, 101.0, 102.0]
    src = analyze_rsi(_series(closes))
    assert src.direction is Direction.NEUTRAL
    assert src.weight == 0.0


def test_analyze_rsi_is_deterministic():
    closes = [100.0 + ((i * 7) % 13) - 6 for i in range(30)]
    a = analyze_rsi(_series(closes))
    b = analyze_rsi(_series(closes))
    assert a.model_dump() == b.model_dump()


# --- Bollinger Bands tests ----------------------------------------------------


def test_bollinger_returns_none_for_insufficient_data():
    closes = [100.0] * 5
    assert _bollinger_bands(closes, period=20) is None


def test_bollinger_bands_calculation():
    closes = [100.0 + i for i in range(20)]
    result = _bollinger_bands(closes, period=20, num_std=2.0)
    assert result is not None
    lower, middle, upper = result
    assert middle == sum(closes[-20:]) / 20
    assert lower < middle < upper


def test_pct_b_at_middle():
    assert _pct_b(100.0, 90.0, 110.0) == 0.5


def test_pct_b_below_lower():
    assert _pct_b(80.0, 90.0, 110.0) < 0.0


def test_pct_b_above_upper():
    assert _pct_b(120.0, 90.0, 110.0) > 1.0


def test_analyze_bollinger_below_lower_is_bullish():
    # Create a series where price drops well below the bands
    closes = [100.0] * 18 + [100.0, 80.0]  # sudden drop
    src = analyze_bollinger(_series(closes))
    assert src.name == "bollinger"
    # Price is way below the 20-SMA -> should be bullish (oversold)
    assert src.direction is Direction.BULLISH


def test_analyze_bollinger_above_upper_is_bearish():
    # Create a series where price spikes above the bands
    closes = [100.0] * 18 + [100.0, 130.0]  # sudden spike
    src = analyze_bollinger(_series(closes))
    assert src.direction is Direction.BEARISH


def test_analyze_bollinger_insufficient_data():
    closes = [100.0, 101.0, 102.0]
    src = analyze_bollinger(_series(closes))
    assert src.direction is Direction.NEUTRAL
    assert src.weight == 0.0


def test_analyze_bollinger_is_deterministic():
    closes = [100.0 + ((i * 3) % 7) - 3 for i in range(25)]
    a = analyze_bollinger(_series(closes))
    b = analyze_bollinger(_series(closes))
    assert a.model_dump() == b.model_dump()


# --- Volume tests --------------------------------------------------------------


def test_obv_basic():
    closes = [100.0, 101.0, 99.0, 102.0]
    volumes = [100.0, 200.0, 150.0, 300.0]
    obv = _obv(closes, volumes)
    assert len(obv) == 4
    assert obv[0] == 0.0
    assert obv[1] == 200.0  # up -> +vol
    assert obv[2] == 50.0  # down -> -vol
    assert obv[3] == 350.0  # up -> +vol


def test_obv_insufficient_data():
    assert _obv([100.0], [1000.0]) == []


def test_analyze_volume_bullish_uptrend():
    # Rising prices with rising volume
    closes = [100.0 + i * 2 for i in range(25)]
    volumes = [1000.0 + i * 50 for i in range(25)]
    src = analyze_volume(_series(closes, volumes))
    assert src.name == "volume"
    # OBV confirms uptrend
    assert src.direction is Direction.BULLISH


def test_analyze_volume_bearish_downtrend():
    # Falling prices with rising volume
    closes = [200.0 - i * 2 for i in range(25)]
    volumes = [1000.0 + i * 50 for i in range(25)]
    src = analyze_volume(_series(closes, volumes))
    assert src.direction is Direction.BEARISH


def test_analyze_volume_no_volume_data():
    # All volumes are 0 or None
    closes = [100.0 + i for i in range(25)]
    series = PriceSeries(
        asset="BTC",
        interval=Interval.DAY,
        candles=[
            Candle(ts=T0 + timedelta(days=i), open=c, high=c, low=c, close=c, volume=0)
            for i, c in enumerate(closes)
        ],
    )
    src = analyze_volume(series)
    assert src.direction is Direction.NEUTRAL
    assert src.weight == 0.0


def test_analyze_volume_is_deterministic():
    closes = [100.0 + ((i * 5) % 11) - 5 for i in range(30)]
    volumes = [1000.0 + ((i * 3) % 7) * 100 for i in range(30)]
    a = analyze_volume(_series(closes, volumes))
    b = analyze_volume(_series(closes, volumes))
    assert a.model_dump() == b.model_dump()


# --- Indian Equity tests -------------------------------------------------------


def test_analyze_indian_equity_uptrend():
    closes = [100.0 + i * 2 for i in range(40)]
    src = analyze_indian_equity(_series(closes, asset="RELIANCE"))
    assert src.name == "in_equity.trend"
    assert src.direction is Direction.BULLISH
    assert src.weight > 0


def test_analyze_indian_equity_downtrend():
    closes = [200.0 - i * 2 for i in range(40)]
    src = analyze_indian_equity(_series(closes, asset="RELIANCE"))
    assert src.direction is Direction.BEARISH


def test_analyze_indian_equity_insufficient_data():
    closes = [100.0, 101.0, 102.0]
    src = analyze_indian_equity(_series(closes))
    assert src.direction is Direction.NEUTRAL
    assert src.weight == 0.0


def test_analyze_indian_equity_includes_gap_info():
    # Create gaps between candles
    candles = []
    for i in range(40):
        open_price = 100.0 + i * 2 + (5.0 if i % 5 == 0 else 0.0)
        close = open_price + 1.0
        candles.append(
            Candle(
                ts=T0 + timedelta(days=i),
                open=open_price,
                high=close + 1,
                low=open_price - 1,
                close=close,
                volume=1000.0,
            )
        )
    series = PriceSeries(asset="RELIANCE", interval=Interval.DAY, candles=candles)
    src = analyze_indian_equity(series)
    assert "avg_gap" in src.detail


def test_analyze_indian_equity_is_deterministic():
    closes = [100.0 + ((i * 7) % 13) - 6 for i in range(40)]
    a = analyze_indian_equity(_series(closes))
    b = analyze_indian_equity(_series(closes))
    assert a.model_dump() == b.model_dump()


# --- helpers for the phase-7 analyzers ------------------------------------------


def _ohlc_series(
    bars: list[tuple[float, float, float, float]],
    volumes: Sequence[float | None] | None = None,
    asset: str = "BTC",
) -> PriceSeries:
    """Series with explicit (open, high, low, close) per bar, so tests can
    craft bounce/rejection candles and controlled ranges."""
    candles = []
    for i, (o, h, low, c) in enumerate(bars):
        vol = volumes[i] if volumes is not None else 1000.0
        candles.append(
            Candle(ts=T0 + timedelta(days=i), open=o, high=h, low=low, close=c, volume=vol)
        )
    return PriceSeries(asset=asset, interval=Interval.DAY, candles=candles)


def _flat_bar(c: float) -> tuple[float, float, float, float]:
    return (c, c + 1.0, c - 1.0, c)


# --- support/resistance ----------------------------------------------------------


def test_support_resistance_insufficient_history():
    src = analyze_support_resistance(_series([100.0] * 5))
    assert src.direction == Direction.NEUTRAL
    assert src.weight == 0.0


def test_support_resistance_bounce_is_bullish():
    # Two swing lows at low=99 form a support; the last bar closes up near it.
    closes = [105, 102, 100, 102, 105, 102, 100, 102, 105, 102, 100]
    bars = [_flat_bar(float(c)) for c in closes]
    bars.append((100.0, 101.5, 99.8, 100.5))  # closed up, within 2% of the level
    src = analyze_support_resistance(_ohlc_series(bars))
    assert src.direction == Direction.BULLISH
    assert src.weight > 0
    assert "support" in src.detail


def test_support_resistance_rejection_is_bearish():
    # Two swing highs at high=111 form a resistance; last bar closes down at it.
    closes = [105, 108, 110, 108, 105, 108, 110, 108, 105, 108, 110]
    bars = [_flat_bar(float(c)) for c in closes]
    bars.append((110.5, 111.2, 109.5, 110.0))  # closed down just under the level
    src = analyze_support_resistance(_ohlc_series(bars))
    assert src.direction == Direction.BEARISH
    assert src.weight > 0
    assert "resistance" in src.detail


def test_support_resistance_mid_range_is_neutral():
    closes = [105, 102, 100, 102, 105, 102, 100, 102, 105, 102, 100, 105]
    bars = [_flat_bar(float(c)) for c in closes]
    src = analyze_support_resistance(_ohlc_series(bars))
    assert src.direction == Direction.NEUTRAL
    assert src.weight == 0.0


def test_support_resistance_deterministic():
    closes = [100.0 + ((i * 11) % 17) for i in range(60)]
    a = analyze_support_resistance(_series(closes))
    b = analyze_support_resistance(_series(closes))
    assert a.model_dump() == b.model_dump()


# --- MACD --------------------------------------------------------------------


def test_macd_insufficient_history():
    src = analyze_macd(_series([100.0] * 20))
    assert src.weight == 0.0
    assert "insufficient" in src.detail


def test_macd_fresh_bullish_crossover():
    # Accelerating decline drives the histogram negative; three sharp recovery
    # bars flip it positive on the last bar (fixture pinned numerically:
    # hist -0.1152 -> +1.2592).
    closes = [200.0 - 0.05 * i * i for i in range(40)]
    closes += [closes[-1] + 8.0 * j for j in range(1, 4)]
    src = analyze_macd(_series(closes))
    assert src.direction == Direction.BULLISH
    assert "fresh crossover" in src.detail


def test_macd_fresh_bearish_crossover():
    # Steady geometric climb keeps the histogram positive; one 10% drop flips
    # it negative (hist +0.2484 -> -0.7783).
    closes = [100.0 * 1.01**i for i in range(40)]
    closes.append(closes[-1] * 0.90)
    src = analyze_macd(_series(closes))
    assert src.direction == Direction.BEARISH
    assert "fresh crossover" in src.detail


def test_macd_accelerating_uptrend_is_bullish():
    closes = [100.0 * 1.02**i for i in range(60)]
    src = analyze_macd(_series(closes))
    assert src.direction == Direction.BULLISH
    assert src.weight > 0


def test_macd_deterministic():
    closes = [100.0 + ((i * 5) % 23) for i in range(80)]
    a = analyze_macd(_series(closes))
    b = analyze_macd(_series(closes))
    assert a.model_dump() == b.model_dump()


# --- VWAP --------------------------------------------------------------------


def test_vwap_no_volume_degrades():
    bars = [_flat_bar(100.0 + i) for i in range(30)]
    src = analyze_vwap(_ohlc_series(bars, volumes=[None] * 30))
    assert src.weight == 0.0
    assert "no usable volume" in src.detail


def test_vwap_zero_volume_degrades():
    bars = [_flat_bar(100.0 + i) for i in range(30)]
    src = analyze_vwap(_ohlc_series(bars, volumes=[0.0] * 30))
    assert src.weight == 0.0


def test_vwap_price_above_is_bullish():
    closes = [100.0 + i * 0.5 for i in range(30)]
    src = analyze_vwap(_series(closes))
    assert src.direction == Direction.BULLISH
    assert src.weight > 0


def test_vwap_price_below_is_bearish():
    closes = [130.0 - i * 0.5 for i in range(30)]
    src = analyze_vwap(_series(closes))
    assert src.direction == Direction.BEARISH
    assert src.weight > 0


def test_vwap_deterministic():
    closes = [100.0 + ((i * 3) % 11) for i in range(40)]
    a = analyze_vwap(_series(closes))
    b = analyze_vwap(_series(closes))
    assert a.model_dump() == b.model_dump()


# --- multi-timeframe -----------------------------------------------------------


def test_multi_timeframe_insufficient_history():
    src = analyze_multi_timeframe(_series([100.0] * 15))
    assert src.weight == 0.0
    assert "insufficient" in src.detail


def test_multi_timeframe_full_alignment_bullish():
    closes = [100.0 * 1.005**i for i in range(100)]
    src = analyze_multi_timeframe(_series(closes))
    assert src.direction == Direction.BULLISH
    assert src.weight == 0.7  # 3/3 horizons agree -> full alignment cap
    assert "3/3" in src.detail


def test_multi_timeframe_full_alignment_bearish():
    closes = [200.0 * 0.995**i for i in range(100)]
    src = analyze_multi_timeframe(_series(closes))
    assert src.direction == Direction.BEARISH
    assert src.weight == 0.7


def test_multi_timeframe_flat_is_neutral():
    src = analyze_multi_timeframe(_series([100.0] * 100))
    assert src.direction == Direction.NEUTRAL
    assert src.weight == 0.0


def test_multi_timeframe_deterministic():
    closes = [100.0 + ((i * 13) % 29) for i in range(100)]
    a = analyze_multi_timeframe(_series(closes))
    b = analyze_multi_timeframe(_series(closes))
    assert a.model_dump() == b.model_dump()


# --- volatility regime -----------------------------------------------------------


def test_volatility_insufficient_history():
    src = analyze_volatility(_series([100.0] * 20))
    assert src.weight == 0.0
    assert volatility_scalar(_series([100.0] * 20)) == 1.0


def test_volatility_classify_regime_boundaries():
    assert classify_regime(0.5) == "low"
    assert classify_regime(0.75) == "low"
    assert classify_regime(1.0) == "normal"
    assert classify_regime(1.5) == "high"
    assert classify_regime(2.5) == "extreme"
    assert classify_regime(4.0) == "extreme"


def test_volatility_extreme_regime_dampens():
    # 50 quiet bars (range 1) then 14 wild bars (range 30): current ATR far
    # above the baseline average -> extreme -> scalar 0.6.
    bars = [(100.0, 100.5, 99.5, 100.0)] * 50 + [(100.0, 115.0, 85.0, 100.0)] * 14
    series = _ohlc_series(bars)
    src = analyze_volatility(series)
    assert "extreme" in src.detail
    assert src.direction == Direction.NEUTRAL
    assert volatility_scalar(series) == 0.6


def test_volatility_low_regime_scalar_is_one():
    # Wild history, quiet present -> low regime, but no dampening.
    bars = [(100.0, 110.0, 90.0, 100.0)] * 50 + [(100.0, 100.5, 99.5, 100.0)] * 14
    series = _ohlc_series(bars)
    src = analyze_volatility(series)
    assert "low" in src.detail
    assert volatility_scalar(series) == 1.0


def test_volatility_normal_regime():
    bars = [(100.0, 101.0, 99.0, 100.0)] * 70
    series = _ohlc_series(bars)
    src = analyze_volatility(series)
    assert "normal" in src.detail
    assert volatility_scalar(series) == 1.0


def test_volatility_deterministic():
    closes = [100.0 + ((i * 7) % 19) for i in range(80)]
    a = analyze_volatility(_series(closes))
    b = analyze_volatility(_series(closes))
    assert a.model_dump() == b.model_dump()
