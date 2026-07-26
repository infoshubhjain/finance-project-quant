"""Strategy discovery: find every `BaseStrategy` subclass available to run.

Two sources, in order:

1. **Built-ins** — `alpha_engine.strategy.builtin`, shipped with the package.
2. **A user folder** — `./strategies/` by default, or `$ALPHA_STRATEGY_DIR`.
   Every `.py` file in it is imported and scanned.

A broken strategy file never breaks discovery: its import error is captured and
returned alongside the strategies that did load, so one typo in one file cannot
take the whole list down.

SECURITY — the line this module must never cross
------------------------------------------------
Loading a strategy **executes arbitrary Python** in this process. Locally that
is fine: it is the same trust level as running the repo at all, and it is the
only way a strategy folder can work.

It is emphatically NOT fine over a network. No HTTP route in this project
accepts strategy source code, and none may be added — that is remote code
execution, not a feature. A caller may *select and parameterise* a strategy that
already exists on the server's disk; putting code there stays a local,
filesystem-level act by whoever runs the server.

Doing it safely needs a real sandbox — process isolation, no network egress,
CPU/memory/wall-clock caps, an escape-proof filesystem — which is the security
boundary described in FUTURE_WORK Phase A2 and belongs in the platform repo,
not here.
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import os
import pkgutil
import sys
from pathlib import Path
from typing import Any

from alpha_engine.strategy.base import BaseStrategy

STRATEGY_DIR_ENV = "ALPHA_STRATEGY_DIR"
_BUILTIN_PACKAGE = "alpha_engine.strategy.builtin"


def strategy_dir() -> Path:
    """User strategy folder. `./strategies/` unless `$ALPHA_STRATEGY_DIR` says
    otherwise — same override pattern as `config.data_dir()`, and for the same
    reason: a scheduled job does not run from the project root."""
    override = os.environ.get(STRATEGY_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return Path("strategies")


def _classes_in(module: Any, module_name: str) -> dict[str, type[BaseStrategy]]:
    """Every concrete BaseStrategy subclass *defined in* this module. The
    `__module__` check keeps imported base classes out of the results."""
    found: dict[str, type[BaseStrategy]] = {}
    for _, obj in inspect.getmembers(module, inspect.isclass):
        if (
            issubclass(obj, BaseStrategy)
            and obj is not BaseStrategy
            and obj.__module__ == module_name
            and not inspect.isabstract(obj)
        ):
            found[obj.__name__] = obj
    return found


def discover_strategies(
    directory: Path | str | None = None,
) -> tuple[dict[str, type[BaseStrategy]], dict[str, str]]:
    """Return `({class_name: class}, {source: error})`.

    Keys are class names (`SMACrossover`), not display names — a display name is
    prose and two strategies may share one, but the API needs a stable handle.
    User strategies override built-ins of the same class name.
    """
    strategies: dict[str, type[BaseStrategy]] = {}
    errors: dict[str, str] = {}

    builtin = importlib.import_module(_BUILTIN_PACKAGE)
    for info in pkgutil.iter_modules(builtin.__path__):
        name = f"{_BUILTIN_PACKAGE}.{info.name}"
        try:
            strategies.update(_classes_in(importlib.import_module(name), name))
        except Exception as e:  # noqa: BLE001 - one bad module must not hide the rest
            errors[name] = f"{type(e).__name__}: {e}"

    folder = Path(directory) if directory is not None else strategy_dir()
    if not folder.is_dir():
        return strategies, errors

    for path in sorted(folder.glob("*.py")):
        if path.name.startswith("_"):
            continue
        module_name = f"alpha_engine_user_strategy_{path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                errors[path.name] = "could not build an import spec"
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            strategies.update(_classes_in(module, module_name))
        except Exception as e:  # noqa: BLE001 - a user file is expected to be broken sometimes
            errors[path.name] = f"{type(e).__name__}: {e}"

    return strategies, errors


def load_strategy(key: str, directory: Path | str | None = None, **params: Any) -> BaseStrategy:
    """Instantiate one strategy by class name. Raises KeyError listing what is
    available, because "unknown strategy" with no list is a useless error."""
    strategies, _errors = discover_strategies(directory)
    cls = strategies.get(key)
    if cls is None:
        known = ", ".join(sorted(strategies)) or "(none found)"
        raise KeyError(f"unknown strategy '{key}'. Available: {known}")
    return cls(**params)


def list_strategies(directory: Path | str | None = None) -> dict[str, Any]:
    """Serializable catalogue — what the API and CLI show."""
    strategies, errors = discover_strategies(directory)
    return {
        "strategies": [
            {
                "key": key,
                "name": cls.name,
                "description": cls.description,
                "params": dict(cls.params),
            }
            for key, cls in sorted(strategies.items())
        ],
        "errors": errors,
        "strategy_dir": str(strategy_dir()),
    }
