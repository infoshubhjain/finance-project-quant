"""Tests for the tool handlers themselves.

`toolkit.py` is the one table behind MCP-over-stdio, MCP-over-HTTP and the AI
terminal, so a handler bug reaches all three at once. Coverage sat at 60% with
the gap almost entirely in the error branches — which are the branches that run
on a fresh clone with an empty cache, i.e. every new user's first minute.

Everything here is cache-only and network-free: the handlers already default to
`no_refresh=True`, which is what makes that possible.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from alpha_engine import toolkit
from alpha_engine.cache.interface import Cache, LocalStore
from alpha_engine.cache.models import Candle, Interval, PriceSeries
from alpha_engine.cli import main as cli
from alpha_engine.schema.signal import Market


@pytest.fixture()
def empty_cache(monkeypatch, tmp_path):
    """A fresh clone: no cached data for anything."""
    store = LocalStore(tmp_path / "cache")
    monkeypatch.setattr(toolkit, "_cache", lambda: Cache(store))
    return store


@pytest.fixture()
def stocked_cache(monkeypatch, tmp_path):
    """Enough daily history for the analyzers to have something to say."""
    store = LocalStore(tmp_path / "cache")
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = [
        Candle(
            ts=t0 + timedelta(days=i),
            open=100 + i,
            high=101 + i,
            low=99 + i,
            close=100 + i,
            volume=1000 + i,
        )
        for i in range(200)
    ]
    store.write_price(PriceSeries(asset="BTC", interval=Interval.DAY, candles=candles))
    monkeypatch.setattr(toolkit, "_cache", lambda: Cache(store))
    return store


# --------------------------------------------------------------------------
# The empty-cache path — every new user's first minute
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool", ["scan", "report", "backtest", "options_backtest", "factors", "strategy_backtest"]
)
def test_no_cached_data_is_a_readable_error_not_a_crash(empty_cache, tool):
    args = {"asset": "BTC"}
    if tool == "strategy_backtest":
        args["strategy"] = "SMACrossover"
    payload = toolkit.call_tool(tool, args)
    assert "error" in payload, f"{tool} should report missing data"
    assert "no cached data" in payload["error"] or "no cached" in payload["error"]
    assert payload["disclaimer"], "even an error carries the disclaimer"


def test_the_no_data_error_says_how_to_fix_it(empty_cache):
    payload = toolkit.call_tool("report", {"asset": "BTC"})
    assert "scan BTC" in (payload.get("hint") or payload["error"])


def test_an_fno_scan_without_a_cached_chain_says_which_command_to_run(empty_cache):
    payload = toolkit.call_tool("scan", {"asset": "NIFTY"})
    assert "fetch-chain" in payload["error"]


# --------------------------------------------------------------------------
# Working handlers
# --------------------------------------------------------------------------


def test_scan_returns_a_signal(stocked_cache):
    payload = toolkit.call_tool("scan", {"asset": "BTC"})
    assert payload["asset"] == "BTC"
    assert payload["direction"] in {"bullish", "bearish", "neutral"}
    assert 0.0 <= payload["confidence"] <= 1.0


def test_scan_does_not_write_to_the_log_by_default(stocked_cache, monkeypatch):
    """The log is a track record, not a scratchpad."""
    written = {"n": 0}
    monkeypatch.setattr(
        "alpha_engine.validation.recorder.record_signal",
        lambda *a, **k: written.__setitem__("n", written["n"] + 1),
    )
    toolkit.call_tool("scan", {"asset": "BTC"})
    assert written["n"] == 0


def test_backtest_reports_its_own_sample_size(stocked_cache):
    payload = toolkit.call_tool("backtest", {"asset": "BTC", "step": 20})
    assert payload["bars"] == 200
    assert "signals_generated" in payload


def test_options_backtest_attaches_the_model_pricing_caveat(stocked_cache):
    """The option leg is Black-Scholes, not filled prices. Saying so is not
    optional — it is the difference between a measurement and a claim."""
    payload = toolkit.call_tool("options_backtest", {"asset": "BTC", "step": 25})
    assert "MODEL-PRICED" in payload["pricing_note"]


def test_factors_always_reports_the_noise_floor(stocked_cache):
    """A top-ranked factor without the noise floor is how backtests lie."""
    payload = toolkit.call_tool("factors", {"asset": "BTC", "top": 3})
    assert "noise_floor_ic" in payload
    assert "noise_floor_note" in payload
    assert len(payload["factors"]) <= 3


def test_an_unknown_factor_family_is_refused(stocked_cache):
    payload = toolkit.call_tool("factors", {"asset": "BTC", "family": "not_a_family"})
    assert "unknown factor family" in payload["error"]


def test_strategy_backtest_strips_the_per_bar_arrays_by_default(stocked_cache):
    """A chat transcript should not be flooded with 4,000 floats."""
    payload = toolkit.call_tool("strategy_backtest", {"asset": "BTC", "strategy": "SMACrossover"})
    assert "equity_curve" not in payload
    assert "series_note" in payload


def test_strategy_backtest_can_return_the_arrays_on_request(stocked_cache):
    payload = toolkit.call_tool(
        "strategy_backtest",
        {"asset": "BTC", "strategy": "SMACrossover", "include_series": True},
    )
    assert len(payload["equity_curve"]) == payload["bars"]


def test_an_unknown_strategy_lists_the_known_ones(stocked_cache):
    payload = toolkit.call_tool("strategy_backtest", {"asset": "BTC", "strategy": "Nope"})
    assert "SMACrossover" in payload["error"]


def test_bad_strategy_params_are_a_message_not_a_traceback(stocked_cache):
    payload = toolkit.call_tool(
        "strategy_backtest",
        {"asset": "BTC", "strategy": "SMACrossover", "params": {"nonsense": 1}},
    )
    assert "nonsense" in payload["error"]


def test_params_must_be_an_object(stocked_cache):
    payload = toolkit.call_tool(
        "strategy_backtest", {"asset": "BTC", "strategy": "SMACrossover", "params": "fast=5"}
    )
    assert "params must be an object" in payload["error"]


def test_trade_on_option_without_an_option_series_is_refused(stocked_cache):
    payload = toolkit.call_tool(
        "strategy_backtest",
        {"asset": "BTC", "strategy": "SMACrossover", "trade_on": "option"},
    )
    assert "error" in payload


def test_a_lookahead_violation_is_surfaced_as_a_warning(stocked_cache, monkeypatch):
    """If the metrics are void, the caller has to be told before reading them."""
    from alpha_engine.strategy import engine as strategy_engine

    real = strategy_engine.run_strategy_backtest

    def peeking(*args, **kwargs):
        report = real(*args, **kwargs)
        return report.model_copy(update={"lookahead_violations": [5, 10]})

    monkeypatch.setattr("alpha_engine.strategy.engine.run_strategy_backtest", peeking)
    payload = toolkit.call_tool("strategy_backtest", {"asset": "BTC", "strategy": "SMACrossover"})
    assert "LOOKAHEAD DETECTED" in payload["warning"]


# --------------------------------------------------------------------------
# Read-only tools
# --------------------------------------------------------------------------


def test_health_reports_per_source_state(monkeypatch, tmp_path):
    from alpha_engine import health

    monkeypatch.setattr(health, "DEFAULT_PATH", tmp_path / "h.json")
    health.record("price.yahoo", items=5, path=tmp_path / "h.json")
    payload = toolkit.call_tool("health", {})
    assert "sources" in payload and "checked_at" in payload


def test_record_stats_on_an_empty_log_says_so(monkeypatch):
    monkeypatch.setattr("alpha_engine.validation.recorder.read_records", lambda *a, **k: [])
    payload = toolkit.call_tool("record_stats", {})
    assert payload["records"] == 0


def test_list_strategies_warns_that_it_cannot_accept_code():
    payload = toolkit.call_tool("list_strategies", {})
    assert "remote code execution" in payload["note"]
    assert any(s["key"] == "SMACrossover" for s in payload["strategies"])


def test_a_missing_required_argument_is_named(empty_cache):
    payload = toolkit.call_tool("scan", {})
    assert "missing required argument" in payload["error"]
    assert "asset" in payload["error"]


# --------------------------------------------------------------------------
# Cache-first, hard
#
# `toolkit.py` calls this non-negotiable: "Every tool defaults to
# no_refresh=True. A public surface that refetches per call gets the host IP
# banned by CoinGecko in a day." It was not true. `_load_series` read
#
#     if series is None or ((stale or too_short) and not no_refresh):
#
# so an EMPTY cache fetched regardless of the flag — the miss branch never
# consulted it. Writing the tests above surfaced it: they reached the real
# CoinGecko and came back 429.
# --------------------------------------------------------------------------


def test_no_refresh_makes_no_network_call_on_a_cache_miss(monkeypatch, tmp_path):
    from alpha_engine import net

    def forbidden(*a, **kw):
        raise AssertionError("no_refresh=True must never touch the network")

    monkeypatch.setattr(net, "get", forbidden)
    monkeypatch.setattr(net, "get_with_retry", forbidden)
    monkeypatch.setattr(net, "post", forbidden)

    series = cli._load_series("BTC", Market.CRYPTO, 90, True, Cache(LocalStore(tmp_path / "cache")))
    assert series.candles == [], "an empty cache must yield an empty series, not a fetch"


@pytest.mark.parametrize(
    "tool", ["scan", "report", "backtest", "options_backtest", "factors", "strategy_backtest"]
)
def test_every_tool_is_network_free_on_an_empty_cache(monkeypatch, tmp_path, tool):
    """The property that makes this API safe to expose at all."""
    from alpha_engine import net

    def forbidden(*a, **kw):
        raise AssertionError(f"{tool} reached the network with an empty cache")

    monkeypatch.setattr(net, "get", forbidden)
    monkeypatch.setattr(net, "get_with_retry", forbidden)
    monkeypatch.setattr(net, "post", forbidden)
    monkeypatch.setattr(toolkit, "_cache", lambda: Cache(LocalStore(tmp_path / "cache")))

    args = {"asset": "BTC"}
    if tool == "strategy_backtest":
        args["strategy"] = "SMACrossover"
    payload = toolkit.call_tool(tool, args)
    assert "error" in payload


def test_without_no_refresh_a_miss_still_fetches(monkeypatch, tmp_path):
    """The fix must not break the CLI, where fetching on a miss is the point."""
    called = {"n": 0}

    def fake_fetch(asset, days, cache):
        called["n"] += 1
        return PriceSeries(asset=asset, interval=Interval.DAY, candles=[])

    monkeypatch.setattr(cli, "_fetch_crypto_daily", fake_fetch)
    cli._load_series("BTC", Market.CRYPTO, 90, False, Cache(LocalStore(tmp_path / "cache")))
    assert called["n"] == 1
