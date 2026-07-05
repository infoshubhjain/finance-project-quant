"""Tests for the Angel One ingestion adapter.

These tests mock the HTTP layer so no real API key or network is needed.
The point is to verify the payload parsing logic and credential handling.

Key properties:
- Missing credentials raise BrokerNotConfiguredError.
- Various response shapes are normalized into OptionsChain.
- The expiry date normalization works for common formats.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from alpha_engine.ingestion.angelone import (
    AngelOneLiveClient,
    _get_with_retry,
    _normalize_expiry,
    _parse_option_chain,
)
from alpha_engine.ingestion.indian_broker import BrokerNotConfiguredError


EXPIRY = datetime(2026, 7, 30, tzinfo=timezone.utc)


# --- expiry normalization ------------------------------------------------------


def test_normalize_expiry_yyyy_mm_dd():
    assert _normalize_expiry("2026-07-30") == "30JUL2026"


def test_normalize_expiry_dd_mm_yyyy():
    assert _normalize_expiry("30-07-2026") == "30JUL2026"


def test_normalize_expiry_dd_mmmyyyy_passthrough():
    assert _normalize_expiry("30JUL2026") == "30JUL2026"


def test_normalize_expiry_dd_slash_mm_slash_yyyy():
    assert _normalize_expiry("30/07/2026") == "30JUL2026"


def test_normalize_expiry_unknown_format_passthrough():
    assert _normalize_expiry("SOMETHING") == "SOMETHING"


# --- payload parsing -----------------------------------------------------------


def test_parse_option_chain_aggregated_format():
    raw = {
        "data": {
            "gregated": [
                {
                    "strikePrice": 20000,
                    "CE": {"openInterest": 1500, "changeinOpenInterest": 200},
                    "PE": {"openInterest": 3000, "changeinOpenInterest": 800},
                },
                {
                    "strikePrice": 20500,
                    "CE": {"openInterest": 1000, "changeinOpenInterest": 100},
                    "PE": {"openInterest": 500, "changeinOpenInterest": 50},
                },
            ]
        }
    }
    result = _parse_option_chain(raw, "NIFTY")
    assert result["underlying"] == "NIFTY"
    assert len(result["records"]) == 2
    assert result["records"][0]["strikePrice"] == 20000
    assert result["records"][0]["CE"]["openInterest"] == 1500
    assert result["records"][0]["PE"]["openInterest"] == 3000


def test_parse_option_chain_records_format():
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


def test_parse_option_chain_ce_pe_split():
    raw = {
        "data": {
            "strike_ce": [
                {"strikePrice": 20000, "openInterest": 1000},
                {"strikePrice": 20500, "openInterest": 800},
            ],
            "strike_pe": [
                {"strikePrice": 20000, "openInterest": 2000},
                {"strikePrice": 20500, "openInterest": 1500},
            ],
        }
    }
    result = _parse_option_chain(raw, "NIFTY")
    assert len(result["records"]) == 2
    assert result["records"][0]["CE"]["openInterest"] == 1000
    assert result["records"][0]["PE"]["openInterest"] == 2000
    assert result["records"][1]["strikePrice"] == 20500


def test_parse_option_chain_flat_list():
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


# --- credential handling -------------------------------------------------------


def test_missing_access_token_raises(monkeypatch):
    monkeypatch.setenv("ANGEL_ONE_API_KEY", "test-key")
    monkeypatch.setenv("ANGEL_ONE_CLIENT_ID", "test-client")
    monkeypatch.delenv("ANGEL_ONE_ACCESS_TOKEN", raising=False)

    with pytest.raises(BrokerNotConfiguredError, match="ANGEL_ONE_ACCESS_TOKEN"):
        AngelOneLiveClient.from_env()


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("ANGEL_ONE_API_KEY", raising=False)
    with pytest.raises(BrokerNotConfiguredError, match="ANGEL_ONE_API_KEY"):
        AngelOneLiveClient.from_env()


# --- fetch_chain integration (mocked) ------------------------------------------


def test_fetch_chain_normalizes_response(monkeypatch):
    monkeypatch.setenv("ANGEL_ONE_API_KEY", "test-key")
    monkeypatch.setenv("ANGEL_ONE_CLIENT_ID", "test-client")
    monkeypatch.setenv("ANGEL_ONE_ACCESS_TOKEN", "test-jwt")

    api_response = {
        "data": {
            "gregated": [
                {
                    "strikePrice": 20000,
                    "CE": {"openInterest": 1500, "changeinOpenInterest": 200},
                    "PE": {"openInterest": 3000, "changeinOpenInterest": 800},
                }
            ]
        }
    }

    class FakeResponse:
        status_code = 200
        def raise_for_status(self) -> None:
            pass
        def json(self) -> dict:
            return api_response

    def fake_get(url: str, **kwargs: Any) -> FakeResponse:
        return FakeResponse()

    import alpha_engine.ingestion.angelone as angelone_mod
    monkeypatch.setattr(angelone_mod.requests, "get", fake_get)

    client = AngelOneLiveClient.from_env()
    chain = client.fetch_chain("NIFTY", "2026-07-30")

    assert chain.underlying == "NIFTY"
    assert len(chain.quotes) == 2  # 1 CE + 1 PE
    assert chain.spot is None  # Angel One doesn't return spot in the chain response


def test_fetch_chain_calls_correct_url(monkeypatch):
    monkeypatch.setenv("ANGEL_ONE_API_KEY", "my-api-key")
    monkeypatch.setenv("ANGEL_ONE_CLIENT_ID", "client-123")
    monkeypatch.setenv("ANGEL_ONE_ACCESS_TOKEN", "jwt-token")

    captured: dict[str, Any] = {}

    class FakeResponse:
        status_code = 200
        def raise_for_status(self) -> None:
            pass
        def json(self) -> dict:
            return {"data": {"expiry": "30JUL2026", "records": [
                {"strikePrice": 20000, "CE": {"openInterest": 100}, "PE": {"openInterest": 200}}
            ]}}

    def fake_get(url: str, **kwargs: Any) -> FakeResponse:
        captured["url"] = url
        captured["params"] = kwargs.get("params", {})
        captured["headers"] = kwargs.get("headers", {})
        return FakeResponse()

    import alpha_engine.ingestion.angelone as angelone_mod
    monkeypatch.setattr(angelone_mod.requests, "get", fake_get)

    client = AngelOneLiveClient.from_env()
    client.fetch_chain("NIFTY", "2026-07-30")

    assert captured["url"] == "https://apiconnect.angelone.in/smartapi/optionchain"
    assert captured["params"]["exchange"] == "NFO"
    assert captured["params"]["tradingsymbol"] == "NIFTY"
    assert "Bearer jwt-token" in captured["headers"]["Authorization"]


# --- rate-limit retry ------------------------------------------------------------


class _StubResponse:
    def __init__(self, status_code: int, headers: dict[str, str] | None = None):
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return {"data": {"records": []}}


def _patch_sleep(monkeypatch) -> list[float]:
    """Replace time.sleep with a recorder so retry tests run instantly."""
    waits: list[float] = []
    import alpha_engine.ingestion.angelone as angelone_mod

    monkeypatch.setattr(angelone_mod.time, "sleep", waits.append)
    return waits


def test_retry_recovers_after_429(monkeypatch):
    waits = _patch_sleep(monkeypatch)
    responses = [_StubResponse(429), _StubResponse(429), _StubResponse(200)]

    import alpha_engine.ingestion.angelone as angelone_mod
    monkeypatch.setattr(
        angelone_mod.requests, "get", lambda url, **kw: responses.pop(0)
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

    import alpha_engine.ingestion.angelone as angelone_mod
    monkeypatch.setattr(angelone_mod.requests, "get", always_429)

    resp = _get_with_retry("http://x", params={}, headers={})
    assert resp.status_code == 429  # last response returned so caller sees the error
    assert calls["n"] == 4  # 1 initial + 3 retries
    assert waits == [2.0, 4.0, 8.0]


def test_retry_honors_retry_after_header(monkeypatch):
    waits = _patch_sleep(monkeypatch)
    responses = [_StubResponse(429, headers={"Retry-After": "7"}), _StubResponse(200)]

    import alpha_engine.ingestion.angelone as angelone_mod
    monkeypatch.setattr(
        angelone_mod.requests, "get", lambda url, **kw: responses.pop(0)
    )

    resp = _get_with_retry("http://x", params={}, headers={})
    assert resp.status_code == 200
    assert waits == [7.0]


def test_retry_does_not_retry_client_errors(monkeypatch):
    waits = _patch_sleep(monkeypatch)

    import alpha_engine.ingestion.angelone as angelone_mod
    monkeypatch.setattr(
        angelone_mod.requests, "get", lambda url, **kw: _StubResponse(401)
    )

    resp = _get_with_retry("http://x", params={}, headers={})
    assert resp.status_code == 401  # bad credentials should fail fast, not retry
    assert waits == []
