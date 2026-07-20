"""Keyless news ingestion from RSS/Atom feeds.

This is the first news source on purpose: it needs no key, no account, and no
new dependency. Regulators and central banks publish their announcements as RSS
because that is what regulators do, and those feeds are the highest-signal,
lowest-noise news available for free.

Parsing uses `xml.etree` from the standard library. RSS is XML with two common
shapes (RSS 2.0 `<item>` and Atom `<entry>`) and this module handles both. A
feed-parsing dependency would buy tolerance for genuinely broken XML, which
these publishers do not emit.

Security note: `xml.etree` is used with `defusedxml`-equivalent care by refusing
external entities — Python's `ElementTree` does not resolve them by default, and
we never parse a feed URL the user did not choose from `FEEDS`.

Every failure here is non-fatal. News is context, and a scan must not die
because a government web server had a bad afternoon.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

from alpha_engine import health, net
from alpha_engine.cache.interface import Cache
from alpha_engine.cache.models import NewsItem
from alpha_engine.config import load_project_env

# A browser-shaped User-Agent. NSE times out entirely without one — not a 403,
# an actual hang, which is the worst kind of failure because it looks like a
# network problem rather than a policy one.
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# The SEC's fair-access policy requires a User-Agent that identifies the
# requester *with contact information*. Every generic UA gets a 403 — verified:
# the project name alone is not enough, and neither is a browser string. Only a
# declaring UA with an email works.
#
# We will not ship someone's address in a public repo, and we will not 403 in
# silence forever either. So the feed is gated on SEC_USER_AGENT: set it and the
# feed works, leave it and the feed is explicitly skipped with a reason.
SEC_UA_ENV = "SEC_USER_AGENT"


@dataclass(frozen=True)
class Feed:
    """One curated feed and what it needs to answer."""

    url: str
    headers: dict[str, str] = field(default_factory=dict)
    # Env var that must be set for this feed to run at all, if any.
    requires_env: str | None = None
    # Env var whose value becomes the User-Agent, if any.
    ua_from_env: str | None = None
    timeout: float = 30.0
    note: str = ""


# Curated feeds. Deliberately small: these are the publishers whose
# announcements move markets, not a general news firehose.
FEEDS: dict[str, Feed] = {
    "sec_edgar": Feed(
        url="https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&output=atom",
        requires_env=SEC_UA_ENV,
        ua_from_env=SEC_UA_ENV,
        note=(
            "SEC requires a User-Agent naming you and your email. "
            f"Set {SEC_UA_ENV}='Your Name your@email.com' to enable this feed."
        ),
    ),
    "fed_press": Feed(url="https://www.federalreserve.gov/feeds/press_all.xml"),
    "rbi_press": Feed(url="https://www.rbi.org.in/pressreleases_rss.xml"),
    "nse_announcements": Feed(
        url="https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml",
        headers={"User-Agent": _BROWSER_UA, "Accept": "application/rss+xml,application/xml"},
        timeout=40.0,
        note="NSE hangs rather than refusing when the User-Agent is not browser-shaped.",
    ),
}

# Namespaces that show up in Atom feeds.
_ATOM = "{http://www.w3.org/2005/Atom}"

# A headline mentioning a ticker gets tagged with it. Word-boundary matched so
# "IT" does not tag every headline containing the word "it".
_TICKER_HINTS: dict[str, tuple[str, ...]] = {
    "BTC": ("bitcoin", "btc"),
    "ETH": ("ethereum", "ether", "eth"),
    "AAPL": ("apple",),
    "MSFT": ("microsoft",),
    "GOOGL": ("google", "alphabet"),
    "NVDA": ("nvidia",),
    "NIFTY": ("nifty",),
    "BANKNIFTY": ("bank nifty", "banknifty"),
}


def _parse_ts(raw: str | None) -> datetime:
    """Best-effort timestamp. Feeds use RFC-822 (RSS) or ISO-8601 (Atom), and a
    handful use neither. An unparseable date becomes "now", which is the
    conservative choice: a decay-weighted analyzer treats it as fresh rather
    than silently dropping the item."""
    if not raw:
        return datetime.now(timezone.utc)
    raw = raw.strip()
    try:
        dt = parsedate_to_datetime(raw)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        pass
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


def tag_assets(headline: str) -> list[str]:
    """Which known assets a headline mentions. Deterministic substring matching
    on word boundaries — no model, no fuzzy matching, no surprises."""
    low = headline.lower()
    tags = []
    for ticker, hints in _TICKER_HINTS.items():
        if any(re.search(rf"\b{re.escape(h)}\b", low) for h in hints):
            tags.append(ticker)
    return tags


def parse_feed(xml_text: str, source: str) -> list[NewsItem]:
    """Parse RSS 2.0 or Atom into NewsItems. Unknown shapes yield an empty list
    rather than raising — a changed feed format must degrade, not crash."""
    try:
        root = ElementTree.fromstring(xml_text)  # noqa: S314 - curated feeds only
    except ElementTree.ParseError as e:
        print(f"[rss] {source}: unparseable XML ({e})", file=sys.stderr)
        return []

    items: list[NewsItem] = []

    # RSS 2.0: channel/item with <title>, <link>, <pubDate>
    for node in root.iter("item"):
        title = (node.findtext("title") or "").strip()
        if not title:
            continue
        items.append(
            NewsItem(
                ts=_parse_ts(node.findtext("pubDate")),
                headline=title,
                source=source,
                url=(node.findtext("link") or "").strip(),
                asset_tags=tag_assets(title),
            )
        )

    # Atom: entry with <title>, <link href=...>, <updated>
    for node in root.iter(f"{_ATOM}entry"):
        title = (node.findtext(f"{_ATOM}title") or "").strip()
        if not title:
            continue
        link_el = node.find(f"{_ATOM}link")
        url = link_el.get("href", "") if link_el is not None else ""
        items.append(
            NewsItem(
                ts=_parse_ts(node.findtext(f"{_ATOM}updated")),
                headline=title,
                source=source,
                url=url,
                asset_tags=tag_assets(title),
            )
        )

    return items


def feed_status(source: str) -> tuple[bool, str]:
    """Whether a feed can run, and why not if it cannot.

    Separated from fetching so `doctor` can report a disabled feed without
    making a network call, and so a feed that is *configured off* never looks
    like a feed that is *broken*.
    """
    feed = FEEDS[source]
    if feed.requires_env and not os.environ.get(feed.requires_env):
        return False, feed.note or f"{feed.requires_env} is not set"
    return True, ""


def fetch_feed(source: str, cache: Cache | None = None) -> list[NewsItem]:
    """Fetch and cache one known feed.

    Raises only on a genuinely unknown source id. Network, policy and parse
    failures return an empty list and are recorded against that feed's health,
    so a feed that quietly dies shows up in `alpha-engine health` instead of
    just producing nothing for months.
    """
    if source not in FEEDS:
        raise ValueError(f"unknown feed '{source}'. Known: {', '.join(sorted(FEEDS))}")

    load_project_env()
    feed = FEEDS[source]
    cache = cache or Cache()
    health_key = f"news.{source}"

    enabled, reason = feed_status(source)
    if not enabled:
        print(f"[rss] {source}: skipped — {reason}", file=sys.stderr)
        return []

    headers = dict(feed.headers)
    if feed.ua_from_env:
        headers["User-Agent"] = os.environ[feed.ua_from_env]

    try:
        resp = net.get(feed.url, headers=headers or None, timeout=feed.timeout)
        if resp.status_code >= 400:
            detail = f"HTTP {resp.status_code}"
            if resp.status_code == 403 and feed.note:
                detail += f" — {feed.note}"
            print(f"[rss] {source}: {detail}", file=sys.stderr)
            health.record(health_key, error=detail)
            return []
        items = parse_feed(resp.text, source)
    except Exception as e:  # noqa: BLE001 - news is optional context, never fatal
        detail = f"{type(e).__name__}: {e}"
        print(f"[rss] {source}: fetch failed ({detail})", file=sys.stderr)
        health.record(health_key, error=detail)
        return []

    # A 200 that parses to nothing is its own failure mode: the feed answered
    # but we could not read it. Recording zero items is what makes that visible
    # after it has happened a few days running.
    health.record(health_key, items=len(items))

    if items:
        cache.put_news(source, items)
    return items


def fetch_all(cache: Cache | None = None) -> list[NewsItem]:
    """Fetch every curated feed. Individual failures are skipped, not raised —
    one dead feed must not cost you the other three."""
    cache = cache or Cache()
    out: list[NewsItem] = []
    for source in FEEDS:
        got = fetch_feed(source, cache=cache)
        print(f"[rss] {source}: {len(got)} items", file=sys.stderr)
        out.extend(got)
    return out
