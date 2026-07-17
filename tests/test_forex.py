"""Tests for the forex market path: the forex_trend analyzer, market
auto-detection for currency pairs, and the forex backtest anchor.

Key properties:
- Currency pairs auto-detect as FOREX without stealing equity tickers.
- The analyzer is deterministic and blends trend with mean-reversion the
  documented way (agree -> add, conflict -> fade, neutral -> reversion).
- The full pipeline scans and backtests a forex series end to end.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from alpha_engine.analyzers.forex_trend import _zscore, analyze_forex_trend
from alpha_engine.cache.models import Candle, Interval, PriceSeries
from alpha_engine.cli.main import detect_market
from alpha_engine.schema.signal import Direction, Market
from alpha_engine.validation.backtest import run_backtest, signal_at

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _series(closes: list[float], asset: str = "EURUSD") -> PriceSeries:
    candles = [
        Candle(
            ts=T0 + timedelta(days=i),
            open=c,
            high=c * 1.002,
            low=c * 0.998,
            close=c,
            volume=None,  # forex often has no real volume; the path must cope
        )
        for i, c in enumerate(closes)
    ]
    return PriceSeries(asset=asset, interval=Interval.DAY, candles=candles)


# --- market detection --------------------------------------------------------------


def test_detect_market_forex_pairs():
    assert detect_market("EURUSD") is Market.FOREX
    assert detect_market("USDJPY") is Market.FOREX
    assert detect_market("GBP/USD") is Market.FOREX


def test_detect_market_does_not_steal_other_symbols():
    assert detect_market("BTC") is Market.CRYPTO
    assert detect_market("NIFTY") is Market.IN_FNO
    assert detect_market("AAPL") is Market.US_EQUITY
    assert detect_market("RELIANCE.NS") is Market.IN_EQUITY
    # six letters but not two currency codes -> still an equity ticker
    assert detect_market("GOOGLE") is Market.US_EQUITY


def test_detect_market_override_wins():
    assert detect_market("EURUSD", override="us_equity") is Market.US_EQUITY


# --- z-score helper ------------------------------------------------------------------


def test_zscore_insufficient_data():
    assert _zscore([1.0] * 10, window=20) is None


def test_zscore_flat_series_is_zero():
    assert _zscore([1.1] * 25, window=20) == 0.0


def test_zscore_sign_follows_stretch():
    up = [1.0] * 24 + [1.5]
    down = [1.0] * 24 + [0.5]
    z_up = _zscore(up, window=20)
    z_down = _zscore(down, window=20)
    assert z_up is not None and z_up > 0
    assert z_down is not None and z_down < 0


# --- analyzer behavior ---------------------------------------------------------------


def test_forex_trend_neutral_range_with_stretch_votes_reversion():
    # Dead-flat range, then a violent 3%+ spike: trend core stays neutral-ish
    # while z-score screams stretched -> the reversion vote fades the spike.
    closes = [1.10 + 0.0002 * ((i * 3) % 5) for i in range(60)] + [1.16]
    src = analyze_forex_trend(_series(closes))
    assert src.name == "forex.trend"
    if "[reversion vote]" in src.detail:
        assert src.direction is Direction.BEARISH
    else:  # trend core may read the spike as trend; then reversion fades it
        assert "[reversion fading trend]" in src.detail


def test_forex_trend_steady_uptrend_is_bullish():
    closes = [1.05 * (1.001**i) for i in range(80)]
    src = analyze_forex_trend(_series(closes))
    assert src.direction is Direction.BULLISH
    assert src.weight > 0


def test_forex_trend_deterministic():
    closes = [1.10 + ((i * 7) % 13) * 0.001 for i in range(80)]
    a = analyze_forex_trend(_series(closes))
    b = analyze_forex_trend(_series(closes))
    assert a.model_dump() == b.model_dump()


# --- end-to-end forex pipeline ---------------------------------------------------------


def test_forex_backtest_runs_without_volume():
    closes = [1.05 * (1.0008**i) for i in range(140)]
    report = run_backtest(_series(closes), market=Market.FOREX)
    assert report.market is Market.FOREX
    assert report.signals_generated > 0


def test_forex_signal_at_no_lookahead():
    base = [1.05 + i * 0.0005 for i in range(110)]
    wild = base + [0.5, 2.0] * 10
    sig_a, _ = signal_at(_series(wild), 109, market=Market.FOREX)
    sig_b, _ = signal_at(_series(base), 109, market=Market.FOREX)
    assert sig_a.direction is sig_b.direction
    assert sig_a.confidence == sig_b.confidence
