"""Bybit perpetuals: funding rate and open interest. Keyless.

Why this exists when `binance_futures.py` already does the same job
------------------------------------------------------------------
Binance geo-blocks datacenter IP ranges. From a laptop it answers fine; from a
GitHub Actions runner every request comes back **HTTP 451 Unavailable For Legal
Reasons**. That is not a flaky network — it is a permanent, deterministic
refusal, and it meant the scheduled scan had been running with *zero* futures
positioning data while the job reported success.

That failure mode is the exact one this repo is built to notice, and it slipped
through because the health record was written per *kind* (`onchain`) rather than
per feed: CoinGecko's dominance number kept the aggregate at "1 item, ok" while
six of seven fetches returned nothing.

Bybit serves the same two metrics, keyless, and answers from datacenter IPs. It
is the fallback, not the replacement: `binance_futures` is tried first because
it is the deeper market, and this takes over when Binance refuses.

Units — the part that will bite you
-----------------------------------
Bybit reports open interest in **base coin** (57,875 BTC). Binance's
`sumOpenInterestValue` is **USD notional** (~$6.9bn). The analyzer reads OI as a
ratio over a window, so either unit works on its own — but a series holding both
has a ~100,000x step at the switchover, which reads as an enormous fake OI
build-up and would flip the conviction multiplier.

Two things prevent that, and both are needed:

1. `binance_futures` records `sumOpenInterest` (contracts), not
   `sumOpenInterestValue`, so both adapters emit the same unit.
2. `analyzers/crypto_onchain.py` reads only one source per metric — see
   `_by_metric` there. That is the guard that holds even if a third adapter
   arrives later with a third unit.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

from alpha_engine import net
from alpha_engine.cache.interface import Cache
from alpha_engine.cache.models import OnChainObservation

_BASE = "https://api.bybit.com/v5/market"
SOURCE = "bybit_futures"

# Linear (USDT-margined) perpetual symbols, matching binance_futures' coverage.
_SYMBOLS: dict[str, str] = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
}


def supports(asset: str) -> bool:
    return asset.upper() in _SYMBOLS


def _get_rows(path: str, params: dict[str, str], what: str) -> list[dict] | None:
    """One shared fetch path. Returns None on any failure, having said so.

    Bybit signals application errors in the body (`retCode != 0`) with an HTTP
    200, so checking the status alone would treat "invalid symbol" as success
    and cache an empty series as though the market had gone quiet.
    """
    try:
        resp = net.get(f"{_BASE}/{path}", params=params, timeout=20)
        if resp.status_code >= 400:
            print(f"[bybit_futures] {what}: HTTP {resp.status_code}", file=sys.stderr)
            return None
        payload = resp.json()
    except Exception as e:  # noqa: BLE001 - positioning data is optional context
        print(f"[bybit_futures] {what}: fetch failed ({e})", file=sys.stderr)
        return None

    if not isinstance(payload, dict):
        print(f"[bybit_futures] CONTRACT BROKEN {what}: expected an object", file=sys.stderr)
        return None
    if payload.get("retCode") != 0:
        print(
            f"[bybit_futures] {what}: retCode={payload.get('retCode')} "
            f"{payload.get('retMsg', '')}".rstrip(),
            file=sys.stderr,
        )
        return None

    rows = (payload.get("result") or {}).get("list")
    if not isinstance(rows, list):
        print(f"[bybit_futures] CONTRACT BROKEN {what}: result.list is not a list", file=sys.stderr)
        return None
    return rows


def fetch_funding_rate(
    asset: str, limit: int = 100, cache: Cache | None = None
) -> list[OnChainObservation]:
    """Historical funding rates. Bybit prints one every 8 hours, so the default
    100 covers roughly the last month — same cadence as Binance, so the two are
    directly comparable (a funding rate is a dimensionless fraction)."""
    asset = asset.upper()
    symbol = _SYMBOLS.get(asset)
    if symbol is None:
        return []

    rows = _get_rows(
        "funding/history",
        {"category": "linear", "symbol": symbol, "limit": str(min(limit, 200))},
        f"funding {asset}",
    )
    if rows is None:
        return []

    obs: list[OnChainObservation] = []
    for row in rows:
        try:
            obs.append(
                OnChainObservation(
                    metric=f"funding_rate_{asset}",
                    ts=datetime.fromtimestamp(
                        int(row["fundingRateTimestamp"]) / 1000, tz=timezone.utc
                    ),
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
    """Historical open interest, in BASE COIN (not USD).

    See the module docstring: this unit must match what `binance_futures` emits,
    or a series that switches source mid-window reports a fabricated build-up.
    """
    asset = asset.upper()
    symbol = _SYMBOLS.get(asset)
    if symbol is None:
        return []

    rows = _get_rows(
        "open-interest",
        {
            "category": "linear",
            "symbol": symbol,
            "intervalTime": period,
            "limit": str(min(limit, 200)),
        },
        f"open interest {asset}",
    )
    if rows is None:
        return []

    obs: list[OnChainObservation] = []
    for row in rows:
        try:
            obs.append(
                OnChainObservation(
                    metric=f"open_interest_{asset}",
                    ts=datetime.fromtimestamp(int(row["timestamp"]) / 1000, tz=timezone.utc),
                    value=float(row["openInterest"]),
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
