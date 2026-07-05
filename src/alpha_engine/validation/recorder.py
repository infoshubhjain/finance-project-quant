"""Immutable signal recording. Every signal the engine emits is appended, with
the price it was emitted at, to an append-only JSONL log under `data/signals/`.

Why append-only JSONL: one line per signal means past records are never parsed,
rewritten, or reserialized when new ones arrive, so there is no code path that
can mutate history. The log is the project's compounding asset: a timestamped
record of what the engine believed and when, joinable later against what the
market actually did. Deleting or editing lines by hand breaks the track record's
honesty; the code will never do it for you.

The `entry_price` on each record is what makes later scoring possible: realized
return is measured from the price at emission time, not from whatever price the
scorer happens to see when it runs.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from alpha_engine.schema.signal import Signal

DEFAULT_ROOT = Path("data/signals")
LOG_NAME = "signals.jsonl"


class SignalRecord(BaseModel):
    """One immutable line in the signal log: the full signal, the price at which
    it was emitted (the anchor for outcome scoring), and when it was recorded."""

    record_id: str = Field("", description="content digest; stable for identical inputs")
    signal: Signal
    entry_price: float | None = Field(
        None, description="last known price at emission; None makes the record unscorable"
    )
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def _digest(signal: Signal, entry_price: float | None) -> str:
    """Deterministic content id: same signal + entry price -> same id. Useful for
    de-duplication and for referencing a record without line numbers."""
    payload = f"{signal.model_dump_json()}|{entry_price}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def record_signal(
    signal: Signal,
    entry_price: float | None = None,
    root: str | Path = DEFAULT_ROOT,
) -> SignalRecord:
    """Append one signal to the log and return the record written. Append mode
    only: existing lines are never read, touched, or rewritten."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    record = SignalRecord(
        record_id=_digest(signal, entry_price),
        signal=signal,
        entry_price=entry_price,
    )
    with (root / LOG_NAME).open("a", encoding="utf-8") as f:
        f.write(record.model_dump_json() + "\n")
    return record


def read_records(root: str | Path = DEFAULT_ROOT) -> list[SignalRecord]:
    """Load every recorded signal, oldest first. Reading never modifies the log.

    Reads line-by-line to avoid loading the entire file into memory at once.
    """
    path = Path(root) / LOG_NAME
    if not path.exists():
        return []
    records: list[SignalRecord] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(SignalRecord.model_validate_json(line))
    return records
