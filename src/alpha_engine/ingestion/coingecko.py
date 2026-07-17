"""CoinGecko ingestion adapter. Chosen as the default source because it needs no
API key, so a freshly cloned repo produces real data immediately. This is the
"zero-setup default" the build plan calls for.

An ingestion adapter's only job: pull from the source, normalize into cache models,
write to the cache. It contains zero analysis. Analyzers never import this module.

Rate limits: CoinGecko's keyless tier is generous but finite. The cache TTL means
we don't hammer it; one fetch fills the store and consumers read locally.
"""

from __future__ import annotations

from datetime import datetime, timezone

from alpha_engine import net
from alpha_engine.cache.interface import Cache
from alpha_engine.cache.models import Candle, Interval, PriceSeries

_BASE = "https://api.coingecko.com/api/v3"

# Map our generic asset symbols to CoinGecko's ids. Extend as needed.
_COINGECKO_IDS: dict[str, str] = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
}


def supports(asset: str) -> bool:
    """True if this adapter can serve the symbol. The CLI uses this to
    auto-detect market: mapped crypto symbols route here, everything else is
    treated as a US equity ticker."""
    return asset.upper() in _COINGECKO_IDS


def fetch_daily(
    asset: str,
    days: int = 90,
    cache: Cache | None = None,
    *,
    base: str = _BASE,
    headers: dict[str, str] | None = None,
) -> PriceSeries:
    """Fetch daily OHLC for a crypto asset, normalize, cache, and return it.

    Raises a clear error if the asset isn't mapped, so a user adding a new coin
    knows exactly what to do. `base`/`headers` exist so the Pro adapter can
    reuse this exact fetch against the authenticated host — same response
    shape, same normalization, one implementation.
    """
    cache = cache or Cache()
    asset = asset.upper()
    coin_id = _COINGECKO_IDS.get(asset)
    if coin_id is None:
        raise ValueError(f"{asset} not mapped to a CoinGecko id. Add it to _COINGECKO_IDS.")

    # /market_chart returns dense daily points: {"prices": [[ts_ms, price], ...]}.
    # The keyless /ohlc endpoint is too sparse for multi-bar moving averages, so we
    # use market_chart and treat each daily close as the bar. (OHLC detail can be
    # layered back in later from a keyed source without changing anything downstream.)
    resp = net.get(
        f"{base}/coins/{coin_id}/market_chart",
        params={"vs_currency": "usd", "days": str(days), "interval": "daily"},
        headers=headers,
        timeout=20,
    )
    resp.raise_for_status()
    payload = resp.json()
    prices = payload.get("prices", [])
    # Same response carries daily volumes, aligned by timestamp. Map them so
    # volume-based features work on the keyless default path.
    volume_by_ts = {int(ts_ms): vol for ts_ms, vol in payload.get("total_volumes", [])}

    candles = [
        Candle(
            ts=datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc),
            open=price,
            high=price,
            low=price,
            close=price,
            volume=volume_by_ts.get(int(ts_ms)),
        )
        for ts_ms, price in prices
    ]

    series = PriceSeries(asset=asset, interval=Interval.DAY, candles=candles)
    cache.put_price(series)
    return series
