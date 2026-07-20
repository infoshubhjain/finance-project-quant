"""NSE corporate disclosures and FII/DII flows — scraped, fragile, worth it.

This is the genuinely differentiated Indian data the blueprint asked for:
corporate announcements, and the daily net buying of foreign (FII) versus
domestic (DII) institutions. No free API serves it. NSE serves it to browsers.

**Read this before trusting anything in this file.**

Scraping is a contract nobody signed. NSE can change a field name on a Tuesday
and every function here goes quiet. That failure mode — silently returning
plausible-looking nothing, or worse, plausible-looking *wrong* numbers — is far
more dangerous than an outage, because a weight computed from garbage still
looks like a weight.

So this module is built to fail loudly:

- Every parser validates that the fields it needs are actually present.
- A response whose shape it does not recognize prints a `CONTRACT BROKEN`
  warning naming the missing field, and returns empty.
- Nothing here ever guesses, coerces, or fills a default for a market number.

Empty output from this module always means "I could not read it", never "there
was nothing to read". Treat a sudden run of empties as a scraper to fix, not as
a market that went quiet.

Session handling: NSE rejects requests without browser-ish headers and a cookie
it sets on the homepage. We fetch the homepage once to acquire cookies, then
call the JSON endpoints. This is exactly as brittle as it sounds.
"""

from __future__ import annotations

import http.cookiejar
import sys
import urllib.request
from datetime import datetime, timezone
from typing import Any

from alpha_engine.cache.interface import Cache
from alpha_engine.cache.models import MacroObservation, NewsItem

_HOME = "https://www.nseindia.com"
_ANNOUNCEMENTS = f"{_HOME}/api/corporate-announcements?index=equities"
_FII_DII = f"{_HOME}/api/fiidiiTradeReact"

SOURCE = "nse_disclosures"

# NSE serves nothing to a client that does not look like a browser.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": _HOME,
}


def _contract_broken(what: str, detail: str) -> None:
    """The loud failure. If you are reading this in your logs, the scraper needs
    fixing — do not treat the empty result as data."""
    print(
        f"[nse] CONTRACT BROKEN in {what}: {detail}\n"
        f"[nse]   NSE changed its response shape. This source is returning NOTHING\n"
        f"[nse]   until the parser is updated. Do not read the empty result as 'no activity'.",
        file=sys.stderr,
    )


def _session_get(url: str, timeout: float = 20) -> Any | None:
    """GET a JSON endpoint with a cookie jar bootstrapped from the homepage.

    Uses urllib directly rather than `alpha_engine.net` because this is the one
    source needing cookie persistence across two requests, which the shared
    helper deliberately does not do.
    """
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    try:
        # Homepage first: this is what sets the cookie the API requires.
        home = urllib.request.Request(_HOME, headers=_HEADERS)
        opener.open(home, timeout=timeout).read()

        req = urllib.request.Request(url, headers=_HEADERS)
        with opener.open(req, timeout=timeout) as resp:  # noqa: S310 - fixed https host
            import json

            return json.loads(resp.read())
    except Exception as e:  # noqa: BLE001 - scraping is best-effort by nature
        print(f"[nse] fetch failed for {url}: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def parse_announcements(payload: Any) -> list[NewsItem]:
    """Parse the corporate-announcements payload into NewsItems.

    Announcements are news — same shape, same sentiment scoring, same decay — so
    they land in `NewsItem` rather than getting a parallel model.
    """
    if not isinstance(payload, list):
        _contract_broken("announcements", f"expected a list, got {type(payload).__name__}")
        return []

    from alpha_engine.ingestion.rss import tag_assets

    items: list[NewsItem] = []
    missing_fields = 0

    for row in payload:
        if not isinstance(row, dict):
            missing_fields += 1
            continue
        # NSE has used both 'desc'/'subject' and 'sm_name'/'symbol' over time.
        subject = row.get("desc") or row.get("subject") or ""
        symbol = row.get("symbol") or row.get("sm_name") or ""
        raw_ts = row.get("an_dt") or row.get("exchdisstime") or row.get("sort_date")

        if not subject or not raw_ts:
            missing_fields += 1
            continue

        ts = None
        for fmt in ("%d-%b-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M:%S"):
            try:
                ts = datetime.strptime(str(raw_ts).strip(), fmt).replace(tzinfo=timezone.utc)
                break
            except ValueError:
                continue
        if ts is None:
            missing_fields += 1
            continue

        headline = f"{symbol}: {subject}".strip(": ")
        items.append(
            NewsItem(
                ts=ts,
                headline=headline,
                source=SOURCE,
                url=str(row.get("attchmntFile") or ""),
                asset_tags=sorted(
                    {*(f"{symbol}.NS" for _ in [1] if symbol), *tag_assets(headline)}
                ),
            )
        )

    # A payload that arrived but yielded nothing usable is the exact silent
    # failure this module exists to prevent.
    if payload and not items:
        _contract_broken(
            "announcements",
            f"{len(payload)} rows arrived but none had usable subject/date fields",
        )
    elif missing_fields > len(payload) / 2 if payload else False:
        print(
            f"[nse] WARNING: {missing_fields}/{len(payload)} announcement rows unparseable",
            file=sys.stderr,
        )

    return items


def parse_fii_dii(payload: Any) -> list[MacroObservation]:
    """Parse the FII/DII daily net-flow payload.

    FII net buying is one of the most-watched numbers in Indian equities:
    foreign institutional money is the marginal buyer, and sustained FII selling
    has historically preceded weakness regardless of what domestic funds do.
    """
    if not isinstance(payload, list):
        _contract_broken("fii_dii", f"expected a list, got {type(payload).__name__}")
        return []

    out: list[MacroObservation] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        category = str(row.get("category") or "").upper()
        raw_date = row.get("date")
        net_value = row.get("netValue")

        if not category or raw_date is None or net_value is None:
            continue

        ts = None
        for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d-%m-%Y"):
            try:
                ts = datetime.strptime(str(raw_date).strip(), fmt).replace(tzinfo=timezone.utc)
                break
            except ValueError:
                continue
        if ts is None:
            continue

        try:
            value = float(str(net_value).replace(",", ""))
        except (TypeError, ValueError):
            continue

        series_id = "FII_NET" if "FII" in category or "FPI" in category else "DII_NET"
        out.append(MacroObservation(series_id=series_id, ts=ts, value=value, source=SOURCE))

    if payload and not out:
        _contract_broken(
            "fii_dii", f"{len(payload)} rows arrived but none had category/date/netValue"
        )

    return out


def fetch_announcements(cache: Cache | None = None) -> list[NewsItem]:
    """Fetch recent corporate announcements. Empty means "could not read"."""
    payload = _session_get(_ANNOUNCEMENTS)
    if payload is None:
        return []
    items = parse_announcements(payload)
    if items and cache is not None:
        cache.put_news(SOURCE, items)
    return items


def fetch_fii_dii(cache: Cache | None = None) -> list[MacroObservation]:
    """Fetch FII/DII daily net flows. Empty means "could not read"."""
    payload = _session_get(_FII_DII)
    if payload is None:
        return []
    obs = parse_fii_dii(payload)
    if obs and cache is not None:
        cache.put_macro(obs)
    return obs
