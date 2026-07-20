"""FOMC meeting calendar — scraped from federalreserve.gov.

`analyzers/macro_calendar.py` dampens confidence before known policy decisions.
This adapter supplies the US half of that calendar automatically, so the most
market-moving recurring event in the world does not depend on someone
remembering to paste dates into a file.

**Why this one gets scraped when the RBI calendar does not.** The Fed publishes
its schedule as structured HTML: one `fomc-meeting__month` cell and one
`fomc-meeting__date` cell per meeting, years ahead. The RBI's MPC page is a
JavaScript shell whose served HTML contains no dates at all — its schedule lives
in PDF press releases. So the Fed is scraped and the RBI stays file-supplied
(`ingestion/calendar_file.py`). That split is a measurement, not a preference.

Three things this parser has to get right, each learned from the real page:

1. **Row markup varies.** Meetings with economic projections carry an extra
   class (`fomc-meeting--shaded row fomc-meeting`), so splitting on
   `class="row fomc-meeting"` silently drops half the year. We split on the
   month cell instead, which is present in every row.

2. **Not every row is a rate decision.** The page includes entries like
   `22 (notation vote)` — a written vote on policy strategy, with no rate
   decision. Treating one as a meeting would dampen confidence on a day nothing
   happens, so any date cell that is not purely `DD` or `DD-DD` (optionally with
   a `*`) is rejected.

3. **Meetings span two days, sometimes two months.** `Apr/May` + `30-1` means
   April 30 to May 1. The decision and statement land on the **last** day, which
   is the date that matters.

The contract check: the FOMC holds eight scheduled meetings a year. A completed
year that parses to a wildly different count means the page changed shape, and
this module says so loudly and returns nothing rather than a partial calendar —
a half-read calendar is worse than none, because the missing dates look like
"no event scheduled".
"""

from __future__ import annotations

import calendar as _calendar
import re
import sys
from datetime import datetime, timezone

from alpha_engine import net
from alpha_engine.cache.interface import Cache
from alpha_engine.cache.models import EventItem

SOURCE = "fomc_calendar"
URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"

# The FOMC holds eight scheduled meetings per year. A parsed year far from this
# is a broken parser, not an unusual year.
EXPECTED_PER_YEAR = 8

# Statements are released at 2pm US Eastern. We store 19:00 UTC (2pm EST) and
# accept being an hour off during daylight saving: the analyzer's horizon is
# measured in days, so sub-hour precision buys nothing and pretending to have it
# would be false precision.
_DECISION_HOUR_UTC = 19

_MONTHS = {m.lower(): i for i, m in enumerate(_calendar.month_name) if m}
_MONTHS.update({m.lower(): i for i, m in enumerate(_calendar.month_abbr) if m})

_YEAR_RE = re.compile(r"<h4><a[^>]*>(\d{4})\s+FOMC\s+Meetings</a></h4>", re.I)
# The month cell is present in every row shape, unlike the row div's class list.
_MONTH_RE = re.compile(
    r"fomc-meeting__month[^>]*>\s*(?:<strong>)?\s*([A-Za-z]+)(?:\s*/\s*([A-Za-z]+))?", re.I
)
# Capture the WHOLE date cell so qualifiers like "(notation vote)" are visible
# and can be rejected, rather than silently ignored by a looser pattern.
_DATE_CELL_RE = re.compile(r"fomc-meeting__date[^>]*>\s*(?:<strong>)?\s*([^<]*)", re.I)
_CLEAN_DATE_RE = re.compile(r"^(\d{1,2})(?:\s*-\s*(\d{1,2}))?(\*?)$")


def _contract_broken(detail: str) -> None:
    print(
        f"[fomc] CONTRACT BROKEN: {detail}\n"
        f"[fomc]   The Federal Reserve calendar page changed shape. Returning NOTHING\n"
        f"[fomc]   rather than a partial calendar — missing dates would look like\n"
        f"[fomc]   'no meeting scheduled' and silently skip the dampening.",
        file=sys.stderr,
    )


def parse_fomc_calendar(html: str) -> list[EventItem]:
    """Parse the FOMC calendar page into EventItems, one per rate decision."""
    year_marks = [(m.start(), int(m.group(1))) for m in _YEAR_RE.finditer(html)]
    if not year_marks:
        _contract_broken("no '<YEAR> FOMC Meetings' headings found")
        return []

    def year_at(pos: int) -> int | None:
        found = None
        for start, year in year_marks:
            if start < pos:
                found = year
        return found

    events: list[EventItem] = []
    per_year: dict[int, int] = {}
    rejected: list[str] = []

    for match in _MONTH_RE.finditer(html):
        year = year_at(match.start())
        if year is None:
            continue

        # The date cell always follows its month cell closely; bounding the
        # search keeps a malformed row from stealing the next row's date.
        cell = _DATE_CELL_RE.search(html[match.end() : match.end() + 400])
        if cell is None:
            continue

        raw = " ".join(cell.group(1).split())
        clean = _CLEAN_DATE_RE.match(raw)
        if clean is None:
            # e.g. "22 (notation vote)" — a written vote, not a rate decision.
            rejected.append(raw)
            continue

        first_month = match.group(1).lower()
        second_month = (match.group(2) or "").lower()
        if first_month not in _MONTHS or (second_month and second_month not in _MONTHS):
            rejected.append(f"{first_month}/{second_month}")
            continue

        last_day = int(clean.group(2) or clean.group(1))
        has_projections = bool(clean.group(3))

        # A two-day meeting decides on its last day. When the row spans two
        # months ("Apr/May", "30-1"), that last day belongs to the second month.
        month = _MONTHS[second_month] if second_month else _MONTHS[first_month]
        event_year = year
        if second_month and _MONTHS[second_month] < _MONTHS[first_month]:
            event_year = year + 1  # a Dec/Jan span rolls into the next year

        try:
            ts = datetime(event_year, month, last_day, _DECISION_HOUR_UTC, tzinfo=timezone.utc)
        except ValueError:
            rejected.append(f"{event_year}-{month}-{last_day}")
            continue

        name = "FOMC rate decision"
        if has_projections:
            # Projection meetings carry a press conference and a dot plot, which
            # move markets more than the rate decision alone.
            name += " + economic projections"

        events.append(
            EventItem(
                ts=ts,
                name=name,
                region="us",
                importance="high",
                source=SOURCE,
            )
        )
        per_year[year] = per_year.get(year, 0) + 1

    if not events:
        _contract_broken(f"found {len(year_marks)} year headings but parsed no meetings")
        return []

    # Sanity: a year that parses to a wildly wrong count means the page shape
    # moved. Reported per year rather than fatally, because the earliest and
    # latest years on the page are legitimately partial.
    for year, count in sorted(per_year.items()):
        if not 6 <= count <= 12:
            _contract_broken(
                f"{year} parsed {count} meetings, expected ~{EXPECTED_PER_YEAR}; "
                f"the page layout has probably changed"
            )
            return []

    if rejected:
        print(
            f"[fomc] skipped {len(rejected)} non-decision row(s): {rejected[:3]}",
            file=sys.stderr,
        )

    return sorted(events, key=lambda e: e.ts)


def fetch_fomc_calendar(cache: Cache | None = None) -> list[EventItem]:
    """Fetch and cache the FOMC calendar. Empty means "could not read it"."""
    try:
        resp = net.get(URL, timeout=25)
        if resp.status_code >= 400:
            print(f"[fomc] HTTP {resp.status_code} from {URL}", file=sys.stderr)
            return []
        html = resp.text
    except Exception as e:  # noqa: BLE001 - the calendar is optional context
        print(f"[fomc] fetch failed: {type(e).__name__}: {e}", file=sys.stderr)
        return []

    events = parse_fomc_calendar(html)
    if events and cache is not None:
        # Merge, never replace: the user's own calendar.json entries live in the
        # same region bucket and must survive a Fed refresh. The cache dedups by
        # (region, name, ts), so re-running this is idempotent.
        cache.put_events("us", events)

    upcoming = sum(1 for e in events if e.ts > datetime.now(timezone.utc))
    print(f"[fomc] loaded {len(events)} meetings ({upcoming} upcoming)", file=sys.stderr)
    return events
