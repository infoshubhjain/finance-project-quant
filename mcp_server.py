"""MCP server: the engine as a tool an AI assistant can call.

Run it directly (`python mcp_server.py`) or point an MCP client at it. It speaks
MCP over stdio: newline-delimited JSON-RPC 2.0 on stdin/stdout.

    {"mcpServers": {"alpha-engine": {"command": "python",
                                     "args": ["/path/to/mcp_server.py"]}}}

For a remote client, `web/server.py` speaks the same protocol over HTTP POST at
`/api/v1/mcp` — same tools, same handlers, because both dispatch into
`alpha_engine.toolkit`.

What lives here and what does not
---------------------------------
This file is **transport only**: JSON-RPC framing, the three-method handshake,
and the stdout discipline below. Every tool definition and implementation lives
in `alpha_engine/toolkit.py` so the stdio server, the HTTP API and the AI
terminal cannot drift apart. Adding a tool here would be a bug — add it there
and all three surfaces get it.

Why this file has no MCP SDK dependency
---------------------------------------
The stdio transport is newline-delimited JSON-RPC and the handshake is three
methods. This repo already replaced `requests` with ~60 lines of `urllib`
because the dependency bought nothing; the same reasoning applies here. If this
server ever needs resources, prompts, or sampling, take the SDK — for a handful
of read-only tools it would be more code to configure than to implement.

Why the architecture fits MCP unusually well
--------------------------------------------
MCP means the model *calls* deterministic tools and *reads* their results. It
never computes the numbers. That is precisely this repo's cardinal rule, so the
engine is already shaped correctly for MCP with no compromise. Most quant MCP
servers get this backwards — they let the model do the reasoning and the maths,
so the output is unreproducible. This one structurally cannot: the model may
only ask the engine questions and relay what tested Python answered.
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any

from alpha_engine.toolkit import DISCLAIMER, HANDLERS, INSTRUCTIONS, TOOLS, call_tool

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "alpha-engine"
SERVER_VERSION = "0.5.0"

# Re-exported, not redefined: these are the same objects `toolkit` owns, so
# anything that patched `mcp_server.HANDLERS` before the registry moved still
# patches the real table. New tools go in `toolkit.py`, never here.
__all__ = ["DISCLAIMER", "HANDLERS", "TOOLS", "call_tool", "handle_request", "serve"]


def handle_request(msg: dict[str, Any]) -> dict[str, Any] | None:
    """Dispatch one JSON-RPC message. Returns None for notifications, which by
    protocol must not be answered."""
    method = msg.get("method")
    msg_id = msg.get("id")

    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": INSTRUCTIONS,
        }
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = msg.get("params") or {}
        payload = call_tool(params.get("name", ""), params.get("arguments") or {})
        result = {
            "content": [{"type": "text", "text": json.dumps(payload, indent=2, default=str)}],
            "isError": "error" in payload,
        }
    elif method in ("notifications/initialized", "initialized"):
        return None  # notification: no response
    elif method == "ping":
        result = {}
    else:
        if msg_id is None:
            return None
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"method not found: {method}"},
        }

    if msg_id is None:
        return None
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def serve(stdin=None, stdout=None) -> int:
    """Read newline-delimited JSON-RPC from stdin, write responses to stdout.

    Note stdout is reserved for protocol traffic — every diagnostic in this
    process must go to stderr, or it corrupts the stream. The engine's ingestion
    layer already prints to stderr throughout, which is why that works.
    """
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout

    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            print("[mcp] dropped unparseable line", file=sys.stderr)
            continue

        try:
            response = handle_request(msg)
        except Exception:  # noqa: BLE001 - one bad request must not kill the server
            traceback.print_exc(file=sys.stderr)
            response = {
                "jsonrpc": "2.0",
                "id": msg.get("id"),
                "error": {"code": -32603, "message": "internal error"},
            }

        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()

    return 0


if __name__ == "__main__":
    raise SystemExit(serve())
