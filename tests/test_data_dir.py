"""Tests for writable-state location.

The engine resolves `data/` relative to the working directory. That is fine for
the repo-based flow and wrong everywhere else:

- A scheduled job that starts outside the project root reads an empty cache and
  tries to create `data/` wherever it landed. From `/` that is
  `OSError: [Errno 30] Read-only file system`.
- The pip-installed `alpha-engine` command is meant to run from any directory,
  and without an override it scatters `data/` folders around — splitting the
  signal log so `record-stats` reports on whichever fragment it found.

`ALPHA_DATA_DIR` fixes both, and `scripts/daily.sh` sets it.
"""

from __future__ import annotations

import os
from pathlib import Path

from alpha_engine.config import DATA_DIR_ENV, data_dir


def test_defaults_to_cwd_relative_data(monkeypatch):
    """Unchanged default: the repo flow must keep working exactly as before."""
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    assert data_dir() == Path("data")


def test_env_var_overrides_the_default(monkeypatch, tmp_path):
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path / "elsewhere"))
    assert data_dir() == tmp_path / "elsewhere"


def test_override_expands_a_home_shortcut(monkeypatch):
    """cron entries and .env files use ~ freely; an unexpanded one would create
    a literal './~' directory."""
    monkeypatch.setenv(DATA_DIR_ENV, "~/alpha-data")
    assert "~" not in str(data_dir())
    assert data_dir().is_absolute()


def test_empty_override_falls_back_to_the_default(monkeypatch):
    """`ALPHA_DATA_DIR=` in a .env file must not resolve to the filesystem root."""
    monkeypatch.setenv(DATA_DIR_ENV, "")
    assert data_dir() == Path("data")


def test_cache_writes_land_under_the_override(monkeypatch, tmp_path):
    """The end-to-end property: with the override set, running from any
    directory reads and writes the same place."""
    from datetime import datetime, timezone

    from alpha_engine.cache.interface import Cache
    from alpha_engine.cache.models import NewsItem

    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path))
    monkeypatch.chdir(tmp_path.parent)  # deliberately not the project root

    cache = Cache()
    cache.put_news(
        "t", [NewsItem(ts=datetime.now(timezone.utc), headline="h", source="t", url="u")]
    )

    assert (tmp_path / "cache" / "news" / "t.json").exists()
    items, _ = cache.get_news()
    assert len(items) == 1


def test_running_from_another_directory_does_not_write_there(monkeypatch, tmp_path):
    """The stray-data-folder failure: with the override set, a run from an
    unrelated directory must leave nothing behind in it."""
    from alpha_engine.cache.interface import Cache

    workdir = tmp_path / "somewhere_else"
    workdir.mkdir()
    datadir = tmp_path / "real_data"

    monkeypatch.setenv(DATA_DIR_ENV, str(datadir))
    monkeypatch.chdir(workdir)

    Cache()  # constructing the store is what creates directories

    assert not (workdir / "data").exists()
    assert (datadir / "cache").exists()


def test_daily_script_sets_the_override():
    """scripts/daily.sh must export it, or the scheduled job stays vulnerable to
    whatever working directory cron hands it."""
    script = Path(__file__).resolve().parents[1] / "scripts" / "daily.sh"
    assert f"export {DATA_DIR_ENV}=" in script.read_text()


def test_env_var_name_is_stable():
    """Documented in RUNNING_IT.md and .env.example; renaming it silently
    orphans anyone's configuration."""
    assert DATA_DIR_ENV == "ALPHA_DATA_DIR"
    assert os.environ.get(DATA_DIR_ENV) is None or True  # presence is optional
