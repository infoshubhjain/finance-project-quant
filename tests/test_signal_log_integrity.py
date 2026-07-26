"""Tests for the signal-log integrity gate.

The daily Action's commit step is simultaneously the log's only backup and the
thing most capable of destroying it: whatever is on disk gets staged, committed
and pushed automatically. A bug that truncated the file would propagate the
damage and the good state would survive only in git history nobody thinks to
check. This gate runs first and refuses.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import verify_signal_log as vsl  # noqa: E402


def _write(path: Path, records: int) -> Path:
    path.write_text("".join(json.dumps({"asset": "BTC", "i": i}) + "\n" for i in range(records)))
    return path


def test_a_healthy_growing_log_passes(tmp_path):
    log = _write(tmp_path / "s.jsonl", 10)
    assert vsl.check(log, baseline=5) == []


def test_an_unchanged_log_passes(tmp_path):
    log = _write(tmp_path / "s.jsonl", 10)
    assert vsl.check(log, baseline=10) == []


def test_a_shrunken_log_is_refused(tmp_path):
    """Append-only means it can never get shorter. If it did, something
    rewrote history and that must not be committed."""
    log = _write(tmp_path / "s.jsonl", 3)
    problems = vsl.check(log, baseline=73)
    assert problems and "SHRANK" in problems[0]


def test_a_truncated_final_line_is_refused(tmp_path):
    """The realistic corruption: a partial write from a killed process."""
    log = _write(tmp_path / "s.jsonl", 3)
    log.write_text(log.read_text() + '{"asset": "BTC", "confid')
    problems = vsl.check(log, baseline=3)
    assert any("not valid JSON" in p for p in problems)


def test_a_json_array_line_is_refused(tmp_path):
    """Each line must be one record object, not a list."""
    log = tmp_path / "s.jsonl"
    log.write_text("[1, 2, 3]\n")
    problems = vsl.check(log, baseline=0)
    assert any("expected an object" in p for p in problems)


def test_blank_lines_are_tolerated(tmp_path):
    log = tmp_path / "s.jsonl"
    log.write_text(json.dumps({"a": 1}) + "\n\n" + json.dumps({"a": 2}) + "\n")
    assert vsl.check(log, baseline=2) == []


def test_a_missing_log_is_reported_not_ignored(tmp_path):
    problems = vsl.check(tmp_path / "absent.jsonl", baseline=None)
    assert problems and "does not exist" in problems[0]


def test_no_baseline_skips_the_shrink_check(tmp_path):
    """A first run has nothing committed to compare against."""
    log = _write(tmp_path / "s.jsonl", 1)
    assert vsl.check(log, baseline=None) == []


def test_main_exits_non_zero_on_a_problem(tmp_path, capsys):
    log = _write(tmp_path / "s.jsonl", 2)
    assert vsl.main([str(log), "--baseline", "50"]) == 1
    assert "::error::" in capsys.readouterr().err


def test_main_exits_zero_on_a_healthy_log(tmp_path, capsys):
    log = _write(tmp_path / "s.jsonl", 5)
    assert vsl.main([str(log), "--baseline", "5"]) == 0
    assert "ok" in capsys.readouterr().out


def test_the_real_repo_log_is_intact():
    """Guards the actual asset, not just the logic."""
    log = Path(__file__).resolve().parents[1] / "data" / "signals" / "signals.jsonl"
    if log.exists():
        assert vsl.check(log, baseline=None) == []
