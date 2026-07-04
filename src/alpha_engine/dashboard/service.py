"""Read-only dashboard data assembly.

The web layer should stay paper-thin. It asks for one payload and renders it;
this module gathers the latest records, scores them against cached prices, and
returns JSON-friendly data structures.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from alpha_engine.cache.interface import Cache
from alpha_engine.validation.outcomes import score_record, summarize_outcomes
from alpha_engine.validation.recorder import SignalRecord, read_records


def latest_records(records: list[SignalRecord]) -> list[SignalRecord]:
    """Return the newest record per asset, newest first."""
    latest: dict[str, SignalRecord] = {}
    for record in records:
        asset = record.signal.asset
        existing = latest.get(asset)
        if existing is None or record.recorded_at > existing.recorded_at:
            latest[asset] = record
    return sorted(latest.values(), key=lambda r: r.recorded_at, reverse=True)


def build_dashboard_payload(
    records_root: str | Path = "data/signals", cache: Cache | None = None
) -> dict[str, Any]:
    """Assemble the current dashboard state.

    The payload is intentionally JSON-friendly so the web layer can serve it as
    either HTML or API output without duplicating logic.
    """
    cache = cache or Cache()
    records = read_records(records_root)
    latest = latest_records(records)

    scored: list[tuple[float, object]] = []
    for record in records:
        series, _stale = cache.get_price(record.signal.asset, "1d")
        if series is None:
            continue
        scored.append((record.signal.confidence, score_record(record, series)))

    by_market: dict[str, int] = defaultdict(int)
    for record in latest:
        by_market[record.signal.market.value] += 1

    return {
        "total_records": len(records),
        "latest_count": len(latest),
        "assets_by_market": dict(sorted(by_market.items())),
        "latest_signals": [
            {
                "record_id": record.record_id,
                "asset": record.signal.asset,
                "market": record.signal.market.value,
                "direction": record.signal.direction.value,
                "confidence": record.signal.confidence,
                "timeframe": record.signal.timeframe.value,
                "timestamp": record.signal.timestamp.isoformat(),
                "recorded_at": record.recorded_at.isoformat(),
                "entry_price": record.entry_price,
                "invalidation_level": record.signal.invalidation_level,
                "thesis": record.signal.thesis,
                "sources": [s.model_dump(mode="json") for s in record.signal.signal_sources],
            }
            for record in latest
        ],
        "outcomes": summarize_outcomes(scored).model_dump(mode="json"),
    }
