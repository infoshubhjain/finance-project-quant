"""Tests for Phase 11b: crypto positioning and on-chain data.

The funding-rate vote is deliberately contrarian, which is the kind of inverted
logic that gets silently flipped during a refactor and then quietly loses money
for a year. It gets an explicit test in both directions.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from alpha_engine.analyzers.crypto_onchain import (
    MAX_WEIGHT,
    analyze_onchain,
)
from alpha_engine.cache.interface import Cache, LocalStore
from alpha_engine.cache.models import OnChainObservation
from alpha_engine.ingestion import binance_futures, glassnode
from alpha_engine.schema.signal import Direction

T0 = datetime(2025, 6, 1, tzinfo=timezone.utc)


def _obs(metric: str, values: list[float]) -> list[OnChainObservation]:
    return [
        OnChainObservation(metric=metric, ts=T0 + timedelta(hours=8 * i), value=v, source="test")
        for i, v in enumerate(values)
    ]


# ---------------------------------------------------------------------------
# The analyzer
# ---------------------------------------------------------------------------


def test_no_data_is_zero_weight_neutral():
    src = analyze_onchain([], asset="BTC")
    assert src.direction is Direction.NEUTRAL
    assert src.weight == 0.0
    assert "no positioning data" in src.detail


def test_crowded_longs_read_bearish():
    """High positive funding = longs paying to stay long = contrarian bearish.
    If this test ever flips, the analyzer has been inverted."""
    src = analyze_onchain(_obs("funding_rate_BTC", [0.001] * 9), asset="BTC")
    assert src.direction is Direction.BEARISH
    assert src.weight > 0


def test_capitulated_shorts_read_bullish():
    src = analyze_onchain(_obs("funding_rate_BTC", [-0.0008] * 9), asset="BTC")
    assert src.direction is Direction.BULLISH


def test_neutral_funding_is_neutral():
    src = analyze_onchain(_obs("funding_rate_BTC", [0.0001] * 9), asset="BTC")
    assert src.direction is Direction.NEUTRAL


def test_coins_moving_onto_exchanges_read_bearish():
    """Positive net flow = supply arriving where it can be sold."""
    src = analyze_onchain(_obs("exchange_netflow_BTC", [500.0] * 8), asset="BTC")
    assert src.direction is Direction.BEARISH


def test_coins_leaving_exchanges_read_bullish():
    src = analyze_onchain(_obs("exchange_netflow_BTC", [-500.0] * 8), asset="BTC")
    assert src.direction is Direction.BULLISH


def test_open_interest_sharpens_but_does_not_create():
    """OI build-up raises conviction in an existing read; it must not turn a
    neutral read into a directional one."""
    funding = _obs("funding_rate_BTC", [0.001] * 9)
    flat_oi = _obs("open_interest_BTC", [1e9, 1.0e9])
    rising_oi = _obs("open_interest_BTC", [1e9, 1.5e9])

    base = analyze_onchain(funding + flat_oi, asset="BTC")
    boosted = analyze_onchain(funding + rising_oi, asset="BTC")
    assert boosted.weight > base.weight

    neutral_only = analyze_onchain(_obs("funding_rate_BTC", [0.0001] * 9) + rising_oi, asset="BTC")
    assert neutral_only.direction is Direction.NEUTRAL


def test_dominance_is_ignored_for_btc_itself():
    """'BTC is gaining market share' says nothing about BTC's own direction."""
    obs = _obs("btc_dominance", [50.0, 55.0])
    assert analyze_onchain(obs, asset="BTC").weight == 0.0


def test_rising_dominance_is_bearish_for_altcoins():
    obs = _obs("btc_dominance", [50.0, 55.0])
    assert analyze_onchain(obs, asset="ETH").direction is Direction.BEARISH


def test_falling_dominance_is_bullish_for_altcoins():
    obs = _obs("btc_dominance", [55.0, 50.0])
    assert analyze_onchain(obs, asset="ETH").direction is Direction.BULLISH


def test_weight_respects_the_cap():
    obs = _obs("funding_rate_BTC", [0.01] * 9) + _obs("open_interest_BTC", [1e9, 5e9])
    assert analyze_onchain(obs, asset="BTC").weight <= MAX_WEIGHT


def test_other_assets_metrics_are_ignored():
    """An ETH funding rate must not drive a BTC read."""
    assert analyze_onchain(_obs("funding_rate_ETH", [0.001] * 9), asset="BTC").weight == 0.0


def test_analyzer_is_deterministic():
    obs = _obs("funding_rate_BTC", [0.001] * 9)
    a, b = analyze_onchain(obs, "BTC"), analyze_onchain(obs, "BTC")
    assert (a.direction, a.weight, a.detail) == (b.direction, b.weight, b.detail)


def test_zero_base_open_interest_does_not_divide_by_zero():
    obs = _obs("funding_rate_BTC", [0.001] * 9) + _obs("open_interest_BTC", [0.0, 100.0])
    assert analyze_onchain(obs, asset="BTC").weight > 0


# ---------------------------------------------------------------------------
# Binance futures adapter
# ---------------------------------------------------------------------------


def test_binance_futures_supports_mapped_symbols_only():
    assert binance_futures.supports("BTC")
    assert not binance_futures.supports("DOGE")


def test_unmapped_symbol_returns_empty():
    assert binance_futures.fetch_funding_rate("DOGE") == []
    assert binance_futures.fetch_open_interest("DOGE") == []


def test_funding_rate_parses_rows(monkeypatch, tmp_path):
    class FakeResp:
        status_code = 200

        def json(self):
            return [
                {"fundingTime": 1717200000000, "fundingRate": "0.0001"},
                {"fundingTime": 1717228800000, "fundingRate": "0.0002"},
            ]

    monkeypatch.setattr(binance_futures.net, "get", lambda *a, **kw: FakeResp())
    obs = binance_futures.fetch_funding_rate("BTC", cache=Cache(LocalStore(tmp_path)))
    assert len(obs) == 2
    assert obs[0].metric == "funding_rate_BTC"
    assert obs[1].value == pytest.approx(0.0002)


def test_open_interest_parses_rows(monkeypatch, tmp_path):
    class FakeResp:
        status_code = 200

        def json(self):
            return [{"timestamp": 1717200000000, "sumOpenInterestValue": "1234567.89"}]

    monkeypatch.setattr(binance_futures.net, "get", lambda *a, **kw: FakeResp())
    obs = binance_futures.fetch_open_interest("BTC", cache=Cache(LocalStore(tmp_path)))
    assert len(obs) == 1
    assert obs[0].value == pytest.approx(1234567.89)


def test_malformed_rows_are_skipped_not_fatal(monkeypatch, tmp_path):
    """One bad row costs one observation, not the whole fetch."""

    class FakeResp:
        status_code = 200

        def json(self):
            return [
                {"fundingTime": 1717200000000, "fundingRate": "0.0001"},
                {"fundingTime": "garbage", "fundingRate": None},
                {"missing": "fields"},
            ]

    monkeypatch.setattr(binance_futures.net, "get", lambda *a, **kw: FakeResp())
    assert len(binance_futures.fetch_funding_rate("BTC", cache=Cache(LocalStore(tmp_path)))) == 1


def test_http_error_returns_empty(monkeypatch):
    class FakeResp:
        status_code = 451  # geo-restricted, the realistic failure here

        def json(self):
            return {}

    monkeypatch.setattr(binance_futures.net, "get", lambda *a, **kw: FakeResp())
    assert binance_futures.fetch_funding_rate("BTC") == []


def test_network_error_returns_empty(monkeypatch):
    def boom(*a, **kw):
        raise OSError("no route to host")

    monkeypatch.setattr(binance_futures.net, "get", boom)
    assert binance_futures.fetch_open_interest("BTC") == []


# ---------------------------------------------------------------------------
# Glassnode gating
# ---------------------------------------------------------------------------


def test_glassnode_without_key_returns_empty(monkeypatch):
    monkeypatch.setattr(glassnode, "has_key", lambda: False)
    assert glassnode.fetch_metric("exchange_netflow", "BTC") == []


def test_glassnode_rejects_unknown_metric():
    with pytest.raises(ValueError, match="unknown metric"):
        glassnode.fetch_metric("not_a_metric", "BTC")


def test_glassnode_unsupported_asset_is_empty():
    assert glassnode.fetch_metric("exchange_netflow", "DOGE") == []


def test_glassnode_parses_rows(monkeypatch, tmp_path):
    class FakeResp:
        status_code = 200

        def json(self):
            return [{"t": 1717200000, "v": 123.4}, {"t": 1717286400, "v": None}]

    monkeypatch.setattr(glassnode, "has_key", lambda: True)
    monkeypatch.setenv("GLASSNODE_API_KEY", "test")
    monkeypatch.setattr(glassnode.net, "get", lambda *a, **kw: FakeResp())

    obs = glassnode.fetch_metric("exchange_netflow", "BTC", cache=Cache(LocalStore(tmp_path)))
    # the null-valued day is missing data, not a zero
    assert len(obs) == 1
    assert obs[0].value == pytest.approx(123.4)
