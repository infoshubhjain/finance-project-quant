"""Tests for the key-gated equity price fallback.

Yahoo is the only keyless source for equity prices and it throttles by IP with
HTTP 429. Every keyless alternative checked on 2026-07-26 now sits behind a
JavaScript proof-of-work challenge served at HTTP 200 — worse than a refusal,
because a naive client caches the challenge page as data. So the second tier is
key-gated, and the property that matters most is that the FIRST tier still is
not: the default path must stay keyless.
"""

from __future__ import annotations

import pytest

from alpha_engine import health, net
from alpha_engine.cache.interface import Cache, LocalStore
from alpha_engine.cli import main as cli
from alpha_engine.ingestion import fmp_prices

FMP_BODY = {
    "symbol": "AAPL",
    "historical": [
        # FMP returns newest-first; every consumer here assumes oldest-first.
        {"date": "2026-01-03", "open": 3, "high": 4, "low": 2, "close": 3.5, "volume": 30},
        {"date": "2026-01-02", "open": 2, "high": 3, "low": 1, "close": 2.5, "volume": 20},
    ],
}


class Resp:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self.headers: dict = {}
        self._p = payload
        self.url = "https://financialmodelingprep.com/"

    def json(self):
        return self._p

    def raise_for_status(self):
        if self.status_code >= 400:
            raise net.HTTPStatusError(f"HTTP {self.status_code}")


# --------------------------------------------------------------------------
# The default path stays keyless
# --------------------------------------------------------------------------


def test_without_a_key_the_fallback_is_invisible(monkeypatch, tmp_path):
    """The cardinal rule: no key means the equity path is exactly what it was."""
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    assert fmp_prices.has_key() is False

    def yahoo_dies(asset, days=365, cache=None):
        raise net.HTTPStatusError("HTTP 429 for yahoo")

    monkeypatch.setattr(cli.yahoo, "fetch_daily", yahoo_dies)
    called = {"fmp": 0}
    monkeypatch.setattr(fmp_prices, "fetch_daily", lambda *a, **k: called.__setitem__("fmp", 1))

    with pytest.raises(net.HTTPStatusError, match="429"):
        cli._fetch_equity_daily("AAPL", 90, Cache(LocalStore(tmp_path)))
    assert called["fmp"] == 0, "the keyed tier must not be reached without a key"


def test_yahoo_is_always_tried_first(monkeypatch, tmp_path):
    monkeypatch.setenv("FMP_API_KEY", "k")
    order: list[str] = []
    monkeypatch.setattr(cli.yahoo, "fetch_daily", lambda *a, **k: order.append("yahoo") or "series")
    monkeypatch.setattr(fmp_prices, "fetch_daily", lambda *a, **k: order.append("fmp"))

    cli._fetch_equity_daily("AAPL", 90, Cache(LocalStore(tmp_path)))
    assert order == ["yahoo"], "FMP must never be called while Yahoo works"


def test_a_throttled_yahoo_falls_through_to_fmp(monkeypatch, tmp_path):
    monkeypatch.setenv("FMP_API_KEY", "k")

    def throttled(*a, **k):
        raise net.HTTPStatusError("HTTP 429 for yahoo")

    monkeypatch.setattr(cli.yahoo, "fetch_daily", throttled)
    monkeypatch.setattr(fmp_prices, "fetch_daily", lambda *a, **k: "fmp-series")
    assert cli._fetch_equity_daily("AAPL", 90, Cache(LocalStore(tmp_path))) == "fmp-series"


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def test_rows_are_reordered_oldest_first():
    candles = fmp_prices._parse(FMP_BODY, "AAPL")
    assert [c.close for c in candles] == [2.5, 3.5]


def test_an_error_body_raises_rather_than_caching_nothing():
    with pytest.raises(ValueError, match="FMP error"):
        fmp_prices._parse({"Error Message": "Invalid API KEY."}, "AAPL")


def test_a_body_without_history_raises():
    with pytest.raises(ValueError, match="no 'historical' list"):
        fmp_prices._parse({"symbol": "AAPL"}, "AAPL")


def test_rows_with_no_close_are_dropped_not_guessed():
    body = {"historical": [{"date": "2026-01-02", "close": None}, FMP_BODY["historical"][0]]}
    assert len(fmp_prices._parse(body, "AAPL")) == 1


def test_a_malformed_row_costs_one_bar_not_the_fetch():
    body = {"historical": [{"date": "nonsense", "close": 1.0}, FMP_BODY["historical"][0]]}
    assert len(fmp_prices._parse(body, "AAPL")) == 1


def test_missing_ohl_falls_back_to_the_close():
    body = {"historical": [{"date": "2026-01-02", "close": 5.0}]}
    candle = fmp_prices._parse(body, "AAPL")[0]
    assert candle.open == candle.high == candle.low == 5.0


def test_adjusted_close_is_ignored():
    """Yahoo returns unadjusted closes. A series that silently switches between
    adjusted and unadjusted puts a step at every split, which every trend
    analyzer reads as a real move."""
    body = {"historical": [{"date": "2026-01-02", "close": 100.0, "adjClose": 25.0}]}
    assert fmp_prices._parse(body, "AAPL")[0].close == 100.0


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------


def test_fetch_without_a_key_is_a_clear_error(monkeypatch, tmp_path):
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="FMP_API_KEY"):
        fmp_prices.fetch_daily("AAPL", cache=Cache(LocalStore(tmp_path)))


def test_a_successful_fetch_caches_and_records_health(monkeypatch, tmp_path):
    monkeypatch.setenv("FMP_API_KEY", "k")
    monkeypatch.setattr(health, "DEFAULT_PATH", tmp_path / "h.json")
    monkeypatch.setattr(net, "get", lambda *a, **kw: Resp(FMP_BODY))

    cache = Cache(LocalStore(tmp_path))
    series = fmp_prices.fetch_daily("AAPL", days=5, cache=cache)
    assert len(series.candles) == 2
    assert cache.get_price("AAPL", "1d")[0] is not None
    assert "price.fmp" in health.load_health(tmp_path / "h.json").sources


def test_the_key_is_sent_as_a_query_parameter(monkeypatch, tmp_path):
    monkeypatch.setenv("FMP_API_KEY", "secret-key")
    seen = {}
    monkeypatch.setattr(net, "get", lambda url, **kw: seen.update(kw) or Resp(FMP_BODY))
    fmp_prices.fetch_daily("AAPL", cache=Cache(LocalStore(tmp_path)))
    assert seen["params"]["apikey"] == "secret-key"


def test_an_empty_result_raises_instead_of_caching_an_empty_series(monkeypatch, tmp_path):
    monkeypatch.setenv("FMP_API_KEY", "k")
    monkeypatch.setattr(health, "DEFAULT_PATH", tmp_path / "h.json")
    monkeypatch.setattr(net, "get", lambda *a, **kw: Resp({"historical": []}))
    with pytest.raises(ValueError, match="no usable bars"):
        fmp_prices.fetch_daily("AAPL", cache=Cache(LocalStore(tmp_path)))


def test_a_failure_is_recorded_in_health(monkeypatch, tmp_path):
    monkeypatch.setenv("FMP_API_KEY", "k")
    monkeypatch.setattr(health, "DEFAULT_PATH", tmp_path / "h.json")
    monkeypatch.setattr(net, "get", lambda *a, **kw: Resp({}, status_code=403))
    with pytest.raises(net.HTTPStatusError):
        fmp_prices.fetch_daily("AAPL", cache=Cache(LocalStore(tmp_path)))
    assert health.load_health(tmp_path / "h.json").sources["price.fmp"].consecutive_errors == 1
