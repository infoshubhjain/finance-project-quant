from __future__ import annotations

from datetime import datetime, timedelta, timezone

from alpha_engine.cache.interface import Cache, LocalStore
from alpha_engine.cache.models import Candle, Interval, PriceSeries
from alpha_engine.dashboard.service import build_dashboard_payload
from alpha_engine.schema.signal import Direction, Market, Signal, SignalSource, Timeframe
from alpha_engine.validation.recorder import record_signal


def _candle(ts: datetime, c: float) -> Candle:
    return Candle(ts=ts, open=c, high=c, low=c, close=c, volume=1000)


def _signal(
    asset: str = "BTC",
    market: Market = Market.CRYPTO,
    direction: Direction = Direction.BULLISH,
    confidence: float = 0.5,
    timeframe: Timeframe = Timeframe.SWING,
    timestamp: datetime | None = None,
) -> Signal:
    return Signal(
        asset=asset,
        market=market,
        direction=direction,
        confidence=confidence,
        timeframe=timeframe,
        timestamp=timestamp or datetime.now(timezone.utc),
        invalidation_level=None,
        thesis="",
        signal_sources=[
            SignalSource(
                name="test.source",
                direction=direction,
                weight=0.5,
                detail="",
            )
        ],
    )


def _series(asset: str, starts_at: datetime, closes: list[float]) -> PriceSeries:
    candles = [_candle(starts_at + timedelta(days=i), closes[i]) for i in range(len(closes))]
    return PriceSeries(asset=asset, interval=Interval.DAY, candles=candles)


def test_empty_records_produces_valid_payload(tmp_path):
    signals_root = tmp_path / "signals"
    signals_root.mkdir()
    payload = build_dashboard_payload(records_root=signals_root)
    assert payload["total_records"] == 0
    assert payload["latest_signals"] == []
    assert payload["outcomes"]["resolved"] == 0


def test_payload_includes_latest_signal_details(tmp_path):
    signals_root = tmp_path / "signals"
    signals_root.mkdir()
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    sig = _signal(asset="BTC", timestamp=base, confidence=0.7)
    record_signal(sig, entry_price=100, root=signals_root)
    payload = build_dashboard_payload(records_root=signals_root)
    assert payload["total_records"] == 1
    assert payload["latest_signals"][0]["asset"] == "BTC"
    assert payload["latest_signals"][0]["confidence"] == 0.7


def test_payload_groups_assets_by_market(tmp_path):
    signals_root = tmp_path / "signals"
    signals_root.mkdir()
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    record_signal(
        _signal(asset="BTC", market=Market.CRYPTO, timestamp=base),
        entry_price=100,
        root=signals_root,
    )
    record_signal(
        _signal(asset="AAPL", market=Market.US_EQUITY, timestamp=base),
        entry_price=150,
        root=signals_root,
    )
    payload = build_dashboard_payload(records_root=signals_root)
    markets = {s["market"] for s in payload["latest_signals"]}
    assert markets == {"crypto", "us_equity"}


def test_payload_outcomes_scoring(tmp_path):
    signals_root = tmp_path / "signals"
    signals_root.mkdir()
    cache_root = tmp_path / "cache"
    cache = Cache(store=LocalStore(root=cache_root))
    base = datetime(2024, 5, 1, tzinfo=timezone.utc)
    sig = _signal(asset="BTC", direction=Direction.BULLISH, confidence=0.8, timestamp=base)
    record_signal(sig, entry_price=100.0, root=signals_root)
    cache.put_price(
        _series(
            "BTC",
            base + timedelta(days=1),
            [101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111],
        )
    )
    payload = build_dashboard_payload(records_root=signals_root, cache=cache)
    assert payload["outcomes"]["resolved"] == 1
    assert payload["outcomes"]["hit_rate"] == 1.0
