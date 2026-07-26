"""Financial Modeling Prep daily equity candles. Key-gated fallback for Yahoo.

Why a fallback exists at all
---------------------------
Yahoo is the only keyless source for equity prices, and it throttles by IP with
**HTTP 429** — observed from a GitHub Actions runner and from a residential
connection after a burst of scans. Every keyless alternative checked on
2026-07-26 is gone: Stooq now answers a JavaScript proof-of-work challenge at
HTTP 200 on both its .com and .pl domains, which is worse than a refusal because
a naive client caches the challenge page as data.

So the second tier has to be key-gated, and that is fine as long as the FIRST
tier never is. The cardinal rule is that the default path stays keyless, not
that keys are forbidden: without `FMP_API_KEY` this module reports itself
unavailable and the equity path is exactly what it was before.

FMP is the choice because the repo already carries an `FMP_API_KEY` for
fundamentals, so an operator who wants resilience adds nothing new.

HONESTY BOUNDARY — read before relying on this
-----------------------------------------------
The request shape follows FMP's published v3 `historical-price-full` contract
and the parsing is unit-tested against recorded response shapes, but it has NOT
been round-tripped against a live key in this codebase — FMP's `demo` key does
not cover this endpoint. Before depending on it, set a real key and run
`alpha-engine scan AAPL` once with Yahoo blocked, and confirm the bars match.
Treat the first real fetch as the actual test.

`adjClose` is deliberately ignored. Yahoo's chart endpoint returns unadjusted
closes, and a series that silently switches between adjusted and unadjusted
prices puts a step at every historical split — which every trend analyzer reads
as a real move.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

from alpha_engine import health, net
from alpha_engine.cache.interface import Cache
from alpha_engine.cache.models import Candle, Interval, PriceSeries

_BASE = "https://financialmodelingprep.com/api/v3"
SOURCE = "fmp"


def has_key() -> bool:
    """True when an FMP key is configured. Absent means this tier is skipped."""
    return bool(os.environ.get("FMP_API_KEY"))


def _parse(payload: dict, asset: str) -> list[Candle]:
    """Normalize FMP's `historical-price-full` body into candles, oldest first.

    Pure, so the parsing rules are testable without a key or a network. Rows
    missing a close are dropped rather than guessed at, matching `yahoo.py`.
    """
    if not isinstance(payload, dict):
        raise ValueError("FMP returned a non-object body")
    if "Error Message" in payload:
        raise ValueError(f"FMP error: {payload['Error Message']}")

    rows = payload.get("historical")
    if not isinstance(rows, list):
        raise ValueError("FMP response has no 'historical' list")

    candles: list[Candle] = []
    for row in rows:
        try:
            close = row["close"]
            if close is None:
                continue
            candles.append(
                Candle(
                    ts=datetime.strptime(row["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc),
                    open=row.get("open") if row.get("open") is not None else close,
                    high=row.get("high") if row.get("high") is not None else close,
                    low=row.get("low") if row.get("low") is not None else close,
                    close=close,
                    volume=row.get("volume"),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue  # one malformed row costs one bar, not the fetch

    # FMP returns newest-first; every consumer in this repo assumes oldest-first.
    candles.sort(key=lambda c: c.ts)
    return candles


def fetch_daily(asset: str, days: int = 365, cache: Cache | None = None) -> PriceSeries:
    """Fetch daily OHLCV for an equity symbol. Raises if no key is configured —
    callers check `has_key()` first, so reaching here without one is a bug."""
    if not has_key():
        raise RuntimeError("FMP_API_KEY is not set")

    cache = cache or Cache()
    asset = asset.upper()

    try:
        resp = net.get_with_retry(
            f"{_BASE}/historical-price-full/{asset}",
            params={"timeseries": str(max(days, 1)), "apikey": os.environ["FMP_API_KEY"]},
            timeout=20,
        )
        resp.raise_for_status()
        candles = _parse(resp.json(), asset)
    except Exception as e:  # noqa: BLE001 - recorded, then re-raised for the caller
        health.record(f"price.{SOURCE}", error=f"{type(e).__name__}: {e}")
        raise

    if not candles:
        health.record(f"price.{SOURCE}", items=0)
        raise ValueError(f"FMP returned no usable bars for {asset}")

    print(f"[ingest] {asset}: {len(candles)} bars from FMP", file=sys.stderr)
    health.record(f"price.{SOURCE}", items=len(candles))
    series = PriceSeries(asset=asset, interval=Interval.DAY, candles=candles)
    cache.put_price(series)
    return series
