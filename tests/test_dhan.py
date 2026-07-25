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

    monkeypatch.setattr(dhan_mod.net, "get", fake_get)

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
            return {
                "status": "success",
                "data": {
                    "optionChain": [
                        {
                            "strikePrice": 20000,
                            "CE": {"openInterest": 100},
                            "PE": {"openInterest": 200},
                        }
                    ]
                },
            }

    def fake_get(url: str, **kwargs: Any) -> FakeResponse:
        captured["url"] = url
        captured["params"] = kwargs.get("params", {})
        captured["headers"] = kwargs.get("headers", {})
        return FakeResponse()

    import alpha_engine.ingestion.dhan as dhan_mod

    monkeypatch.setattr(dhan_mod.net, "get", fake_get)

    client = DhanLiveClient.from_env()
    client.fetch_chain("NIFTY", "2026-07-30")

    assert captured["url"] == "https://api.dhan.co/v2/optionchain"
    assert captured["params"]["symbol"] == "NIFTY"
    assert captured["params"]["expiry"] == "2026-07-30"
    assert "Bearer test-token" in captured["headers"]["Authorization"]
