"""Project-wide environment loading helpers.

The app already uses environment variables for all optional integrations. This
module makes that experience friendlier by loading a local `.env` file if one
exists, without requiring an extra dependency.
"""

from __future__ import annotations

import os
from pathlib import Path

_DOTENV_FILENAMES = (".env.local", ".env")
_ENV_LOADED = False

DATA_DIR_ENV = "ALPHA_DATA_DIR"


def data_dir() -> Path:
    """Root directory for everything the engine writes: cache, signal log,
    trades, calibration, health.

    Default is `data/` relative to the current working directory, which is what
    this project has always done and what the repo-based flow expects.

    `ALPHA_DATA_DIR` overrides it, and that override is what makes the engine
    safe to run from anywhere. Two concrete problems it solves:

    - A scheduled job whose working directory is not the project root tries to
      create `data/` wherever it happens to start. From `/` that is
      `OSError: [Errno 30] Read-only file system`.
    - The pip-installed `alpha-engine` command is meant to be runnable from any
      directory, and without this it scatters a `data/` folder into whichever
      one you were standing in — so your signal log silently splits across
      several places and `record-stats` reports on whichever fragment it found.

    `scripts/daily.sh` sets this explicitly, so the scheduled path does not
    depend on the working directory at all.
    """
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return Path("data")


def _load_env_file(path: Path) -> None:
    """Parse a .env file: one KEY=VALUE per line, `export` prefix allowed.
    Every key this project uses is a bare token (API keys, model names), so
    quote/comment handling would be solving a problem that doesn't exist."""
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        # Strip inline comments (# onwards)
        if "#" in value:
            value = value.split("#", 1)[0].strip()
        # Strip surrounding quotes if present (common .env convention)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def load_project_env() -> None:
    """Load the nearest local `.env` files once, if present.

    Existing environment variables always win. This keeps shell exports and CI
    overrides authoritative while making the local developer flow easier.
    """
    global _ENV_LOADED
    if _ENV_LOADED:
        return

    project_root = Path(__file__).resolve().parents[2]
    search_roots = [Path.cwd(), project_root, *Path.cwd().parents]
    seen: set[Path] = set()
    for root in search_roots:
        for filename in _DOTENV_FILENAMES:
            path = (root / filename).resolve()
            if path in seen or not path.exists() or not path.is_file():
                continue
            seen.add(path)
            _load_env_file(path)

    _ENV_LOADED = True
