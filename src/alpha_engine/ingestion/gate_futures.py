"""Gate.io USDT perpetuals: funding rate and open interest. Keyless.

The tier that actually holds. Every other futures source in this repo is blocked
from somewhere that matters, and the blocks point in opposite directions —
measured from a GitHub Actions runner and from a US home connection on
2026-07-26:

    exchange   home     CI        note
    binance    200      451       regulatory geo-block on datacenter ranges
    bybit      200      403       CloudFront country block
    okx        timeout  200       unreachable from the US residential IP
    kraken     timeout  200       same
    gate       200      200       <- this one
    deribit    200      200       BTC/ETH only
    kucoin     200      200
    bitget     200      200

That table is why `orchestrator/engine.py` walks a *chain* of adapters rather
than having one fallback: a single second choice would have fixed CI and broken
the laptop, or the reverse. Gate is the tier that answers in both places, so it
is what the scheduled scan ends up using.

Units
-----
`open_interest` here is a **contract count**, and a Gate BTC contract is 0.0001
BTC — so this number is ~10,000x the base-coin figure Binance and Bybit report.
That is fine, and the reason is worth stating because it looks like a bug:
`analyzers/crypto_onchain.py` reads open interest only as `last / first` over a
window, and `_by_metric` there refuses to mix two sources into one series. The
ratio is therefore unit-free and the series is always internally consistent.

Matching units across exchanges was the original plan and it does not survive
contact with reality — three venues, three conventions (base coin, contracts,
USD notional). The single-source rule is the guard that actually holds, and it
holds for a fourth adapter nobody has written yet.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

from alpha_engine import net
from alpha_engine.cache.interface import Cache
from alpha_engine.cache.models import OnChainObservation

_BASE = "https://api.gateio.ws/api/v4/futures/usdt"
SOURCE = "gate_futures"

# USDT-margined perpetual contracts, matching the coverage of the other adapters.
_SYMBOLS: dict[str, str] = {
    "BTC": "BTC_USDT",
    "ETH": "ETH_USDT",
    "SOL": "SOL_USDT",
}


def supports(asset: str) -> bool:
    return asset.upper() in _SYMBOLS


def _get_rows(path: str, params: dict[str, str], what: str) -> list[dict] | None:
    """One shared fetch path. Returns None on any failure, having said so.

    Gate returns a bare JSON array on success and a `{"label": ...}` object on
    error, so a non-list body is an error even when the status is 200.
    """
    try:
        resp = net.get(f"{_BASE}/{path}", params=params, timeout=20)
        if resp.status_code >= 400:
            print(f"[gate_futures] {what}: HTTP {resp.status_code}", file=sys.stderr)
            return None
        payload = resp.json()
    except Exception as e:  # noqa: BLE001 - positioning data is optional context
        print(f"[gate_futures] {what}: fetch failed ({e})", file=sys.stderr)
        return None

    if not isinstance(payload, list):
        label = payload.get("label") if isinstance(payload, dict) else None
        print(
            f"[gate_futures] CONTRACT BROKEN {what}: expected a list"
            + (f" (error: {label})" if label else ""),
            file=sys.stderr,
        )
        return None
    return payload


def fetch_funding_rate(
    asset: str, limit: int = 100, cache: Cache | None = None
) -> list[OnChainObservation]:
    """Historical funding rates, newest first from the API.

    Gate prints funding every 8 hours like the others, and a funding rate is a
    dimensionless fraction, so these values are directly comparable with
    Binance's and Bybit's. Timestamps arrive in SECONDS here, not milliseconds
    — the other two adapters use milliseconds, and getting this wrong dates
    every observation to 1970 and silently empties the analyzer's window.
    """
    asset = asset.upper()
    contract = _SYMBOLS.get(asset)
    if contract is None:
        return []

    rows = _get_rows(
        "funding_rate",
        {"contract": contract, "limit": str(min(limit, 1000))},
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
                    ts=datetime.fromtimestamp(int(row["t"]), tz=timezone.utc),
                    value=float(row["r"]),
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
    """Historical open interest, in CONTRACTS (see the module docstring).

    Comes from `contract_stats`, which bundles open interest with long/short
    ratios and liquidation sizes. Only open interest is taken: the rest has no
    consumer, and an unused field in a cache is a field nobody notices going
    stale.
    """
    asset = asset.upper()
    contract = _SYMBOLS.get(asset)
    if contract is None:
        return []

    rows = _get_rows(
        "contract_stats",
        {"contract": contract, "interval": period, "limit": str(min(limit, 100))},
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
                    ts=datetime.fromtimestamp(int(row["time"]), tz=timezone.utc),
                    value=float(row["open_interest"]),
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
