"""Live Dhan adapter for Indian options-chain data.

Dhan (https://dhan.co) provides a REST API for derivatives data. Like the
Breeze and Angel One adapters, this uses plain `requests` instead of any SDK
to avoid import-time side effects and keep the code deterministic and testable.

The adapter is credential-gated: it fails fast with a descriptive message
if the required env vars are missing. The default repo clone needs no keys.

Env contract:
    DHAN_CLIENT_ID   — required (the numeric client ID from Dhan)
    DHAN_ACCESS_TOKEN — required (the API access token)
"""

from __future__ import annotations

import time
from typing import Any

import requests

from alpha_engine.cache.models import OptionsChain
from alpha_engine.ingestion.indian_broker import (
    BrokerCredentials,
    IndianBroker,
    load_broker_credentials,
)
from alpha_engine.ingestion.indian_fno import parse_indian_chain_payload

_API_BASE = "https://api.dhan.co/v2"

# Dhan rate-limits market-data calls (~60 req/min on the free tier), but a
# burst of fetches can still hit 429. Retrying with exponential backoff keeps
# a `watch NIFTY BANKNIFTY ...` batch usable without the caller thinking about it.
_MAX_RETRIES = 3
_BACKOFF_BASE_SECONDS = 2.0
_RETRYABLE_STATUSES = {429, 500, 502, 503}


def _get_with_retry(
    url: str,
    *,
    params: dict[str, str],
    headers: dict[str, str],
    timeout: int = 20,
) -> requests.Response:
    """GET with retries on rate-limit (429) and transient server errors.

    Honors a Retry-After header when the API sends one; otherwise waits
    2s, 4s, 8s. The final attempt's response is returned as-is so the
    caller's raise_for_status() surfaces the real error message.
    """
    resp: requests.Response | None = None
    for attempt in range(_MAX_RETRIES + 1):
        resp = requests.get(url, params=params, headers=headers, timeout=timeout)
        if resp.status_code not in _RETRYABLE_STATUSES or attempt == _MAX_RETRIES:
            return resp
        retry_after = resp.headers.get("Retry-After")
        try:
            wait = float(retry_after) if retry_after else _BACKOFF_BASE_SECONDS * (2**attempt)
        except ValueError:
            wait = _BACKOFF_BASE_SECONDS * (2**attempt)
        time.sleep(wait)
    assert resp is not None  # loop always runs at least once
    return resp


def _headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _parse_option_chain(raw: Any, underlying: str) -> dict[str, Any]:
    """Transform Dhan's option chain response into the normalized format
    that `parse_indian_chain_payload` expects.

    Dhan returns a shape like:
    {
        "status": "success",
        "data": {
            "spotPrice": 24500.0,
            "expiryDates": ["2026-07-30", ...],
            "optionChain": [
                {
                    "strikePrice": 24000,
                    "CE": {"openInterest": 1234, "lastPrice": 450, ...},
                    "PE": {"openInterest": 5678, "lastPrice": 120, ...}
                },
                ...
            ]
        }
    }

    The actual shape may vary; we handle the common patterns.
    """
    if isinstance(raw, list):
        return _parse_strike_list(raw, underlying, {})

    data = raw.get("data", raw) if isinstance(raw, dict) else {}

    # Dhan may nest under "optionChain" or "records"
    if "optionChain" in data and isinstance(data["optionChain"], list):
        return _parse_option_chain_list(data["optionChain"], underlying, data)
    if "records" in data and isinstance(data["records"], list):
        return {"underlying": underlying, "records": data["records"], **data}
    if "data" in data and isinstance(data["data"], list):
        return _parse_strike_list(data["data"], underlying, data)

    # Fallback: try to find any list of option records
    for key in ("options", "results", "optionData", "chain"):
        if key in data and isinstance(data[key], list):
            return {"underlying": underlying, "records": data[key], **data}

    return {"underlying": underlying, "records": [], **data}


def _parse_option_chain_list(items: list[dict], underlying: str, meta: dict) -> dict[str, Any]:
    """Parse Dhan's optionChain format where each entry has strike + CE + PE."""
    records = []
    for item in items:
        strike = item.get("strikePrice") or item.get("strike_price") or item.get("strike")
        if strike is None:
            continue
        record: dict[str, Any] = {"strikePrice": float(strike)}
        for key, right_label in [("CE", "CE"), ("PE", "PE"), ("ce", "CE"), ("pe", "PE")]:
            opt = item.get(key)
            if opt and isinstance(opt, dict):
                record[right_label] = {
                    "openInterest": opt.get("openInterest", opt.get("oi", 0)),
                    "changeinOpenInterest": opt.get(
                        "changeinOpenInterest", opt.get("oi_change", 0)
                    ),
                    "totalTradedVolume": opt.get("totalTradedVolume", opt.get("volume", 0)),
                    "lastPrice": opt.get("lastPrice", opt.get("ltp", 0)),
                }
        records.append(record)

    # Extract spot price if available
    spot = meta.get("spotPrice") or meta.get("ltp") or meta.get("spot")
    result: dict[str, Any] = {"underlying": underlying, "records": records}
    if spot is not None:
        result["spot"] = spot
    return result


def _parse_strike_list(items: list[dict], underlying: str, meta: dict) -> dict[str, Any]:
    """Parse a flat list where each entry may be a single option or a pair."""
    records = []
    for item in items:
        strike = item.get("strikePrice") or item.get("strike_price") or item.get("strike")
        if strike is None:
            continue
        record: dict[str, Any] = {"strikePrice": float(strike)}
        for key, right_label in [("CE", "CE"), ("PE", "PE"), ("ce", "CE"), ("pe", "PE")]:
            opt = item.get(key)
            if opt and isinstance(opt, dict):
                record[right_label] = {
                    "openInterest": opt.get("openInterest", opt.get("oi", 0)),
                    "changeinOpenInterest": opt.get(
                        "changeinOpenInterest", opt.get("oi_change", 0)
                    ),
                    "totalTradedVolume": opt.get("totalTradedVolume", opt.get("volume", 0)),
                    "lastPrice": opt.get("lastPrice", opt.get("ltp", 0)),
                }
        records.append(record)
    return {"underlying": underlying, "records": records, **meta}


class DhanLiveClient:
    """Thin live Dhan client using the documented REST contract.

    The client authenticates using a client ID and access token. Both are
    required and loaded from environment variables.

    Usage:
        client = DhanLiveClient.from_env()
        chain = client.fetch_chain("NIFTY", "2026-07-30")
    """

    def __init__(self, credentials: BrokerCredentials | None = None) -> None:
        self.credentials = credentials or load_broker_credentials(IndianBroker.DHAN)
        if not self.credentials.access_token:
            from alpha_engine.ingestion.indian_broker import BrokerNotConfiguredError

            raise BrokerNotConfiguredError(
                "DHAN_ACCESS_TOKEN is required for Dhan "
                "(generate from https://dhan.co -> My Account -> API)"
            )
        self._token = self.credentials.access_token
        self._client_id = self.credentials.client_id or ""

    @classmethod
    def from_env(cls) -> DhanLiveClient:
        return cls()

    def fetch_chain(self, underlying: str, expiry_date: str) -> OptionsChain:
        """Fetch the full option chain for an underlying and expiry.

        The expiry_date format should be YYYY-MM-DD (e.g. "2026-07-30").
        """
        url = f"{_API_BASE}/optionchain"
        params = {
            "symbol": underlying.upper(),
            "expiry": expiry_date,
        }
        headers = _headers(self._token)
        resp = _get_with_retry(url, params=params, headers=headers)
        resp.raise_for_status()
        raw = resp.json()

        payload = _parse_option_chain(raw, underlying)
        payload.setdefault("underlying", underlying)
        payload.setdefault("expiry", expiry_date)
        return parse_indian_chain_payload(payload, underlying=underlying)
