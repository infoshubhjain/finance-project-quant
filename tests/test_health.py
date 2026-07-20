"""Tests for source health tracking.

This module exists for one failure: a scraper that stops returning data without
raising. Every adapter here degrades to empty on purpose, so "the feed broke in
March" and "quiet Tuesday" produce identical output — and the engine gets
quietly worse for months.

The distinction these tests protect is therefore between *empty* and *broken*,
and between *broken* and *deliberately switched off*. Collapsing any of those
three into the others is what makes the whole thing useless.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from alpha_engine.health import (
    ERROR_STREAK_LIMIT,
    QUIET_TOLERANCE,
    HealthReport,
    SourceHealth,
    check,
    load_health,
    record,
    save_health,
    tolerance_for,
)

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _entry(source: str = "news", **kw) -> SourceHealth:
    base = {"source": source, "total_attempts": 1}
    base.update(kw)
    return SourceHealth(**base)


def _ago(days: float) -> str:
    return (NOW - timedelta(days=days)).isoformat()


# ---------------------------------------------------------------------------
# Tolerance inheritance
# ---------------------------------------------------------------------------


def test_known_source_uses_its_own_tolerance():
    assert tolerance_for("news") == QUIET_TOLERANCE["news"]


def test_sub_source_inherits_its_parent_tolerance():
    """`news.rbi_press` must get the news tolerance, not the generic default.
    Without inheritance every individual feed gets more than twice the grace it
    should before anyone is told it died."""
    assert tolerance_for("news.rbi_press") == QUIET_TOLERANCE["news"]


def test_unknown_source_falls_back_to_the_default():
    assert tolerance_for("something_new") == timedelta(days=7)


# ---------------------------------------------------------------------------
# Status classification — the three distinctions that matter
# ---------------------------------------------------------------------------


def test_never_run_is_unknown_not_broken():
    assert SourceHealth(source="news").status(NOW) == "unknown"


def test_recent_data_is_ok():
    assert _entry(last_ok=_ago(0.5)).status(NOW) == "ok"


def test_quiet_past_tolerance_is_degraded():
    """The core case: no error, no crash, just nothing for weeks."""
    assert _entry(last_ok=_ago(30)).status(NOW) == "degraded"


def test_quiet_within_tolerance_is_still_ok():
    assert _entry(last_ok=_ago(1)).status(NOW) == "ok"


def test_error_streak_is_broken_regardless_of_timing():
    entry = _entry(last_ok=_ago(0), consecutive_errors=ERROR_STREAK_LIMIT)
    assert entry.status(NOW) == "broken"


def test_one_error_is_not_yet_broken():
    """A single blip is a bad afternoon, not a dead source."""
    assert _entry(last_ok=_ago(0), consecutive_errors=1).status(NOW) == "ok"


def test_repeatedly_attempted_never_produced_is_broken():
    entry = _entry(last_ok=None, total_attempts=ERROR_STREAK_LIMIT + 1)
    assert entry.status(NOW) == "broken"


def test_a_success_clears_the_error_streak(tmp_path):
    path = tmp_path / "health.json"
    for _ in range(5):
        record("news", error="boom", path=path)
    assert load_health(path).sources["news"].status() == "broken"
    record("news", items=3, path=path)
    assert load_health(path).sources["news"].consecutive_errors == 0


def test_empty_is_not_an_error(tmp_path):
    """A feed with nothing to say today has not failed."""
    path = tmp_path / "health.json"
    record("news", items=0, path=path)
    entry = load_health(path).sources["news"]
    assert entry.consecutive_empty == 1
    assert entry.consecutive_errors == 0


def test_repeated_emptiness_eventually_reads_as_degraded(tmp_path):
    """But a feed with nothing to say for a month has."""
    path = tmp_path / "health.json"
    record("news", items=5, path=path, now=NOW - timedelta(days=40))
    for day in range(30):
        record("news", items=0, path=path, now=NOW - timedelta(days=30 - day))
    assert load_health(path).sources["news"].status(NOW) == "degraded"


# ---------------------------------------------------------------------------
# Recording and persistence
# ---------------------------------------------------------------------------


def test_record_accumulates_totals(tmp_path):
    path = tmp_path / "health.json"
    record("news", items=10, path=path)
    record("news", items=5, path=path)
    entry = load_health(path).sources["news"]
    assert entry.total_attempts == 2
    assert entry.total_items == 15


def test_record_truncates_giant_errors(tmp_path):
    """An adapter can raise a multi-kilobyte traceback; this file must stay
    glanceable and must not grow without bound."""
    path = tmp_path / "health.json"
    record("news", error="x" * 10_000, path=path)
    assert len(load_health(path).sources["news"].last_error) <= 200


def test_missing_file_is_an_empty_report(tmp_path):
    assert load_health(tmp_path / "nope.json").sources == {}


def test_corrupt_file_does_not_raise(tmp_path, capsys):
    """Diagnostics must never be load-bearing. A broken health file must not
    take down the daily job it exists to monitor."""
    path = tmp_path / "health.json"
    path.write_text("{not json")
    assert load_health(path).sources == {}
    assert "ignoring unreadable" in capsys.readouterr().err


def test_round_trip_survives_save_and_load(tmp_path):
    path = tmp_path / "health.json"
    report = HealthReport(sources={"news": _entry(last_ok=_ago(1), total_items=7)})
    save_health(report, path)
    assert load_health(path).sources["news"].total_items == 7


def test_saved_file_is_valid_json(tmp_path):
    path = tmp_path / "health.json"
    record("news", items=1, path=path)
    assert "sources" in json.loads(path.read_text())


def test_save_leaves_no_temp_file_behind(tmp_path):
    """The atomic-rename dance must not litter, or the data dir fills with
    .tmp files over months."""
    path = tmp_path / "health.json"
    record("news", items=1, path=path)
    assert list(tmp_path.glob("*.tmp")) == []


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def test_degraded_lists_only_problem_sources():
    report = HealthReport(
        sources={
            "good": _entry("good", last_ok=_ago(0)),
            "quiet": _entry("quiet", last_ok=_ago(60)),
            "dead": _entry("dead", last_ok=_ago(0), consecutive_errors=9),
        }
    )
    assert {s.source for s in report.degraded(NOW)} == {"quiet", "dead"}


def test_broken_sorts_before_degraded():
    """Worst first, so a truncated alert still shows the important one."""
    report = HealthReport(
        sources={
            "quiet": _entry("quiet", last_ok=_ago(60)),
            "dead": _entry("dead", last_ok=_ago(0), consecutive_errors=9),
        }
    )
    assert report.degraded(NOW)[0].source == "dead"


def test_healthy_report_has_nothing_degraded():
    report = HealthReport(sources={"a": _entry("a", last_ok=_ago(0))})
    assert report.degraded(NOW) == []


def test_summary_is_json_serializable():
    report = HealthReport(sources={"news": _entry(last_ok=_ago(1))})
    json.dumps(report.summary(NOW))  # must not raise


def test_explain_is_actionable_for_each_state():
    assert "never run" in SourceHealth(source="x").explain(NOW)
    assert "consecutive errors" in _entry(consecutive_errors=9, last_ok=_ago(0)).explain(NOW)
    assert "check the adapter" in _entry(last_ok=_ago(60)).explain(NOW)


def test_check_reads_from_disk(tmp_path):
    path = tmp_path / "health.json"
    save_health(HealthReport(sources={"quiet": _entry("quiet", last_ok=_ago(60))}), path)
    assert [s.source for s in check(path, NOW)] == ["quiet"]


# ---------------------------------------------------------------------------
# Concurrency
#
# Found by the deep-debug pass: the atomic-write temp name used only the PID,
# so two threads in one process built the SAME temp filename and the second
# rename died with FileNotFoundError. It crashed and lost most of the updates.
# `web/server.py` runs a ThreadingHTTPServer, so this was reachable.
# ---------------------------------------------------------------------------


def test_concurrent_records_do_not_crash(tmp_path):
    import threading

    path = tmp_path / "health.json"
    errors: list[str] = []

    def worker(n: int) -> None:
        try:
            for _ in range(20):
                record(f"src{n}", items=1, path=path)
        except Exception as e:  # noqa: BLE001 - the point is to catch any crash
            errors.append(f"{type(e).__name__}: {e}")

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"concurrent writes raised: {errors[:2]}"
    assert len(load_health(path).sources) == 5


def test_concurrent_records_leave_the_file_valid(tmp_path):
    """Atomic rename means the file is never torn, even under contention."""
    import threading

    path = tmp_path / "health.json"

    def worker(n: int) -> None:
        for _ in range(15):
            record(f"src{n}", items=1, path=path)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    json.loads(path.read_text())  # must not raise
    assert list(tmp_path.glob("*.tmp")) == []


# ---------------------------------------------------------------------------
# Diagnostics must never be load-bearing
#
# `refresh_context` records health from inside its except handler. If writing
# health could raise, a handled source failure would become an unhandled crash
# — the monitoring taking down the thing it monitors.
# ---------------------------------------------------------------------------


def test_save_never_raises_when_the_path_is_unwritable(tmp_path, capsys):
    import os
    import stat

    locked = tmp_path / "locked"
    locked.mkdir()
    os.chmod(locked, stat.S_IRUSR | stat.S_IXUSR)
    try:
        assert save_health(HealthReport(), locked / "health.json") is False
        assert "could not write" in capsys.readouterr().err
    finally:
        os.chmod(locked, stat.S_IRWXU)


def test_record_never_raises_when_the_path_is_unwritable(tmp_path):
    import os
    import stat

    locked = tmp_path / "locked"
    locked.mkdir()
    os.chmod(locked, stat.S_IRUSR | stat.S_IXUSR)
    try:
        # Must return an entry rather than raising, so the caller's error
        # handling continues normally.
        entry = record("news", error="boom", path=locked / "health.json")
        assert entry.consecutive_errors == 1
    finally:
        os.chmod(locked, stat.S_IRWXU)


def test_failed_save_leaves_no_partial_file(tmp_path):
    import os
    import stat

    locked = tmp_path / "locked"
    locked.mkdir()
    os.chmod(locked, stat.S_IRUSR | stat.S_IXUSR)
    try:
        save_health(HealthReport(), locked / "health.json")
    finally:
        os.chmod(locked, stat.S_IRWXU)
    assert list(locked.glob("*")) == []


def test_successful_save_reports_true(tmp_path):
    assert save_health(HealthReport(), tmp_path / "health.json") is True


def test_naive_timestamp_does_not_crash():
    """health.json is plain JSON that people hand-edit, and older versions may
    have written a different shape. Subtracting a naive datetime from an aware
    one raises TypeError — which would crash `health` and `doctor` on the very
    file they exist to report on."""
    naive = SourceHealth(
        source="news",
        last_ok=datetime(2026, 5, 1).isoformat(),  # deliberately tz-naive
        total_attempts=5,
    )
    assert naive.status(NOW) == "degraded"
    assert "check the adapter" in naive.explain(NOW)


def test_garbage_timestamp_does_not_crash():
    entry = SourceHealth(source="news", last_ok="not a date at all", total_attempts=5)
    assert entry.quiet_for(NOW) is None
    assert entry.status(NOW) == "ok"  # unusable date -> no evidence of decay
    entry.explain(NOW)  # must not raise


def test_clock_skew_does_not_report_a_source_as_degraded():
    """An NTP correction can leave last_ok in the future. That is a clock
    problem, not a dead feed, and must not be reported as one."""
    future = SourceHealth(
        source="news",
        last_ok=(NOW + timedelta(days=2)).isoformat(),
        total_attempts=5,
    )
    assert future.status(NOW) == "ok"
