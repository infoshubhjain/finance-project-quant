"""Order model + the signal -> order translation, with hard safety caps.

An Order is a normalized instruction, broker-agnostic — the Dhan/Angel adapters
map it to their own wire format, exactly like ingestion adapters map their data
into cache/models. Nothing here places anything; this module only *describes*
the order and refuses to build a dangerous one.

Translation rules (deliberately boring):
- Only an actionable signal becomes an order. Neutral or low-confidence -> None.
- Equity mode: bullish -> BUY, bearish -> SELL (intraday short).
- Option mode: bullish -> BUY an ATM CALL, bearish -> BUY an ATM PUT. Long-only
  premium; you never short an option here (undefined risk, wrong for v1).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

from alpha_engine.cache.models import OptionRight
from alpha_engine.schema.signal import Direction, Signal


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class Instrument(str, Enum):
    EQUITY = "equity"
    OPTION = "option"


class Order(BaseModel):
    """A normalized, broker-agnostic order instruction."""

    asset: str = Field(..., description="underlying symbol, e.g. 'NIFTY', 'RELIANCE'")
    side: OrderSide
    quantity: int = Field(..., gt=0, description="units (equity) or lots-worth of units (option)")
    instrument: Instrument = Instrument.EQUITY
    order_type: str = Field("market", description="'market' or 'limit'")
    limit_price: float | None = None
    product: str = Field("intraday", description="'intraday' or 'delivery'")

    # Option-only fields (None for equity):
    right: OptionRight | None = None
    strike: float | None = None
    expiry: str | None = Field(None, description="YYYY-MM-DD")

    note: str = ""


class OrderCapError(ValueError):
    """Raised when an order breaches a hard safety cap. Caught before any broker
    call so a runaway order can never leave the machine."""


def _cap(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def check_caps(order: Order, est_price: float | None = None) -> None:
    """Refuse oversized orders. Raises OrderCapError if a cap is breached.

    Two independent ceilings, both overridable by env:
    - MAX_ORDER_QTY   (default 100 units)   — guards a fat-finger quantity.
    - MAX_ORDER_NOTIONAL (default 500000)   — guards value when a price is known.

    The caps are deliberately low. A real prop desk raises them consciously; the
    default must never let a bug trade large.
    """
    max_qty = _cap("MAX_ORDER_QTY", 100)
    if order.quantity > max_qty:
        raise OrderCapError(f"quantity {order.quantity} exceeds MAX_ORDER_QTY={max_qty:g}")
    if est_price is not None and est_price > 0:
        notional = order.quantity * est_price
        max_notional = _cap("MAX_ORDER_NOTIONAL", 500_000)
        if notional > max_notional:
            raise OrderCapError(
                f"notional {notional:,.0f} exceeds MAX_ORDER_NOTIONAL={max_notional:,.0f}"
            )


def atm_strike(spot: float, step: float = 50.0) -> float:
    """Nearest at-the-money strike on a `step`-wide grid (NIFTY = 50, BANKNIFTY
    = 100, most stocks vary). Rounds to the closest strike."""
    if step <= 0:
        return spot
    return round(spot / step) * step


def signal_to_order(
    signal: Signal,
    spot: float,
    quantity: int = 1,
    as_option: bool = False,
    strike_step: float = 50.0,
    expiry: str | None = None,
    product: str = "intraday",
) -> Order | None:
    """Map a signal to one order, or None if the signal is not actionable.

    `as_option=True` builds an ATM option (call for bullish, put for bearish);
    otherwise an equity buy/sell. `spot` is the current underlying price, used
    for the ATM strike. This adds NO decision — direction and confidence already
    came from the deterministic pipeline; this just picks the instrument.
    """
    if not signal.is_actionable():
        return None

    bullish = signal.direction is Direction.BULLISH

    if as_option:
        return Order(
            asset=signal.asset,
            side=OrderSide.BUY,  # long premium only
            quantity=quantity,
            instrument=Instrument.OPTION,
            right=OptionRight.CALL if bullish else OptionRight.PUT,
            strike=atm_strike(spot, strike_step),
            expiry=expiry,
            product=product,
            note=f"from signal conf={signal.confidence:.2f} {signal.direction.value}",
        )

    return Order(
        asset=signal.asset,
        side=OrderSide.BUY if bullish else OrderSide.SELL,
        quantity=quantity,
        instrument=Instrument.EQUITY,
        product=product,
        note=f"from signal conf={signal.confidence:.2f} {signal.direction.value}",
    )


class ExecutionResult(BaseModel):
    """The outcome of trying to place one order. `status`: 'paper' (simulated),
    'live' (sent to broker), or 'rejected' (cap breach / broker error)."""

    order: Order
    status: str
    broker: str = ""
    broker_order_id: str | None = None
    message: str = ""
    est_price: float | None = None
    at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
