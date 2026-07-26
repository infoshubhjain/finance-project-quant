"""Tests for the on-chain failover that the CI outage exposed.

Background, because these tests only make sense with it: `binance_futures`
answers **HTTP 451** to every request from a datacenter IP. The scheduled scan
runs on a GitHub Actions runner, so for a month it fetched zero futures
positioning data while the workflow reported success — because health was
recorded per *kind* (`onchain`) and CoinGecko's single dominance reading kept the
aggregate at "1 item, ok".

Four things are pinned here, each mapping to one part of that failure:

1. Bybit parses correctly and reports errors that arrive with HTTP 200.
2. The failover triggers, and latches instead of re-probing per asset.
3. Two sources never blend into one series (the units differ).
4. Health is recorded per feed, so a dead feed cannot hide behind a live one.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from alpha_engine import health
from alpha_engine.analyzers.crypto_onchain import analyze_onchain
from alpha_engine.cache.interface import Cache, LocalStore
from alpha_engine.cache.models import OnChainObservation
from alpha_engine.ingestion import binance_futures, bybit_futures
from alpha_engine.orchestrator import engine

T0 = datetime(2026, 7, 1, tzinfo=timezone.utc)


class FakeResp:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


def _ok(rows):
    return FakeResp({"retCode": 0, "retMsg": "OK", "result": {"list": rows}})


# --------------------------------------------------------------------------
# 1. The Bybit adapter
# --------------------------------------------------------------------------


def test_bybit_parses_funding_rows(monkeypatch, tmp_path):
    rows = [
        {
            "symbol": "BTCUSDT",
            "fundingRate": "-0.00001212",
            "fundingRateTimestamp": "1785024000000",
        },
        {"symbol": "BTCUSDT", "fundingRate": "0.00004668", "fundingRateTimestamp": "1784995200000"},
    ]
    monkeypatch.setattr(bybit_futures.net, "get", lambda *a, **kw: _ok(rows))
    obs = bybit_futures.fetch_funding_rate("BTC", cache=Cache(LocalStore(tmp_path)))
    assert len(obs) == 2
    assert obs[0].metric == "funding_rate_BTC"
    assert obs[0].value == pytest.approx(-0.00001212)
    assert obs[0].source == "bybit_futures"


def test_bybit_open_interest_is_base_coin(monkeypatch, tmp_path):
    """Must match binance_futures' unit — see test_onchain.py for the other half."""
    rows = [{"openInterest": "57875.553", "timestamp": "1785024000000"}]
    monkeypatch.setattr(bybit_futures.net, "get", lambda *a, **kw: _ok(rows))
    obs = bybit_futures.fetch_open_interest("BTC", cache=Cache(LocalStore(tmp_path)))
    assert obs[0].value == pytest.approx(57875.553)


def test_bybit_reports_an_application_error_sent_with_http_200(monkeypatch, tmp_path):
    """Bybit signals 'invalid symbol' as retCode!=0 with HTTP 200. Trusting the
    status alone would cache an empty series as though the market went quiet."""
    monkeypatch.setattr(
        bybit_futures.net,
        "get",
        lambda *a, **kw: FakeResp({"retCode": 10001, "retMsg": "params error", "result": {}}),
    )
    assert bybit_futures.fetch_funding_rate("BTC", cache=Cache(LocalStore(tmp_path))) == []


def test_bybit_survives_a_malformed_body(monkeypatch, tmp_path):
    monkeypatch.setattr(
        bybit_futures.net, "get", lambda *a, **kw: FakeResp(["not", "an", "object"])
    )
    assert bybit_futures.fetch_open_interest("BTC", cache=Cache(LocalStore(tmp_path))) == []


def test_bybit_skips_malformed_rows_without_losing_the_good_ones(monkeypatch, tmp_path):
    rows = [
        {"fundingRate": "0.0001", "fundingRateTimestamp": "1785024000000"},
        {"fundingRate": None, "fundingRateTimestamp": "garbage"},
    ]
    monkeypatch.setattr(bybit_futures.net, "get", lambda *a, **kw: _ok(rows))
    assert len(bybit_futures.fetch_funding_rate("BTC", cache=Cache(LocalStore(tmp_path)))) == 1


def test_bybit_and_binance_cover_the_same_assets():
    """The fallback is useless if it supports fewer symbols than the primary."""
    for asset in ("BTC", "ETH", "SOL"):
        assert binance_futures.supports(asset)
        assert bybit_futures.supports(asset)
    assert not bybit_futures.supports("DOGE")


def test_bybit_http_error_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(bybit_futures.net, "get", lambda *a, **kw: FakeResp({}, status_code=503))
    assert bybit_futures.fetch_funding_rate("BTC", cache=Cache(LocalStore(tmp_path))) == []


# --------------------------------------------------------------------------
# 2. Failover behaviour
# --------------------------------------------------------------------------


def _refresh_onchain(monkeypatch, tmp_path, binance_returns, bybit_returns):
    """Run just the onchain leg of refresh_context with both adapters stubbed."""
    calls: dict[str, list[str]] = {"binance": [], "bybit": []}

    def fake_binance(asset, cache=None):
        calls["binance"].append(asset)
        return binance_returns

    def fake_bybit(asset, cache=None):
        calls["bybit"].append(asset)
        return bybit_returns

    monkeypatch.setattr(binance_futures, "fetch_all", fake_binance)
    monkeypatch.setattr(bybit_futures, "fetch_all", fake_bybit)
    monkeypatch.setattr(
        "alpha_engine.ingestion.coingecko.fetch_btc_dominance", lambda cache=None: 54.2
    )

    report = engine.refresh_context(
        Cache(LocalStore(tmp_path)), ("BTC", "ETH", "SOL"), kinds={"onchain"}
    )
    return report, calls


def test_binance_working_means_bybit_is_never_called(monkeypatch, tmp_path):
    obs = [OnChainObservation(metric="funding_rate_BTC", ts=T0, value=0.0001, source="binance")]
    report, calls = _refresh_onchain(monkeypatch, tmp_path, obs, [])

    assert calls["binance"] == ["BTC", "ETH", "SOL"]
    assert calls["bybit"] == []
    assert report.item_counts["onchain.binance_futures"] == 3
    # An untried fallback must not be recorded, or it ages into a false alarm.
    assert "onchain.bybit_futures" not in report.item_counts


def test_binance_geoblocked_falls_over_to_bybit(monkeypatch, tmp_path):
    """The CI case: Binance returns nothing for everything."""
    obs = [OnChainObservation(metric="funding_rate_BTC", ts=T0, value=0.0001, source="bybit")]
    report, calls = _refresh_onchain(monkeypatch, tmp_path, [], obs)

    assert calls["bybit"] == ["BTC", "ETH", "SOL"]
    assert report.item_counts["onchain.bybit_futures"] == 3
    assert report.item_counts["onchain.binance_futures"] == 0


def test_the_failover_latches_instead_of_reprobing_every_asset(monkeypatch, tmp_path):
    """A geo-block is a property of the host, not the symbol. Re-probing would
    spend one doomed request per asset per run to relearn the same fact."""
    _report, calls = _refresh_onchain(monkeypatch, tmp_path, [], [])
    assert calls["binance"] == ["BTC"], "Binance should be probed once, then dropped"


# --------------------------------------------------------------------------
# 3. Two sources must never blend into one series
# --------------------------------------------------------------------------


def _oi(source: str, values: list[float], start: datetime) -> list[OnChainObservation]:
    return [
        OnChainObservation(
            metric="open_interest_BTC", ts=start + timedelta(days=i), value=v, source=source
        )
        for i, v in enumerate(values)
    ]


def _funding(value: float = 0.0001) -> list[OnChainObservation]:
    """Open interest only ever *confirms* a vote, so it is invisible without one.
    This supplies the vote so the OI note is actually rendered."""
    return [
        OnChainObservation(metric="funding_rate_BTC", ts=T0, value=value, source="bybit_futures")
    ]


def test_analyzer_uses_only_the_freshest_source_for_a_metric():
    """Binance historically wrote USD notional, Bybit writes base coin. Blended,
    the switchover looks like a 100,000x open-interest build-up."""
    old = _oi("binance_futures", [6_900_000_000.0, 6_950_000_000.0], T0)
    new = _oi("bybit_futures", [57_875.0, 57_900.0], T0 + timedelta(days=2))

    source = analyze_onchain(_funding() + old + new, asset="BTC")
    # Read off the fresh source alone: 57900/57875 - 1 ≈ +0.0%, not +100000%.
    assert "oi=+0.0%" in source.detail


def test_blending_two_units_would_have_faked_a_buildup():
    """Guard the guard: prove the blended series really is catastrophic, so the
    test above is testing something real rather than passing vacuously."""
    old = _oi("binance_futures", [6_900_000_000.0], T0)
    new = _oi("bybit_futures", [57_875.0], T0 + timedelta(days=1))
    blended = sorted(old + new, key=lambda o: o.ts)
    values = [o.value for o in blended]
    assert values[-1] / values[0] - 1.0 < -0.99  # a fabricated ~100% collapse


def test_a_single_source_series_is_untouched():
    only = _oi("bybit_futures", [100.0, 120.0], T0)
    source = analyze_onchain(_funding() + only, asset="BTC")
    assert "oi=+20.0%" in source.detail


def test_no_positioning_data_still_returns_a_zero_weight_neutral():
    source = analyze_onchain([], asset="BTC")
    assert source.weight == 0.0
    assert source.detail == "no positioning data"


# --------------------------------------------------------------------------
# 4. Per-feed health
# --------------------------------------------------------------------------


def test_health_is_recorded_per_feed_not_only_per_kind(monkeypatch, tmp_path):
    """The bug that hid the outage: one live feed kept the aggregate green."""
    health_file = tmp_path / "health.json"
    monkeypatch.setattr(health, "DEFAULT_PATH", health_file)

    obs = [OnChainObservation(metric="funding_rate_BTC", ts=T0, value=0.0001, source="bybit")]
    _refresh_onchain(monkeypatch, tmp_path, [], obs)

    recorded = health.load_health(health_file).sources
    assert "onchain" in recorded, "the aggregate is still recorded"
    assert "onchain.binance_futures" in recorded, "the dead feed must be visible on its own"
    assert "onchain.bybit_futures" in recorded
    assert "onchain.coingecko_dominance" in recorded
    # The dead feed reports zero even though the kind's total is healthy.
    assert recorded["onchain.binance_futures"].total_items == 0
    assert recorded["onchain"].total_items > 0


def test_a_fetcher_returning_a_plain_int_still_records_its_kind(monkeypatch, tmp_path):
    """News records its own per-feed health inside rss.py, so it returns a total
    and must keep working."""
    health_file = tmp_path / "health.json"
    monkeypatch.setattr(health, "DEFAULT_PATH", health_file)
    monkeypatch.setattr("alpha_engine.ingestion.rss.fetch_all", lambda cache=None: [])

    report = engine.refresh_context(Cache(LocalStore(tmp_path)), ("BTC",), kinds={"news"})
    assert report.item_counts["news"] == 0
    assert "news" in health.load_health(health_file).sources
