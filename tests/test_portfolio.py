"""Tests for portfolio-level analytics: correlation math, the aggregate
portfolio view, concentration flags, and the dashboard integration.

Key properties:
- Pearson correlation pins: lockstep -> +1, mirror -> -1, flat -> None.
- Net bias and conviction weights follow confidences deterministically.
- Concentration flags fire on unanimous direction and on highly correlated
  same-direction pairs; diversification score reflects the matrix.
- The dashboard payload carries the portfolio section.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from alpha_engine.analyzers.correlation import (
    CorrelationMatrix,
    correlation_matrix,
    diversification_pairs,
    pearson,
    rolling_correlation,
)
from alpha_engine.analyzers.portfolio_signal import build_portfolio_view
from alpha_engine.cache.interface import Cache, LocalStore
from alpha_engine.cache.models import Candle, Interval, PriceSeries
from alpha_engine.dashboard.service import build_dashboard_payload
from alpha_engine.schema.signal import Direction, Market, Signal, SignalSource, Timeframe
from alpha_engine.validation.recorder import record_signal

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _series(closes: list[float], asset: str) -> PriceSeries:
    candles = [
        Candle(ts=T0 + timedelta(days=i), open=c, high=c * 1.01, low=c * 0.99, close=c)
        for i, c in enumerate(closes)
    ]
    return PriceSeries(asset=asset, interval=Interval.DAY, candles=candles)


def _signal(asset: str, direction: Direction, confidence: float) -> Signal:
    return Signal(
        asset=asset,
        market=Market.CRYPTO,
        direction=direction,
        confidence=confidence,
        timeframe=Timeframe.SWING,
        signal_sources=[SignalSource(name="t", direction=direction, weight=0.5)],
        timestamp=T0,
    )


# Wiggly base series: deterministic pseudo-noise around a level.
def _wiggle(n: int, scale: float = 1.0, phase: int = 0) -> list[float]:
    return [100.0 + scale * (((i + phase) * 7) % 13 - 6) for i in range(n)]


# --- pearson / rolling correlation --------------------------------------------------


def test_pearson_lockstep_is_one():
    a = [0.01, -0.02, 0.03, -0.01, 0.02]
    assert abs(pearson(a, a) - 1.0) < 1e-9


def test_pearson_mirror_is_minus_one():
    a = [0.01, -0.02, 0.03, -0.01, 0.02]
    b = [-x for x in a]
    assert abs(pearson(a, b) + 1.0) < 1e-9


def test_pearson_zero_variance_is_none():
    assert pearson([0.0] * 5, [0.01, 0.02, 0.03, 0.04, 0.05]) is None
    assert pearson([0.1], [0.1]) is None  # too short


def test_rolling_correlation_insufficient_history():
    a = _series(_wiggle(10), "A")
    b = _series(_wiggle(10), "B")
    assert rolling_correlation(a, b, window=30) is None


def test_correlation_matrix_shape_and_diagonal():
    series = {
        "A": _series(_wiggle(40), "A"),
        "B": _series(_wiggle(40, phase=3), "B"),
        "C": _series([100.0 + i for i in range(40)], "C"),
    }
    m = correlation_matrix(series, window=30)
    assert m.assets == ["A", "B", "C"]
    assert all(m.matrix[i][i] == 1.0 for i in range(3))
    # symmetric
    assert m.matrix[0][1] == m.matrix[1][0]
    assert m.pair("A", "B") == m.matrix[0][1]


def test_diversification_pairs_filters_by_threshold():
    m = CorrelationMatrix(
        assets=["A", "B", "C"],
        window=30,
        matrix=[[1.0, 0.9, 0.1], [0.9, 1.0, None], [0.1, None, 1.0]],
    )
    pairs = diversification_pairs(m, max_abs_corr=0.3)
    assert pairs == [("A", "C", 0.1)]


# --- portfolio view ------------------------------------------------------------------


def test_portfolio_view_empty():
    view = build_portfolio_view([])
    assert view.signal_count == 0
    assert view.direction is Direction.NEUTRAL
    assert view.conviction_weights == {}


def test_portfolio_view_net_bias_and_weights():
    signals = [
        _signal("BTC", Direction.BULLISH, 0.6),
        _signal("ETH", Direction.BEARISH, 0.2),
        _signal("SOL", Direction.NEUTRAL, 0.9),  # neutral excluded from weights
    ]
    view = build_portfolio_view(signals)
    assert view.signal_count == 3
    assert view.directional_count == 2
    # (0.6 - 0.2) / 0.8 = 0.5
    assert view.net_bias == 0.5
    assert view.direction is Direction.BULLISH
    assert view.conviction_weights == {"BTC": 0.75, "ETH": 0.25}


def test_portfolio_view_all_bullish_flags_concentration():
    signals = [_signal(a, Direction.BULLISH, 0.5) for a in ("BTC", "ETH", "SOL")]
    view = build_portfolio_view(signals)
    assert any("bullish" in f for f in view.concentration_flags)


def test_portfolio_view_correlated_pair_flagged():
    closes = [100.0 * (1.01 ** (i % 5)) for i in range(40)]
    series = {
        "BTC": _series(closes, "BTC"),
        "ETH": _series([c * 2 for c in closes], "ETH"),  # perfectly correlated returns
    }
    signals = [
        _signal("BTC", Direction.BULLISH, 0.5),
        _signal("ETH", Direction.BULLISH, 0.5),
    ]
    view = build_portfolio_view(signals, series)
    assert view.correlations is not None
    assert view.diversification_score is not None
    assert view.diversification_score < 0.1  # 1 - |corr ~ 1|
    assert any("effectively one position" in f for f in view.concentration_flags)


def test_portfolio_view_uncorrelated_scores_high():
    series = {
        "BTC": _series(_wiggle(40, phase=0), "BTC"),
        "AAPL": _series([100.0 + 5 * ((i * 11) % 7) for i in range(40)], "AAPL"),
    }
    signals = [
        _signal("BTC", Direction.BULLISH, 0.5),
        _signal("AAPL", Direction.BEARISH, 0.5),
    ]
    view = build_portfolio_view(signals, series)
    assert view.diversification_score is not None
    assert 0.0 <= view.diversification_score <= 1.0


def test_portfolio_view_deterministic():
    series = {
        "BTC": _series(_wiggle(40), "BTC"),
        "ETH": _series(_wiggle(40, phase=5), "ETH"),
    }
    signals = [
        _signal("BTC", Direction.BULLISH, 0.5),
        _signal("ETH", Direction.BULLISH, 0.4),
    ]
    a = build_portfolio_view(signals, series)
    b = build_portfolio_view(signals, series)
    assert a.model_dump() == b.model_dump()


# --- dashboard integration -------------------------------------------------------------


def test_dashboard_payload_includes_portfolio(tmp_path):
    root = tmp_path / "signals"
    record_signal(_signal("BTC", Direction.BULLISH, 0.6), entry_price=100, root=root)
    record_signal(_signal("ETH", Direction.BEARISH, 0.3), entry_price=2000, root=root)

    cache = Cache(store=LocalStore(root=tmp_path / "cache"))
    cache.store.write_price(_series(_wiggle(40), "BTC"))
    cache.store.write_price(_series(_wiggle(40, phase=4), "ETH"))

    payload = build_dashboard_payload(records_root=root, cache=cache)
    portfolio = payload["portfolio"]
    assert portfolio["signal_count"] == 2
    assert portfolio["directional_count"] == 2
    assert "BTC" in portfolio["conviction_weights"]
    assert portfolio["correlations"] is not None
