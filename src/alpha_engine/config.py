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
