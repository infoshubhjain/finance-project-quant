"""Shared test fixtures.

The one thing in here exists because of a real bug: running the test suite was
corrupting live operational state.

`health.record()` defaults to `data/health.json` — the real one. Several tests
deliberately make a fetch fail (monkeypatching `net.get` to raise) to prove that
failures are isolated. Those calls went on to record a genuine-looking error
against the real health file, so after any test run `alpha-engine health`
reported `news.fed_press: 3 consecutive errors: OSError: network down` and the
daily job exited non-zero over a failure that never happened.

A monitoring system that cries wolf gets ignored, which makes it worse than no
monitoring at all. So health writes are redirected to a temp file for the whole
session.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_health_file(tmp_path_factory, monkeypatch):
    """Point health tracking at a throwaway file for every test.

    Autouse and unconditional: a test that writes real health data is a test
    that lies to the operator later, and opting in per-test would mean
    remembering to, which nobody does.

    Tests that care about health specifically pass an explicit `path=` and are
    unaffected by this.
    """
    health_file = tmp_path_factory.mktemp("health") / "health.json"
    monkeypatch.setattr("alpha_engine.health.DEFAULT_PATH", health_file)
