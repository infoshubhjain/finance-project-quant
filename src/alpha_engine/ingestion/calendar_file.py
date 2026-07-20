"""Macro calendar loader: scheduled events from a local JSON file.

`analyzers/macro_calendar.py` dampens confidence before known events — an FOMC
decision, an RBI policy meeting, a CPI print. To do that it needs to know the
calendar, and this is where the calendar comes from.

**This file is the fallback, not the primary source.** FOMC dates are scraped
automatically by `ingestion/fomc_calendar.py` — the Fed publishes its schedule as
structured HTML, years ahead, and it parses reliably. You do not need to list
them here.

What this file is for is everything that *cannot* be scraped:

- **RBI MPC dates.** The RBI's MPC page is a JavaScript shell; its served HTML
  contains no dates at all, and the schedule lives in PDF press releases.
- **CPI and other statistical releases**, which are scattered across agency
  sites in no common format.
- **Earnings dates**, which are per-company and mostly behind paid APIs.

Both loaders write into the same event cache and are merged, so a scraped FOMC
date and a hand-entered RBI date sit side by side.

The engine ships **no dates of its own**. A wrong policy date would dampen
confidence on the wrong day and leave it undampened on the right one — worse
than having no calendar at all. So this file is empty until you fill it, and the
engine behaves exactly as it always did meanwhile. No calendar is not the same
as no events, and nothing here pretends otherwise.

Format — a JSON list at `calendar.json` in the project root:

    [
      {
        "ts": "2026-09-17T18:00:00Z",
        "name": "FOMC rate decision",
        "region": "us",
        "importance": "high"
      },
      {
        "ts": "2026-10-01T05:00:00Z",
        "name": "RBI MPC decision",
        "region": "in",
        "importance": "high"
      }
    ]

`region` is one of `us`, `in`, `global`. `importance` is `high`, `medium` or
`low` and controls how much confidence is dampened. `asset_tags` is optional and
only needed for company-specific events like an earnings date.

Load it with `alpha-engine ingest --kind events`.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from alpha_engine.cache.interface import Cache
from alpha_engine.cache.models import EventItem

DEFAULT_PATH = "calendar.json"
SOURCE = "calendar_file"

_VALID_REGIONS = {"us", "in", "global"}
_VALID_IMPORTANCE = {"high", "medium", "low"}


def parse_calendar(raw: object) -> list[EventItem]:
    """Parse a calendar payload into EventItems.

    Every row is validated. A row with an unknown region or a malformed date is
    skipped loudly rather than coerced into a default — an event silently filed
    under the wrong region would dampen the wrong market.
    """
    if not isinstance(raw, list):
        print(
            f"[calendar] expected a JSON list, got {type(raw).__name__}; ignoring",
            file=sys.stderr,
        )
        return []

    events: list[EventItem] = []
    for i, row in enumerate(raw):
        if not isinstance(row, dict):
            print(f"[calendar] row {i} is not an object; skipped", file=sys.stderr)
            continue

        ts_raw = row.get("ts")
        name = str(row.get("name") or "").strip()
        region = str(row.get("region") or "").strip().lower()
        importance = str(row.get("importance") or "medium").strip().lower()

        if not name or ts_raw is None:
            print(f"[calendar] row {i} is missing 'name' or 'ts'; skipped", file=sys.stderr)
            continue

        try:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        except ValueError:
            print(
                f"[calendar] row {i} ('{name}') has an unparseable date; skipped", file=sys.stderr
            )
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        if region not in _VALID_REGIONS:
            print(
                f"[calendar] row {i} ('{name}') has region '{region}'; "
                f"expected one of {sorted(_VALID_REGIONS)}; skipped",
                file=sys.stderr,
            )
            continue

        if importance not in _VALID_IMPORTANCE:
            print(
                f"[calendar] row {i} ('{name}') has importance '{importance}'; "
                f"defaulting to medium",
                file=sys.stderr,
            )
            importance = "medium"

        tags = row.get("asset_tags") or []
        events.append(
            EventItem(
                ts=ts,
                name=name,
                region=region,
                importance=importance,
                asset_tags=[str(t).upper() for t in tags] if isinstance(tags, list) else [],
                source=SOURCE,
            )
        )

    return events


def load_calendar(path: str | Path = DEFAULT_PATH, cache: Cache | None = None) -> list[EventItem]:
    """Read `calendar.json` and cache its events, grouped by region.

    A missing file is normal and silent-ish: it means no calendar is configured,
    and the engine runs exactly as it does without one.
    """
    p = Path(path)
    if not p.exists():
        print(
            f"[calendar] no {p} found; running without a macro calendar "
            f"(see ingestion/calendar_file.py for the format)",
            file=sys.stderr,
        )
        return []

    try:
        raw = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"[calendar] could not read {p}: {e}", file=sys.stderr)
        return []

    events = parse_calendar(raw)
    if events and cache is not None:
        by_region: dict[str, list[EventItem]] = {}
        for event in events:
            by_region.setdefault(event.region, []).append(event)
        for region, items in by_region.items():
            cache.put_events(region, items)

    print(f"[calendar] loaded {len(events)} events from {p}", file=sys.stderr)
    return events
