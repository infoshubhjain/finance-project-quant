"""Execution layer: translation, safety caps, paper-first gate, webhook auth."""

from __future__ import annotations

import pytest

from alpha_engine.cache.models import OptionRight
from alpha_engine.execution.executor import live_enabled, place_order, read_trades
from alpha_engine.execution.orders import (
    Instrument,
    Order,
    OrderCapError,
    OrderSide,
    atm_strike,
    check_caps,
    signal_to_order,
)
from alpha_engine.execution.webhook import build_order
from alpha_engine.schema.signal import Direction, Market, Signal, Timeframe


def _signal(direction: Direction, confidence: float) -> Signal:
    return Signal(
        asset="NIFTY",
        market=Market.IN_EQUITY,
        direction=direction,
        confidence=confidence,
        timeframe=Timeframe.SWING,
    )


# --- translation -----------------------------------------------------------


def test_bullish_equity_is_buy():
    o = signal_to_order(_signal(Direction.BULLISH, 0.7), spot=24500)
    assert o is not None and o.side is OrderSide.BUY and o.instrument is Instrument.EQUITY


def test_bearish_equity_is_sell():
    o = signal_to_order(_signal(Direction.BEARISH, 0.7), spot=24500)
    assert o is not None and o.side is OrderSide.SELL


def test_bullish_option_is_atm_call():
    o = signal_to_order(_signal(Direction.BULLISH, 0.7), spot=24512, as_option=True)
    assert o is not None
    assert o.instrument is Instrument.OPTION
    assert o.side is OrderSide.BUY  # long premium only
    assert o.right is OptionRight.CALL
    assert o.strike == 24500  # rounded to nearest 50


def test_bearish_option_is_atm_put():
    o = signal_to_order(_signal(Direction.BEARISH, 0.7), spot=24540, as_option=True)
    assert o is not None and o.right is OptionRight.PUT and o.strike == 24550


def test_neutral_and_low_confidence_place_no_order():
    assert signal_to_order(_signal(Direction.NEUTRAL, 0.9), spot=100) is None
    assert signal_to_order(_signal(Direction.BULLISH, 0.30), spot=100) is None


def test_atm_strike_rounds_to_grid():
    assert atm_strike(24512, 50) == 24500
    assert atm_strike(24540, 50) == 24550
    assert atm_strike(451, 100) == 500


# --- safety caps -----------------------------------------------------------


def _order(qty: int) -> Order:
    return Order(asset="NIFTY", side=OrderSide.BUY, quantity=qty)


def test_cap_rejects_oversized_quantity(monkeypatch):
    monkeypatch.delenv("MAX_ORDER_QTY", raising=False)
    with pytest.raises(OrderCapError):
        check_caps(_order(101))  # default cap is 100
    check_caps(_order(100))  # exactly at cap is fine


def test_cap_rejects_oversized_notional(monkeypatch):
    monkeypatch.delenv("MAX_ORDER_NOTIONAL", raising=False)
    monkeypatch.setenv("MAX_ORDER_QTY", "1000")
    with pytest.raises(OrderCapError):
        check_caps(_order(100), est_price=10_000)  # 1,000,000 > 500,000 default


# --- paper-first gate ------------------------------------------------------


def test_place_order_is_paper_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("LIVE_TRADING", raising=False)
    assert live_enabled() is False
    result = place_order(_order(1), est_price=24500, root=tmp_path)
    assert result.status == "paper"
    assert result.broker_order_id is None
    # It was logged immutably.
    logged = read_trades(root=tmp_path)
    assert len(logged) == 1 and logged[0].status == "paper"


def test_cap_breach_is_rejected_and_logged(monkeypatch, tmp_path):
    monkeypatch.delenv("LIVE_TRADING", raising=False)
    monkeypatch.delenv("MAX_ORDER_QTY", raising=False)
    result = place_order(_order(9999), est_price=24500, root=tmp_path)
    assert result.status == "rejected"
    assert "cap breach" in result.message
    assert read_trades(root=tmp_path)[0].status == "rejected"


def test_live_enabled_reads_env(monkeypatch):
    monkeypatch.setenv("LIVE_TRADING", "1")
    assert live_enabled() is True
    monkeypatch.setenv("LIVE_TRADING", "no")
    assert live_enabled() is False


# --- webhook payload -> order ----------------------------------------------


def test_webhook_builds_bullish_option():
    o = build_order({"asset": "nifty", "direction": "bullish", "as_option": True, "spot": 24500})
    assert o.instrument is Instrument.OPTION and o.right is OptionRight.CALL


def test_webhook_builds_bearish_equity():
    o = build_order({"asset": "RELIANCE", "direction": "sell", "quantity": 5})
    assert o.side is OrderSide.SELL and o.quantity == 5


def test_webhook_rejects_bad_direction():
    with pytest.raises(ValueError):
        build_order({"asset": "NIFTY", "direction": "sideways"})


def test_webhook_option_needs_spot():
    with pytest.raises(ValueError):
        build_order({"asset": "NIFTY", "direction": "bullish", "as_option": True})


def test_webhook_needs_asset():
    with pytest.raises(ValueError):
        build_order({"direction": "bullish"})
