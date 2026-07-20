"""Place an order — paper by default, live only behind an explicit gate.

The whole safety model lives in `place_order`, in this order:

1. Safety caps run FIRST, before any broker is contacted. A cap breach is
   rejected and logged; nothing leaves the machine.
2. If LIVE_TRADING is not truthy in the environment, the order is simulated
   ("paper") and logged. This is the default — a fresh clone cannot trade money.
3. Only with LIVE_TRADING=1 AND broker credentials present does a real order go
   out, through the broker adapter.

Every attempt — paper, live, or rejected — is appended to an immutable trade log
(data/trades/trades.jsonl), the same append-only pattern as the signal recorder.
"""

from __future__ import annotations

import os
from pathlib import Path

from alpha_engine.config import data_dir
from alpha_engine.execution.orders import (
    ExecutionResult,
    Order,
    OrderCapError,
    check_caps,
)

DEFAULT_ROOT = data_dir() / "trades"
LOG_NAME = "trades.jsonl"

_TRUTHY = {"1", "true", "yes", "on"}


def live_enabled() -> bool:
    """True only when LIVE_TRADING is explicitly set truthy. Default: paper."""
    return os.getenv("LIVE_TRADING", "").strip().lower() in _TRUTHY


def _record(result: ExecutionResult, root: str | Path) -> None:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with (root / LOG_NAME).open("a", encoding="utf-8") as f:
        f.write(result.model_dump_json() + "\n")


def place_order(
    order: Order,
    broker: str = "dhan",
    est_price: float | None = None,
    root: str | Path = DEFAULT_ROOT,
) -> ExecutionResult:
    """Place one order and return the result. Paper unless LIVE_TRADING=1."""
    # 1. Caps first — a runaway order dies here, before any broker contact.
    try:
        check_caps(order, est_price)
    except OrderCapError as e:
        result = ExecutionResult(
            order=order,
            status="rejected",
            broker=broker,
            message=f"cap breach: {e}",
            est_price=est_price,
        )
        _record(result, root)
        return result

    # 2. Paper mode (the default). Simulate and log; touch no money.
    if not live_enabled():
        result = ExecutionResult(
            order=order,
            status="paper",
            broker=broker,
            message="paper fill (LIVE_TRADING not set)",
            est_price=est_price,
        )
        _record(result, root)
        return result

    # 3. Live mode. Explicitly enabled; route to the broker adapter.
    try:
        broker_order_id, message = _dispatch_live(order, broker)
        result = ExecutionResult(
            order=order,
            status="live",
            broker=broker,
            broker_order_id=broker_order_id,
            message=message,
            est_price=est_price,
        )
    except Exception as e:  # noqa: BLE001 - any broker/network failure -> rejected, logged
        result = ExecutionResult(
            order=order,
            status="rejected",
            broker=broker,
            message=f"broker error: {e}",
            est_price=est_price,
        )
    _record(result, root)
    return result


def _dispatch_live(order: Order, broker: str) -> tuple[str, str]:
    """Route a live order to the chosen broker. Dhan today; Angel One next."""
    if broker == "dhan":
        from alpha_engine.execution.dhan import place_order_dhan

        return place_order_dhan(order)
    raise NotImplementedError(f"live broker '{broker}' not wired yet (dhan is)")


def read_trades(root: str | Path = DEFAULT_ROOT) -> list[ExecutionResult]:
    """Load the trade log oldest-first. Reading never mutates it."""
    path = Path(root) / LOG_NAME
    if not path.exists():
        return []
    out: list[ExecutionResult] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(ExecutionResult.model_validate_json(line))
    return out
