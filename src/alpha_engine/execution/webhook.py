"""Inbound trade webhook: receive an alert, place a (paper) order.

The classic prop pattern: an external trigger (a TradingView alert, your own
cron, a chart button) POSTs a JSON trade instruction here, and the engine routes
it through the SAME paper-first executor. The webhook bypasses no safety gate —
live orders still require LIVE_TRADING=1, and the size caps still apply.

Auth: every request must present the shared secret from WEBHOOK_SECRET, as header
'X-Webhook-Token' or JSON field 'token', compared in constant time. If
WEBHOOK_SECRET is unset the server refuses to start — an open money endpoint must
never exist by accident.

Payload:
    {"asset":"NIFTY", "direction":"bullish", "spot":24500,
     "quantity":1, "as_option":true, "expiry":"2026-07-31"}

The direction here is the CALLER'S explicit trade decision (e.g. from your chart
alert). The webhook does not generate a signal — it executes an instruction — so
it does not touch the deterministic signal pipeline or the cardinal rule.
"""

from __future__ import annotations

import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from alpha_engine.cache.models import OptionRight
from alpha_engine.config import load_project_env
from alpha_engine.execution.executor import live_enabled, place_order
from alpha_engine.execution.orders import Instrument, Order, OrderSide, atm_strike

_BULLISH = {"bullish", "buy", "long"}
_BEARISH = {"bearish", "sell", "short"}


def build_order(payload: dict) -> Order:
    """Build a normalized Order from a webhook payload. Raises ValueError on a
    malformed instruction (bad direction, missing asset, missing option spot)."""
    asset = str(payload.get("asset", "")).strip().upper()
    if not asset:
        raise ValueError("missing 'asset'")

    direction = str(payload.get("direction", "")).strip().lower()
    if direction in _BULLISH:
        bullish = True
    elif direction in _BEARISH:
        bullish = False
    else:
        raise ValueError(f"'direction' must be bullish/bearish, got {payload.get('direction')!r}")

    quantity = int(payload.get("quantity", 1))
    product = str(payload.get("product", "intraday"))

    if payload.get("as_option"):
        if "spot" not in payload:
            raise ValueError("option order needs 'spot' for the ATM strike")
        spot = float(payload["spot"])
        return Order(
            asset=asset,
            side=OrderSide.BUY,
            quantity=quantity,
            instrument=Instrument.OPTION,
            right=OptionRight.CALL if bullish else OptionRight.PUT,
            strike=atm_strike(spot, float(payload.get("strike_step", 50))),
            expiry=payload.get("expiry"),
            product=product,
            note="via webhook",
        )

    return Order(
        asset=asset,
        side=OrderSide.BUY if bullish else OrderSide.SELL,
        quantity=quantity,
        instrument=Instrument.EQUITY,
        product=product,
        note="via webhook",
    )


def _authorized(headers, payload: dict, secret: str) -> bool:
    supplied = headers.get("X-Webhook-Token") or payload.get("token") or ""
    return hmac.compare_digest(str(supplied), secret)


class _Handler(BaseHTTPRequestHandler):
    secret = ""  # set by serve()

    def _reply(self, code: int, body: dict) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        if self.path.rstrip("/") not in ("", "/webhook"):
            self._reply(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._reply(400, {"error": "invalid JSON body"})
            return

        if not _authorized(self.headers, payload, self.secret):
            self._reply(401, {"error": "bad or missing webhook token"})
            return

        try:
            order = build_order(payload)
        except (ValueError, KeyError, TypeError) as e:
            self._reply(400, {"error": str(e)})
            return

        est = float(payload["spot"]) if "spot" in payload else None
        result = place_order(order, broker=str(payload.get("broker", "dhan")), est_price=est)
        self._reply(200, json.loads(result.model_dump_json()))

    def log_message(self, *args) -> None:  # silence default stderr spam
        pass


def serve(host: str = "127.0.0.1", port: int = 8787) -> None:
    """Start the webhook server. Refuses to start without WEBHOOK_SECRET."""
    load_project_env()
    secret = os.getenv("WEBHOOK_SECRET", "").strip()
    if not secret:
        raise RuntimeError(
            "WEBHOOK_SECRET is not set. Refusing to start an unauthenticated trade "
            "webhook. Set WEBHOOK_SECRET to a long random string first."
        )
    _Handler.secret = secret
    mode = "LIVE" if live_enabled() else "PAPER"
    server = ThreadingHTTPServer((host, port), _Handler)
    print(f"[webhook] listening on http://{host}:{port}/webhook  mode={mode}")
    print("[webhook] every order still passes size caps; live needs LIVE_TRADING=1")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
