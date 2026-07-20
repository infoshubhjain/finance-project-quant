"""Tests for Phase 14: the MCP server.

The protocol tests matter, but the four *non-negotiables* matter more. They are
the difference between exposing a research engine and exposing a machine that
launders model guesses into authoritative-looking JSON:

1. the disclaimer travels with every payload,
2. tools are cache-first (no hammering free APIs),
3. nothing writes to the signal log unless explicitly asked,
4. no tool accepts a number that becomes a decision.

Each gets an explicit test below.
"""

from __future__ import annotations

import io
import json

import mcp_server


def _rpc(method: str, params: dict | None = None, msg_id: int | None = 1) -> dict:
    msg = {"jsonrpc": "2.0", "method": method}
    if msg_id is not None:
        msg["id"] = msg_id
    if params is not None:
        msg["params"] = params
    return msg


# ---------------------------------------------------------------------------
# Protocol handshake
# ---------------------------------------------------------------------------


def test_initialize_returns_protocol_and_server_info():
    resp = mcp_server.handle_request(_rpc("initialize"))
    assert resp["result"]["protocolVersion"] == mcp_server.PROTOCOL_VERSION
    assert resp["result"]["serverInfo"]["name"] == "alpha-engine"


def test_initialize_instructions_carry_the_disclaimer():
    """The instructions land in the assistant's context; the framing has to be
    there too."""
    resp = mcp_server.handle_request(_rpc("initialize"))
    assert "RESEARCH ONLY" in resp["result"]["instructions"]


def test_tools_list_exposes_every_handler():
    resp = mcp_server.handle_request(_rpc("tools/list"))
    names = {t["name"] for t in resp["result"]["tools"]}
    assert names == set(mcp_server.HANDLERS)


def test_every_tool_declares_an_input_schema():
    for tool in mcp_server.TOOLS:
        assert tool["inputSchema"]["type"] == "object"
        assert tool["description"]


def test_notifications_get_no_response():
    """Answering a notification is a protocol violation."""
    assert mcp_server.handle_request(_rpc("notifications/initialized", msg_id=None)) is None


def test_unknown_method_returns_method_not_found():
    resp = mcp_server.handle_request(_rpc("does/not/exist"))
    assert resp["error"]["code"] == -32601


def test_unknown_notification_is_silent():
    assert mcp_server.handle_request(_rpc("unknown/notify", msg_id=None)) is None


def test_ping_is_answered():
    assert mcp_server.handle_request(_rpc("ping"))["result"] == {}


# ---------------------------------------------------------------------------
# Non-negotiable 1: the disclaimer travels with every payload
# ---------------------------------------------------------------------------


def test_every_tool_result_carries_the_disclaimer(monkeypatch):
    for name in mcp_server.HANDLERS:
        monkeypatch.setitem(mcp_server.HANDLERS, name, lambda a: {"ok": True})
        payload = mcp_server.call_tool(name, {})
        assert payload["disclaimer"] == mcp_server.DISCLAIMER, f"{name} dropped the disclaimer"


def test_error_results_carry_the_disclaimer_too():
    """The framing must survive the failure path, which is exactly where a
    caller is most likely to improvise."""
    assert "disclaimer" in mcp_server.call_tool("not_a_tool", {})


def test_tool_exception_becomes_data_not_a_crash(monkeypatch):
    def boom(args):
        raise RuntimeError("kaboom")

    monkeypatch.setitem(mcp_server.HANDLERS, "scan", boom)
    payload = mcp_server.call_tool("scan", {"asset": "BTC"})
    assert "kaboom" in payload["error"]
    assert "disclaimer" in payload


def test_tools_call_marks_errors(monkeypatch):
    monkeypatch.setitem(mcp_server.HANDLERS, "scan", lambda a: {"error": "nope"})
    resp = mcp_server.handle_request(
        _rpc("tools/call", {"name": "scan", "arguments": {"asset": "BTC"}})
    )
    assert resp["result"]["isError"] is True


def test_tools_call_wraps_payload_as_text_content(monkeypatch):
    monkeypatch.setitem(mcp_server.HANDLERS, "scan", lambda a: {"direction": "bullish"})
    resp = mcp_server.handle_request(
        _rpc("tools/call", {"name": "scan", "arguments": {"asset": "BTC"}})
    )
    content = resp["result"]["content"][0]
    assert content["type"] == "text"
    assert json.loads(content["text"])["direction"] == "bullish"


# ---------------------------------------------------------------------------
# Non-negotiable 2: cache-first, always
# ---------------------------------------------------------------------------


def test_scan_never_refreshes_from_the_network(monkeypatch):
    """`no_refresh=True` on every load. An MCP server that refetches on every
    call gets the user's IP banned by CoinGecko within a day."""
    seen = {}

    def fake_load_series(asset, market, days, no_refresh, cache):
        seen["no_refresh"] = no_refresh
        raise RuntimeError("stop here")

    monkeypatch.setattr("alpha_engine.cli.main._load_series", fake_load_series)
    mcp_server.call_tool("scan", {"asset": "BTC"})
    assert seen["no_refresh"] is True


def test_report_never_refreshes_from_the_network(monkeypatch):
    seen = {}

    def fake_load_series(asset, market, days, no_refresh, cache):
        seen["no_refresh"] = no_refresh
        raise RuntimeError("stop here")

    monkeypatch.setattr("alpha_engine.cli.main._load_series", fake_load_series)
    mcp_server.call_tool("report", {"asset": "BTC"})
    assert seen["no_refresh"] is True


def test_factors_never_refreshes_from_the_network(monkeypatch):
    seen = {}

    def fake_load_series(asset, market, days, no_refresh, cache):
        seen["no_refresh"] = no_refresh
        raise RuntimeError("stop here")

    monkeypatch.setattr("alpha_engine.cli.main._load_series", fake_load_series)
    mcp_server.call_tool("factors", {"asset": "BTC"})
    assert seen["no_refresh"] is True


# ---------------------------------------------------------------------------
# Non-negotiable 3: read-only by default
# ---------------------------------------------------------------------------


def test_scan_does_not_write_to_the_log_by_default(monkeypatch):
    """The signal log is the compounding asset. A chatty assistant running
    exploratory scans must not be able to pollute the track record."""
    writes = []
    monkeypatch.setattr(
        "alpha_engine.validation.recorder.record_signal",
        lambda *a, **kw: writes.append(a),
    )

    class FakeSignal:
        def model_dump_json(self):
            return "{}"

    monkeypatch.setattr("alpha_engine.cli.main._load_series", lambda *a, **kw: _fake_series())
    monkeypatch.setattr("alpha_engine.cli.main._build_price_signal", lambda *a, **kw: FakeSignal())

    mcp_server.call_tool("scan", {"asset": "BTC"})
    assert writes == []


def test_scan_writes_only_when_explicitly_asked(monkeypatch):
    writes = []
    monkeypatch.setattr(
        "alpha_engine.validation.recorder.record_signal",
        lambda *a, **kw: writes.append(a),
    )

    class FakeSignal:
        def model_dump_json(self):
            return "{}"

    monkeypatch.setattr("alpha_engine.cli.main._load_series", lambda *a, **kw: _fake_series())
    monkeypatch.setattr("alpha_engine.cli.main._build_price_signal", lambda *a, **kw: FakeSignal())

    mcp_server.call_tool("scan", {"asset": "BTC", "record": True})
    assert len(writes) == 1


def _fake_series():
    from datetime import datetime, timedelta, timezone

    from alpha_engine.cache.models import Candle, Interval, PriceSeries

    t0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
    return PriceSeries(
        asset="BTC",
        interval=Interval.DAY,
        candles=[
            Candle(ts=t0 + timedelta(days=i), open=100, high=101, low=99, close=100)
            for i in range(30)
        ],
    )


# ---------------------------------------------------------------------------
# Non-negotiable 4: no tool accepts a decision-bearing number
# ---------------------------------------------------------------------------


def test_no_tool_accepts_a_decision_bearing_input():
    """There must be no set_confidence, no override_weight, no way for a caller
    to inject a number that becomes part of a signal. The tools answer
    questions; they do not accept opinions."""
    forbidden = {"confidence", "weight", "direction", "score", "reliability", "bias"}
    for tool in mcp_server.TOOLS:
        params = set(tool["inputSchema"].get("properties", {}))
        assert not (params & forbidden), f"{tool['name']} exposes a decision input"


def test_no_tool_name_suggests_mutation():
    for tool in mcp_server.TOOLS:
        assert not tool["name"].startswith(("set_", "override_", "update_", "write_"))


# ---------------------------------------------------------------------------
# The stdio loop
# ---------------------------------------------------------------------------


def test_serve_handles_a_full_session():
    stdin = io.StringIO(
        json.dumps(_rpc("initialize"))
        + "\n"
        + json.dumps(_rpc("notifications/initialized", msg_id=None))
        + "\n"
        + json.dumps(_rpc("tools/list", msg_id=2))
        + "\n"
    )
    stdout = io.StringIO()
    assert mcp_server.serve(stdin, stdout) == 0

    responses = [json.loads(line) for line in stdout.getvalue().strip().split("\n")]
    # two requests, one notification -> two responses
    assert len(responses) == 2
    assert responses[0]["id"] == 1
    assert responses[1]["id"] == 2


def test_serve_survives_unparseable_input():
    """A malformed line must not kill a long-running server."""
    stdin = io.StringIO("not json at all\n" + json.dumps(_rpc("ping")) + "\n")
    stdout = io.StringIO()
    mcp_server.serve(stdin, stdout)
    assert json.loads(stdout.getvalue().strip())["id"] == 1


def test_serve_skips_blank_lines():
    stdin = io.StringIO("\n\n" + json.dumps(_rpc("ping")) + "\n\n")
    stdout = io.StringIO()
    mcp_server.serve(stdin, stdout)
    assert len(stdout.getvalue().strip().split("\n")) == 1
