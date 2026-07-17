"""Tests for the Indian broker adapter scaffold."""

from __future__ import annotations

import pytest

from alpha_engine.ingestion.indian_broker import (
    BrokerNotConfiguredError,
    IndianBroker,
    load_broker_credentials,
)
from alpha_engine.ingestion.indian_fno import parse_indian_chain_payload


def test_parse_indian_chain_payload_uses_shared_chain_shape():
    payload = {
        "underlying": "NIFTY",
        "expiry": "2026-07-30T00:00:00Z",
        "records": [{"strikePrice": 20000, "CE": {"openInterest": 1000}}],
    }
    chain = parse_indian_chain_payload(payload)
    assert chain.underlying == "NIFTY"
    assert len(chain.quotes) == 1


def test_load_broker_credentials_requires_env(monkeypatch):
    monkeypatch.delenv("ANGEL_ONE_API_KEY", raising=False)
    with pytest.raises(BrokerNotConfiguredError):
        load_broker_credentials(IndianBroker.ANGEL_ONE)


def test_load_broker_credentials_round_trips_env(monkeypatch):
    monkeypatch.setenv("BREEZE_API_KEY", "key")
    monkeypatch.setenv("BREEZE_API_SECRET", "secret")
    monkeypatch.setenv("BREEZE_CLIENT_ID", "client")
    monkeypatch.setenv("BREEZE_USER_ID", "user")
    creds = load_broker_credentials(IndianBroker.BREEZE)
    assert creds.api_key == "key"
    assert creds.api_secret == "secret"
    assert creds.client_id == "client"
    assert creds.user_id == "user"
