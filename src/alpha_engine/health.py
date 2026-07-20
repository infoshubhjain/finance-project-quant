"""Source health tracking: making silent decay loud.

Every ingestion adapter in this project is deliberately fault-tolerant. A dead
RSS feed, a geo-blocked exchange, a rate-limited API — all return empty and let
the scan continue. That is right for uptime and **wrong for noticing rot**.

The failure this module exists to prevent is not a crash. It is this: NSE
changes a field name in March, the scraper starts returning nothing, and every
signal from then on is computed without Indian disclosure data. Nothing errors.
Nothing looks broken. The engine just quietly gets worse, and you find out in
August.

So every refresh records what happened, and that record is checked. Three
states, and the distinction between the last two is the entire point:

- **ok** — the source returned data.
- **empty** — the source answered but had nothing. Normal for one day (no news
  on a Sunday), suspicious for a week, broken for a month.
- **error** — the source raised or returned an HTTP error.

A source is **degraded** when it has not produced data for longer than its quiet
tolerance. That tolerance is per-source because the sources have genuinely
different rhythms: news should arrive daily, fundamentals quarterly, a calendar
almost never.

Storage is one small JSON file. This is diagnostics, not the signal log — it is
safe to delete, and losing it costs you nothing but the history of failures.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from alpha_engine.config import data_dir

DEFAULT_PATH = data_dir() / "health.json"

# How long a source may produce nothing before it is considered degraded.
# Set from how often the source genuinely has something new to say.
QUIET_TOLERANCE: dict[str, timedelta] = {
    "news": timedelta(days=3),  # feeds publish most weekdays
    "onchain": timedelta(days=2),  # funding prints three times a day
    "fundamentals": timedelta(days=100),  # quarterly filings
    "events": timedelta(days=120),  # the Fed publishes a year ahead
    "price": timedelta(days=4),  # covers a long weekend
}
_DEFAULT_TOLERANCE = timedelta(days=7)


def tolerance_for(source: str) -> timedelta:
    """How long `source` may stay quiet before it counts as degraded.

    Sub-sources are named `kind.feed` (e.g. `news.rbi_press`) and inherit their
    kind's tolerance. Without this inheritance every individual feed silently
    falls back to the generic 7-day default, so a news feed that should be
    flagged after 3 days of silence gets more than twice the grace it should.
    """
    if source in QUIET_TOLERANCE:
        return QUIET_TOLERANCE[source]
    parent = source.split(".", 1)[0]
    return QUIET_TOLERANCE.get(parent, _DEFAULT_TOLERANCE)


# Consecutive errors before a source is called broken regardless of timing.
ERROR_STREAK_LIMIT = 3


@dataclass
class SourceHealth:
    """What we know about one source's recent behaviour."""

    source: str
    last_attempt: str | None = None
    last_ok: str | None = None  # last time it returned at least one item
    last_error: str | None = None
    last_error_at: str | None = None
    consecutive_errors: int = 0
    consecutive_empty: int = 0
    total_attempts: int = 0
    total_items: int = 0

    def quiet_for(self, now: datetime | None = None) -> timedelta | None:
        """How long since this source last produced data. None if it never has,
        and None rather than an exception if the stored timestamp is unusable.

        `record()` always writes an aware UTC timestamp, but this file is plain
        JSON that people hand-edit, and an older version may have written a
        different shape. Subtracting a naive datetime from an aware one raises
        TypeError, which in a diagnostics module means `health` and `doctor`
        both crash on a file they were supposed to be reporting about.
        """
        if self.last_ok is None:
            return None
        now = now or datetime.now(timezone.utc)
        try:
            last = datetime.fromisoformat(self.last_ok)
        except (TypeError, ValueError):
            return None
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return now - last

    def status(self, now: datetime | None = None) -> str:
        """`ok`, `degraded`, `broken`, or `unknown`."""
        if self.total_attempts == 0:
            return "unknown"
        if self.consecutive_errors >= ERROR_STREAK_LIMIT:
            return "broken"
        if self.last_ok is None:
            # Attempted repeatedly and never once produced anything.
            return "broken" if self.total_attempts >= ERROR_STREAK_LIMIT else "unknown"
        quiet = self.quiet_for(now)
        tolerance = tolerance_for(self.source)
        if quiet is not None and quiet > tolerance:
            return "degraded"
        return "ok"

    def explain(self, now: datetime | None = None) -> str:
        """One line a human can act on."""
        state = self.status(now)
        if state == "unknown":
            return "never run"
        if state == "broken":
            if self.consecutive_errors >= ERROR_STREAK_LIMIT:
                return f"{self.consecutive_errors} consecutive errors: {self.last_error}"
            return f"{self.total_attempts} attempts, never returned data"
        quiet = self.quiet_for(now)
        days = quiet.total_seconds() / 86400.0 if quiet else 0.0
        if state == "degraded":
            tol = tolerance_for(self.source).days
            return f"no data for {days:.1f}d (expected within {tol}d) — check the adapter"
        return f"last data {days:.1f}d ago, {self.total_items} items total"


@dataclass
class HealthReport:
    sources: dict[str, SourceHealth] = field(default_factory=dict)

    def degraded(self, now: datetime | None = None) -> list[SourceHealth]:
        """Sources needing attention, worst first."""
        bad = [s for s in self.sources.values() if s.status(now) in ("degraded", "broken")]
        return sorted(bad, key=lambda s: (s.status(now) != "broken", s.source))

    def summary(self, now: datetime | None = None) -> dict[str, Any]:
        return {
            "checked_at": (now or datetime.now(timezone.utc)).isoformat(),
            "sources": {
                name: {**asdict(s), "status": s.status(now), "explain": s.explain(now)}
                for name, s in sorted(self.sources.items())
            },
            "degraded": [s.source for s in self.degraded(now)],
        }


def _path(path: str | Path | None = None) -> Path:
    return Path(path) if path else DEFAULT_PATH


def load_health(path: str | Path | None = None) -> HealthReport:
    """Read the health file. A missing or corrupt file is an empty report — this
    is diagnostics, and losing it must never break a run."""
    p = _path(path)
    if not p.exists():
        return HealthReport()
    try:
        raw = json.loads(p.read_text())
        return HealthReport(
            sources={k: SourceHealth(**v) for k, v in raw.get("sources", {}).items()}
        )
    except Exception as e:  # noqa: BLE001 - diagnostics must never be load-bearing
        print(f"[health] ignoring unreadable {p}: {e}", file=sys.stderr)
        return HealthReport()


def save_health(report: HealthReport, path: str | Path | None = None) -> bool:
    """Write atomically, same rename dance the cache uses, so a crash mid-write
    cannot leave a truncated file behind.

    Never raises. Returns whether the write landed.

    This module's whole promise is that diagnostics are not load-bearing, and
    that promise is only real if writing them cannot fail the run. The path that
    makes it concrete: `refresh_context` records health from inside its *except*
    handler, so a raise here would replace a handled source failure with an
    unhandled crash — the monitoring killing the thing it monitors.
    """
    p = _path(path)
    payload = {"sources": {k: asdict(v) for k, v in report.sources.items()}}
    tmp = None
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        # Unique per process AND per thread: a shared PID makes two threads
        # build the same temp name, and the second rename then fails outright.
        tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.rename(p)
        return True
    except OSError as e:
        print(f"[health] could not write {p}: {e}", file=sys.stderr)
        # Do not leave a half-written temp file behind to be found later.
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        return False


# `record()` is read-modify-write, which is a lost-update race: two callers read
# the same state, and the second write erases the first. For a *cache* that is
# tolerable — the next refresh refills it. For health it is not: the lost update
# is the failure history this module exists to keep.
#
# ponytail: an in-process lock, because that is the concurrency that actually
# exists (a threaded server, a parallel refresh). Two separate PROCESSES calling
# record() at once can still lose an update; `scripts/daily.sh` holds a lock so
# the scheduled path cannot, and a manual run racing the cron loses at most one
# attempt count. Upgrade to a file lock only if that ever matters.
_RECORD_LOCK = threading.Lock()


def record(
    source: str,
    items: int = 0,
    error: str | None = None,
    path: str | Path | None = None,
    now: datetime | None = None,
) -> SourceHealth:
    """Record one refresh attempt and persist it.

    Returns the updated entry so a caller can react immediately rather than
    re-reading the file. Never raises: see `save_health`.
    """
    with _RECORD_LOCK:
        return _record_locked(source, items, error, path, now)


def _record_locked(
    source: str,
    items: int,
    error: str | None,
    path: str | Path | None,
    now: datetime | None,
) -> SourceHealth:
    now = now or datetime.now(timezone.utc)
    report = load_health(path)
    entry = report.sources.get(source) or SourceHealth(source=source)

    entry.last_attempt = now.isoformat()
    entry.total_attempts += 1

    if error is not None:
        entry.consecutive_errors += 1
        # `last_error` is truncated: an adapter can raise a multi-kilobyte
        # traceback string, and this file is meant to stay glanceable.
        entry.last_error = str(error)[:200]
        entry.last_error_at = now.isoformat()
    else:
        entry.consecutive_errors = 0
        if items > 0:
            entry.last_ok = now.isoformat()
            entry.consecutive_empty = 0
            entry.total_items += items
        else:
            entry.consecutive_empty += 1

    report.sources[source] = entry
    save_health(report, path)
    return entry


def check(path: str | Path | None = None, now: datetime | None = None) -> list[SourceHealth]:
    """Every source currently needing attention."""
    return load_health(path).degraded(now)
