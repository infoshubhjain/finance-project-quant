"""Tests for the live Dhan order adapter — the module that can move real money.

An audit found this file at **0% coverage**. Every other risky path in the repo
is pinned; the one that places real orders was not, and "it is gated behind
LIVE_TRADING" protects you from running it by accident, not from it being wrong
when you finally mean to run it.

Nothing here touches the network. What is pinned is the part that would be
expensive to get wrong and silent when it was:

1. **The payload mapping.** A flipped BUY/SELL, a wrong exchange segment or a
   market order sent with a stale price is a real trade at a wrong price.
2. **securityId resolution.** Dhan identifies contracts numerically. A wrong id
   is not a failed order, it is a *successful order on the wrong instrument* —
   the single worst outcome in this file, so the adapter must refuse rather than
   guess, and these tests hold it to that.
3. **Response parsing.** Dhan nests the order id under `data`; missing it must
   not silently produce an empty order id that looks like a success.
4. **Credentials come first.** No request may be built, let alone sent, before
   credentials are confirmed present.
"""

from __future__ import annotations

import json

import pytest

from alpha_engine.execution import dhan
from alpha_engine.execution.orders import Instrument, Order, OrderSide
from alpha_engine.cache.models import OptionRight
from alpha_engine.ingestion.indian_broker import (
    BrokerCredentials,
    BrokerNotConfiguredError,
    IndianBroker,
)


@pytest.fixture()
def instrument_map(tmp_path, monkeypatch):
    """Point the adapter at a throwaway instrument master."""
    path = tmp_path / "dhan_instruments.json"
    path.write_text(
        json.dumps({"NIFTY 24500 CALL": "43911", "NIFTY 24000 PUT": "43912", "RELIANCE": "2885"})
    )
    monkeypatch.setattr(dhan, "_INSTRUMENT_MAP", path)
    return path


@pytest.fixture()
def creds(monkeypatch):
    monkeypatch.setattr(
        dhan,
        "load_broker_credentials",
        lambda broker: BrokerCredentials(
            broker=IndianBroker.DHAN,
            api_key="k",
            client_id="CLIENT123",
            access_token="TOKEN456",
        ),
    )


def _option(side=OrderSide.BUY, right=OptionRight.CALL, strike=24500.0, **kw) -> Order:
    return Order(
        asset="NIFTY",
        side=side,
        quantity=50,
        instrument=Instrument.OPTION,
        right=right,
        strike=strike,
        expiry="2026-08-27",
        **kw,
    )


# --------------------------------------------------------------------------
# 1. Payload mapping
# --------------------------------------------------------------------------


def test_buy_option_maps_to_the_documented_contract(instrument_map):
    payload = dhan._to_dhan_payload(_option(), "CLIENT123")
    assert payload == {
        "dhanClientId": "CLIENT123",
        "transactionType": "BUY",
        "exchangeSegment": "NSE_FNO",
        "productType": "INTRADAY",
        "orderType": "MARKET",
        "securityId": "43911",
        "quantity": 50,
        "price": 0,
    }


def test_sell_maps_to_sell(instrument_map):
    """A flipped side is a real trade in the wrong direction."""
    payload = dhan._to_dhan_payload(_option(side=OrderSide.SELL), "C")
    assert payload["transactionType"] == "SELL"


def test_equity_uses_the_cash_segment(instrument_map):
    order = Order(asset="RELIANCE", side=OrderSide.BUY, quantity=10)
    payload = dhan._to_dhan_payload(order, "C")
    assert payload["exchangeSegment"] == "NSE_EQ"
    assert payload["securityId"] == "2885"


def test_delivery_product_maps_to_cnc(instrument_map):
    order = Order(asset="RELIANCE", side=OrderSide.BUY, quantity=10, product="delivery")
    assert dhan._to_dhan_payload(order, "C")["productType"] == "CNC"


def test_limit_order_carries_its_price(instrument_map):
    order = _option(order_type="limit", limit_price=132.5)
    payload = dhan._to_dhan_payload(order, "C")
    assert payload["orderType"] == "LIMIT"
    assert payload["price"] == 132.5


def test_market_order_sends_zero_price_not_a_stale_one(instrument_map):
    """A market order carrying a leftover limit price is a price instruction
    Dhan may honour. It must be zero."""
    order = _option(order_type="market", limit_price=999.0)
    payload = dhan._to_dhan_payload(order, "C")
    assert payload["orderType"] == "MARKET"
    assert payload["price"] == 0


def test_put_resolves_to_its_own_security_id(instrument_map):
    payload = dhan._to_dhan_payload(_option(right=OptionRight.PUT, strike=24000.0), "C")
    assert payload["securityId"] == "43912"


def test_strike_is_formatted_without_a_trailing_zero(instrument_map):
    """The map key is built with %g, so 24500.0 must look up '24500'. If this
    breaks, every option order fails to resolve — noisily, at least."""
    assert dhan._to_dhan_payload(_option(strike=24500.0), "C")["securityId"] == "43911"


# --------------------------------------------------------------------------
# 2. securityId resolution — refuse, never guess
# --------------------------------------------------------------------------


def test_an_unmapped_instrument_is_refused(instrument_map):
    """The worst possible bug in this file is a *successful* order on the wrong
    contract. Refusing is the only acceptable behaviour."""
    with pytest.raises(BrokerNotConfiguredError, match="no Dhan securityId mapped"):
        dhan._resolve_security_id(_option(strike=99999.0))


def test_a_missing_instrument_master_is_refused_with_a_fix(tmp_path, monkeypatch):
    monkeypatch.setattr(dhan, "_INSTRUMENT_MAP", tmp_path / "absent.json")
    with pytest.raises(BrokerNotConfiguredError, match="instrument master"):
        dhan._resolve_security_id(_option())


def test_an_empty_mapping_value_is_treated_as_unmapped(tmp_path, monkeypatch):
    """`""` and `0` are falsy; either would produce a request with no
    instrument rather than an error."""
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"NIFTY 24500 CALL": ""}))
    monkeypatch.setattr(dhan, "_INSTRUMENT_MAP", path)
    with pytest.raises(BrokerNotConfiguredError):
        dhan._resolve_security_id(_option())


def test_a_numeric_security_id_is_stringified(tmp_path, monkeypatch):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"RELIANCE": 2885}))
    monkeypatch.setattr(dhan, "_INSTRUMENT_MAP", path)
    resolved = dhan._resolve_security_id(Order(asset="RELIANCE", side=OrderSide.BUY, quantity=1))
    assert resolved == "2885" and isinstance(resolved, str)


# --------------------------------------------------------------------------
# 3. Sending and response parsing
# --------------------------------------------------------------------------


class FakeResp:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self._payload = payload
        self.url = "https://api.dhan.co/v2/orders"

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_a_successful_order_returns_the_broker_id(instrument_map, creds, monkeypatch):
    sent = {}

    def fake_post(url, *, json=None, headers=None, **kw):
        sent["url"] = url
        sent["json"] = json
        sent["headers"] = headers
        return FakeResp({"data": {"orderId": "112233", "orderStatus": "TRANSIT"}})

    monkeypatch.setattr(dhan.net, "post", fake_post)
    order_id, status = dhan.place_order_dhan(_option())

    assert order_id == "112233"
    assert "TRANSIT" in status
    assert sent["url"].endswith("/v2/orders")
    assert sent["headers"]["access-token"] == "TOKEN456"
    assert sent["json"]["dhanClientId"] == "CLIENT123"


def test_a_flat_response_shape_is_also_parsed(instrument_map, creds, monkeypatch):
    monkeypatch.setattr(
        dhan.net, "post", lambda *a, **kw: FakeResp({"orderId": "9", "orderStatus": "PENDING"})
    )
    assert dhan.place_order_dhan(_option())[0] == "9"


def test_an_http_error_raises_rather_than_reporting_success(instrument_map, creds, monkeypatch):
    monkeypatch.setattr(dhan.net, "post", lambda *a, **kw: FakeResp({"errorMessage": "no"}, 400))
    with pytest.raises(RuntimeError):
        dhan.place_order_dhan(_option())


def test_missing_credentials_stop_it_before_any_request(instrument_map, monkeypatch):
    """No network call may be built, let alone sent, without credentials."""
    called = {"n": 0}

    def boom(broker):
        raise BrokerNotConfiguredError("DHAN_ACCESS_TOKEN is required")

    monkeypatch.setattr(dhan, "load_broker_credentials", boom)
    monkeypatch.setattr(dhan.net, "post", lambda *a, **kw: called.__setitem__("n", 1))

    with pytest.raises(BrokerNotConfiguredError):
        dhan.place_order_dhan(_option())
    assert called["n"] == 0, "a request was attempted without credentials"


def test_an_unmapped_instrument_stops_it_before_any_request(instrument_map, creds, monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(dhan.net, "post", lambda *a, **kw: called.__setitem__("n", 1))
    with pytest.raises(BrokerNotConfiguredError):
        dhan.place_order_dhan(_option(strike=1.0))
    assert called["n"] == 0
