"""Reserve Bank of India data — scraped, fragile, and the missing half of the
macro agent.

The engine reads the Fed through FRED. It had no way to read the RBI at all,
which meant an Indian equity signal was being tilted by American monetary policy
and nothing else. That is not a small gap: an Indian bank's cost of funds is set
in Mumbai, not Washington.

Sources, in order of how much they can be trusted:

1. **The policy-rate page.** The repo rate is published as plain text on a
   stable RBI URL. This is the single most important number here and the
   easiest to read.
2. **The DBIE statistics portal.** Richer (CPI, WPI, forex reserves, credit
   growth) and considerably more brittle.

Everything the module-level docstring of `nse_disclosures.py` says about
scraping applies here word for word: an unrecognized page shape produces a loud
`CONTRACT BROKEN` warning and an empty result, never a guessed number. A wrong
repo rate would silently tilt every Indian equity signal in the engine.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timezone

from alpha_engine import net
from alpha_engine.cache.interface import Cache
from alpha_engine.cache.models import MacroObservation

SOURCE = "rbi"

_RATES_URL = "https://www.rbi.org.in/Scripts/BS_ViewMasterDirections.aspx"
_HOME_URL = "https://www.rbi.org.in"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
}

# The RBI homepage carries a rates table. These patterns pull the headline
# policy rates out of it. Written permissively (whitespace, optional markup)
# because the exact HTML around them changes more often than the labels do.
_RATE_PATTERNS: dict[str, re.Pattern[str]] = {
    "RBI_REPO_RATE": re.compile(r"Policy\s*Repo\s*Rate[^0-9]{0,80}?(\d+\.?\d*)\s*%?", re.I | re.S),
    "RBI_REVERSE_REPO": re.compile(
        r"Reverse\s*Repo\s*Rate[^0-9]{0,80}?(\d+\.?\d*)\s*%?", re.I | re.S
    ),
    "RBI_CRR": re.compile(r"Cash\s*Reserve\s*Ratio[^0-9]{0,80}?(\d+\.?\d*)\s*%?", re.I | re.S),
    "RBI_SLR": re.compile(
        r"Statutory\s*Liquidity\s*Ratio[^0-9]{0,80}?(\d+\.?\d*)\s*%?", re.I | re.S
    ),
}

# Sanity bounds. A regex that latches onto the wrong number is the realistic
# scraping failure, and an 87% repo rate must be rejected as nonsense rather
# than cached as macro data.
_PLAUSIBLE: dict[str, tuple[float, float]] = {
    "RBI_REPO_RATE": (0.0, 20.0),
    "RBI_REVERSE_REPO": (0.0, 20.0),
    "RBI_CRR": (0.0, 15.0),
    "RBI_SLR": (0.0, 45.0),
}


def _contract_broken(what: str, detail: str) -> None:
    print(
        f"[rbi] CONTRACT BROKEN in {what}: {detail}\n"
        f"[rbi]   The RBI page shape changed. Returning NOTHING rather than a guess.\n"
        f"[rbi]   Do not read the empty result as 'rates unchanged'.",
        file=sys.stderr,
    )


def parse_rates(html: str, now: datetime | None = None) -> list[MacroObservation]:
    """Extract policy rates from RBI page HTML.

    Every extracted value is bounds-checked before it is accepted. A number
    outside its plausible range means the regex matched the wrong thing, which
    is treated as a broken contract rather than as a surprising rate.
    """
    now = now or datetime.now(timezone.utc)
    out: list[MacroObservation] = []
    rejected: list[str] = []

    for series_id, pattern in _RATE_PATTERNS.items():
        match = pattern.search(html)
        if not match:
            continue
        try:
            value = float(match.group(1))
        except (TypeError, ValueError):
            continue

        low, high = _PLAUSIBLE[series_id]
        if not (low <= value <= high):
            rejected.append(f"{series_id}={value} (outside {low}-{high})")
            continue

        out.append(MacroObservation(series_id=series_id, ts=now, value=value, source=SOURCE))

    if rejected:
        _contract_broken("parse_rates", f"implausible values rejected: {', '.join(rejected)}")

    if html.strip() and not out and not rejected:
        _contract_broken("parse_rates", "page fetched but no policy rate labels matched")

    return out


def fetch_rates(cache: Cache | None = None) -> list[MacroObservation]:
    """Fetch current RBI policy rates. Empty means "could not read"."""
    try:
        resp = net.get(_HOME_URL, headers=_HEADERS, timeout=20)
        if resp.status_code >= 400:
            print(f"[rbi] rates: HTTP {resp.status_code}", file=sys.stderr)
            return []
        html = resp.text
    except Exception as e:  # noqa: BLE001 - macro is optional context
        print(f"[rbi] rates fetch failed: {e}", file=sys.stderr)
        return []

    obs = parse_rates(html)
    if obs and cache is not None:
        cache.put_macro(obs)
    return obs
