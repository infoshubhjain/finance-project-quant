"""Live Breeze adapter for Indian options-chain data.

This is the credential-gated piece the plan has been waiting on. The adapter
does not use the Breeze SDK directly because that package performs network work
at import time. Instead, it mirrors the official request shape using plain
`requests`, which keeps the code deterministic and testable.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from alpha_engine import net
from alpha_engine.cache.models import OptionsChain
from alpha_engine.ingestion.indian_broker import (
    BrokerCredentials,
    IndianBroker,
    load_broker_credentials,
)
from alpha_engine.ingestion.indian_fno import parse_indian_chain_payload

_API_URL = "https://api.icicidirect.com/breezeapi/api/v1/"


@dataclass(frozen=True, slots=True)
class BreezeSession:
    user_id: str
    session_key: str

    @property
    def header_token(self) -> str:
        payload = f"{self.user_id}:{self.session_key}".encode("ascii")
        return base64.b64encode(payload).decode("ascii")


def _json_body(payload: dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"))


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")[:19] + ".000Z"


def _checksum(timestamp: str, body: str, api_secret: str) -> str:
    digest = sha256((timestamp + body + api_secret).encode("utf-8")).hexdigest()
    return f"token {digest}"


def _post_or_get(url: str, method: str, body: str, headers: dict[str, str]) -> net.Response:
    method = method.upper()
    if method == "GET":
        return net.get(url=url, data=body, headers=headers, timeout=20)
    if method == "POST":
        return net.post(url=url, data=body, headers=headers, timeout=20)
    raise ValueError(f"unsupported method: {method}")


def _customerdetails(credentials: BrokerCredentials) -> BreezeSession:
    body = _json_body({"SessionToken": credentials.access_token, "AppKey": credentials.api_key})
    headers = {"Content-Type": "application/json"}
    resp = net.get(url=f"{_API_URL}customerdetails", data=body, headers=headers, timeout=20)
    resp.raise_for_status()
    payload = resp.json()

    success = payload.get("Success") if isinstance(payload, dict) else None
    session_token = success.get("session_token") if isinstance(success, dict) else None
    if not session_token:
        raise RuntimeError(f"could not resolve Breeze session from customer details: {payload}")

    decoded = base64.b64decode(session_token.encode("ascii")).decode("ascii")
    parts = decoded.split(":")
    if len(parts) != 2:
        raise RuntimeError("unexpected Breeze session token format")

    return BreezeSession(user_id=parts[0], session_key=parts[1])


def _option_chain_body(
    underlying: str,
    expiry_date: str,
    *,
    exchange_code: str = "NFO",
    product_type: str = "options",
    right: str = "others",
    strike_price: str = "",
) -> str:
    payload: dict[str, Any] = {
        "stock_code": underlying,
        "exchange_code": exchange_code,
        "product_type": product_type,
        "expiry_date": expiry_date,
        "right": right,
    }
    if strike_price:
        payload["strike_price"] = strike_price
    return _json_body(payload)


def _unwrap_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        if "Success" in payload and payload["Success"] is not None:
            success = payload["Success"]
            if isinstance(success, dict):
                if "data" in success and isinstance(success["data"], list):
                    return {"records": success["data"], **success}
                if "options" in success and isinstance(success["options"], list):
                    return {"records": success["options"], **success}
                if "records" in success and isinstance(success["records"], list):
                    return success
                return success
            if isinstance(success, list):
                return {"records": success}
        if "data" in payload and isinstance(payload["data"], list):
            return {"records": payload["data"], **payload}
        if "records" in payload and isinstance(payload["records"], list):
            return payload
    if isinstance(payload, list):
        return {"records": payload}
    raise ValueError(f"unsupported Breeze payload shape: {payload!r}")


class BreezeLiveClient:
    """Thin live Breeze client built on the documented REST contract."""

    def __init__(self, credentials: BrokerCredentials | None = None) -> None:
        self.credentials = credentials or load_broker_credentials(IndianBroker.BREEZE)
        self.session = _customerdetails(self.credentials)

    @classmethod
    def from_env(cls) -> BreezeLiveClient:
        return cls()

    def _headers(self, body: str) -> dict[str, str]:
        ts = _timestamp()
        return {
            "Content-Type": "application/json",
            "X-Checksum": _checksum(ts, body, self.credentials.api_secret or ""),
            "X-Timestamp": ts,
            "X-AppKey": self.credentials.api_key,
            "X-SessionToken": self.session.header_token,
            "User-Agent": "Alpha-Engine/1.0",
        }

    def fetch_chain(self, underlying: str, expiry_date: str) -> OptionsChain:
        body = _option_chain_body(underlying=underlying, expiry_date=expiry_date)
        headers = self._headers(body)
        resp = _post_or_get(f"{_API_URL}optionchain", "POST", body, headers)
        resp.raise_for_status()
        payload = _unwrap_payload(resp.json())
        payload.setdefault("underlying", underlying)
        payload.setdefault("expiry", expiry_date)
        return parse_indian_chain_payload(payload, underlying=underlying)
