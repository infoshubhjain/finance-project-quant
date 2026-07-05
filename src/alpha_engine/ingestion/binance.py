"""Binance public-data adapter: the keyless crypto *fallback*.

PLAN.md's Task 2a asked for CoinCap in this role, but CoinCap's keyless v2 API
was shut down in 2025 and v3 requires a paid key — so the honest replacement
is Binance's public market-data endpoint, which needs no key and actually
returns full OHLCV (CoinGecko's keyless tier only gives closes). The role is
unchanged: when CoinGecko rate-limits (HTTP 429) or errors, the CLI retries
the fetch here so a scan still completes with zero setup.

Availability note, stated plainly: api.binance.com is geo-restricted in a few
jurisdictions. The CLI treats this adapter as best-effort — if both sources
fail, the scan reports the error rather than pretending.

Rate limits: the public klines endpoint allows far more than we ever ask for;
the cache TTL keeps calls rare regardless.
"""

from __future__ import annotations

from datetime import datetime, timezone

import requests

from alpha_engine.cache.interface import Cache
from alpha_engine.cache.models import Candle, Interval, PriceSeries

_BASE = "https://api.binance.com/api/v3"

# Map our generic asset symbols to Binance spot pairs (USDT-quoted).
_BINANCE_SYMBOLS: dict[str, str] = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
}


def supports(asset: str) -> bool:
    """True if this adapter can serve the symbol."""
    return asset.upper() in _BINANCE_SYMBOLS


def fetch_daily(asset: str, days: int = 90, cache: Cache | None = None) -> PriceSeries:
    """Fetch daily OHLCV for a crypto asset, normalize, cache, and return it.

    Binance klines rows are positional arrays:
    [open_time_ms, open, high, low, close, volume, close_time_ms, ...]
    """
    cache = cache or Cache()
    asset = asset.upper()
    symbol = _BINANCE_SYMBOLS.get(asset)
    if symbol is None:
        raise ValueError(f"{asset} not mapped to a Binance pair. Add it to _BINANCE_SYMBOLS.")

    resp = requests.get(
        f"{_BASE}/klines",
        params={"symbol": symbol, "interval": "1d", "limit": str(min(days, 1000))},
        timeout=20,
    )
    resp.raise_for_status()
    rows = resp.json()

    candles = [
        Candle(
            ts=datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
        )
        for row in rows
    ]

    series = PriceSeries(asset=asset, interval=Interval.DAY, candles=candles)
    cache.put_price(series)
    return series
