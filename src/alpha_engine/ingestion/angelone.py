"""Live Angel One adapter for Indian options-chain data.

Angel One's SmartAPI provides market data via a REST API. Like the Breeze
adapter, this uses the stdlib HTTP helper (`alpha_engine.net`) instead of the SDK to avoid import-time
side effects and keep the code deterministic and testable.

The adapter is credential-gated: it fails fast with a descriptive message
if the required env vars are missing. The default repo clone needs no keys.

Env contract:
    ANGEL_ONE_API_KEY     — required
    ANGEL_ONE_CLIENT_ID   — required (the "clientId" / "vendorCode")
    ANGEL_ONE_ACCESS_TOKEN — required (JWT from the login endpoint)
    ANGEL_ONE_REFRESH_TOKEN — optional (for token refresh, not wired yet)
"""

from __future__ import annotations

import time
from typing import Any

from alpha_engine import net
from alpha_engine.cache.models import OptionsChain
from alpha_engine.ingestion.indian_broker import (
    BrokerCredentials,
    IndianBroker,
    load_broker_credentials,
)
from alpha_engine.ingestion.indian_fno import parse_indian_chain_payload

_API_BASE = "https://apiconnect.angelone.in/smartapi"

# Angel One exchange and segment constants
_EXCHANGE_NFO = "NFO"
_SEGMENT_OPTIONS = "OPT"
_RIGHT_CE = "CE"
_RIGHT_PE = "PE"


# SmartAPI rate-limits market-data calls (~10 req/min on the free tier), so a
# burst of fetches gets HTTP 429 back. Retrying with exponential backoff keeps
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
) -> net.Response:
    """GET with retries on rate-limit (429) and transient server errors.

    Honors a Retry-After header when the API sends one; otherwise waits
    2s, 4s, 8s. The final attempt's response is returned as-is so the
    caller's raise_for_status() surfaces the real error message.
    """
    resp: net.Response | None = None
    for attempt in range(_MAX_RETRIES + 1):
        resp = net.get(url, params=params, headers=headers, timeout=timeout)
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


def _headers(token: str, api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-User-Type": "USER",
        "X-Source": "WEB",
        "X-ClientCode": api_key,
    }


def _parse_option_chain(raw: Any, underlying: str) -> dict[str, Any]:
    """Transform Angel One's option chain response into the normalized format
    that `parse_indian_chain_payload` expects.

    Angel One returns a shape like:
    {
        "data": {
            "expiry": "30JUL2026",
            " strike_ce": [...],
            " strike_pe": [...]
        }
    }

    The actual shape varies; we handle the common patterns.
    """
    if isinstance(raw, list):
        return _parse_strike_list(raw, underlying, {})
    data = raw.get("data", raw) if isinstance(raw, dict) else {}

    # Angel One may nest under "gregated" or "data"
    if "gregated" in data and isinstance(data["gregated"], list):
        return _parse_aggregated(data["gregated"], underlying, data)
    if "data" in data and isinstance(data["data"], list):
        return _parse_strike_list(data["data"], underlying, data)
    if "strike_ce" in data or "strike_pe" in data:
        return _parse_ce_pe_split(data, underlying)
    if "records" in data and isinstance(data["records"], list):
        return {"underlying": underlying, "records": data["records"], **data}

    # Fallback: try to find any list of option records
    for key in ("options", "results", "optionData"):
        if key in data and isinstance(data[key], list):
            return {"underlying": underlying, "records": data[key], **data}

    return {"underlying": underlying, "records": [], **data}


def _parse_aggregated(items: list[dict], underlying: str, meta: dict) -> dict[str, Any]:
    """Parse the 'gregated' format where each entry has strike + CE + PE."""
    records = []
    for item in items:
        strike = item.get("strikePrice") or item.get("strike_price") or item.get("strike")
        if strike is None:
            continue
        record: dict[str, Any] = {"strikePrice": float(strike)}
        for key, right_label in [("CE", "CE"), ("PE", "PE")]:
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


def _parse_ce_pe_split(data: dict, underlying: str) -> dict[str, Any]:
    """Parse a format with separate strike_ce and strike_pe lists."""
    ce_list = data.get("strike_ce", [])
    pe_list = data.get("strike_pe", [])

    by_strike: dict[float, dict] = {}
    for item in ce_list:
        strike_raw = item.get("strikePrice") or item.get("strike")
        if strike_raw is None:
            continue
        strike = float(strike_raw)
        by_strike.setdefault(strike, {})["CE"] = {
            "openInterest": item.get("openInterest", 0),
            "changeinOpenInterest": item.get("changeinOpenInterest", 0),
            "totalTradedVolume": item.get("totalTradedVolume", 0),
            "lastPrice": item.get("lastPrice", 0),
        }
    for item in pe_list:
        strike_raw = item.get("strikePrice") or item.get("strike")
        if strike_raw is None:
            continue
        strike = float(strike_raw)
        by_strike.setdefault(strike, {})["PE"] = {
            "openInterest": item.get("openInterest", 0),
            "changeinOpenInterest": item.get("changeinOpenInterest", 0),
            "totalTradedVolume": item.get("totalTradedVolume", 0),
            "lastPrice": item.get("lastPrice", 0),
        }

    records = [{"strikePrice": s, **opt} for s, opt in sorted(by_strike.items())]
    return {"underlying": underlying, "records": records}


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


class AngelOneLiveClient:
    """Thin live Angel One client using the documented SmartAPI REST contract.

    The client authenticates using an API key and access token (JWT). The
    access token is obtained by logging in via the SmartAPI login endpoint,
    but for simplicity we accept a pre-existing token from env.

    Usage:
        client = AngelOneLiveClient.from_env()
        chain = client.fetch_chain("NIFTY", "30JUL2026")
    """

    def __init__(self, credentials: BrokerCredentials | None = None) -> None:
        self.credentials = credentials or load_broker_credentials(IndianBroker.ANGEL_ONE)
        if not self.credentials.access_token:
            from alpha_engine.ingestion.indian_broker import BrokerNotConfiguredError

            raise BrokerNotConfiguredError(
                "ANGEL_ONE_ACCESS_TOKEN is required for Angel One "
                "(login via SmartAPI first, or set the env var directly)"
            )
        self._token = self.credentials.access_token

    @classmethod
    def from_env(cls) -> AngelOneLiveClient:
        return cls()

    def fetch_chain(self, underlying: str, expiry_date: str) -> OptionsChain:
        """Fetch the full option chain for an underlying and expiry.

        The expiry_date format should be DDMMMYYYY (e.g. "30JUL2026") or
        YYYY-MM-DD — the adapter normalizes it to Angel One's expected format.
        """
        normalized_expiry = _normalize_expiry(expiry_date)
        url = f"{_API_BASE}/optionchain"
        params = {
            "exchange": _EXCHANGE_NFO,
            "tradingsymbol": underlying,
            "expirydate": normalized_expiry,
        }
        headers = _headers(self._token, self.credentials.api_key)
        resp = _get_with_retry(url, params=params, headers=headers)
        resp.raise_for_status()
        raw = resp.json()

        payload = _parse_option_chain(raw, underlying)
        payload.setdefault("underlying", underlying)
        # Always use ISO format expiry for parse_indian_chain_payload compatibility
        payload["expiry"] = expiry_date
        return parse_indian_chain_payload(payload, underlying=underlying)


def _normalize_expiry(expiry: str) -> str:
    """Normalize expiry date to DDMMMYYYY format (e.g. '30JUL2026').

    Accepts YYYY-MM-DD, DD-MM-YYYY, DD/MM/YYYY, or already-normalized forms.
    """
    from datetime import datetime as dt

    # Already in DDMMMYYYY format
    if len(expiry) == 9 and expiry[2:5].isalpha() and expiry[2:5].isupper():
        return expiry

    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            parsed = dt.strptime(expiry, fmt)
            return parsed.strftime("%d%b%Y").upper()
        except ValueError:
            continue

    # If nothing matched, return as-is and let the API decide
    return expiry
