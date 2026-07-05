"""OANDA v20 adapter for forex daily candles.

OANDA's practice (demo) accounts are free and include full market-data API
access, which makes it the cheapest honest path to real forex candles. The
adapter is credential-gated: without OANDA_API_KEY it raises a descriptive
error and the CLI reports that forex needs a key — the default clone stays
keyless because no *default* path routes here.

Env contract:
    OANDA_API_KEY     — required (personal access token from the account page)
    OANDA_ACCOUNT_ID  — required (v20 account id; kept for future order-book
                        endpoints even though /candles doesn't need it)
    OANDA_ENV         — optional: "practice" (default) or "live"

Pairs are accepted as EURUSD, EUR/USD or EUR_USD and normalized to OANDA's
EUR_USD instrument form. Candles use mid prices; volume is tick volume (count
of price changes), which is the only volume forex has — documented, not hidden.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone

import requests

from alpha_engine.cache.interface import Cache
from alpha_engine.cache.models import Candle, Interval, PriceSeries

_HOSTS = {
    "practice": "https://api-fxpractice.oanda.com",
    "live": "https://api-fxtrade.oanda.com",
}

# Major pairs the CLI auto-detects. Extend as needed; any 6-letter pair of
# known currency codes routes here even if not listed.
MAJOR_PAIRS = {
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "USDCHF",
    "AUDUSD",
    "USDCAD",
    "NZDUSD",
}

_CURRENCIES = {
    "USD",
    "EUR",
    "GBP",
    "JPY",
    "CHF",
    "AUD",
    "CAD",
    "NZD",
    "INR",
    "SGD",
    "HKD",
    "CNH",
}

_PAIR_RE = re.compile(r"^([A-Z]{3})[/_-]?([A-Z]{3})$")


class MissingAPIKeyError(RuntimeError):
    """Raised when the OANDA adapter is asked to fetch without credentials."""


def supports(asset: str) -> bool:
    """True when the symbol parses as a currency pair of known codes."""
    return normalize_pair(asset) is not None


def normalize_pair(asset: str) -> str | None:
    """EURUSD / EUR/USD / EUR_USD -> EUR_USD; None if it isn't a forex pair."""
    m = _PAIR_RE.match(asset.upper())
    if not m:
        return None
    base, quote = m.group(1), m.group(2)
    if base not in _CURRENCIES or quote not in _CURRENCIES or base == quote:
        return None
    return f"{base}_{quote}"


def fetch_daily(
    asset: str,
    days: int = 90,
    cache: Cache | None = None,
    api_key: str | None = None,
) -> PriceSeries:
    """Fetch daily mid-price candles for a forex pair, normalize, cache, return.

    The asset is cached under its compact form (EURUSD) so the rest of the
    engine treats it like any other symbol.
    """
    key = api_key or os.environ.get("OANDA_API_KEY")
    if not key:
        raise MissingAPIKeyError(
            "OANDA_API_KEY not set. Forex needs a free OANDA practice account "
            "(https://www.oanda.com) — create one, generate a personal access "
            "token, and set OANDA_API_KEY / OANDA_ACCOUNT_ID."
        )

    instrument = normalize_pair(asset)
    if instrument is None:
        raise ValueError(f"{asset} does not parse as a forex pair (e.g. EURUSD, EUR/USD).")

    env = os.environ.get("OANDA_ENV", "practice")
    host = _HOSTS.get(env)
    if host is None:
        raise ValueError(f"OANDA_ENV must be 'practice' or 'live', not {env!r}.")

    cache = cache or Cache()
    resp = requests.get(
        f"{host}/v3/instruments/{instrument}/candles",
        params={"granularity": "D", "count": str(min(days, 500)), "price": "M"},
        headers={"Authorization": f"Bearer {key}"},
        timeout=20,
    )
    resp.raise_for_status()
    payload = resp.json()

    candles = []
    for row in payload.get("candles", []):
        if not row.get("complete", True):
            continue  # skip the still-forming bar; analyzers assume closed candles
        mid = row.get("mid", {})
        ts_raw = row.get("time", "")
        # OANDA timestamps are RFC3339 with nanoseconds; trim to microseconds.
        ts_raw = re.sub(r"(\.\d{6})\d*", r"\1", ts_raw).replace("Z", "+00:00")
        candles.append(
            Candle(
                ts=datetime.fromisoformat(ts_raw).astimezone(timezone.utc),
                open=float(mid["o"]),
                high=float(mid["h"]),
                low=float(mid["l"]),
                close=float(mid["c"]),
                volume=float(row.get("volume", 0)) or None,
            )
        )

    series = PriceSeries(asset=instrument.replace("_", ""), interval=Interval.DAY, candles=candles)
    cache.put_price(series)
    return series
