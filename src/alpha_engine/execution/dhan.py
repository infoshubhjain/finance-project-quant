"""Live Dhan order placement. Reached ONLY when LIVE_TRADING=1 (see executor.py).

Maps a normalized Order to Dhan's documented POST /v2/orders contract and sends
it with the same stdlib HTTP helper and credential loader the Dhan chain adapter
already uses.

HONESTY BOUNDARY — read before trusting this with money:
- Dhan identifies instruments by a numeric `securityId`, not by symbol. Mapping
  "NIFTY 24500 CE" -> a securityId needs Dhan's instrument master. This adapter
  reads that mapping from `data/dhan_instruments.json` (symbol -> securityId) and
  refuses to guess: an unmapped symbol raises, it does not send a wrong order.
- This request shape follows Dhan's published v2 order API, but it has NOT been
  round-tripped against a live account in this codebase. Before enabling
  LIVE_TRADING, place ONE tiny order and confirm the fill in the Dhan app. Treat
  the first real fill as the actual test — code review is not a substitute here.
"""

from __future__ import annotations

import json
from pathlib import Path

from alpha_engine import net
from alpha_engine.execution.orders import Instrument, Order, OrderSide
from alpha_engine.ingestion.indian_broker import (
    BrokerNotConfiguredError,
    IndianBroker,
    load_broker_credentials,
)

_API_BASE = "https://api.dhan.co/v2"
_INSTRUMENT_MAP = Path("data/dhan_instruments.json")


def _resolve_security_id(order: Order) -> str:
    """Look up Dhan's securityId for this order's instrument. Raises a clear
    error rather than guessing — a wrong id would trade the wrong contract.

    The map key for an option is 'SYMBOL STRIKE RIGHT' (e.g. 'NIFTY 24500 CALL');
    for equity it is just the symbol.
    """
    if order.instrument is Instrument.OPTION:
        key = f"{order.asset} {order.strike:g} {order.right.value.upper()}"  # type: ignore[union-attr]
    else:
        key = order.asset

    if not _INSTRUMENT_MAP.exists():
        raise BrokerNotConfiguredError(
            f"{_INSTRUMENT_MAP} not found — live Dhan orders need a symbol->securityId "
            "map. Download Dhan's instrument master and save it as that file."
        )
    mapping = json.loads(_INSTRUMENT_MAP.read_text())
    security_id = mapping.get(key)
    if not security_id:
        raise BrokerNotConfiguredError(
            f"no Dhan securityId mapped for '{key}' in {_INSTRUMENT_MAP}"
        )
    return str(security_id)


def _to_dhan_payload(order: Order, client_id: str) -> dict:
    segment = "NSE_FNO" if order.instrument is Instrument.OPTION else "NSE_EQ"
    txn = "BUY" if order.side is OrderSide.BUY else "SELL"
    order_type = "LIMIT" if order.order_type == "limit" else "MARKET"
    product = "INTRADAY" if order.product == "intraday" else "CNC"

    payload = {
        "dhanClientId": client_id,
        "transactionType": txn,
        "exchangeSegment": segment,
        "productType": product,
        "orderType": order_type,
        "securityId": _resolve_security_id(order),
        "quantity": order.quantity,
        "price": order.limit_price if order.order_type == "limit" else 0,
    }
    return payload


def place_order_dhan(order: Order) -> tuple[str, str]:
    """Send a live order to Dhan. Returns (broker_order_id, status message).

    Raises on missing credentials, unmapped instrument, or an HTTP error — the
    executor turns any exception into a logged 'rejected' result.
    """
    creds = load_broker_credentials(IndianBroker.DHAN)  # raises if unconfigured
    payload = _to_dhan_payload(order, creds.client_id or "")

    resp = net.post(
        f"{_API_BASE}/orders",
        json=payload,
        headers={
            "access-token": creds.access_token or "",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    resp.raise_for_status()
    data = resp.json()
    inner = data.get("data", data) if isinstance(data, dict) else {}
    order_id = str(inner.get("orderId") or data.get("orderId") or "")
    status = inner.get("orderStatus") or data.get("orderStatus") or "submitted"
    return order_id, f"dhan {status}"
