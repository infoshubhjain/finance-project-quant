"""CoinGecko Pro adapter: the *keyed upgrade path* over the keyless default.

Same provider, same response shape, same normalization as `coingecko.py` —
so this module is only the credential gate plus the authenticated host. The
actual fetch is coingecko.fetch_daily pointed at the Pro base URL; analyzers
can't tell (and must not care) which tier fed the cache.

Credential gating follows the FRED pattern: no COINGECKO_API_KEY means a
descriptive MissingAPIKeyError, and the CLI simply keeps using the keyless
adapter — the default clone never requires this module.
"""

from __future__ import annotations

import os

from alpha_engine.cache.interface import Cache
from alpha_engine.cache.models import PriceSeries
from alpha_engine.ingestion import coingecko

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
    """Fetch daily prices from CoinGecko Pro, normalize, cache, and return."""
    key = api_key or os.environ.get("COINGECKO_API_KEY")
    if not key:
        raise MissingAPIKeyError(
            "COINGECKO_API_KEY not set. The keyless CoinGecko adapter works without "
            "it; a Pro key only raises rate limits (https://www.coingecko.com/en/api)."
        )
    return coingecko.fetch_daily(
        asset,
        days=days,
        cache=cache,
        base=_PRO_BASE,
        headers={"x-cg-pro-api-key": key},
    )
