"""MCP server: the engine as a tool an AI assistant can call.

Run it directly (`python mcp_server.py`) or point an MCP client at it. It speaks
MCP over stdio: newline-delimited JSON-RPC 2.0 on stdin/stdout.

Why this file has no MCP SDK dependency
---------------------------------------
The stdio transport is newline-delimited JSON-RPC and the handshake is three
methods. This repo already replaced `requests` with ~60 lines of `urllib`
because the dependency bought nothing; the same reasoning applies here. If this
server ever needs resources, prompts, or sampling, take the SDK — for five
read-only tools it would be more code to configure than to implement.

Why the architecture fits MCP unusually well
--------------------------------------------
MCP means the model *calls* deterministic tools and *reads* their results. It
never computes the numbers. That is precisely this repo's cardinal rule, so the
engine is already shaped correctly for MCP with no compromise. Most quant MCP
servers get this backwards — they let the model do the reasoning and the maths,
so the output is unreproducible. This one structurally cannot: the model may
only ask the engine questions and relay what tested Python answered.

The four non-negotiables for this surface (from FUTURE_WORK Phase 14)
---------------------------------------------------------------------
1. **The disclaimer travels with every payload.** Not in the README — in the
   tool response itself. MCP results get pasted into other people's contexts,
   so the research-only framing must be inseparable from the data.
2. **Cache-first, hard.** An MCP server that gets popular will get your IP
   banned by CoinGecko within a day. Every tool defaults to `no_refresh=True`
   and serves stale-but-labelled data rather than hammering free APIs.
3. **Read-only by default.** No tool writes to the signal log unless explicitly
   asked. The log is the compounding asset; an exploratory assistant must not
   be able to pollute it.
4. **No tool accepts a number that becomes a decision.** There is no
   `set_confidence`, no `override_weight`. The tools answer questions; they do
   not accept opinions.
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "alpha-engine"
SERVER_VERSION = "0.3.0"

DISCLAIMER = (
    "RESEARCH ONLY. This is not financial advice, not a recommendation, and not "
    "a solicitation to trade. Signals are the output of deterministic statistical "
    "models with no proven edge. Past behaviour does not predict future results. "
    "Anyone acting on this is doing so entirely at their own risk."
)


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[dict[str, Any]] = [
    {
        "name": "scan",
        "description": (
            "Generate a research signal for one asset: direction, calibrated "
            "confidence, invalidation level, and every contributing source with "
            "its weight. Serves cached data by default."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "asset": {"type": "string", "description": "Ticker, e.g. BTC, AAPL, RELIANCE.NS"},
                "market": {
                    "type": "string",
                    "enum": ["crypto", "us_equity", "in_equity", "in_fno", "forex"],
                    "description": "Override auto-detection",
                },
                "days": {"type": "integer", "description": "History window (default 90)"},
                "record": {
                    "type": "boolean",
                    "description": "Append to the signal log. Default false: the log is a "
                    "track record, not a scratchpad.",
                },
            },
            "required": ["asset"],
        },
    },
    {
        "name": "report",
        "description": (
            "Full quantitative report for one asset: trend, momentum, volatility "
            "regime, volume structure, and model reads (Kalman/GARCH/HMM)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "asset": {"type": "string"},
                "market": {"type": "string"},
                "days": {"type": "integer"},
            },
            "required": ["asset"],
        },
    },
    {
        "name": "backtest",
        "description": (
            "Replay history through the analyzer with no lookahead, and report "
            "hit rate, average return, and per-analyzer attribution."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "asset": {"type": "string"},
                "market": {"type": "string"},
                "days": {"type": "integer", "description": "History to replay (default 365)"},
                "step": {"type": "integer", "description": "Bars between signals (default 5)"},
            },
            "required": ["asset"],
        },
    },
    {
        "name": "factors",
        "description": (
            "Rank 500+ deterministic factors by measured predictive power (rank "
            "IC) for one asset. Includes the multiple-testing noise floor, which "
            "says what the best purely random factor would have scored."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "asset": {"type": "string"},
                "market": {"type": "string"},
                "days": {"type": "integer", "description": "History window (default 365)"},
                "horizon": {"type": "integer", "description": "Forward return bars (default 10)"},
                "family": {"type": "string", "description": "Restrict to one factor family"},
                "top": {"type": "integer", "description": "Rows to return (default 25)"},
            },
            "required": ["asset"],
        },
    },
    {
        "name": "record_stats",
        "description": (
            "The live track record: how recorded signals actually resolved. "
            "Read-only. This is the honest answer to 'does it work?'"
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]


# ---------------------------------------------------------------------------
# Tool implementations — thin wrappers over the existing pipeline
# ---------------------------------------------------------------------------


def _cache():
    from alpha_engine.cache.interface import Cache

    return Cache()


def _resolve(asset: str, market: str | None):
    from alpha_engine.cli.main import detect_market

    return asset.upper(), detect_market(asset, market)


def tool_scan(args: dict[str, Any]) -> dict[str, Any]:
    from alpha_engine.cli.main import _build_price_signal, _load_series
    from alpha_engine.schema.signal import Market

    asset, market = _resolve(args["asset"], args.get("market"))
    cache = _cache()

    if market is Market.IN_FNO:
        from alpha_engine.cli.main import _build_fno_signal

        chain, _stale = cache.get_chain(asset)
        if chain is None:
            return {"error": f"no options chain cached for {asset}; run `fetch-chain` first"}
        signal = _build_fno_signal(asset, chain)
    else:
        # no_refresh=True: cache-first is a hard rule on this surface.
        series = _load_series(asset, market, args.get("days", 90), True, cache)
        if not series.candles:
            return {"error": f"no cached data for {asset}; run `scan {asset}` in the CLI first"}
        signal = _build_price_signal(asset, market, series, cache, no_refresh=True)

    # Writing to the log is opt-in. The log is the compounding asset.
    if args.get("record"):
        from alpha_engine.validation.recorder import record_signal

        record_signal(signal)

    return json.loads(signal.model_dump_json())


def tool_report(args: dict[str, Any]) -> dict[str, Any]:
    from alpha_engine.cli.main import _load_series
    from alpha_engine.quant.report import build_report

    asset, market = _resolve(args["asset"], args.get("market"))
    series = _load_series(asset, market, args.get("days", 180), True, _cache())
    if not series.candles:
        return {"error": f"no cached data for {asset}"}
    return json.loads(build_report(series, market.value).model_dump_json())


def tool_backtest(args: dict[str, Any]) -> dict[str, Any]:
    from alpha_engine.cli.main import _load_series
    from alpha_engine.validation.backtest import run_backtest

    asset, market = _resolve(args["asset"], args.get("market"))
    series = _load_series(asset, market, args.get("days", 365), True, _cache())
    if not series.candles:
        return {"error": f"no cached data for {asset}"}
    return json.loads(run_backtest(series, market, step=args.get("step", 5)).model_dump_json())


def tool_factors(args: dict[str, Any]) -> dict[str, Any]:
    from alpha_engine.cli.main import _load_series
    from alpha_engine.quant.factors import FACTOR_REGISTRY, compute_panel, factor_names
    from alpha_engine.quant.ranking import noise_floor_ic, rank_factors

    asset, market = _resolve(args["asset"], args.get("market"))
    series = _load_series(asset, market, args.get("days", 365), True, _cache())
    if not series.candles:
        return {"error": f"no cached data for {asset}"}

    family = args.get("family")
    names = factor_names(families=[family] if family else None)
    if not names:
        return {"error": f"unknown factor family '{family}'"}

    panel = compute_panel(series, names=names)
    scores = rank_factors(series, panel, horizon=args.get("horizon", 10))

    obs = [s.n_obs for s in scores if s.n_obs > 0]
    median_obs = sorted(obs)[len(obs) // 2] if obs else 0
    floor = noise_floor_ic(len(scores), median_obs)

    top = args.get("top", 25)
    return {
        "asset": asset,
        "bars": len(series.candles),
        "factors_scored": len(scores),
        "noise_floor_ic": round(floor, 4) if floor else None,
        "noise_floor_note": (
            "An |IC| below the noise floor is what the best of this many purely "
            "random factors would reach by chance. Below it means nothing."
        ),
        "factors": [
            {
                "factor": s.name,
                "family": FACTOR_REGISTRY[s.name].family if s.name in FACTOR_REGISTRY else None,
                "rank_ic": s.rank_ic,
                "hit_rate": s.hit_rate,
                "coverage": round(s.coverage, 3),
                "n_obs": s.n_obs,
            }
            for s in scores[:top]
        ],
    }


def tool_record_stats(args: dict[str, Any]) -> dict[str, Any]:
    """Score recorded signals against cached prices — the same flow as the
    `record-stats` CLI command, so the two can never disagree."""
    from alpha_engine.validation.outcomes import score_record, summarize_outcomes
    from alpha_engine.validation.recorder import read_records

    records = read_records()
    if not records:
        return {"records": 0, "note": "no signals recorded yet"}

    cache = _cache()
    scored = []
    for record in records:
        series, _stale = cache.get_price(record.signal.asset, "1d")
        if series is None:
            continue  # no cached prices for this asset; reported as skipped
        scored.append((record.signal.confidence, score_record(record, series)))

    payload = json.loads(summarize_outcomes(scored).model_dump_json())
    payload["records_total"] = len(records)
    payload["records_skipped_no_prices"] = len(records) - len(scored)
    return payload


HANDLERS = {
    "scan": tool_scan,
    "report": tool_report,
    "backtest": tool_backtest,
    "factors": tool_factors,
    "record_stats": tool_record_stats,
}


def call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Run one tool and attach the disclaimer. Errors come back as data rather
    than exceptions so the assistant can relay them instead of dying."""
    handler = HANDLERS.get(name)
    if handler is None:
        return {"error": f"unknown tool '{name}'", "disclaimer": DISCLAIMER}
    try:
        result = handler(args or {})
    except Exception as e:  # noqa: BLE001 - a tool failure is a result, not a crash
        result = {"error": f"{type(e).__name__}: {e}"}
    result["disclaimer"] = DISCLAIMER
    return result


# ---------------------------------------------------------------------------
# JSON-RPC / MCP plumbing
# ---------------------------------------------------------------------------


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
            "instructions": (
                "Deterministic quant research engine. Every number these tools "
                "return was computed by tested Python, never by a language "
                "model. Relay the results; do not recompute or extrapolate them. " + DISCLAIMER
            ),
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
