"""Tests for the calibration module (validation/calibrate.py).

All tests use crafted inputs — no network, no randomness.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from alpha_engine.cache.models import Candle, Interval, PriceSeries
from alpha_engine.schema.signal import Direction, Market, Signal, SignalSource, Timeframe
from alpha_engine.validation.calibrate import (
    _shrink,
    calibrate,
    load_calibration,
    write_calibration,
    CalibrationResult,
    AnalyzerCalibration,
)
from alpha_engine.validation.recorder import SignalRecord
from alpha_engine.cache.interface import Cache, LocalStore

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _candle(day: int, close: float) -> Candle:
    return Candle(
        ts=T0 + timedelta(days=day),
        open=close,
        high=close * 1.02,
        low=close * 0.98,
        close=close,
        volume=1000.0,
    )


def _series(asset: str, closes: list[float]) -> PriceSeries:
    return PriceSeries(
        asset=asset,
        interval=Interval.DAY,
        candles=[_candle(i, c) for i, c in enumerate(closes)],
    )


def _signal(
    asset: str = "BTC",
    direction: Direction = Direction.BULLISH,
    confidence: float = 0.7,
    source_names: list[str] | None = None,
) -> Signal:
    if source_names is None:
        source_names = ["test"]
    return Signal(
        asset=asset,
        market=Market.CRYPTO,
        direction=direction,
        confidence=confidence,
        timeframe=Timeframe.SWING,
        signal_sources=[
            SignalSource(name=n, direction=direction, weight=0.5, detail="test")
            for n in source_names
        ],
        invalidation_level=None,
        thesis="",
        timestamp=T0,
    )


def _record(
    signal: Signal,
    entry_price: float = 100.0,
    recorded_at: datetime | None = None,
) -> SignalRecord:
    return SignalRecord(
        record_id="",
        signal=signal,
        entry_price=entry_price,
        recorded_at=recorded_at or T0,
    )


# ---------------------------------------------------------------------------
# _shrink
# ---------------------------------------------------------------------------


class TestShrink:
    def test_half_on_zero_samples(self):
        """Zero samples → default 0.50."""
        assert _shrink(0, 0, 30.0) == 0.5

    def test_perfect_with_many_samples(self):
        """100 hits out of 100, k=30 → (100 + 15) / (100 + 30) ≈ 0.8846."""
        result = _shrink(100, 100, 30.0)
        assert abs(result - 0.8846) < 0.001

    def test_shrinkage_pin_12_at_75_percent(self):
        """12 samples at 75% (9 hits) must NOT yield 0.75.
        Shrinkage: (9 + 30*0.5) / (12 + 30) = 24/42 ≈ 0.5714."""
        result = _shrink(9, 12, 30.0)
        assert result < 0.75, f"shrinkage should pull below 0.75, got {result}"
        assert result > 0.50, f"should stay above 0.50, got {result}"
        assert abs(result - 24 / 42) < 0.001

    def test_shrinkage_converges_with_large_n(self):
        """With very large n, shrunk value approaches empirical rate."""
        # 80 hits out of 100 → empirical 0.80
        result = _shrink(80, 100, 30.0)
        # (80 + 15) / (100 + 30) = 95/130 ≈ 0.7308
        assert abs(result - 95 / 130) < 0.001

    def test_zero_hits(self):
        """0 hits out of 30 → (0 + 15) / (30 + 30) = 0.25."""
        result = _shrink(0, 30, 30.0)
        assert abs(result - 0.25) < 0.001


# ---------------------------------------------------------------------------
# load_calibration — no-file path
# ---------------------------------------------------------------------------


class TestLoadCalibrationNoFile:
    def test_returns_empty_dict(self, tmp_path: Path):
        """Missing calibration file → empty dict (fresh-clone path)."""
        result = load_calibration(tmp_path / "nonexistent.json")
        assert result == {}

    def test_returns_empty_dict_on_corrupt_file(self, tmp_path: Path):
        """Corrupt calibration file → empty dict (graceful fallback)."""
        p = tmp_path / "bad.json"
        p.write_text("not json {{{")
        result = load_calibration(p)
        assert result == {}


# ---------------------------------------------------------------------------
# write_calibration + load_calibration round-trip
# ---------------------------------------------------------------------------


class TestCalibrationRoundTrip:
    def test_round_trip(self, tmp_path: Path):
        """Write then load preserves analyzer reliabilities."""
        result = CalibrationResult(
            window_records=100,
            window_resolved=80,
            shrinkage_k=30.0,
            min_samples=50,
            analyzers=[
                AnalyzerCalibration(
                    name="rsi",
                    empirical_hit_rate=0.65,
                    shrunk_reliability=0.58,
                    resolved_count=40,
                    hit_count=26,
                    used_default=False,
                ),
                AnalyzerCalibration(
                    name="bollinger",
                    empirical_hit_rate=None,
                    shrunk_reliability=0.50,
                    resolved_count=0,
                    hit_count=0,
                    used_default=True,
                ),
            ],
        )
        path = tmp_path / "calibration.json"
        write_calibration(result, path)

        loaded = load_calibration(path)
        assert loaded["rsi"] == 0.58
        assert loaded["bollinger"] == 0.50
        assert len(loaded) == 2

    def test_json_contains_metadata(self, tmp_path: Path):
        """Written JSON includes generated_at, shrinkage_k, min_samples."""
        result = CalibrationResult(
            window_records=50,
            window_resolved=40,
            shrinkage_k=25.0,
            min_samples=30,
            analyzers=[],
        )
        path = tmp_path / "calibration.json"
        write_calibration(result, path)

        data = json.loads(path.read_text())
        assert "generated_at" in data
        assert data["shrinkage_k"] == 25.0
        assert data["min_samples"] == 30
        assert data["window_records"] == 50


# ---------------------------------------------------------------------------
# calibrate — full integration with crafted records
# ---------------------------------------------------------------------------


class TestCalibrate:
    def _make_records_and_cache(
        self,
    ) -> tuple[list[SignalRecord], LocalStore]:
        """Build a set of records with known outcomes and a matching cache.

        Scenario:
        - BTC: 3 BULLISH signals, all hit (price goes up)
        - ETH: 2 BULLISH signals, 1 hit, 1 miss
        - Each signal has two analyzers: "good_analyzer" (direction matches)
          and "bad_analyzer" (also matches direction in this case)
        """
        # BTC: uptrend — all 3 signals hit
        btc_closes = [100.0 + i * 2.0 for i in range(30)]
        # ETH: up then down — first signal hits, second misses
        eth_closes = [100.0 + i * 1.0 for i in range(10)] + [109.0 - i * 2.0 for i in range(20)]

        store = LocalStore(root=Path("/tmp/test_calibrate_cache"))
        btc_series = _series("BTC", btc_closes)
        eth_series = _series("ETH", eth_closes)
        store.write_price(btc_series)
        store.write_price(eth_series)

        records = []
        # BTC signal 1: emitted at day 0, price 100
        sig1 = _signal("BTC", Direction.BULLISH, 0.7, ["good_analyzer", "bad_analyzer"])
        records.append(_record(sig1, entry_price=100.0, recorded_at=T0))

        # BTC signal 2: emitted at day 5, price 110
        sig2 = _signal("BTC", Direction.BULLISH, 0.6, ["good_analyzer"])
        records.append(_record(sig2, entry_price=110.0, recorded_at=T0 + timedelta(days=5)))

        # BTC signal 3: emitted at day 10, price 120
        sig3 = _signal("BTC", Direction.BULLISH, 0.8, ["good_analyzer", "bad_analyzer"])
        records.append(_record(sig3, entry_price=120.0, recorded_at=T0 + timedelta(days=10)))

        # ETH signal 1: emitted at day 0, price 100 — will hit (price goes to 109)
        sig4 = _signal("ETH", Direction.BULLISH, 0.65, ["good_analyzer"])
        records.append(_record(sig4, entry_price=100.0, recorded_at=T0))

        # ETH signal 2: emitted at day 15, price 104 — will miss (price drops)
        sig5 = _signal("ETH", Direction.BULLISH, 0.55, ["bad_analyzer"])
        records.append(_record(sig5, entry_price=104.0, recorded_at=T0 + timedelta(days=15)))

        return records, store

    def test_calibrate_produces_results(self, tmp_path: Path):
        """calibrate() produces per-analyzer results."""
        records, store = self._make_records_and_cache()
        cache = Cache(store=store)

        result = calibrate(records=records, cache=cache, min_samples=1, shrinkage_k=30.0)

        assert isinstance(result, CalibrationResult)
        assert result.window_records == 5
        assert result.window_resolved > 0
        assert len(result.analyzers) > 0

    def test_good_analyzer_beats_bad(self, tmp_path: Path):
        """good_analyzer (more hits) should have higher reliability than bad_analyzer."""
        records, store = self._make_records_and_cache()
        cache = Cache(store=store)

        result = calibrate(records=records, cache=cache, min_samples=1, shrinkage_k=30.0)

        by_name = {a.name: a for a in result.analyzers}
        assert "good_analyzer" in by_name
        assert "bad_analyzer" in by_name
        assert (
            by_name["good_analyzer"].shrunk_reliability > by_name["bad_analyzer"].shrunk_reliability
        )

    def test_min_samples_floors_to_default(self, tmp_path: Path):
        """Analyzers with fewer than min_samples keep used_default=True."""
        records, store = self._make_records_and_cache()
        cache = Cache(store=store)

        # Set min_samples very high so all analyzers use default
        result = calibrate(records=records, cache=cache, min_samples=1000, shrinkage_k=30.0)

        for a in result.analyzers:
            assert a.used_default is True
            assert a.shrunk_reliability == 0.5

    def test_neutral_signals_excluded(self, tmp_path: Path):
        """Neutral signals are not scored and don't appear in calibration."""
        neutral = _signal("BTC", Direction.NEUTRAL, 0.0, ["test"])
        record = _record(neutral, entry_price=100.0)

        store = LocalStore(root=Path("/tmp/test_calibrate_neutral"))
        btc_series = _series("BTC", [100.0 + i for i in range(30)])
        store.write_price(btc_series)
        cache = Cache(store=store)

        result = calibrate(records=[record], cache=cache, min_samples=1, shrinkage_k=30.0)
        assert result.window_resolved == 0
        assert len(result.analyzers) == 0
