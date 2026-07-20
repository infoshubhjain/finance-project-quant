"""Glassnode on-chain metrics — key-gated.

True on-chain data: exchange balances, active addresses, and net flows. This is
what the original blueprint meant by a "crypto agent" — the chain itself, rather
than another moving average with a different name.

The single most useful free-tier metric is exchange net flow. Coins moving *onto*
exchanges are coins positioned to be sold; coins moving *off* exchanges are coins
going into storage. It is one of the few genuinely non-price signals available in
crypto.

Gating: no `GLASSNODE_API_KEY` means this module reports unavailable and the
crypto analyzer proceeds on the keyless Binance funding/OI data alone.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

from alpha_engine import net
from alpha_engine.cache.interface import Cache
from alpha_engine.cache.models import OnChainObservation
from alpha_engine.config import load_project_env

_BASE = "https://api.glassnode.com/v1/metrics"
SOURCE = "glassnode"

# Tier-1 (free) daily metrics, mapped to the endpoint path each lives at.
METRICS: dict[str, str] = {
    "exchange_netflow": "transactions/transfers_volume_exchanges_net",
    "active_addresses": "addresses/active_count",
    "exchange_balance": "distribution/balance_exchanges",
}

_ASSET_SYMBOLS = {"BTC": "BTC", "ETH": "ETH"}


def has_key() -> bool:
    load_project_env()
    return bool(os.environ.get("GLASSNODE_API_KEY"))


def supports(asset: str) -> bool:
    return asset.upper() in _ASSET_SYMBOLS


def fetch_metric(
    metric: str,
    asset: str = "BTC",
    cache: Cache | None = None,
) -> list[OnChainObservation]:
    """Fetch one daily on-chain metric. Empty list on any failure or no key."""
    if metric not in METRICS:
        raise ValueError(f"unknown metric '{metric}'. Known: {', '.join(sorted(METRICS))}")

    asset = asset.upper()
    if not supports(asset):
        return []

    if not has_key():
        print(
            "[glassnode] GLASSNODE_API_KEY not set; skipping on-chain metrics "
            "(free tier: https://glassnode.com)",
            file=sys.stderr,
        )
        return []

    try:
        resp = net.get(
            f"{_BASE}/{METRICS[metric]}",
            params={
                "a": asset,
                "i": "24h",
                "api_key": os.environ["GLASSNODE_API_KEY"],
            },
            timeout=20,
        )
        if resp.status_code >= 400:
            print(f"[glassnode] {metric}: HTTP {resp.status_code}", file=sys.stderr)
            return []
        rows = resp.json()
    except Exception as e:  # noqa: BLE001 - on-chain data is optional context
        print(f"[glassnode] {metric} fetch failed: {e}", file=sys.stderr)
        return []

    if not isinstance(rows, list):
        return []

    obs: list[OnChainObservation] = []
    for row in rows:
        try:
            # Glassnode rows are {"t": unix_seconds, "v": value}; v may be null
            # for days with no data, which is missing, not zero.
            if row.get("v") is None:
                continue
            obs.append(
                OnChainObservation(
                    metric=f"{metric}_{asset}",
                    ts=datetime.fromtimestamp(int(row["t"]), tz=timezone.utc),
                    value=float(row["v"]),
                    chain="bitcoin" if asset == "BTC" else "ethereum",
                    source=SOURCE,
                )
            )
        except (KeyError, TypeError, ValueError):
            continue

    if obs and cache is not None:
        cache.put_onchain(f"{metric}_{asset}", obs)
    return obs


def fetch_all(asset: str = "BTC", cache: Cache | None = None) -> list[OnChainObservation]:
    """Every known metric for one asset."""
    cache = cache or Cache()
    out: list[OnChainObservation] = []
    for metric in METRICS:
        out.extend(fetch_metric(metric, asset=asset, cache=cache))
    return out
