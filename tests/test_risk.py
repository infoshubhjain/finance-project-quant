"""Tests for the risk agent (analyzers/risk.py).

All tests use crafted inputs — no network, no randomness.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from alpha_engine.analyzers.risk import (
    PositionSize,
    RiskReport,
    build_risk_report,
    drawdown_metrics,
    historical_cvar,
    historical_var,
    normalize_positions,
    regime_gate,
    tail_risk_flag,
    vol_position_size,
)
from alpha_engine.cache.models import Candle, Interval, PriceSeries
from alpha_engine.quant.models import HmmResult
from alpha_engine.schema.signal import Direction, Market, Signal, SignalSource, Timeframe

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _candle(day: int, close: float, volume: float = 1000.0) -> Candle:
    return Candle(
        ts=T0 + timedelta(days=day),
        open=close,
        high=close * 1.02,
        low=close * 0.98,
        close=close,
        volume=volume,
    )


def _series(closes: list[float]) -> PriceSeries:
    return PriceSeries(
        asset="TEST",
        interval=Interval.DAY,
        candles=[_candle(i, c) for i, c in enumerate(closes)],
    )


def _signal(
    asset: str = "BTC",
    direction: Direction = Direction.BULLISH,
    confidence: float = 0.7,
) -> Signal:
    return Signal(
        asset=asset,
        market=Market.CRYPTO,
        direction=direction,
        confidence=confidence,
        timeframe=Timeframe.SWING,
        signal_sources=[
            SignalSource(name="test", direction=direction, weight=0.5, detail="test"),
        ],
        invalidation_level=None,
        thesis="",
        timestamp=T0,
    )


# ---------------------------------------------------------------------------
# vol_position_size
# ---------------------------------------------------------------------------


class TestVolPositionSize:
    def test_returns_none_for_short_series(self):
        closes = [100.0] * 10
        assert vol_position_size(closes, "X") is None

    def test_higher_vol_gets_lower_weight(self):
        # Low-vol asset: tight range
        low_vol = [100.0 + i * 0.1 + 0.01 * ((-1) ** i) for i in range(100)]
        # High-vol asset: wide swings
        high_vol = [100.0 + i * 0.5 + 3.0 * ((-1) ** i) for i in range(100)]

        ps_low = vol_position_size(low_vol, "LOW")
        ps_high = vol_position_size(high_vol, "HIGH")

        assert ps_low is not None
        assert ps_high is not None
        # Low vol should get higher weight
        assert ps_low.weight > ps_high.weight

    def test_daily_vol_positive(self):
        closes = [100.0 + i * 0.5 + 2.0 * ((-1) ** i) for i in range(100)]
        ps = vol_position_size(closes, "X")
        assert ps is not None
        assert ps.daily_vol > 0
        assert ps.annualized_vol > ps.daily_vol

    def test_flat_series_returns_none(self):
        closes = [100.0] * 100
        ps = vol_position_size(closes, "X")
        assert ps is None


# ---------------------------------------------------------------------------
# normalize_positions
# ---------------------------------------------------------------------------


class TestNormalizePositions:
    def test_weights_sum_to_one(self):
        positions = [
            PositionSize(asset="A", weight=3.0, daily_vol=0.02, annualized_vol=0.32),
            PositionSize(asset="B", weight=1.0, daily_vol=0.04, annualized_vol=0.64),
        ]
        normed = normalize_positions(positions)
        total = sum(p.weight for p in normed)
        assert abs(total - 1.0) < 0.01

    def test_preserves_order(self):
        positions = [
            PositionSize(asset="A", weight=3.0, daily_vol=0.02, annualized_vol=0.32),
            PositionSize(asset="B", weight=1.0, daily_vol=0.04, annualized_vol=0.64),
        ]
        normed = normalize_positions(positions)
        assert normed[0].asset == "A"
        assert normed[1].asset == "B"

    def test_empty_list(self):
        assert normalize_positions([]) == []


# ---------------------------------------------------------------------------
# historical_var
# ---------------------------------------------------------------------------


class TestHistoricalVar:
    def test_var_is_negative(self):
        # Series with known negative returns
        closes = [100.0 - i * 0.5 for i in range(100)]
        var = historical_var(closes, 0.95, window=60)
        assert var is not None
        assert var < 0

    def test_returns_none_for_short_series(self):
        closes = [100.0] * 5
        assert historical_var(closes, 0.95, 60) is None

    def test_stable_series_has_small_var(self):
        # Very stable series
        closes = [100.0 + 0.001 * i for i in range(100)]
        var = historical_var(closes, 0.95, window=60)
        assert var is not None
        assert abs(var) < 0.01


# ---------------------------------------------------------------------------
# historical_cvar
# ---------------------------------------------------------------------------


class TestHistoricalCVaR:
    def test_cvar_worse_than_var(self):
        closes = [100.0 + 2.0 * ((-1) ** i) - i * 0.1 for i in range(100)]
        var = historical_var(closes, 0.95, window=60)
        cvar = historical_cvar(closes, 0.95, window=60)
        assert var is not None
        assert cvar is not None
        # CVaR is the average of losses beyond VaR, so it should be <= VaR
        assert cvar <= var

    def test_returns_none_for_short_series(self):
        closes = [100.0] * 5
        assert historical_cvar(closes, 0.95, 60) is None


# ---------------------------------------------------------------------------
# drawdown_metrics
# ---------------------------------------------------------------------------


class TestDrawdownMetrics:
    def test_no_drawdown_on_upward_series(self):
        closes = [100.0 + i for i in range(50)]
        result = drawdown_metrics(closes)
        assert result is not None
        max_dd, cur_dd = result
        assert max_dd == 0.0
        assert cur_dd == 0.0

    def test_drawdown_on_v_shaped_series(self):
        # Rise then fall
        closes = [100.0 + i for i in range(20)] + [119.0 - i for i in range(20)]
        result = drawdown_metrics(closes)
        assert result is not None
        max_dd, _cur_dd = result
        assert max_dd < 0

    def test_returns_none_for_empty(self):
        assert drawdown_metrics([]) is None


# ---------------------------------------------------------------------------
# tail_risk_flag
# ---------------------------------------------------------------------------


class TestTailRiskFlag:
    def test_returns_none_for_short_series(self):
        closes = [100.0] * 10
        assert tail_risk_flag(closes, window=60) is None

    def test_returns_tail_risk_for_volatile_series(self):
        closes = [100.0 + 3.0 * ((-1) ** i) for i in range(100)]
        tr = tail_risk_flag(closes, window=60)
        assert tr is not None
        assert tr.var_95 < 0
        assert tr.cvar_95 <= tr.var_95
        assert tr.max_drawdown <= 0


# ---------------------------------------------------------------------------
# regime_gate
# ---------------------------------------------------------------------------


class TestRegimeGate:
    def test_bull_regime(self):
        hmm = HmmResult(bull_prob=0.8, bull_mean=0.001, bear_mean=-0.001, iterations=10)
        label, conf = regime_gate(hmm)
        assert label == "bull_regime"
        assert conf == 0.8

    def test_bear_regime(self):
        hmm = HmmResult(bull_prob=0.2, bull_mean=0.001, bear_mean=-0.001, iterations=10)
        label, conf = regime_gate(hmm)
        assert label == "bear_regime"
        assert conf == 0.8

    def test_neutral_regime(self):
        hmm = HmmResult(bull_prob=0.5, bull_mean=0.001, bear_mean=-0.001, iterations=10)
        label, _conf = regime_gate(hmm)
        assert label == "neutral_regime"

    def test_no_hmm(self):
        label, conf = regime_gate(None)
        assert label == "unknown"
        assert conf == 0.0


# ---------------------------------------------------------------------------
# build_risk_report
# ---------------------------------------------------------------------------


class TestBuildRiskReport:
    def _make_signals_and_series(
        self,
    ) -> tuple[list[Signal], dict[str, PriceSeries]]:
        """Create two assets with different volatility profiles."""
        # Low-vol: steady uptrend
        low_closes = [100.0 + i * 0.5 + 0.01 * ((-1) ** i) for i in range(100)]
        # High-vol: choppy
        high_closes = [100.0 + 3.0 * ((-1) ** i) + i * 0.2 for i in range(100)]

        signals = [
            _signal("LOWVOL", Direction.BULLISH, 0.7),
            _signal("HIGHVOL", Direction.BULLISH, 0.5),
        ]
        series = {
            "LOWVOL": _series(low_closes),
            "HIGHVOL": _series(high_closes),
        }
        return signals, series

    def test_report_structure(self):
        signals, series = self._make_signals_and_series()
        report = build_risk_report(signals, series)
        assert isinstance(report, RiskReport)
        assert len(report.position_sizes) == 2
        assert len(report.tail_risks) == 2

    def test_low_vol_gets_higher_weight(self):
        signals, series = self._make_signals_and_series()
        report = build_risk_report(signals, series)
        weights = {ps.asset: ps.weight for ps in report.position_sizes}
        assert weights["LOWVOL"] > weights["HIGHVOL"]

    def test_neutral_signals_excluded(self):
        neutral = _signal("NEUTRAL", Direction.NEUTRAL, 0.5)
        bullish = _signal("BULL", Direction.BULLISH, 0.7)
        closes = [100.0 + i * 0.5 + 0.1 * ((-1) ** i) for i in range(100)]
        series = {"BULL": _series(closes)}
        report = build_risk_report([neutral, bullish], series)
        assert len(report.position_sizes) == 1
        assert report.position_sizes[0].asset == "BULL"

    def test_regime_gate_with_hmm(self):
        signals, series = self._make_signals_and_series()
        hmm = HmmResult(bull_prob=0.75, bull_mean=0.001, bear_mean=-0.001, iterations=10)
        report = build_risk_report(signals, series, hmm=hmm)
        assert report.regime_gate == "bull_regime"

    def test_risk_score_range(self):
        signals, series = self._make_signals_and_series()
        report = build_risk_report(signals, series)
        assert 0 <= report.risk_score <= 100

    def test_empty_signals(self):
        report = build_risk_report([], {})
        assert report.position_sizes == []
        assert report.tail_risks == []
