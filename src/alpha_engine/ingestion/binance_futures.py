"""Binance futures: funding rate and open interest. Keyless.

FUTURE_WORK calls this "the highest-value/lowest-cost item in the whole phase",
and that is right. The funding rate is a genuine positioning signal — perpetual
futures have no expiry, so an exchange keeps their price tethered to spot by
making one side pay the other every eight hours. When funding is strongly
positive, longs are paying shorts to keep their position open, which means the
crowd is leaning long and paying for the privilege. That is a *contrarian* read,
and Binance serves it without a key.

Open interest is the total value of contracts currently open. Rising OI with
rising price means new money entering; rising OI with falling price means shorts
piling in. Alone it is directionless, which is why the analyzer pairs it with
price.

Availability: api.binance.com is geo-restricted in some jurisdictions. Like the
spot adapter, this one is best-effort — failures return empty and the crypto
read simply proceeds without positioning data.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

from alpha_engine import net
from alpha_engine.cache.interface import Cache
from alpha_engine.cache.models import OnChainObservation

_BASE = "https://fapi.binance.com/fapi/v1"
_FUTURES_DATA = "https://fapi.binance.com/futures/data"
SOURCE = "binance_futures"

# Perpetual contract symbols for the assets the engine supports.
_SYMBOLS: dict[str, str] = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
}


def supports(asset: str) -> bool:
    return asset.upper() in _SYMBOLS


def _get_json(url: str, params: dict[str, str], what: str) -> list | dict | None:
    """One shared fetch path. Returns None on any failure, having said so."""
    try:
        resp = net.get(url, params=params, timeout=20)
        if resp.status_code >= 400:
            print(f"[binance_futures] {what}: HTTP {resp.status_code}", file=sys.stderr)
            return None
        return resp.json()
    except Exception as e:  # noqa: BLE001 - positioning data is optional context
        print(f"[binance_futures] {what}: fetch failed ({e})", file=sys.stderr)
        return None


def fetch_funding_rate(
    asset: str, limit: int = 100, cache: Cache | None = None
) -> list[OnChainObservation]:
    """Historical funding rates. Binance prints one every 8 hours, so the
    default 100 covers roughly the last month."""
    asset = asset.upper()
    symbol = _SYMBOLS.get(asset)
    if symbol is None:
        return []

    rows = _get_json(
        f"{_BASE}/fundingRate",
        {"symbol": symbol, "limit": str(min(limit, 1000))},
        f"funding {asset}",
    )
    if not isinstance(rows, list):
        return []

    obs: list[OnChainObservation] = []
    for row in rows:
        try:
            obs.append(
                OnChainObservation(
                    metric=f"funding_rate_{asset}",
                    ts=datetime.fromtimestamp(int(row["fundingTime"]) / 1000, tz=timezone.utc),
                    value=float(row["fundingRate"]),
                    source=SOURCE,
                )
            )
        except (KeyError, TypeError, ValueError):
            continue  # a malformed row is one lost observation, not a failure

    if obs and cache is not None:
        cache.put_onchain(f"funding_rate_{asset}", obs)
    return obs


def fetch_open_interest(
    asset: str, period: str = "1d", limit: int = 60, cache: Cache | None = None
) -> list[OnChainObservation]:
    """Historical open interest, in USD notional.

    Note the endpoint lives under /futures/data (not /fapi/v1) and only retains
    about 30 days of history regardless of `limit` — Binance's rule, not ours.
    """
    asset = asset.upper()
    symbol = _SYMBOLS.get(asset)
    if symbol is None:
        return []

    rows = _get_json(
        f"{_FUTURES_DATA}/openInterestHist",
        {"symbol": symbol, "period": period, "limit": str(min(limit, 500))},
        f"open interest {asset}",
    )
    if not isinstance(rows, list):
        return []

    obs: list[OnChainObservation] = []
    for row in rows:
        try:
            obs.append(
                OnChainObservation(
                    metric=f"open_interest_{asset}",
                    ts=datetime.fromtimestamp(int(row["timestamp"]) / 1000, tz=timezone.utc),
                    value=float(row["sumOpenInterestValue"]),
                    source=SOURCE,
                )
            )
        except (KeyError, TypeError, ValueError):
            continue

    if obs and cache is not None:
        cache.put_onchain(f"open_interest_{asset}", obs)
    return obs


def fetch_all(asset: str, cache: Cache | None = None) -> list[OnChainObservation]:
    """Both metrics for one asset. Partial success is success: funding without
    OI still tells you something real."""
    cache = cache or Cache()
    return [
        *fetch_funding_rate(asset, cache=cache),
        *fetch_open_interest(asset, cache=cache),
    ]
