"""Tests for the launcher script, `start.sh`.

`start.sh` is the one command a new user runs, and every relative path inside it
(`pip install -e .`, `data/`, `ruff .`, `pytest`, `portfolio.json`) resolves
against the *working directory*, not the script's directory. Invoked from
anywhere but the project root that meant:

- `pip install -e ".[dev]"` installing whatever directory you were standing in,
- a stray `data/` tree created there, splitting the signal log so `record-stats`
  reports on whichever fragment it found.

`scripts/daily.sh` already cds first for exactly this reason. These tests pin
the same guarantee for `start.sh`, because it is a one-line fix that reads like
a redundant line and is easy to delete.

Grep-level assertions on purpose: actually invoking the script needs a venv, a
pip install and the network, none of which belong in this suite.
"""

from __future__ import annotations

from pathlib import Path

START_SH = Path(__file__).resolve().parent.parent / "start.sh"


def test_launcher_runs_from_the_project_root() -> None:
    """Without this, `~ $ /path/to/start.sh scan BTC` writes into the home dir."""
    body = START_SH.read_text()
    assert 'cd "$SCRIPT_DIR"' in body


def test_launcher_resolves_state_through_the_data_dir_override() -> None:
    """start.sh checks the signal log and cache size itself. Those checks have to
    look where `config.data_dir()` writes, or `ALPHA_DATA_DIR` users get a doctor
    reporting on an empty directory and a dashboard that re-seeds on every launch."""
    body = START_SH.read_text()
    assert 'DATA_DIR="${ALPHA_DATA_DIR:-$SCRIPT_DIR/data}"' in body
    # No path may reach around DATA_DIR back to a hardcoded repo-relative one.
    assert "$SCRIPT_DIR/data/" not in body


def test_doctor_survives_a_degraded_source() -> None:
    """`health` exits non-zero when a source is degraded, and the script runs under
    `set -o pipefail`. Unguarded, that aborts doctor mid-report and hides the cron
    and end-to-end sections in precisely the case doctor exists to diagnose."""
    body = START_SH.read_text()
    assert "run_cli health 2>&1 | sed 's/^/    /' || true" in body
