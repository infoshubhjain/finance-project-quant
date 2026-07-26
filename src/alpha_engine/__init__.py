from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _installed_version

from alpha_engine.config import load_project_env

load_project_env()

try:
    # One source of truth: pyproject.toml, read back through the installed
    # metadata. This was hardcoded and drifted to two different answers —
    # `__init__` said 0.1.0 while pyproject said 0.5.0, and the dashboard footer
    # rendered "v0.1.0" on top of 0.5.0 code. A version number nobody can trust
    # is worse than none, because it is the first thing quoted in a bug report.
    __version__ = _installed_version("alpha-engine")
except PackageNotFoundError:  # pragma: no cover - source tree, not installed
    __version__ = "0.0.0+unknown"
