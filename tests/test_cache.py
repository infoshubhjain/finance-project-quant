"""Tests for cache staleness and the Cache read/write interface.

Key properties:
- Fresh data is not stale.
- Data older than its TTL is stale.
- get_price/get_macro/get_chain return (None, True) when nothing is cached.
- put/get round-trips preserve data exactly.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

from alpha_engine.cache.interface import Cache, LocalStore, is_stale
from alpha_engine.cache.models import (
    Candle,
    Interval,
    MacroObservation,
    OptionQuote,
    OptionRight,
    OptionsChain,
    PriceSeries,
)

T0 = datetime(2024, 6, 15, tzinfo=timezone.utc)


def _price_series(asset: str = "BTC", fetched_at: datetime | None = None) -> PriceSeries:
    candles = [
        Candle(
            ts=T0 + timedelta(days=i),
            open=100.0 + i,
            high=105.0 + i,
            low=95.0 + i,
            close=100.0 + i,
        )
        for i in range(5)
    ]
    return PriceSeries(
        asset=asset,
        interval=Interval.DAY,
        candles=candles,
        fetched_at=fetched_at or datetime.now(timezone.utc),
    )


def _macro_obs(series_id: str = "FEDFUNDS", ts: datetime | None = None) -> list[MacroObservation]:
    return [
        MacroObservation(
            series_id=series_id,
            ts=ts or datetime.now(timezone.utc),
            value=5.25,
            source="FRED",
        )
    ]


def _chain(underlying: str = "NIFTY", fetched_at: datetime | None = None) -> OptionsChain:
    return OptionsChain(
        underlying=underlying,
        expiry=datetime(2026, 7, 30, tzinfo=timezone.utc),
        spot=20000.0,
        quotes=[
            OptionQuote(strike=20000, right=OptionRight.CALL, oi=1000),
            OptionQuote(strike=20000, right=OptionRight.PUT, oi=2000),
        ],
        fetched_at=fetched_at or datetime.now(timezone.utc),
    )


# --- is_stale ----------------------------------------------------------------


def test_fresh_data_is_not_stale():
    now = datetime.now(timezone.utc)
    assert is_stale(now, "price", "1d") is False
    assert is_stale(now, "macro") is False
    assert is_stale(now, "chain") is False


def test_old_data_is_stale():
    old = datetime.now(timezone.utc) - timedelta(hours=25)
    assert is_stale(old, "price", "1d") is True  # TTL is 12 hours
    assert is_stale(old, "macro") is True  # TTL is 1 day
    assert is_stale(old, "chain") is True  # TTL is 15 minutes


def test_price_1h_has_one_hour_ttl():
    just_under = datetime.now(timezone.utc) - timedelta(minutes=59)
    just_over = datetime.now(timezone.utc) - timedelta(hours=1, minutes=1)
    assert is_stale(just_under, "price", "1h") is False
    assert is_stale(just_over, "price", "1h") is True


def test_price_1m_has_two_minute_ttl():
    just_under = datetime.now(timezone.utc) - timedelta(minutes=1)
    just_over = datetime.now(timezone.utc) - timedelta(minutes=3)
    assert is_stale(just_under, "price", "1m") is False
    assert is_stale(just_over, "price", "1m") is True


def test_chain_has_fifteen_minute_ttl():
    just_under = datetime.now(timezone.utc) - timedelta(minutes=14)
    just_over = datetime.now(timezone.utc) - timedelta(minutes=16)
    assert is_stale(just_under, "chain") is False
    assert is_stale(just_over, "chain") is True


def test_unknown_kind_defaults_to_one_hour():
    just_under = datetime.now(timezone.utc) - timedelta(minutes=59)
    just_over = datetime.now(timezone.utc) - timedelta(hours=1, minutes=1)
    assert is_stale(just_under, "unknown_kind") is False
    assert is_stale(just_over, "unknown_kind") is True


# --- Cache get returns (None, True) when empty --------------------------------


def test_get_price_empty_returns_none_stale(tmp_path):
    cache = Cache(store=LocalStore(root=tmp_path))
    series, stale = cache.get_price("BTC", "1d")
    assert series is None
    assert stale is True


def test_get_macro_empty_returns_empty_stale(tmp_path):
    cache = Cache(store=LocalStore(root=tmp_path))
    obs, stale = cache.get_macro("FEDFUNDS")
    assert obs == []
    assert stale is True


def test_get_chain_empty_returns_none_stale(tmp_path):
    cache = Cache(store=LocalStore(root=tmp_path))
    chain, stale = cache.get_chain("NIFTY")
    assert chain is None
    assert stale is True


# --- put/get round-trip -------------------------------------------------------


def test_price_round_trip_preserves_data(tmp_path):
    cache = Cache(store=LocalStore(root=tmp_path))
    now = datetime.now(timezone.utc)
    original = _price_series(fetched_at=now)
    cache.put_price(original)
    loaded, _stale = cache.get_price("BTC", "1d")
    assert loaded is not None
    assert loaded.asset == original.asset
    assert len(loaded.candles) == len(original.candles)
    assert loaded.candles[0].close == original.candles[0].close


def test_macro_round_trip_preserves_data(tmp_path):
    cache = Cache(store=LocalStore(root=tmp_path))
    now = datetime.now(timezone.utc)
    original = _macro_obs(ts=now)
    cache.put_macro(original)
    loaded, _stale = cache.get_macro("FEDFUNDS")
    assert len(loaded) == 1
    assert loaded[0].value == 5.25


def test_chain_round_trip_preserves_data(tmp_path):
    cache = Cache(store=LocalStore(root=tmp_path))
    now = datetime.now(timezone.utc)
    original = _chain(fetched_at=now)
    cache.put_chain(original)
    loaded, _stale = cache.get_chain("NIFTY")
    assert loaded is not None
    assert loaded.underlying == "NIFTY"
    assert len(loaded.quotes) == 2
    assert loaded.spot == 20000.0


def test_freshly_written_price_is_not_stale(tmp_path):
    cache = Cache(store=LocalStore(root=tmp_path))
    cache.put_price(_price_series(fetched_at=datetime.now(timezone.utc)))
    _, stale = cache.get_price("BTC", "1d")
    assert stale is False


def test_freshly_written_chain_is_not_stale(tmp_path):
    cache = Cache(store=LocalStore(root=tmp_path))
    cache.put_chain(_chain(fetched_at=datetime.now(timezone.utc)))
    _, stale = cache.get_chain("NIFTY")
    assert stale is False


def test_stale_price_detected_after_ttl(tmp_path):
    cache = Cache(store=LocalStore(root=tmp_path))
    old_time = datetime.now(timezone.utc) - timedelta(hours=25)
    cache.put_price(_price_series(fetched_at=old_time))
    _, stale = cache.get_price("BTC", "1d")
    assert stale is True


# --- macro freshness --------------------------------------------------------
#
# These pin a bug that made the macro cache useless without ever erroring:
# staleness was computed from the newest OBSERVATION's date rather than from
# when the series was fetched. FRED's monthly series are always weeks old by
# construction (CPI for June publishes in July and stays current all month), so
# a 1-day TTL judged every series permanently stale and every equity scan
# refetched all three — twelve FRED calls per four-asset batch instead of three.


def test_macro_written_now_is_not_stale(tmp_path):
    """The regression itself: a file written this second must not be stale
    merely because monthly data is dated last month."""
    store = LocalStore(tmp_path)
    cache = Cache(store)
    forty_five_days_ago = datetime.now(timezone.utc) - timedelta(days=45)
    store.write_macro(
        [MacroObservation(series_id="CPIAUCSL", ts=forty_five_days_ago, value=310.0, source="fred")]
    )

    obs, stale = cache.get_macro("CPIAUCSL")
    assert obs and stale is False


def test_macro_goes_stale_once_the_ttl_passes(tmp_path):
    """The other half — the fix must not make macro permanently fresh."""
    store = LocalStore(tmp_path)
    cache = Cache(store)
    store.write_macro(
        [
            MacroObservation(
                series_id="UNRATE", ts=datetime.now(timezone.utc), value=4.1, source="fred"
            )
        ]
    )

    path = tmp_path / "macro" / "UNRATE.json"
    two_days_ago = time.time() - 2 * 86400
    os.utime(path, (two_days_ago, two_days_ago))

    _obs, stale = cache.get_macro("UNRATE")
    assert stale is True


def test_missing_macro_series_is_stale(tmp_path):
    obs, stale = Cache(LocalStore(tmp_path)).get_macro("NOPE")
    assert obs == [] and stale is True


def test_macro_fetched_at_is_none_for_a_series_never_written(tmp_path):
    assert LocalStore(tmp_path).macro_fetched_at("NOPE") is None
