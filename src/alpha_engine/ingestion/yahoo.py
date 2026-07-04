"""Yahoo Finance ingestion adapter for US equity daily candles. Chosen over the
plan's original Finnhub suggestion because Yahoo's chart endpoint needs no API
key, which keeps the equity path as zero-setup as the crypto one. (Finnhub's
free tier no longer includes stock candles; Stooq now sits behind a JavaScript
challenge. Verified at build time.)

Same contract as every ingestion adapter: pull, normalize into cache models,
write to the cache. Zero analysis. Analyzers never import this module.

Yahoo's endpoint is unofficial-but-stable and requires a browser-ish
User-Agent. Be a polite guest: the cache TTL means one fetch a day per symbol.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import requests

from alpha_engine.cache.interface import Cache
from alpha_engine.cache.models import Candle, Interval, PriceSeries

_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"
_HEADERS = {"User-Agent": "Mozilla/5.0 (alpha-engine; research/education tool)"}


def _parse_chart(payload: dict) -> list[Candle]:
    """Normalize Yahoo's chart JSON into candles. Pure function so the parsing
    rules are unit-testable without the network. Bars with a null close (halts,
    partial rows Yahoo sometimes emits) are dropped rather than guessed at."""
    chart = payload.get("chart", {})
    error = chart.get("error")
    if error:
        raise ValueError(f"Yahoo chart error: {error.get('description', error)}")
    results = chart.get("result") or []
    if not results:
        raise ValueError("Yahoo chart returned no result")

    result = results[0]
    timestamps = result.get("timestamp") or []
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []

    candles: list[Candle] = []
    for i, ts in enumerate(timestamps):
        if i >= len(closes) or closes[i] is None:
            continue
        close = closes[i]
        candles.append(
            Candle(
                ts=datetime.fromtimestamp(ts, tz=timezone.utc),
                open=opens[i] if i < len(opens) and opens[i] is not None else close,
                high=highs[i] if i < len(highs) and highs[i] is not None else close,
                low=lows[i] if i < len(lows) and lows[i] is not None else close,
                close=close,
                volume=volumes[i] if i < len(volumes) else None,
            )
        )
    return candles


def fetch_daily(asset: str, days: int = 365, cache: Cache | None = None) -> PriceSeries:
    """Fetch daily OHLCV for a US equity symbol, normalize, cache, and return it."""
    cache = cache or Cache()
    asset = asset.upper()

    now = int(time.time())
    resp = requests.get(
        f"{_BASE}/{asset}",
        params={
            "period1": str(now - days * 86400),
            "period2": str(now),
            "interval": "1d",
            "events": "history",
        },
        headers=_HEADERS,
        timeout=20,
    )
    resp.raise_for_status()
    candles = _parse_chart(resp.json())

    series = PriceSeries(asset=asset, interval=Interval.DAY, candles=candles)
    cache.put_price(series)
    return series
