"""Tests for the read-only dashboard payload."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from alpha_engine.cache.interface import Cache, LocalStore
from alpha_engine.cache.models import Candle, Interval, PriceSeries
from alpha_engine.dashboard.service import build_dashboard_payload, latest_records
from alpha_engine.schema.signal import Direction, Market, Signal, SignalSource, Timeframe
from alpha_engine.validation.recorder import record_signal


def _series(asset: str, start: datetime, closes: list[float]) -> PriceSeries:
    candles = [
        Candle(ts=start + timedelta(days=i), open=c, high=c, low=c, close=c)
        for i, c in enumerate(closes)
    ]
    return PriceSeries(asset=asset, interval=Interval.DAY, candles=candles)


def test_latest_records_picks_newest_per_asset(tmp_path):
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    sig_old = Signal(
        asset="BTC",
        market=Market.CRYPTO,
        direction=Direction.BULLISH,
        confidence=0.6,
        timeframe=Timeframe.SWING,
        signal_sources=[
            SignalSource(name="t", direction=Direction.BULLISH, weight=0.6, detail="test")
        ],
        timestamp=base,
        invalidation_level=None,
        thesis="test",
    )
    sig_new = sig_old.model_copy(update={"timestamp": base + timedelta(days=1)})
    signals_root = tmp_path / "signals"
    records = [
        record_signal(sig_old, entry_price=100, root=signals_root),
        record_signal(sig_new, entry_price=101, root=signals_root),
    ]

    latest = latest_records(records)
    assert len(latest) == 1
    assert latest[0].signal.timestamp == sig_new.timestamp


def test_build_dashboard_payload_scores_records(tmp_path):
    signals_root = tmp_path / "signals"
    cache_root = tmp_path / "cache"
    cache = Cache(store=LocalStore(root=cache_root))
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    signal = Signal(
        asset="BTC",
        market=Market.CRYPTO,
        direction=Direction.BULLISH,
        confidence=0.75,
        timeframe=Timeframe.SWING,
        signal_sources=[
            SignalSource(name="t", direction=Direction.BULLISH, weight=0.6, detail="test")
        ],
        timestamp=base,
        invalidation_level=None,
        thesis="test",
    )
    record_signal(signal, entry_price=100.0, root=signals_root)
    cache.put_price(
        _series(
            "BTC",
            base + timedelta(days=1),
            [101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111],
        )
    )

    payload = build_dashboard_payload(records_root=signals_root, cache=cache)
    assert payload["total_records"] == 1
    assert payload["latest_count"] == 1
    assert payload["latest_signals"][0]["asset"] == "BTC"
    assert payload["outcomes"]["resolved"] == 1
    assert payload["outcomes"]["hit_rate"] == 1.0
