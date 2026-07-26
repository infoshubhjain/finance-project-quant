#!/usr/bin/env python3
"""Integrity gate for the append-only signal log.

`data/signals/signals.jsonl` is the project's compounding asset. It is explicitly
not regenerable, and this repository is its only copy — the daily Action commits
it back and that commit *is* the backup. Which means the same step that protects
the log is also the one that could destroy it: a bug that truncated or corrupted
the file would be staged, committed and pushed automatically, and the previous
good state would only exist in git history nobody thinks to check.

So before that commit happens, two things are verified. Both are impossible in
normal operation, and both are unrecoverable once pushed:

1. **The log never shrinks.** It is append-only by design. Fewer lines than the
   committed version means something rewrote history.
2. **Every line is valid JSON.** A partial write (disk full, killed mid-flush)
   leaves a truncated final line, and a reader that skips bad lines would
   silently under-report the track record forever after.

Exits non-zero on either, which stops the commit. Run standalone too:

    python scripts/verify_signal_log.py data/signals/signals.jsonl --baseline 73
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def committed_line_count(path: Path) -> int | None:
    """How many lines the last commit had for this file, or None if it is not
    tracked yet (a first run, which has nothing to compare against)."""
    try:
        blob = subprocess.run(
            ["git", "show", f"HEAD:{path.as_posix()}"],
            capture_output=True,
            check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return blob.count(b"\n")


def check(path: Path, baseline: int | None) -> list[str]:
    """Return a list of problems. Empty means the log is safe to commit."""
    problems: list[str] = []
    if not path.exists():
        return [f"{path} does not exist"]

    lines = path.read_text().splitlines()
    non_empty = [line for line in lines if line.strip()]

    if baseline is not None and len(non_empty) < baseline:
        problems.append(
            f"signal log SHRANK from {baseline} to {len(non_empty)} records. "
            "The log is append-only and this repo is its only copy."
        )

    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as e:
            problems.append(f"line {number} is not valid JSON: {e}")
            continue
        if not isinstance(record, dict):
            problems.append(f"line {number} is a {type(record).__name__}, expected an object")

    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", default="data/signals/signals.jsonl")
    parser.add_argument(
        "--baseline",
        type=int,
        default=None,
        help="record count to compare against (default: the count in HEAD)",
    )
    args = parser.parse_args(argv)

    path = Path(args.path)
    baseline = args.baseline if args.baseline is not None else committed_line_count(path)
    problems = check(path, baseline)

    if problems:
        for problem in problems:
            # ::error:: makes it a GitHub Actions annotation; harmless elsewhere.
            print(f"::error::{problem}", file=sys.stderr)
        return 1

    count = len([ln for ln in path.read_text().splitlines() if ln.strip()])
    print(f"signal log ok: {count} records, valid JSONL, not shrunk")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
