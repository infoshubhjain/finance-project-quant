"""Finnhub company news — key-gated, company-tagged headlines.

The RSS feeds in `ingestion/rss.py` carry regulator and central-bank
announcements. What they do not carry is "news about AAPL specifically", which
is what a per-asset sentiment read actually wants. Finnhub's free tier does,
so this adapter exists to fill that hole for anyone willing to get a free key.

Gating: with no `FINNHUB_API_KEY` the module reports itself unavailable and the
pipeline proceeds without it. The keyless path stays the default path.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

from alpha_engine import net
from alpha_engine.cache.interface import Cache
from alpha_engine.cache.models import NewsItem
from alpha_engine.config import load_project_env
from alpha_engine.ingestion.rss import tag_assets

_BASE = "https://finnhub.io/api/v1"
SOURCE = "finnhub"


def has_key() -> bool:
    load_project_env()
    return bool(os.environ.get("FINNHUB_API_KEY"))


def fetch_company_news(
    asset: str,
    days: int = 14,
    cache: Cache | None = None,
) -> list[NewsItem]:
    """Fetch recent company news for one ticker.

    Returns an empty list (never raises) when the key is absent or the request
    fails: news is optional context and must never take a scan down.
    """
    if not has_key():
        print(
            "[finnhub] FINNHUB_API_KEY not set; skipping company news "
            "(free key: https://finnhub.io)",
            file=sys.stderr,
        )
        return []

    cache = cache or Cache()
    asset = asset.upper()
    today = datetime.now(timezone.utc).date()

    try:
        resp = net.get(
            f"{_BASE}/company-news",
            params={
                "symbol": asset,
                "from": (today - timedelta(days=days)).isoformat(),
                "to": today.isoformat(),
                "token": os.environ["FINNHUB_API_KEY"],
            },
            timeout=20,
        )
        if resp.status_code >= 400:
            print(f"[finnhub] HTTP {resp.status_code} for {asset}", file=sys.stderr)
            return []
        rows = resp.json()
    except Exception as e:  # noqa: BLE001 - optional context, never fatal
        print(f"[finnhub] fetch failed for {asset}: {e}", file=sys.stderr)
        return []

    if not isinstance(rows, list):
        return []

    items: list[NewsItem] = []
    for row in rows:
        headline = str(row.get("headline") or "").strip()
        if not headline:
            continue
        ts_raw = row.get("datetime")
        try:
            ts = datetime.fromtimestamp(float(ts_raw), tz=timezone.utc)
        except (TypeError, ValueError):
            continue
        # The queried ticker is always a tag; the lexicon may find more.
        tags = sorted({asset, *tag_assets(headline)})
        items.append(
            NewsItem(
                ts=ts,
                headline=headline,
                source=SOURCE,
                url=str(row.get("url") or ""),
                asset_tags=tags,
            )
        )

    if items:
        cache.put_news(f"{SOURCE}_{asset}", items)
    return items
