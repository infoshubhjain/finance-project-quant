"""CoinGecko Pro adapter: the *keyed upgrade path* over the keyless default.

Same provider and response shape as `coingecko.py`, but authenticated against
the Pro host, which lifts the rate limits and history caps. Nothing downstream
changes: the adapter normalizes into the same PriceSeries, so analyzers can't
tell (and must not care) which tier fed the cache.

Credential gating follows the FRED pattern: no COINGECKO_API_KEY means a
descriptive MissingAPIKeyError, and the CLI simply keeps using the keyless
adapter — the default clone never requires this module.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import requests

from alpha_engine.cache.interface import Cache
from alpha_engine.cache.models import Candle, Interval, PriceSeries
from alpha_engine.ingestion.coingecko import _COINGECKO_IDS

_PRO_BASE = "https://pro-api.coingecko.com/api/v3"


class MissingAPIKeyError(RuntimeError):
    """Raised when the Pro adapter is asked to fetch without a key."""


def has_key() -> bool:
    return bool(os.environ.get("COINGECKO_API_KEY"))


def fetch_daily(
    asset: str,
    days: int = 90,
    cache: Cache | None = None,
    api_key: str | None = None,
) -> PriceSeries:
    """Fetch daily prices from CoinGecko Pro, normalize, cache, and return.

    Shares the symbol map with the keyless adapter so both tiers cover the
    same assets — a Pro key should widen limits, never change coverage.
    """
    key = api_key or os.environ.get("COINGECKO_API_KEY")
    if not key:
        raise MissingAPIKeyError(
            "COINGECKO_API_KEY not set. The keyless CoinGecko adapter works without "
            "it; a Pro key only raises rate limits (https://www.coingecko.com/en/api)."
        )

    cache = cache or Cache()
    asset = asset.upper()
    coin_id = _COINGECKO_IDS.get(asset)
    if coin_id is None:
        raise ValueError(f"{asset} not mapped to a CoinGecko id. Add it to _COINGECKO_IDS.")

    resp = requests.get(
        f"{_PRO_BASE}/coins/{coin_id}/market_chart",
        params={"vs_currency": "usd", "days": str(days), "interval": "daily"},
        headers={"x-cg-pro-api-key": key},
        timeout=20,
    )
    resp.raise_for_status()
    prices = resp.json().get("prices", [])

    candles = [
        Candle(
            ts=datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc),
            open=price,
            high=price,
            low=price,
            close=price,
        )
        for ts_ms, price in prices
    ]

    series = PriceSeries(asset=asset, interval=Interval.DAY, candles=candles)
    cache.put_price(series)
    return series
