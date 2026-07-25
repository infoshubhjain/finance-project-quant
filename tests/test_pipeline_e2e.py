"""End-to-end pipeline tests: does a scan produce a complete, valid Signal?

The ~2200 other tests are unit tests — each proves one analyzer or helper in
isolation. None of them run the whole `_build_price_signal` synthesis path and
assert the *Signal* that falls out the end is well-formed. That is exactly the
gap where a wiring bug hides: every unit passes, but an analyzer is never
appended, a field is left `None`, or the disclaimer drops off the thesis, and
nothing catches it until a human reads the JSON.

These tests run the real pipeline (the same function `scan` and the orchestrator
call) on a synthetic price series, with the cache pointed at an empty temp dir
so the run is deterministic and touches no network. They assert the contract
every consumer downstream relies on: all decision-bearing fields populated and
in range, sources present, invalidation coherent with direction, and the
research-only disclaimer intact.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from alpha_engine.cache.interface import Cache
from alpha_engine.cache.models import Candle, Interval, PriceSeries
from alpha_engine.schema.signal import (
    SCHEMA_VERSION,
    Direction,
    Market,
    Signal,
    Timeframe,
)

_T0 = datetime(2023, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    """Point all writable/readable state at an empty temp dir, so the context
    loaders (news, on-chain, macro, events) read nothing and the run depends
    only on the price series handed in. Without this a stray cached headline
    could add a sentiment source and make the assertions flap."""
    monkeypatch.setenv("ALPHA_DATA_DIR", str(tmp_path))


def _series(closes: list[float], asset: str = "BTC") -> PriceSeries:
    candles = [
        Candle(
            ts=_T0 + timedelta(days=i),
            open=c,
            high=c * 1.02,
            low=c * 0.98,
            close=c,
            volume=1_000_000.0 * (1.0 + 0.1 * (i % 5)),
        )
        for i, c in enumerate(closes)
    ]
    return PriceSeries(asset=asset, interval=Interval.DAY, candles=candles)


def _uptrend(n: int = 120) -> list[float]:
    # Steady climb with a mild wobble — enough structure for every analyzer to
    # have something to say, deterministic so the signal is reproducible.
    return [100.0 * math.exp(0.004 * i) * (1.0 + 0.01 * math.sin(i)) for i in range(n)]


def _build(asset: str, market: Market, closes: list[float]) -> Signal:
    from alpha_engine.cli.main import _build_price_signal

    series = _series(closes, asset=asset)
    return _build_price_signal(asset, market, series, Cache(), no_refresh=True)


def _assert_valid_signal(sig: Signal, asset: str, market: Market) -> None:
    """The contract every downstream consumer (dashboard, recorder, MCP) relies on."""
    assert sig.schema_version == SCHEMA_VERSION
    assert sig.asset == asset
    assert sig.market is market
    assert isinstance(sig.direction, Direction)
    assert 0.0 <= sig.confidence <= 1.0
    assert sig.timeframe is Timeframe.SWING

    # Sources must actually be present — a synthesis over an empty list is the
    # classic "everything passed but nothing was wired in" failure.
    assert sig.signal_sources, "no signal sources — an analyzer branch never ran"
    for src in sig.signal_sources:
        assert src.name
        assert isinstance(src.direction, Direction)
        assert 0.0 <= src.weight <= 1.0

    # Invalidation is direction-aware: a directional call names the price that
    # would prove it wrong; a neutral one has nothing to invalidate.
    if sig.direction is Direction.NEUTRAL:
        assert sig.invalidation_level is None
    else:
        assert isinstance(sig.invalidation_level, float)

    # The disclaimer is load-bearing and must never be dropped from the thesis.
    assert sig.thesis
    assert "not investment advice" in sig.thesis.lower()

    assert sig.timestamp.tzinfo is not None  # schema requires tz-aware UTC


def test_crypto_scan_produces_a_complete_signal() -> None:
    _assert_valid_signal(_build("BTC", Market.CRYPTO, _uptrend()), "BTC", Market.CRYPTO)


def test_equity_scan_produces_a_complete_signal() -> None:
    _assert_valid_signal(_build("AAPL", Market.US_EQUITY, _uptrend()), "AAPL", Market.US_EQUITY)


def test_uptrend_reads_bullish_with_coherent_invalidation() -> None:
    """A clean uptrend should read bullish, and a bullish invalidation must sit
    below the latest price — the level that, if broken, ends the thesis."""
    closes = _uptrend()
    sig = _build("BTC", Market.CRYPTO, closes)
    assert sig.direction is Direction.BULLISH
    assert sig.invalidation_level is not None
    assert sig.invalidation_level < closes[-1]


def test_pipeline_is_deterministic() -> None:
    """Same inputs, same signal — the cardinal rule. Everything but the wall-clock
    timestamp must be byte-identical across runs."""
    closes = _uptrend()
    a = _build("BTC", Market.CRYPTO, closes).model_dump(mode="json")
    b = _build("BTC", Market.CRYPTO, closes).model_dump(mode="json")
    a.pop("timestamp")
    b.pop("timestamp")
    assert a == b
