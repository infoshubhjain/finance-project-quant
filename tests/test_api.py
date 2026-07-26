"""Tests for the HTTP API surface and its policy layer.

Policy is tested directly (no socket) and the routing is tested through a real
server on an ephemeral port, same pattern as `test_web.py`.

The properties that matter here are all security properties. This is the first
surface in the repo a stranger can reach, so each of these is a boundary rather
than a feature:

- The write gate holds: no recording to the signal log unless the operator said so.
- A key, when configured, is actually required — and compared without leaking timing.
- Rate limiting bites.
- The API and the MCP server expose the same tools, because they share one table.
- No tool accepts strategy source code.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from alpha_engine import mcp as mcp_server
from alpha_engine import toolkit
from alpha_engine.web.api import (
    ApiConfig,
    ApiState,
    RateLimiter,
    authorize,
    catalogue,
    coerce_query_args,
    dispatch_mcp,
    dispatch_tool,
)
from alpha_engine.web.server import AppHandler


# --------------------------------------------------------------------------
# The shared tool table
# --------------------------------------------------------------------------


def test_every_tool_has_a_handler_and_vice_versa():
    assert sorted(toolkit.tool_names()) == sorted(toolkit.HANDLERS)


def test_mcp_server_and_api_share_one_table():
    """The whole reason toolkit.py exists. If these ever diverge, a tool fixed
    on one surface is still broken on the other."""
    assert mcp_server.TOOLS is toolkit.TOOLS
    assert mcp_server.HANDLERS is toolkit.HANDLERS


def test_no_tool_accepts_code():
    """Accepting strategy source over HTTP would be remote code execution."""
    for tool in toolkit.TOOLS:
        properties = tool["inputSchema"].get("properties", {})
        for name in properties:
            assert name not in ("code", "source", "script", "strategy_code", "eval")


def test_every_tool_result_carries_the_disclaimer():
    payload = toolkit.call_tool("record_stats", {})
    assert payload["disclaimer"] == toolkit.DISCLAIMER


def test_unknown_tool_lists_what_is_available():
    payload = toolkit.call_tool("nope", {})
    assert "error" in payload
    assert "scan" in payload["available"]


def test_a_tool_that_raises_returns_data_not_an_exception(monkeypatch):
    def boom(_args):
        raise RuntimeError("kaboom")

    monkeypatch.setitem(toolkit.HANDLERS, "health", boom)
    payload = toolkit.call_tool("health", {})
    assert payload["error"] == "RuntimeError: kaboom"
    assert payload["disclaimer"] == toolkit.DISCLAIMER


def test_catalogue_is_self_describing():
    cat = catalogue()
    assert cat["tools"] is toolkit.TOOLS
    assert "mcp" in cat["usage"]
    assert cat["disclaimer"]


# --------------------------------------------------------------------------
# The write gate
# --------------------------------------------------------------------------


def test_recording_is_refused_on_a_read_only_server():
    state = ApiState(config=ApiConfig(allow_writes=False))
    status, payload = dispatch_tool(state, "scan", {"asset": "BTC", "record": True})
    assert status == 403
    assert "read-only" in payload["error"]


def test_reads_are_allowed_on_a_read_only_server(monkeypatch):
    monkeypatch.setitem(toolkit.HANDLERS, "scan", lambda a: {"direction": "bullish"})
    state = ApiState(config=ApiConfig(allow_writes=False))
    status, payload = dispatch_tool(state, "scan", {"asset": "BTC"})
    assert status == 200
    assert payload["direction"] == "bullish"


def test_recording_is_permitted_once_the_operator_opts_in(monkeypatch):
    monkeypatch.setitem(toolkit.HANDLERS, "scan", lambda a: {"recorded": a.get("record")})
    state = ApiState(config=ApiConfig(allow_writes=True))
    status, payload = dispatch_tool(state, "scan", {"asset": "BTC", "record": True})
    assert status == 200
    assert payload["recorded"] is True


def test_unknown_tool_is_404_not_500():
    state = ApiState()
    status, payload = dispatch_tool(state, "definitely_not_a_tool", {})
    assert status == 404
    assert "available" in payload


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------


def test_no_auth_required_when_no_key_configured():
    ok, _ = authorize(ApiState(config=ApiConfig(api_key=None)), None)
    assert ok


def test_missing_header_is_rejected_when_a_key_is_configured():
    state = ApiState(config=ApiConfig(api_key="secret"))
    ok, message = authorize(state, None)
    assert not ok
    assert "Authorization" in message


def test_wrong_key_is_rejected():
    state = ApiState(config=ApiConfig(api_key="secret"))
    ok, _ = authorize(state, "Bearer wrong")
    assert not ok


def test_correct_key_is_accepted_with_or_without_the_bearer_prefix():
    state = ApiState(config=ApiConfig(api_key="secret"))
    assert authorize(state, "Bearer secret")[0]
    assert authorize(state, "secret")[0]


# --------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------


def test_rate_limiter_blocks_past_the_limit():
    limiter = RateLimiter(per_minute=3)
    assert [limiter.allow("1.2.3.4", now=0.0) for _ in range(4)] == [True, True, True, False]


def test_rate_limiter_resets_on_the_next_window():
    limiter = RateLimiter(per_minute=1)
    assert limiter.allow("a", now=0.0)
    assert not limiter.allow("a", now=30.0)
    assert limiter.allow("a", now=60.0)


def test_rate_limiter_is_per_client():
    limiter = RateLimiter(per_minute=1)
    assert limiter.allow("a", now=0.0)
    assert limiter.allow("b", now=0.0)


def test_rate_limit_of_zero_disables_it():
    limiter = RateLimiter(per_minute=0)
    assert all(limiter.allow("a", now=0.0) for _ in range(100))


# --------------------------------------------------------------------------
# Query-string coercion
# --------------------------------------------------------------------------


def test_query_args_are_coerced_to_their_real_types():
    args = coerce_query_args(
        {"asset": ["BTC"], "days": ["90"], "record": ["true"], "rate": ["0.06"]}
    )
    assert args == {"asset": "BTC", "days": 90, "record": True, "rate": 0.06}


def test_repeated_query_param_takes_the_last_value():
    assert coerce_query_args({"asset": ["BTC", "ETH"]}) == {"asset": "ETH"}


# --------------------------------------------------------------------------
# MCP over HTTP
# --------------------------------------------------------------------------


def test_mcp_over_http_lists_the_same_tools():
    response = dispatch_mcp(ApiState(), {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert response["result"]["tools"] is toolkit.TOOLS


def test_mcp_over_http_enforces_the_write_gate():
    """The gate must not be bypassable by switching transport."""
    state = ApiState(config=ApiConfig(allow_writes=False))
    response = dispatch_mcp(
        state,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "scan", "arguments": {"asset": "BTC", "record": True}},
        },
    )
    assert response["result"]["isError"] is True
    assert "read-only" in response["result"]["content"][0]["text"]


def test_mcp_notification_gets_no_response():
    assert (
        dispatch_mcp(ApiState(), {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    )


# --------------------------------------------------------------------------
# End to end over a real socket
# --------------------------------------------------------------------------


@pytest.fixture()
def api_server():
    """A server with auth off and writes off — the default posture."""
    AppHandler.state = ApiState(config=ApiConfig(rate_limit_per_min=0))
    server = ThreadingHTTPServer(("127.0.0.1", 0), AppHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
    AppHandler.state = ApiState()


def _get(url: str):
    with urllib.request.urlopen(url) as resp:  # noqa: S310 - localhost test server
        return resp.status, json.loads(resp.read())


def _post(url: str, payload: dict):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request) as resp:  # noqa: S310 - localhost test server
        return resp.status, json.loads(resp.read())


def test_tools_catalogue_over_http(api_server):
    status, payload = _get(api_server + "/api/v1/tools")
    assert status == 200
    assert {t["name"] for t in payload["tools"]} == set(toolkit.tool_names())


def test_tool_call_over_http_get(api_server):
    status, payload = _get(api_server + "/api/v1/tools/list_strategies")
    assert status == 200
    assert any(s["key"] == "SMACrossover" for s in payload["strategies"])
    assert payload["disclaimer"]


def test_tool_call_over_http_post(api_server):
    status, payload = _post(api_server + "/api/v1/tools/list_strategies", {})
    assert status == 200
    assert "strategies" in payload


def test_mcp_endpoint_over_http(api_server):
    status, payload = _post(
        api_server + "/api/v1/mcp", {"jsonrpc": "2.0", "id": 7, "method": "tools/list"}
    )
    assert status == 200
    assert payload["id"] == 7
    assert len(payload["result"]["tools"]) == len(toolkit.TOOLS)


def test_providers_endpoint(api_server):
    status, payload = _get(api_server + "/api/v1/providers")
    assert status == 200
    assert {p["key"] for p in payload["providers"]} >= {"openai", "anthropic"}


def test_agent_without_a_key_is_a_clean_400(api_server):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _post(api_server + "/api/v1/agent", {"question": "hi"})
    assert excinfo.value.code == 400
    assert "api_key" in json.loads(excinfo.value.read())["error"]


def test_agent_without_a_question_is_a_clean_400(api_server):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _post(api_server + "/api/v1/agent", {"api_key": "sk-test"})
    assert excinfo.value.code == 400


def test_oversized_body_is_refused(api_server):
    request = urllib.request.Request(
        api_server + "/api/v1/tools/list_strategies",
        data=b'{"x":"' + b"a" * 70_000 + b'"}',
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(request)  # noqa: S310 - localhost test server
    assert excinfo.value.code == 400
    assert "too large" in json.loads(excinfo.value.read())["error"]


def test_bad_tool_name_is_refused_before_dispatch(api_server):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _get(api_server + "/api/v1/tools/..%2F..%2Fetc")
    assert excinfo.value.code == 400


def test_both_sections_and_the_landing_page_are_served(api_server):
    for path, marker in (
        ("/", b"Terminal"),
        ("/dashboard", b"Total Signals"),
        ("/terminal", b"Bring your own key"),
    ):
        with urllib.request.urlopen(api_server + path) as resp:  # noqa: S310 - test server
            assert resp.status == 200
            assert marker in resp.read()


def test_security_headers_are_present(api_server):
    with urllib.request.urlopen(api_server + "/terminal") as resp:  # noqa: S310 - test server
        csp = resp.headers.get("Content-Security-Policy")
    # connect-src 'self' is what stops an injected script exfiltrating a pasted key.
    assert "connect-src 'self'" in csp
    assert "default-src 'self'" in csp


def test_json_responses_are_not_cached(api_server):
    with urllib.request.urlopen(api_server + "/api/v1/tools") as resp:  # noqa: S310 - test server
        assert resp.headers.get("Cache-Control") == "no-store"


def test_auth_is_enforced_when_a_key_is_configured():
    AppHandler.state = ApiState(config=ApiConfig(api_key="topsecret", rate_limit_per_min=0))
    server = ThreadingHTTPServer(("127.0.0.1", 0), AppHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            _get(base + "/api/v1/tools/list_strategies")
        assert excinfo.value.code == 401

        request = urllib.request.Request(
            base + "/api/v1/tools/list_strategies",
            headers={"Authorization": "Bearer topsecret"},
        )
        with urllib.request.urlopen(request) as resp:  # noqa: S310 - localhost test server
            assert resp.status == 200
    finally:
        server.shutdown()
        server.server_close()
        AppHandler.state = ApiState()


# --------------------------------------------------------------------------
# Numeric bounds
#
# These close a hole the red-team pass found: the API answered `step=-5` with
# HTTP 200 and a backtest reporting zero signals, because `range(warmup, n, -5)`
# is empty rather than an error. Nothing raised, nothing looked broken, and the
# caller got a confidently wrong number — the one outcome this project treats as
# worse than a failure.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("arg", "value"),
    [
        ("step", -5),
        ("step", 0),
        ("days", 0),
        ("days", 999_999_999),
        ("horizon", 0),
        ("top", 0),
        ("capital", -1000),
        ("txn_cost_bps", -1),
        ("dte_bars", 0),
    ],
)
def test_out_of_range_arguments_are_refused(arg, value):
    payload = toolkit.call_tool("backtest", {"asset": "BTC", arg: value})
    assert "error" in payload, f"{arg}={value} should have been refused"
    assert arg in payload["error"]


def test_in_range_arguments_are_accepted(monkeypatch):
    monkeypatch.setitem(toolkit.HANDLERS, "backtest", lambda a: {"ok": a.get("step")})
    payload = toolkit.call_tool("backtest", {"asset": "BTC", "step": 5, "days": 365})
    assert payload["ok"] == 5


def test_a_non_numeric_value_for_a_numeric_argument_is_refused():
    payload = toolkit.call_tool("backtest", {"asset": "BTC", "days": "all of them"})
    assert "must be a number" in payload["error"]


def test_booleans_are_not_accepted_as_numbers():
    """bool is a subclass of int in Python, so `days=True` would sail through a
    naive isinstance check and become days=1."""
    payload = toolkit.call_tool("backtest", {"asset": "BTC", "days": True})
    assert "must be a number" in payload["error"]


def test_bounds_apply_on_every_transport():
    """The validation lives in call_tool precisely so no transport can skip it."""
    state = ApiState()
    status, payload = dispatch_tool(state, "backtest", {"asset": "BTC", "step": -5})
    assert status == 400 and "error" in payload

    response = dispatch_mcp(
        state,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "backtest", "arguments": {"asset": "BTC", "step": -5}},
        },
    )
    assert response["result"]["isError"] is True


def test_unbounded_arguments_are_left_alone():
    """`asset` and `strategy` are strings; the bounds table must not touch them."""
    assert toolkit.validate_args("scan", {"asset": "BTC", "market": "crypto"}) is None
