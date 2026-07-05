"""Tests for the new analyzers: RSI, Bollinger Bands, Volume, and Indian Equity.

Each analyzer is a pure function from PriceSeries to SignalSource. These tests
verify determinism, boundary conditions, and expected behavior on crafted inputs.
No network, no LLM, no randomness.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


from alpha_engine.analyzers.rsi import _rsi, analyze_rsi
from alpha_engine.analyzers.bollinger import _bollinger_bands, _pct_b, analyze_bollinger
from alpha_engine.analyzers.volume import _obv, analyze_volume
from alpha_engine.analyzers.indian_equity import analyze_indian_equity
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
    lower, middle, upper = _bollinger_bands(closes, period=20, num_std=2.0)
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
    assert obv[2] == 50.0   # down -> -vol
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
