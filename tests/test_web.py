"""Tests for the web dashboard server and the per-asset history service.

The HTTP tests boot the real ThreadingHTTPServer on an ephemeral port so the
routing, static-file safety, and JSON endpoints are exercised end to end —
no real market data or network beyond localhost is involved.

Key properties:
- The static frontend is served at / and under /static/.
- Path traversal out of web/static/ is refused.
- /api/asset/<SYMBOL> returns the recorded history; bad symbols get HTTP 400.
- build_asset_history scores records against cached prices when available.
"""

from __future__ import annotations

import json
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError

import pytest

from alpha_engine.cache.interface import Cache, LocalStore
from alpha_engine.cache.models import Candle, Interval, PriceSeries
from alpha_engine.dashboard.service import build_asset_history
from alpha_engine.schema.signal import Direction, Market, Signal, SignalSource, Timeframe
from alpha_engine.validation.recorder import record_signal
from web.server import DashboardHandler

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _signal(asset: str = "BTC", confidence: float = 0.7) -> Signal:
    return Signal(
        asset=asset,
        market=Market.CRYPTO,
        direction=Direction.BULLISH,
        confidence=confidence,
        timeframe=Timeframe.SWING,
        signal_sources=[SignalSource(name="t", direction=Direction.BULLISH, weight=0.6, detail="")],
        invalidation_level=None,
        thesis="",
        timestamp=T0,
    )


def _series(asset: str, closes: list[float]) -> PriceSeries:
    candles = [
        Candle(ts=T0 + timedelta(days=i), open=c, high=c, low=c, close=c)
        for i, c in enumerate(closes)
    ]
    return PriceSeries(asset=asset, interval=Interval.DAY, candles=candles)


# --- build_asset_history ---------------------------------------------------------


def test_asset_history_empty(tmp_path):
    cache = Cache(store=LocalStore(root=tmp_path / "cache"))
    payload = build_asset_history("BTC", records_root=tmp_path / "signals", cache=cache)
    assert payload == {"asset": "BTC", "count": 0, "history": []}


def test_asset_history_filters_and_sorts(tmp_path):
    root = tmp_path / "signals"
    record_signal(_signal("BTC", confidence=0.6), entry_price=100, root=root)
    record_signal(_signal("ETH"), entry_price=2000, root=root)
    record_signal(_signal("BTC", confidence=0.8), entry_price=110, root=root)

    cache = Cache(store=LocalStore(root=tmp_path / "cache"))
    payload = build_asset_history("btc", records_root=root, cache=cache)

    assert payload["asset"] == "BTC"  # symbol is upcased
    assert payload["count"] == 2  # ETH excluded
    recorded = [r["recorded_at"] for r in payload["history"]]
    assert recorded == sorted(recorded, reverse=True)  # newest first
    # no cached prices for BTC -> no outcome scoring
    assert all(r["outcome"] is None for r in payload["history"])


def test_asset_history_scores_against_cached_prices(tmp_path):
    root = tmp_path / "signals"
    record_signal(_signal("BTC"), entry_price=100, root=root)

    cache = Cache(store=LocalStore(root=tmp_path / "cache"))
    cache.store.write_price(_series("BTC", [100.0] * 3 + [120.0] * 10))

    payload = build_asset_history("BTC", records_root=root, cache=cache)
    outcome = payload["history"][0]["outcome"]
    assert outcome is not None
    assert "status" in outcome and "realized_return" in outcome


# --- HTTP server -----------------------------------------------------------------


@pytest.fixture()
def server_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _get(url: str) -> tuple[int, bytes, str]:
    with urllib.request.urlopen(url) as resp:  # noqa: S310 - localhost test server
        return resp.status, resp.read(), resp.headers.get("Content-Type", "")


def test_index_served_from_static(server_url):
    status, body, ctype = _get(server_url + "/")
    assert status == 200
    assert "text/html" in ctype
    assert b"Alpha Engine" in body


def test_static_assets_served(server_url):
    status, body, ctype = _get(server_url + "/static/app.js")
    assert status == 200
    assert "javascript" in ctype
    assert b"api/dashboard" in body


def test_path_traversal_refused(server_url):
    with pytest.raises(HTTPError) as excinfo:
        _get(server_url + "/static/%2e%2e/server.py")
    assert excinfo.value.code == 404


def test_unknown_path_404(server_url):
    with pytest.raises(HTTPError) as excinfo:
        _get(server_url + "/definitely-not-here")
    assert excinfo.value.code == 404


def test_api_dashboard_returns_json(server_url):
    status, body, ctype = _get(server_url + "/api/dashboard")
    assert status == 200
    assert "application/json" in ctype
    payload = json.loads(body)
    assert "total_records" in payload
    assert "latest_signals" in payload


def test_api_asset_returns_json(server_url):
    status, body, _ctype = _get(server_url + "/api/asset/BTC")
    assert status == 200
    payload = json.loads(body)
    assert payload["asset"] == "BTC"
    assert isinstance(payload["history"], list)


def test_api_asset_rejects_bad_symbol(server_url):
    with pytest.raises(HTTPError) as excinfo:
        _get(server_url + "/api/asset/" + urllib.parse.quote("../etc/passwd", safe=""))
    assert excinfo.value.code == 400
