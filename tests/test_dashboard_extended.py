"""Tests for the read-only dashboard payload and edge cases.

Key properties:
- Empty records produce a valid payload with zero counts.
- Latest records per asset picks the newest timestamp.
- Outcome scoring is included in the payload.
- Assets are grouped by market correctly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from alpha_engine.cache.interface import Cache, LocalStore
from alpha_engine.cache.models import Candle, Interval, PriceSeries
from alpha_engine.dashboard.service import build_dashboard_payload, latest_records
from alpha_engine.narrative.narrator import write_thesis
from alpha_engine.schema.signal import Direction, Market, Signal, SignalSource, Timeframe
from alpha_engine.validation.recorder import record_signal

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _series(asset: str, start: datetime, closes: list[float]) -> PriceSeries:
    candles = [
        Candle(ts=start + timedelta(days=i), open=c, high=c, low=c, close=c)
        for i, c in enumerate(closes)
    ]
    return PriceSeries(asset=asset, interval=Interval.DAY, candles=candles)


def _signal(
    asset: str = "BTC",
    market: Market = Market.CRYPTO,
    direction: Direction = Direction.BULLISH,
    confidence: float = 0.7,
    timestamp: datetime | None = None,
) -> Signal:
    return Signal(
        asset=asset,
        market=market,
        direction=direction,
        confidence=confidence,
        timeframe=Timeframe.SWING,
        signal_sources=[SignalSource(name="t", direction=direction, weight=0.6)],
        timestamp=timestamp or T0,
    )


# --- latest_records -----------------------------------------------------------


def test_latest_records_empty_list():
    assert latest_records([]) == []


def test_latest_records_single_asset_multiple_records():
    sig_old = _signal(timestamp=T0)
    sig_new = _signal(timestamp=T0 + timedelta(days=5))
    rec_old = record_signal(sig_old, entry_price=100, root="/tmp/test_a")
    rec_new = record_signal(sig_new, entry_price=110, root="/tmp/test_a")
    latest = latest_records([rec_old, rec_new])
    assert len(latest) == 1
    assert latest[0].signal.timestamp == T0 + timedelta(days=5)


def test_latest_records_multiple_assets(tmp_path):
    sig_btc = _signal(asset="BTC", market=Market.CRYPTO, timestamp=T0)
    sig_aapl = _signal(asset="AAPL", market=Market.US_EQUITY, timestamp=T0 + timedelta(days=1))
    rec_btc = record_signal(sig_btc, entry_price=50000, root=tmp_path)
    rec_aapl = record_signal(sig_aapl, entry_price=150, root=tmp_path)
    latest = latest_records([rec_btc, rec_aapl])
    assert len(latest) == 2
    assets = {r.signal.asset for r in latest}
    assert assets == {"BTC", "AAPL"}


def test_latest_records_sorted_newest_first(tmp_path):
    sig_old = _signal(asset="BTC", timestamp=T0)
    sig_new = _signal(asset="ETH", timestamp=T0 + timedelta(days=10))
    rec_old = record_signal(sig_old, entry_price=100, root=tmp_path)
    rec_new = record_signal(sig_new, entry_price=200, root=tmp_path)
    latest = latest_records([rec_old, rec_new])
    assert latest[0].signal.asset == "ETH"
    assert latest[1].signal.asset == "BTC"


# --- build_dashboard_payload ---------------------------------------------------


def test_empty_records_produces_valid_payload(tmp_path):
    signals_root = tmp_path / "empty_signals"
    signals_root.mkdir()
    cache = Cache(store=LocalStore(root=tmp_path / "cache"))
    payload = build_dashboard_payload(records_root=str(signals_root), cache=cache)
    assert payload["total_records"] == 0
    assert payload["latest_count"] == 0
    assert payload["assets_by_market"] == {}
    assert payload["latest_signals"] == []
    assert payload["outcomes"]["total"] == 0


def test_payload_includes_latest_signal_details(tmp_path):
    signals_root = tmp_path / "signals"
    signals_root.mkdir()
    cache = Cache(store=LocalStore(root=tmp_path / "cache"))
    base = datetime(2024, 3, 1, tzinfo=timezone.utc)
    signal = _signal(asset="BTC", confidence=0.85, timestamp=base)
    signal = write_thesis(signal)  # narrative layer fills thesis before recording
    record_signal(signal, entry_price=60000.0, root=str(signals_root))
    cache.put_price(
        _series(
            "BTC",
            base + timedelta(days=1),
            [60100, 60200, 60300, 60400, 60500, 60600, 60700, 60800, 60900, 61000, 61100],
        )
    )

    payload = build_dashboard_payload(records_root=str(signals_root), cache=cache)
    sig = payload["latest_signals"][0]
    assert sig["asset"] == "BTC"
    assert sig["market"] == "crypto"
    assert sig["direction"] == "bullish"
    assert sig["confidence"] == 0.85
    assert sig["entry_price"] == 60000.0
    assert sig["thesis"] != ""


def test_payload_groups_assets_by_market(tmp_path):
    signals_root = tmp_path / "signals"
    signals_root.mkdir()
    cache = Cache(store=LocalStore(root=tmp_path / "cache"))
    base = datetime(2024, 4, 1, tzinfo=timezone.utc)
    record_signal(
        _signal(asset="BTC", market=Market.CRYPTO, timestamp=base),
        entry_price=100,
        root=str(signals_root),
    )
    record_signal(
        _signal(asset="ETH", market=Market.CRYPTO, timestamp=base),
        entry_price=200,
        root=str(signals_root),
    )
    record_signal(
        _signal(asset="AAPL", market=Market.US_EQUITY, timestamp=base),
        entry_price=150,
        root=str(signals_root),
    )

    payload = build_dashboard_payload(records_root=str(signals_root), cache=cache)
    assert payload["assets_by_market"]["crypto"] == 2
    assert payload["assets_by_market"]["us_equity"] == 1


def test_payload_outcomes_scoring(tmp_path):
    signals_root = tmp_path / "signals"
    signals_root.mkdir()
    cache_root = tmp_path / "cache"
    cache = Cache(store=LocalStore(root=cache_root))
    base = datetime(2024, 5, 1, tzinfo=timezone.utc)
    signal = _signal(asset="BTC", direction=Direction.BULLISH, confidence=0.8, timestamp=base)
    record_signal(signal, entry_price=100.0, root=str(signals_root))
    # Price rises over 10 bars -> bullish hit
    cache.put_price(
        _series(
            "BTC", base + timedelta(days=1), [101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111]
        )
    )

    payload = build_dashboard_payload(records_root=str(signals_root), cache=cache)
    assert payload["outcomes"]["resolved"] == 1
    assert payload["outcomes"]["hits"] == 1
    assert payload["outcomes"]["hit_rate"] == 1.0
