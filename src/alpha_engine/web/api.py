"""HTTP API policy: who may call, how often, and what they may change.

`server.py` owns sockets and routing. This module owns the decisions, so they
can be tested without binding a port — and so the rules are readable in one
place rather than scattered through a request handler.

The engine itself is safe to expose: every tool is cache-first and read-only,
and none accepts a number that becomes a decision (see `toolkit.py`). What is
*not* automatically safe is the surface around it, so four things are policy
here rather than assumptions:

1. **Localhost by default.** `server.py` binds 127.0.0.1. Anything else is an
   explicit choice by whoever runs it.
2. **Writes are off unless enabled.** Only `scan` can write (appending to the
   signal log), and only when asked. The log is a track record; a stranger must
   not be able to pad it. `--allow-writes` opts in.
3. **A key is required the moment you leave localhost.** Binding to 0.0.0.0
   without `ALPHA_API_KEY` set is refused, not warned about. A warning printed
   to a terminal nobody is watching is not a security control.
4. **Rate limiting.** `factors` and `backtest` are seconds of CPU each. Without
   a limit, one client with a loop is a denial of service against the operator's
   own laptop.

Deliberately NOT here: user accounts, per-key quotas, billing, an execution
endpoint. Those belong to the platform described in FUTURE_WORK Part B, which is
a different repo for good reasons. This is a self-hosted API — the openalgo
model, where the operator runs their own instance and their data stays on it.
"""

from __future__ import annotations

import hmac
import json
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, ClassVar

from alpha_engine.toolkit import DISCLAIMER, TOOLS, WRITE_CAPABLE, call_tool, tool_names

API_KEY_ENV = "ALPHA_API_KEY"

#: Requests per minute per client IP. Generous for a human, tight enough that a
#: runaway loop hits it before it saturates the box.
DEFAULT_RATE_LIMIT = 60

#: Largest JSON body accepted. Every legitimate request here is a few hundred
#: bytes; anything larger is a mistake or an attempt to exhaust memory.
MAX_BODY_BYTES = 64 * 1024


@dataclass
class ApiConfig:
    """Runtime policy for one server process."""

    api_key: str | None = None
    allow_writes: bool = False
    rate_limit_per_min: int = DEFAULT_RATE_LIMIT
    #: Value for Access-Control-Allow-Origin, or None to send no CORS headers at
    #: all (the default — a browser on another origin then cannot read replies).
    cors_origin: str | None = None

    @classmethod
    def from_env(cls, **overrides: Any) -> ApiConfig:
        key = os.environ.get(API_KEY_ENV) or None
        return cls(api_key=key, **overrides)

    def requires_auth(self) -> bool:
        return bool(self.api_key)


class RateLimiter:
    """Fixed-window counter per client, with a lock because the server is
    threaded.

    ponytail: fixed window, not a sliding one. A client can burst 2x the limit
    across a window boundary; that is fine for protecting a laptop from a
    runaway loop. Switch to a token bucket if this ever fronts real traffic.
    """

    def __init__(self, per_minute: int = DEFAULT_RATE_LIMIT) -> None:
        self.per_minute = per_minute
        self._lock = threading.Lock()
        self._hits: dict[str, tuple[int, int]] = {}  # client -> (window, count)

    def allow(self, client: str, now: float | None = None) -> bool:
        if self.per_minute <= 0:
            return True
        window = int((now if now is not None else time.time()) // 60)
        with self._lock:
            stored_window, count = self._hits.get(client, (window, 0))
            if stored_window != window:
                stored_window, count = window, 0
            if count >= self.per_minute:
                return False
            self._hits[client] = (stored_window, count + 1)
            # The map only ever holds clients seen this minute or last; drop the
            # rest so a long-lived server does not accumulate one entry per IP.
            if len(self._hits) > 4096:
                self._hits = {k: v for k, v in self._hits.items() if v[0] >= window - 1}
        return True


@dataclass
class ApiState:
    """Everything a request handler needs, built once at startup."""

    config: ApiConfig = field(default_factory=ApiConfig)
    limiter: RateLimiter = field(default_factory=RateLimiter)
    log: "RequestLog" = field(default_factory=lambda: RequestLog())

    def __post_init__(self) -> None:
        if self.limiter.per_minute != self.config.rate_limit_per_min:
            self.limiter = RateLimiter(self.config.rate_limit_per_min)


def authorize(state: ApiState, auth_header: str | None) -> tuple[bool, str]:
    """Check the Authorization header. Returns `(ok, message)`.

    Compared with `hmac.compare_digest` rather than `==`: string comparison
    short-circuits on the first differing byte, which leaks the key's prefix to
    anyone willing to time the responses.
    """
    if not state.config.requires_auth():
        return True, ""
    if not auth_header:
        return False, "missing Authorization header (expected: Bearer <key>)"
    token = auth_header.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(token, state.config.api_key or ""):
        return False, "invalid API key"
    return True, ""


def catalogue() -> dict[str, Any]:
    """The self-describing tool list — what `GET /api/v1/tools` returns.

    A caller (human or model) should be able to learn the whole API from this
    one response without reading the source.
    """
    return {
        "engine": "alpha-engine",
        "tools": TOOLS,
        "usage": {
            "call": "POST /api/v1/tools/<name> with a JSON object of arguments",
            "call_get": "GET /api/v1/tools/<name>?asset=BTC (query params, values coerced)",
            "mcp": "POST /api/v1/mcp with a JSON-RPC 2.0 message (MCP over HTTP)",
        },
        "disclaimer": DISCLAIMER,
    }


def coerce_query_args(raw: dict[str, list[str]]) -> dict[str, Any]:
    """Turn query-string values into the types the tool schemas expect.

    Query strings are all text, so `?days=90&record=true` arrives as strings and
    a tool that does `args.get("days", 90) + 1` would concatenate instead of add.
    Numbers become numbers, `true`/`false` become bools, everything else stays a
    string.
    """
    args: dict[str, Any] = {}
    for key, values in raw.items():
        if not values:
            continue
        value = values[-1]
        lowered = value.lower()
        if lowered in ("true", "false"):
            args[key] = lowered == "true"
            continue
        try:
            args[key] = int(value)
            continue
        except ValueError:
            pass
        try:
            args[key] = float(value)
            continue
        except ValueError:
            pass
        args[key] = value
    return args


def dispatch_tool(state: ApiState, name: str, args: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Run one tool under policy. Returns `(http_status, payload)`."""
    if name not in tool_names():
        return 404, {
            "error": f"unknown tool '{name}'",
            "available": tool_names(),
            "disclaimer": DISCLAIMER,
        }

    if not state.config.allow_writes and name in WRITE_CAPABLE and args.get("record"):
        return 403, {
            "error": "this server is read-only; recording to the signal log is disabled",
            "hint": "restart with --allow-writes to permit it",
            "disclaimer": DISCLAIMER,
        }

    payload = call_tool(name, args)
    return (400 if "error" in payload else 200), payload


def dispatch_mcp(state: ApiState, msg: dict[str, Any]) -> dict[str, Any] | None:
    """MCP JSON-RPC over HTTP, so a remote MCP client can use the same tools as
    the stdio server. Tool calls route through `dispatch_tool` so the write gate
    and the tool table apply identically on both transports."""
    from alpha_engine import mcp as mcp_server

    if msg.get("method") == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name", "")
        _status, payload = dispatch_tool(state, name, params.get("arguments") or {})
        msg_id = msg.get("id")
        if msg_id is None:
            return None
        import json

        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "content": [{"type": "text", "text": json.dumps(payload, indent=2, default=str)}],
                "isError": "error" in payload,
            },
        }

    return mcp_server.handle_request(msg)


# ---------------------------------------------------------------------------
# Request logging
# ---------------------------------------------------------------------------


@dataclass
class RequestLog:
    """One line per request: enough to debug an incident, never enough to leak.

    The access log was silenced entirely, and for a real reason — `POST
    /api/v1/agent` carries the caller's LLM API key in its body, and the default
    `BaseHTTPRequestHandler` logger is one careless change away from printing
    bodies. But "no logging at all" means a production incident leaves no trace,
    and that is its own failure.

    So this logs a fixed set of fields and has no code path that can reach a
    body, a header or a query value:

        method, path, status, duration, client, bytes

    **The path is a route template, not the URL.** `/api/v1/tools/scan` logs as
    `/api/v1/tools/<tool>` — asset symbols and every other query value are
    dropped, so a log line cannot reconstruct what anyone looked up. That also
    makes the lines aggregatable, which is what you actually want when asking
    "what got slow".

    Off by default (`--log-requests`), because the honest default for a
    single-operator tool that handles other people's credentials is to record
    nothing.
    """

    enabled: bool = False
    stream: Any = None  # defaults to stderr; injectable for tests

    #: URL shapes collapsed to a template. Ordered: first match wins.
    _ROUTES: ClassVar[tuple[tuple[str, str], ...]] = (
        ("/api/v1/tools/", "/api/v1/tools/<tool>"),
        ("/api/asset/", "/api/asset/<symbol>"),
        ("/static/", "/static/<file>"),
    )

    def template(self, path: str) -> str:
        """Collapse a path to its route. Values never survive this."""
        path = path.split("?", 1)[0]
        for prefix, shape in self._ROUTES:
            if path.startswith(prefix):
                return shape
        return path if path in _KNOWN_PATHS else "<other>"

    def write(
        self, method: str, path: str, status: int, started: float, client: str, size: int
    ) -> None:
        if not self.enabled:
            return
        stream = self.stream if self.stream is not None else sys.stderr
        duration_ms = (time.time() - started) * 1000.0
        print(
            json.dumps(
                {
                    "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "method": method,
                    "route": self.template(path),
                    "status": status,
                    "ms": round(duration_ms, 1),
                    "client": client,
                    "bytes": size,
                }
            ),
            file=stream,
            flush=True,
        )


#: Paths logged verbatim because they carry no user input. Anything not here
#: becomes `<other>` rather than being echoed — a 404 for
#: `/../../etc/passwd?key=sk-...` must not put that string in a log file.
_KNOWN_PATHS = frozenset(
    {
        "/",
        "/index.html",
        "/dashboard",
        "/terminal",
        "/api/dashboard",
        "/api/v1/tools",
        "/api/v1/mcp",
        "/api/v1/agent",
        "/api/v1/providers",
    }
)
