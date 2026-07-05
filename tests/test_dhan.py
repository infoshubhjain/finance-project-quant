"""Tests for the Dhan ingestion adapter.

These tests mock the HTTP layer so no real API key or network is needed.
The point is to verify the payload parsing logic and credential handling.

Key properties:
- Missing credentials raise BrokerNotConfiguredError.
- Various response shapes are normalized into OptionsChain.
- The adapter follows the same pattern as Breeze and Angel One.
"""

from __future__ import annotations

from typing import Any

import pytest

from alpha_engine.ingestion.dhan import (
    DhanLiveClient,
    _get_with_retry,
    _parse_option_chain,
)
from alpha_engine.ingestion.indian_broker import BrokerNotConfiguredError


# --- payload parsing -----------------------------------------------------------


def test_parse_option_chain_dhan_format():
    """Dhan's typical response with optionChain nested under data."""
    raw = {
        "status": "success",
        "data": {
            "spotPrice": 24500.0,
            "optionChain": [
                {
                    "strikePrice": 24000,
                    "CE": {"openInterest": 1500, "changeinOpenInterest": 200, "lastPrice": 450},
                    "PE": {"openInterest": 3000, "changeinOpenInterest": 800, "lastPrice": 120},
                },
                {
                    "strikePrice": 24500,
                    "CE": {"openInterest": 1000, "changeinOpenInterest": 100, "lastPrice": 200},
                    "PE": {"openInterest": 500, "changeinOpenInterest": 50, "lastPrice": 250},
                },
            ],
        },
    }
    result = _parse_option_chain(raw, "NIFTY")
    assert result["underlying"] == "NIFTY"
    assert len(result["records"]) == 2
    assert result["records"][0]["strikePrice"] == 24000
    assert result["records"][0]["CE"]["openInterest"] == 1500
    assert result["records"][0]["PE"]["openInterest"] == 3000
    assert result["spot"] == 24500.0


def test_parse_option_chain_records_format():
    """Fallback to records format."""
    raw = {
        "data": {
            "records": [
                {
                    "strikePrice": 20000,
                    "CE": {"openInterest": 1500},
                    "PE": {"openInterest": 3000},
                }
            ]
        }
    }
    result = _parse_option_chain(raw, "NIFTY")
    assert len(result["records"]) == 1
    assert result["records"][0]["strikePrice"] == 20000


def test_parse_option_chain_flat_list():
    """Flat list format."""
    raw = [
        {"strikePrice": 20000, "CE": {"openInterest": 1000}, "PE": {"openInterest": 2000}},
        {"strikePrice": 20500, "CE": {"openInterest": 800}},
    ]
    result = _parse_option_chain(raw, "NIFTY")
    assert len(result["records"]) == 2
    assert result["underlying"] == "NIFTY"


def test_parse_option_chain_empty_data():
    raw = {"data": {}}
    result = _parse_option_chain(raw, "NIFTY")
    assert result["records"] == []


def test_parse_option_chain_lowercase_ce_pe():
    """Handle lowercase ce/pe keys."""
    raw = [
        {"strikePrice": 20000, "ce": {"openInterest": 1000}, "pe": {"openInterest": 2000}},
    ]
    result = _parse_option_chain(raw, "NIFTY")
    assert result["records"][0]["CE"]["openInterest"] == 1000
    assert result["records"][0]["PE"]["openInterest"] == 2000


# --- credential handling -------------------------------------------------------


def test_missing_client_id_raises(monkeypatch):
    monkeypatch.delenv("DHAN_CLIENT_ID", raising=False)
    monkeypatch.delenv("DHAN_ACCESS_TOKEN", raising=False)
    with pytest.raises(BrokerNotConfiguredError, match="DHAN_CLIENT_ID"):
        DhanLiveClient.from_env()


def test_missing_access_token_raises(monkeypatch):
    monkeypatch.setenv("DHAN_CLIENT_ID", "12345")
    monkeypatch.delenv("DHAN_ACCESS_TOKEN", raising=False)
    with pytest.raises(BrokerNotConfiguredError, match="DHAN_ACCESS_TOKEN"):
        DhanLiveClient.from_env()


# --- fetch_chain integration (mocked) ------------------------------------------


def test_fetch_chain_normalizes_response(monkeypatch):
    monkeypatch.setenv("DHAN_CLIENT_ID", "12345")
    monkeypatch.setenv("DHAN_ACCESS_TOKEN", "test-token")

    api_response = {
        "status": "success",
        "data": {
            "spotPrice": 24500.0,
            "optionChain": [
                {
                    "strikePrice": 24000,
                    "CE": {"openInterest": 1500, "changeinOpenInterest": 200},
                    "PE": {"openInterest": 3000, "changeinOpenInterest": 800},
                }
            ],
        },
    }

    class FakeResponse:
        status_code = 200
        def raise_for_status(self) -> None:
            pass
        def json(self) -> dict:
            return api_response

    def fake_get(url: str, **kwargs: Any) -> FakeResponse:
        return FakeResponse()

    import alpha_engine.ingestion.dhan as dhan_mod
    monkeypatch.setattr(dhan_mod.requests, "get", fake_get)

    client = DhanLiveClient.from_env()
    chain = client.fetch_chain("NIFTY", "2026-07-30")

    assert chain.underlying == "NIFTY"
    assert len(chain.quotes) == 2  # 1 CE + 1 PE


def test_fetch_chain_calls_correct_url(monkeypatch):
    monkeypatch.setenv("DHAN_CLIENT_ID", "12345")
    monkeypatch.setenv("DHAN_ACCESS_TOKEN", "test-token")

    captured: dict[str, Any] = {}

    class FakeResponse:
        status_code = 200
        def raise_for_status(self) -> None:
            pass
        def json(self) -> dict:
            return {"status": "success", "data": {"optionChain": [
                {"strikePrice": 20000, "CE": {"openInterest": 100}, "PE": {"openInterest": 200}}
            ]}}

    def fake_get(url: str, **kwargs: Any) -> FakeResponse:
        captured["url"] = url
        captured["params"] = kwargs.get("params", {})
        captured["headers"] = kwargs.get("headers", {})
        return FakeResponse()

    import alpha_engine.ingestion.dhan as dhan_mod
    monkeypatch.setattr(dhan_mod.requests, "get", fake_get)

    client = DhanLiveClient.from_env()
    client.fetch_chain("NIFTY", "2026-07-30")

    assert captured["url"] == "https://api.dhan.co/v2/optionchain"
    assert captured["params"]["symbol"] == "NIFTY"
    assert captured["params"]["expiry"] == "2026-07-30"
    assert "Bearer test-token" in captured["headers"]["Authorization"]


# --- rate-limit retry ------------------------------------------------------------


class _StubResponse:
    def __init__(self, status_code: int, headers: dict[str, str] | None = None):
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return {"status": "success", "data": {"optionChain": []}}


def _patch_sleep(monkeypatch) -> list[float]:
    """Replace time.sleep with a recorder so retry tests run instantly."""
    waits: list[float] = []
    import alpha_engine.ingestion.dhan as dhan_mod
    monkeypatch.setattr(dhan_mod.time, "sleep", waits.append)
    return waits


def test_retry_recovers_after_429(monkeypatch):
    waits = _patch_sleep(monkeypatch)
    responses = [_StubResponse(429), _StubResponse(429), _StubResponse(200)]

    import alpha_engine.ingestion.dhan as dhan_mod
    monkeypatch.setattr(
        dhan_mod.requests, "get", lambda url, **kw: responses.pop(0)
    )

    resp = _get_with_retry("http://x", params={}, headers={})
    assert resp.status_code == 200
    assert waits == [2.0, 4.0]  # exponential backoff between attempts


def test_retry_gives_up_after_max_attempts(monkeypatch):
    waits = _patch_sleep(monkeypatch)
    calls = {"n": 0}

    def always_429(url: str, **kw: Any) -> _StubResponse:
        calls["n"] += 1
        return _StubResponse(429)

    import alpha_engine.ingestion.dhan as dhan_mod
    monkeypatch.setattr(dhan_mod.requests, "get", always_429)

    resp = _get_with_retry("http://x", params={}, headers={})
    assert resp.status_code == 429  # last response returned so caller sees the error
    assert calls["n"] == 4  # 1 initial + 3 retries
    assert waits == [2.0, 4.0, 8.0]


def test_retry_honors_retry_after_header(monkeypatch):
    waits = _patch_sleep(monkeypatch)
    responses = [_StubResponse(429, headers={"Retry-After": "7"}), _StubResponse(200)]

    import alpha_engine.ingestion.dhan as dhan_mod
    monkeypatch.setattr(
        dhan_mod.requests, "get", lambda url, **kw: responses.pop(0)
    )

    resp = _get_with_retry("http://x", params={}, headers={})
    assert resp.status_code == 200
    assert waits == [7.0]


def test_retry_does_not_retry_client_errors(monkeypatch):
    waits = _patch_sleep(monkeypatch)

    import alpha_engine.ingestion.dhan as dhan_mod
    monkeypatch.setattr(
        dhan_mod.requests, "get", lambda url, **kw: _StubResponse(401)
    )

    resp = _get_with_retry("http://x", params={}, headers={})
    assert resp.status_code == 401  # bad credentials should fail fast, not retry
    assert waits == []
