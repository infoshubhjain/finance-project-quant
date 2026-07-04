"""Read-only web dashboard for the recorded signal log.

Run:
    python -m web.server

This is intentionally minimal: a single process, no auth, no writes, no trading
actions. It serves the latest dashboard payload as JSON and renders a static
HTML view that fetches it.
"""

from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from alpha_engine.dashboard.service import build_dashboard_payload

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Alpha Engine Dashboard</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #0b0f14;
      --panel: #111827;
      --panel-2: #0f172a;
      --text: #e5eef8;
      --muted: #8aa0b8;
      --line: #233044;
      --accent: #34d399;
      --accent-2: #60a5fa;
      --warn: #fbbf24;
      --danger: #fb7185;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background:
        radial-gradient(circle at top left, rgba(96, 165, 250, 0.14), transparent 30%),
        radial-gradient(circle at top right, rgba(52, 211, 153, 0.12), transparent 35%),
        linear-gradient(180deg, #08101a, var(--bg));
      color: var(--text);
      font: 15px/1.5 Inter, ui-sans-serif, system-ui, -apple-system, sans-serif;
    }
    .wrap { max-width: 1200px; margin: 0 auto; padding: 32px 20px 56px; }
    header {
      display: flex; align-items: baseline; justify-content: space-between; gap: 16px;
      margin-bottom: 24px;
    }
    h1 { font-size: clamp(28px, 4vw, 42px); margin: 0; letter-spacing: -0.03em; }
    .sub { color: var(--muted); }
    .grid {
      display: grid;
      grid-template-columns: repeat(12, 1fr);
      gap: 16px;
    }
    .card {
      background: rgba(17, 24, 39, 0.88);
      border: 1px solid rgba(35, 48, 68, 0.9);
      border-radius: 18px;
      padding: 18px;
      box-shadow: 0 20px 70px rgba(0,0,0,0.22);
      backdrop-filter: blur(10px);
    }
    .span-3 { grid-column: span 3; }
    .span-4 { grid-column: span 4; }
    .span-8 { grid-column: span 8; }
    .span-12 { grid-column: span 12; }
    .metric { font-size: 30px; font-weight: 700; letter-spacing: -0.03em; }
    .label { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.12em; }
    table { width: 100%; border-collapse: collapse; }
    th, td { padding: 10px 8px; text-align: left; border-bottom: 1px solid var(--line); vertical-align: top; }
    th { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em; }
    .pill {
      display: inline-flex; align-items: center; gap: 6px;
      padding: 4px 10px; border-radius: 999px; font-size: 12px;
      border: 1px solid var(--line); background: rgba(255,255,255,0.03);
    }
    .bull { color: var(--accent); }
    .bear { color: var(--danger); }
    .neutral { color: var(--warn); }
    pre {
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      color: var(--text);
      font: inherit;
    }
    .muted { color: var(--muted); }
    @media (max-width: 900px) {
      .span-3, .span-4, .span-8 { grid-column: span 12; }
      header { flex-direction: column; align-items: flex-start; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <div>
        <h1>Alpha Engine</h1>
        <div class="sub">Read-only signal dashboard. No writes, no orders, no trading actions.</div>
      </div>
      <div class="sub" id="updated">Loading...</div>
    </header>

    <div class="grid">
      <section class="card span-3">
        <div class="label">Recorded Signals</div>
        <div class="metric" id="total-records">0</div>
      </section>
      <section class="card span-3">
        <div class="label">Latest Assets</div>
        <div class="metric" id="latest-count">0</div>
      </section>
      <section class="card span-3">
        <div class="label">Resolved Hit Rate</div>
        <div class="metric" id="hit-rate">-</div>
      </section>
      <section class="card span-3">
        <div class="label">Avg Return</div>
        <div class="metric" id="avg-return">-</div>
      </section>

      <section class="card span-12">
        <div class="label">Assets by Market</div>
        <div id="markets" style="display:flex; flex-wrap:wrap; gap:8px; margin-top:10px;"></div>
      </section>

      <section class="card span-12">
        <div class="label">Latest Signals</div>
        <div style="overflow:auto; margin-top:12px;">
          <table id="signals-table">
            <thead>
              <tr>
                <th>Asset</th>
                <th>Market</th>
                <th>Direction</th>
                <th>Confidence</th>
                <th>Recorded</th>
                <th>Thesis</th>
              </tr>
            </thead>
            <tbody></tbody>
          </table>
        </div>
      </section>
    </div>
  </div>

  <script>
    const fmtPct = (value) => value == null ? "-" : `${(value * 100).toFixed(1)}%`;
    const fmtDate = (value) => value ? new Date(value).toLocaleString() : "-";

    function pill(text, cls) {
      return `<span class="pill ${cls || ''}">${text}</span>`;
    }

    function render(payload) {
      document.getElementById("updated").textContent = `Updated ${new Date().toLocaleString()}`;
      document.getElementById("total-records").textContent = payload.total_records ?? 0;
      document.getElementById("latest-count").textContent = payload.latest_count ?? 0;
      document.getElementById("hit-rate").textContent = fmtPct(payload.outcomes?.hit_rate ?? null);
      document.getElementById("avg-return").textContent = payload.outcomes?.avg_realized_return == null
        ? "-"
        : `${(payload.outcomes.avg_realized_return * 100).toFixed(2)}%`;

      const markets = document.getElementById("markets");
      markets.innerHTML = Object.entries(payload.assets_by_market || {})
        .map(([market, count]) => pill(`${market}: ${count}`))
        .join("");

      const tbody = document.querySelector("#signals-table tbody");
      tbody.innerHTML = (payload.latest_signals || []).map((row) => {
        const cls = row.direction === "bullish" ? "bull" : row.direction === "bearish" ? "bear" : "neutral";
        return `
          <tr>
            <td><strong>${row.asset}</strong></td>
            <td>${row.market}</td>
            <td>${pill(row.direction, cls)}</td>
            <td>${fmtPct(row.confidence)}</td>
            <td class="muted">${fmtDate(row.recorded_at)}</td>
            <td><pre>${row.thesis || ""}</pre></td>
          </tr>
        `;
      }).join("");
    }

    fetch("/api/dashboard")
      .then((r) => r.json())
      .then(render)
      .catch((err) => {
        document.body.insertAdjacentHTML("beforeend", `<pre class="wrap">Failed to load dashboard: ${err}</pre>`);
      });
  </script>
</body>
</html>
"""


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "AlphaDashboard/1.0"

    def do_GET(self) -> None:  # noqa: N802
        if self.path in {"/", "/index.html"}:
            self._send_html(INDEX_HTML)
            return
        if self.path == "/api/dashboard":
            payload = build_dashboard_payload()
            self._send_json(payload)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
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
