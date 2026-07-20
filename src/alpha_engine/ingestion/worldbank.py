"""World Bank open data — global indicators, keyless.

The World Bank publishes a genuinely open, keyless, stable JSON API. It is
annual data, so it is useless for timing and valuable for regime: which
economies are growing, where inflation actually sits, how the cross-country
picture has shifted.

Chosen for exactly that reason — it is the one macro source that will still work
in five years without an account, and it covers every country rather than just
the US.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

from alpha_engine import net
from alpha_engine.cache.interface import Cache
from alpha_engine.cache.models import MacroObservation

_BASE = "https://api.worldbank.org/v2"
SOURCE = "worldbank"

# Indicator code -> readable series id. Kept short: these are the four that
# actually change how you read a market.
INDICATORS: dict[str, str] = {
    "NY.GDP.MKTP.KD.ZG": "GDP_GROWTH",
    "FP.CPI.TOTL.ZG": "CPI_INFLATION",
    "FR.INR.RINR": "REAL_INTEREST_RATE",
    "SL.UEM.TOTL.ZS": "UNEMPLOYMENT",
}

# ISO-2 country codes for the regions this engine covers.
COUNTRIES: dict[str, str] = {"us": "US", "in": "IN", "global": "WLD"}


def fetch_indicator(
    indicator: str,
    country: str = "US",
    years: int = 20,
    cache: Cache | None = None,
) -> list[MacroObservation]:
    """Fetch one indicator for one country. Empty list on any failure."""
    if indicator not in INDICATORS:
        raise ValueError(f"unknown indicator '{indicator}'. Known: {', '.join(INDICATORS)}")

    series_id = f"{INDICATORS[indicator]}_{country.upper()}"

    try:
        resp = net.get(
            f"{_BASE}/country/{country}/indicator/{indicator}",
            params={"format": "json", "per_page": str(years)},
            timeout=20,
        )
        if resp.status_code >= 400:
            print(f"[worldbank] {series_id}: HTTP {resp.status_code}", file=sys.stderr)
            return []
        payload = resp.json()
    except Exception as e:  # noqa: BLE001 - macro breadth is optional context
        print(f"[worldbank] {series_id} fetch failed: {e}", file=sys.stderr)
        return []

    # The API returns [metadata, rows]; rows is null when the query matched
    # nothing, which is a legitimate answer rather than an error.
    if not isinstance(payload, list) or len(payload) < 2 or not isinstance(payload[1], list):
        print(f"[worldbank] {series_id}: unexpected response shape", file=sys.stderr)
        return []

    obs: list[MacroObservation] = []
    for row in payload[1]:
        try:
            if row.get("value") is None:
                continue  # a year with no reading is missing, not zero
            obs.append(
                MacroObservation(
                    series_id=series_id,
                    ts=datetime(int(row["date"]), 12, 31, tzinfo=timezone.utc),
                    value=float(row["value"]),
                    source=SOURCE,
                )
            )
        except (KeyError, TypeError, ValueError):
            continue

    if obs and cache is not None:
        cache.put_macro(obs)
    return obs


def fetch_region(region: str = "us", cache: Cache | None = None) -> list[MacroObservation]:
    """Every indicator for one region ('us', 'in', 'global')."""
    country = COUNTRIES.get(region.lower())
    if country is None:
        raise ValueError(f"unknown region '{region}'. Known: {', '.join(COUNTRIES)}")

    cache = cache or Cache()
    out: list[MacroObservation] = []
    for indicator in INDICATORS:
        out.extend(fetch_indicator(indicator, country=country, cache=cache))
    return out
