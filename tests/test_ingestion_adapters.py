"""Tests for the Task-2 ingestion adapters: Binance (keyless crypto fallback),
CoinGecko Pro (keyed upgrade), and OANDA (forex, key-gated).

All HTTP is mocked; the point is payload normalization, credential gating,
and the CLI's crypto fallback chain. No network, no keys.
"""

from __future__ import annotations

from typing import Any

import pytest

from alpha_engine.cache.interface import Cache, LocalStore
from alpha_engine.cache.models import Interval
from alpha_engine.ingestion import binance, coingecko, coingecko_pro, oanda


class _FakeResponse:
    def __init__(self, payload: Any, status: int = 200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Any:
        return self._payload


def _tmp_cache(tmp_path) -> Cache:
    return Cache(store=LocalStore(root=tmp_path / "cache"))


# --- Binance ---------------------------------------------------------------------


_BINANCE_ROWS = [
    # [open_time_ms, open, high, low, close, volume, close_time_ms, ...]
    [1704067200000, "42000.0", "43000.5", "41500.0", "42800.0", "1234.5", 1704153599999],
    [1704153600000, "42800.0", "44100.0", "42600.0", "44000.0", "2345.6", 1704239999999],
]


def test_binance_supports_mapped_symbols():
    assert binance.supports("BTC")
    assert binance.supports("eth")
    assert not binance.supports("DOGE")


def test_binance_normalizes_klines(tmp_path, monkeypatch):
    captured: dict[str, Any] = {}

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        captured["url"] = url
        captured["params"] = kwargs.get("params", {})
        return _FakeResponse(_BINANCE_ROWS)

    monkeypatch.setattr(binance.requests, "get", fake_get)
    cache = _tmp_cache(tmp_path)
    series = binance.fetch_daily("BTC", days=90, cache=cache)

    assert captured["params"]["symbol"] == "BTCUSDT"
    assert series.asset == "BTC"
    assert series.interval == Interval.DAY
    assert len(series.candles) == 2
    assert series.candles[0].open == 42000.0
    assert series.candles[0].high == 43000.5
    assert series.candles[0].volume == 1234.5
    # normalized data landed in the cache
    cached, _stale = cache.get_price("BTC", "1d")
    assert cached is not None and len(cached.candles) == 2


def test_binance_unmapped_symbol_raises(tmp_path):
    with pytest.raises(ValueError, match="not mapped"):
        binance.fetch_daily("DOGE", cache=_tmp_cache(tmp_path))


# --- CoinGecko Pro ---------------------------------------------------------------


def test_coingecko_pro_requires_key(tmp_path, monkeypatch):
    monkeypatch.delenv("COINGECKO_API_KEY", raising=False)
    assert not coingecko_pro.has_key()
    with pytest.raises(coingecko_pro.MissingAPIKeyError, match="COINGECKO_API_KEY"):
        coingecko_pro.fetch_daily("BTC", cache=_tmp_cache(tmp_path))


def test_coingecko_pro_sends_key_header(tmp_path, monkeypatch):
    monkeypatch.setenv("COINGECKO_API_KEY", "pro-key-123")
    captured: dict[str, Any] = {}

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        captured["url"] = url
        captured["headers"] = kwargs.get("headers", {})
        return _FakeResponse({"prices": [[1704067200000, 42800.0], [1704153600000, 44000.0]]})

    # The pro module delegates to the shared keyless adapter, so the HTTP
    # call (and this patch) lives in coingecko — the assertions below still
    # prove the pro host and key header are what actually go on the wire.
    monkeypatch.setattr(coingecko.requests, "get", fake_get)
    series = coingecko_pro.fetch_daily("BTC", days=30, cache=_tmp_cache(tmp_path))

    assert "pro-api.coingecko.com" in captured["url"]
    assert captured["headers"]["x-cg-pro-api-key"] == "pro-key-123"
    assert len(series.candles) == 2
    assert series.candles[1].close == 44000.0


# --- OANDA -----------------------------------------------------------------------


def test_oanda_pair_normalization():
    assert oanda.normalize_pair("EURUSD") == "EUR_USD"
    assert oanda.normalize_pair("eur/usd") == "EUR_USD"
    assert oanda.normalize_pair("GBP_JPY") == "GBP_JPY"
    assert oanda.normalize_pair("AAPL") is None
    assert oanda.normalize_pair("USDUSD") is None  # same currency twice
    assert oanda.normalize_pair("XXXYYY") is None  # unknown codes


def test_oanda_supports():
    assert oanda.supports("EURUSD")
    assert oanda.supports("USD/JPY")
    assert not oanda.supports("BTC")
    assert not oanda.supports("RELIANCE.NS")


def test_oanda_requires_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OANDA_API_KEY", raising=False)
    with pytest.raises(oanda.MissingAPIKeyError, match="OANDA_API_KEY"):
        oanda.fetch_daily("EURUSD", cache=_tmp_cache(tmp_path))


def test_oanda_normalizes_candles(tmp_path, monkeypatch):
    monkeypatch.setenv("OANDA_API_KEY", "token-abc")
    captured: dict[str, Any] = {}
    payload = {
        "instrument": "EUR_USD",
        "candles": [
            {
                "time": "2026-07-01T21:00:00.000000000Z",
                "complete": True,
                "volume": 51234,
                "mid": {"o": "1.0850", "h": "1.0901", "l": "1.0833", "c": "1.0888"},
            },
            {
                "time": "2026-07-02T21:00:00.000000000Z",
                "complete": False,  # still-forming bar must be skipped
                "volume": 1000,
                "mid": {"o": "1.0888", "h": "1.0899", "l": "1.0870", "c": "1.0891"},
            },
        ],
    }

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        captured["url"] = url
        captured["headers"] = kwargs.get("headers", {})
        return _FakeResponse(payload)

    monkeypatch.setattr(oanda.requests, "get", fake_get)
    series = oanda.fetch_daily("EUR/USD", days=90, cache=_tmp_cache(tmp_path))

    assert "api-fxpractice.oanda.com" in captured["url"]  # practice is the default env
    assert "EUR_USD" in captured["url"]
    assert captured["headers"]["Authorization"] == "Bearer token-abc"
    assert series.asset == "EURUSD"
    assert len(series.candles) == 1  # incomplete bar dropped
    assert series.candles[0].close == 1.0888
    assert series.candles[0].volume == 51234


def test_oanda_rejects_bad_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OANDA_API_KEY", "token")
    monkeypatch.setenv("OANDA_ENV", "sandbox")
    with pytest.raises(ValueError, match="OANDA_ENV"):
        oanda.fetch_daily("EURUSD", cache=_tmp_cache(tmp_path))


# --- CLI crypto fallback chain -----------------------------------------------------


def test_crypto_fallback_uses_binance_when_coingecko_fails(tmp_path, monkeypatch):
    from alpha_engine.cli import main as cli_main

    monkeypatch.delenv("COINGECKO_API_KEY", raising=False)

    def coingecko_fails(*args: Any, **kwargs: Any):
        raise RuntimeError("429 rate limited")

    def _fake_series(asset: str):
        from datetime import datetime, timezone

        from alpha_engine.cache.models import Candle, PriceSeries

        return PriceSeries(
            asset=asset,
            interval=Interval.DAY,
            candles=[
                Candle(
                    ts=datetime(2026, 7, 1, tzinfo=timezone.utc),
                    open=1.0,
                    high=1.0,
                    low=1.0,
                    close=1.0,
                )
            ],
        )

    monkeypatch.setattr(cli_main.coingecko, "fetch_daily", coingecko_fails)
    monkeypatch.setattr(cli_main.binance, "fetch_daily", lambda a, days, cache: _fake_series(a))

    series = cli_main._fetch_crypto_daily("BTC", 90, _tmp_cache(tmp_path))
    assert series.asset == "BTC"
    assert len(series.candles) == 1


def test_crypto_fallback_prefers_pro_with_key(tmp_path, monkeypatch):
    from alpha_engine.cli import main as cli_main

    monkeypatch.setenv("COINGECKO_API_KEY", "pro-key")
    calls: list[str] = []

    def pro_ok(asset: str, days: int = 90, cache: Cache | None = None):
        calls.append("pro")
        from datetime import datetime, timezone

        from alpha_engine.cache.models import Candle, PriceSeries

        return PriceSeries(
            asset=asset,
            interval=Interval.DAY,
            candles=[
                Candle(
                    ts=datetime(2026, 7, 1, tzinfo=timezone.utc),
                    open=2.0,
                    high=2.0,
                    low=2.0,
                    close=2.0,
                )
            ],
        )

    monkeypatch.setattr(cli_main.coingecko_pro, "fetch_daily", pro_ok)
    series = cli_main._fetch_crypto_daily("BTC", 90, _tmp_cache(tmp_path))
    assert calls == ["pro"]
    assert series.candles[0].close == 2.0
