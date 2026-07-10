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


def _strip_inline_comment(value: str) -> str:
    in_quotes = False
    quote_char = ""
    out: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch in {"'", '"'}:
            if in_quotes and ch == quote_char:
                in_quotes = False
                quote_char = ""
            elif not in_quotes:
                in_quotes = True
                quote_char = ch
            out.append(ch)
        elif ch == "#" and not in_quotes:
            break
        else:
            out.append(ch)
        i += 1
    return "".join(out).strip()


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _load_env_file(path: Path) -> None:
    """Parse a .env file: one KEY=VALUE per line, `export` prefix allowed,
    quotes and quote-aware inline comments handled. Values are single-line —
    every key this project uses is a token, so multi-line support would be
    parser complexity with no user."""
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = _unquote(_strip_inline_comment(raw_value.strip()))


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
