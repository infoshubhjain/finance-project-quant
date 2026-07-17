from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from alpha_engine.analyzers.correlation import correlation_matrix
from alpha_engine.analyzers.portfolio_signal import (
    build_portfolio_view,
)
from alpha_engine.cache.models import Candle, Interval, PriceSeries
from alpha_engine.schema.signal import (
    Direction,
    Market,
    Signal,
    Timeframe,
)

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _series(closes: list[float], asset: str = "A") -> PriceSeries:
    return PriceSeries(
        asset=asset,
        interval=Interval.DAY,
        candles=[
            Candle(ts=T0 + timedelta(days=i), open=c, high=c, low=c, close=c)
            for i, c in enumerate(closes)
        ],
    )


class TestBuildPortfolioView:
    def test_single_bullish_signal(self):
        sig = Signal(
            asset="A",
            market=Market.CRYPTO,
            direction=Direction.BULLISH,
            confidence=0.8,
            timeframe=Timeframe.SWING,
            invalidation_level=None,
            thesis="",
        )
        view = build_portfolio_view([sig], {})
        assert view.net_bias == 1.0

    def test_single_bearish_signal(self):
        sig = Signal(
            asset="A",
            market=Market.CRYPTO,
            direction=Direction.BEARISH,
            confidence=0.8,
            timeframe=Timeframe.SWING,
            invalidation_level=None,
            thesis="",
        )
        view = build_portfolio_view([sig], {})
        assert view.net_bias == -1.0

    def test_balanced_signals_are_zero_net(self):
        bull = Signal(
            asset="A",
            market=Market.CRYPTO,
            direction=Direction.BULLISH,
            confidence=0.5,
            timeframe=Timeframe.SWING,
            invalidation_level=None,
            thesis="",
        )
        bear = Signal(
            asset="B",
            market=Market.CRYPTO,
            direction=Direction.BEARISH,
            confidence=0.5,
            timeframe=Timeframe.SWING,
            invalidation_level=None,
            thesis="",
        )
        view = build_portfolio_view([bull, bear], {})
        assert view.net_bias == 0.0

    def test_net_bias_ignores_neutral(self):
        bull = Signal(
            asset="A",
            market=Market.CRYPTO,
            direction=Direction.BULLISH,
            confidence=0.8,
            timeframe=Timeframe.SWING,
            invalidation_level=None,
            thesis="",
        )
        neutral = Signal(
            asset="B",
            market=Market.CRYPTO,
            direction=Direction.NEUTRAL,
            confidence=0.9,
            timeframe=Timeframe.SWING,
            invalidation_level=None,
            thesis="",
        )
        view = build_portfolio_view([bull, neutral], {})
        assert view.net_bias == 1.0


class TestCorrelation:
    def test_correlation_matrix_perfect_positive(self):
        s1 = _series([10, 20, 30, 40], asset="A")
        s2 = _series([20, 40, 60, 80], asset="B")
        matrix = correlation_matrix({"A": s1, "B": s2}, window=3)
        assert matrix is not None
        corr_val = matrix.pair("A", "B")
        assert corr_val is not None and math.isclose(corr_val, 1.0)

    def test_correlation_matrix_perfect_negative(self):
        s1 = _series([100, 110, 121, 133], asset="A")
        s2 = _series([100, 90, 81, 72.9], asset="B")
        matrix = correlation_matrix({"A": s1, "B": s2}, window=3)
        assert matrix is not None
        corr_val = matrix.pair("A", "B")
        assert corr_val is not None and corr_val < -0.95
