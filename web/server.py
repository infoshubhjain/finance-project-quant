"""Read-only web dashboard for the recorded signal log.

Run:
    python -m web.server

This is intentionally minimal: a single process, no auth, no writes, no trading
actions. The frontend lives in `web/static/` as plain HTML/CSS/JS (no build
step) and talks to two JSON endpoints:

    GET /api/dashboard        the aggregate payload (tiles, charts, signal feed)
    GET /api/asset/<SYMBOL>   full recorded history for one asset

Everything else is served from `web/static/`, resolved safely so a crafted
path can never escape that directory.
"""

from __future__ import annotations

import argparse
import json
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from alpha_engine.dashboard.service import build_asset_history, build_dashboard_payload

STATIC_ROOT = Path(__file__).resolve().parent / "static"

# Asset symbols are short tickers (BTC, AAPL, NIFTY, RELIANCE.NS). Anything
# else in the URL segment is rejected before touching the filesystem or log.
_ASSET_RE = re.compile(r"^[A-Za-z0-9._&-]{1,24}$")

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "AlphaDashboard/1.0"

    def do_GET(self) -> None:  # noqa: N802
        path = unquote(urlsplit(self.path).path)

        if path == "/api/dashboard":
            self._send_json(build_dashboard_payload())
            return

        if path.startswith("/api/asset/"):
            symbol = path.removeprefix("/api/asset/")
            if not _ASSET_RE.match(symbol):
                self.send_error(HTTPStatus.BAD_REQUEST, "invalid asset symbol")
                return
            self._send_json(build_asset_history(symbol))
            return

        if path in {"/", "/index.html"}:
            path = "/static/index.html"
        if path.startswith("/static/"):
            self._send_static(path.removeprefix("/static/"))
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _send_static(self, relative: str) -> None:
        """Serve one file from STATIC_ROOT, refusing anything that resolves
        outside it (e.g. /static/../../.env traversal attempts)."""
        target = (STATIC_ROOT / relative).resolve()
        if not target.is_relative_to(STATIC_ROOT) or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")
            return
        content_type = _CONTENT_TYPES.get(target.suffix, "application/octet-stream")
        body = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: dict) -> None:
        body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only dashboard for Alpha Engine.")
    parser.add_argument("--host", default="127.0.0.1", help="bind address")
    parser.add_argument("--port", type=int, default=8000, help="port to listen on")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(f"Dashboard listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
