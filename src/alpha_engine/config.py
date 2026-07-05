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
    """Parse a .env file, handling multi-line values enclosed in quotes."""
    lines = path.read_text().splitlines()
    i = 0
    while i < len(lines):
        raw_line = lines[i]
        line = raw_line.strip()
        if not line or line.startswith("#"):
            i += 1
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            i += 1
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if not key or key in os.environ:
            i += 1
            continue

        # Handle multi-line quoted values: keep reading until closing quote found.
        if raw_value and raw_value[0] in {"'", '"'}:
            quote_char = raw_value[0]
            # Check if closing quote exists on same line (excluding trailing comment)
            stripped = _strip_inline_comment(raw_value)
            if stripped.endswith(quote_char):
                value = _unquote(stripped)
            else:
                # Multi-line: accumulate lines until closing quote.
                accumulated = [raw_value]
                i += 1
                while i < len(lines):
                    next_line = lines[i]
                    accumulated.append(next_line)
                    joined = " ".join(line.strip() for line in accumulated)
                    # strip inline comment then check for closing quote
                    comment_stripped = _strip_inline_comment(joined)
                    if comment_stripped.endswith(quote_char):
                        value = _unquote(comment_stripped)
                        break
                    i += 1
                else:
                    # Reached end of file without closing quote — use what we have.
                    joined = " ".join(line.strip() for line in accumulated)
                    value = _unquote(_strip_inline_comment(joined))
        else:
            value = _unquote(_strip_inline_comment(raw_value))

        os.environ[key] = value
        i += 1


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
