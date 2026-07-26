#!/usr/bin/env python3
"""Entry point for MCP clients that launch a script by path.

    {"mcpServers": {"alpha-engine": {"command": "python",
                                     "args": ["/path/to/mcp_server.py"]}}}

Everything real lives in `alpha_engine.mcp`, inside the installed package, so
the server works identically whether you cloned the repo or `pip install`ed it.
This file exists only because MCP client configs point at a path, and it stays
a shim: no protocol logic, no tool definitions.

Equivalent, and preferred once installed:

    python -m alpha_engine.mcp
"""

from alpha_engine.mcp import serve

if __name__ == "__main__":
    raise SystemExit(serve())
