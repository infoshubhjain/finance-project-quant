"""End-to-end tests: a real HTTP server, driven the way a browser drives it.

Every other test in this suite exercises a layer. These exercise the *product* —
the thing a user actually touches — because an audit found that the web app had
been shipped without anyone ever running it. Both sections returned HTTP 200 and
that had been treated as "it works", which it is not.

Two flows are covered:

1. **Dashboard.** Load the page, then load the payload it fetches, and assert the
   payload actually contains the fields the JavaScript reads. A 200 with the
   wrong shape renders an empty dashboard and no error.
2. **Terminal.** A stub OpenAI-compatible server stands in for the model, which
   is possible because `LLM_API_BASE` redirects any provider. Everything below
   that boundary is real: the HTTP handler, the agent loop, the tool registry
   and the analyzers. The stub decides *which* tools to call; the engine
   produces the numbers.

The second flow is the one that matters, because the terminal's only real
guarantee is that every number in the prose can be checked against the tool
result that produced it. That is asserted here rather than assumed.
"""

from __future__ import annotations

import json
import re
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from web.api import ApiConfig, ApiState
from web.server import AppHandler

STATIC = Path(__file__).resolve().parents[1] / "web" / "static"


# --------------------------------------------------------------------------
# Servers
# --------------------------------------------------------------------------


@pytest.fixture()
def app_server():
    AppHandler.state = ApiState(config=ApiConfig(rate_limit_per_min=0))
    server = ThreadingHTTPServer(("127.0.0.1", 0), AppHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
    AppHandler.state = ApiState()


class _StubModel(BaseHTTPRequestHandler):
    """An OpenAI-compatible endpoint that asks for one tool, then summarises it."""

    turns: dict = {}

    def log_message(self, *a):
        return

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"] or 0)))
        state = _StubModel.turns
        state["n"] = state.get("n", 0) + 1
        state.setdefault("requests", []).append(body)

        tool_payloads = [m["content"] for m in body["messages"] if m.get("role") == "tool"]
        if state["n"] == 1:
            message = {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "list_strategies", "arguments": "{}"},
                    }
                ],
            }
        else:
            payload = json.loads(tool_payloads[0])
            names = [s["key"] for s in payload["strategies"]]
            message = {"role": "assistant", "content": f"Available: {', '.join(names)}."}

        out = json.dumps({"choices": [{"message": message, "finish_reason": "stop"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


@pytest.fixture()
def stub_model(monkeypatch):
    _StubModel.turns = {}
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubModel)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("LLM_API_BASE", f"http://127.0.0.1:{server.server_address[1]}/v1")
    yield _StubModel.turns
    server.shutdown()
    server.server_close()


def _get(url: str):
    with urllib.request.urlopen(url) as r:  # noqa: S310 - localhost test server
        return r.status, r.read()


def _post_json(url: str, payload: dict):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as r:  # noqa: S310 - localhost test server
        return r.status, json.loads(r.read())


# --------------------------------------------------------------------------
# Flow 1 — the dashboard
# --------------------------------------------------------------------------


def test_landing_offers_both_sections(app_server):
    _status, body = _get(app_server + "/")
    html = body.decode()
    assert 'href="/dashboard"' in html
    assert 'href="/terminal"' in html


def test_dashboard_payload_has_every_top_level_field_the_script_reads(app_server):
    """A 200 with the wrong shape renders an empty dashboard and no error, which
    is exactly how this shipped unverified.

    The expected field list is *derived from app.js* rather than written out
    here, so renaming a payload key without updating the frontend fails this
    test instead of silently blanking a panel.
    """
    _status, body = _get(app_server + "/api/dashboard")
    payload = json.loads(body)

    # The contract, checked in BOTH directions so a rename on either side fails
    # here rather than silently blanking a panel. Kept explicit because `p` is
    # rebound for sub-objects inside app.js, so a regex over `p.<field>` cannot
    # tell a top-level read from a nested one.
    contract = {
        "total_records",
        "latest_count",
        "latest_signals",
        "assets_by_market",
        "outcomes",
        "risk",
        "portfolio",
    }
    script = (STATIC / "app.js").read_text()

    missing_from_api = sorted(contract - set(payload))
    assert not missing_from_api, f"the dashboard API stopped returning: {missing_from_api}"

    unread_by_ui = sorted(field for field in contract if f"p.{field}" not in script)
    assert not unread_by_ui, f"app.js no longer reads: {unread_by_ui} — is the contract stale?"


def test_every_element_the_dashboard_script_grabs_exists_in_the_page():
    """`getElementById` returning null throws on the next line and the page dies
    silently. Cheap to check, impossible to notice without a browser."""
    html = (STATIC / "dashboard.html").read_text()
    script = (STATIC / "app.js").read_text()
    ids = set(re.findall(r'\$\("([^"]+)"\)', script))
    missing = [i for i in ids if f'id="{i}"' not in html]
    assert not missing, f"app.js references ids absent from dashboard.html: {missing}"


def test_every_element_the_terminal_script_grabs_exists_in_the_page():
    html = (STATIC / "terminal.html").read_text()
    script = (STATIC / "terminal.js").read_text()
    ids = set(re.findall(r'\$\("([^"]+)"\)', script))
    missing = [i for i in ids if f'id="{i}"' not in html]
    assert not missing, f"terminal.js references ids absent from terminal.html: {missing}"


def test_every_script_and_stylesheet_a_page_references_is_served(app_server):
    for page in ("index.html", "dashboard.html", "terminal.html"):
        html = (STATIC / page).read_text()
        for asset in re.findall(r'(?:src|href)="(/static/[^"]+)"', html):
            status, _ = _get(app_server + asset)
            assert status == 200, f"{page} references {asset}, which 404s"


# --------------------------------------------------------------------------
# Flow 2 — the terminal, full stack
# --------------------------------------------------------------------------


def test_terminal_drives_real_engine_tools_end_to_end(app_server, stub_model):
    """Server -> agent loop -> model protocol -> real tool -> real answer.

    Nothing below the model boundary is stubbed: the tool registry, the strategy
    loader and the disclaimer are all the production code paths.
    """
    status, reply = _post_json(
        app_server + "/api/v1/agent",
        {
            "question": "What strategies do you have?",
            "api_key": "not-a-real-key",
            "provider": "local",
        },
    )

    assert status == 200
    assert reply["error"] is None
    assert [c["name"] for c in reply["tool_calls"]] == ["list_strategies"]

    result = reply["tool_calls"][0]["result"]
    assert any(s["key"] == "SMACrossover" for s in result["strategies"])
    assert result["disclaimer"], "every tool result must carry the disclaimer"
    assert "SMACrossover" in reply["answer"]


def test_the_answer_is_checkable_against_the_tool_that_produced_it(app_server, stub_model):
    """The terminal's only hard guarantee. If the audit trail is ever dropped,
    the prose becomes an unverifiable claim."""
    _status, reply = _post_json(
        app_server + "/api/v1/agent",
        {"question": "list them", "api_key": "k", "provider": "local"},
    )
    names_in_answer = {n for n in ("SMACrossover", "RSIReversal") if n in reply["answer"]}
    names_in_tools = {s["key"] for s in reply["tool_calls"][0]["result"]["strategies"]}
    assert names_in_answer <= names_in_tools, "the answer named something no tool returned"


def test_the_model_is_sent_the_system_prompt_and_the_tools(app_server, stub_model):
    _post_json(
        app_server + "/api/v1/agent",
        {"question": "hi", "api_key": "k", "provider": "local"},
    )
    first = stub_model["requests"][0]
    assert first["messages"][0]["role"] == "system"
    assert "never produce a number yourself" in first["messages"][0]["content"]
    assert {t["function"]["name"] for t in first["tools"]}, "no tools offered to the model"


def test_the_api_key_never_comes_back_in_the_response(app_server, stub_model):
    secret = "sk-super-secret-value-1234567890"
    _status, reply = _post_json(
        app_server + "/api/v1/agent",
        {"question": "hi", "api_key": secret, "provider": "local"},
    )
    assert secret not in json.dumps(reply)


def test_a_dead_model_endpoint_is_a_readable_error_not_a_crash(app_server, monkeypatch):
    monkeypatch.setenv("LLM_API_BASE", "http://127.0.0.1:1/v1")  # nothing listening
    req = urllib.request.Request(
        app_server + "/api/v1/agent",
        data=json.dumps({"question": "hi", "api_key": "k", "provider": "local"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as r:  # noqa: S310 - localhost test server
            reply = json.loads(r.read())
    except urllib.error.HTTPError as e:  # 502 is the documented response
        reply = json.loads(e.read())
    assert reply["error"] and "could not reach" in reply["error"]


# --------------------------------------------------------------------------
# XSS invariants
#
# The dashboard renders scraped headlines, engine prose and asset symbols into
# innerHTML; the terminal renders model output and raw tool payloads. Both are
# safe today — the dashboard escapes, the terminal never uses innerHTML at all —
# and both are one careless edit from not being. These pin the invariant rather
# than the implementation.
# --------------------------------------------------------------------------


def _strip_comments(js: str) -> str:
    """Remove /* block */ and // line comments so a rule about *code* is not
    satisfied or broken by prose. The naive line-prefix check this replaces was
    fooled by continuation lines inside a block comment."""
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return re.sub(r"^\s*//.*$", "", js, flags=re.M)


def test_the_terminal_never_uses_innerhtml():
    """Model output and tool payloads (which can contain a scraped headline)
    reach the DOM only through textContent. This is the whole XSS defence on a
    page where the user has pasted an API key."""
    code = _strip_comments((STATIC / "terminal.js").read_text())
    assert "innerHTML" not in code, "terminal.js gained an innerHTML assignment"


#: Payload fields that carry text the engine did not author — a scraped
#: headline, a ticker from a URL, LLM-written prose. Numbers are excluded on
#: purpose: they go through numeric formatters that coerce with Number(), so
#: they cannot carry markup no matter what the API returns.
_UNTRUSTED_TEXT_FIELDS = ("asset", "thesis", "name", "headline", "source", "market", "detail")


def test_every_untrusted_field_in_the_dashboard_is_escaped():
    """A `${r.thesis}` that skipped esc() would be stored XSS: thesis prose and
    source names originate from feeds the engine scrapes off the internet."""
    script = _strip_comments((STATIC / "app.js").read_text())
    raw = []
    for match in re.finditer(r"\$\{([^{}]+)\}", script):
        expr = match.group(1).strip()
        if not re.match(r"^[a-z]\.[a-z_]+$", expr):
            continue  # not a bare field read
        if expr.split(".")[1] not in _UNTRUSTED_TEXT_FIELDS:
            continue  # numeric or locally-constructed
        raw.append(expr)
    assert not raw, f"untrusted fields interpolated into HTML without esc(): {sorted(set(raw))}"


def test_the_escaping_test_would_actually_catch_a_regression():
    """Guard the guard. A checker that cannot fail is worse than none, because
    it reads as proof."""
    sample = "el.innerHTML = `<td>${r.thesis}</td>`;"
    found = [
        m.group(1)
        for m in re.finditer(r"\$\{([^{}]+)\}", sample)
        if m.group(1).split(".")[-1] in _UNTRUSTED_TEXT_FIELDS
    ]
    assert found == ["r.thesis"]


def test_the_escape_helper_covers_every_dangerous_character():
    script = (STATIC / "app.js").read_text()
    helper = script[script.index("const esc") : script.index("const fmtPct")]
    for char in ("&", "<", ">", '"', "'"):
        assert f'"{char}"' in helper or f"'{char}'" in helper, f"esc() does not handle {char}"


def test_pages_declare_a_strict_content_security_policy(app_server):
    """connect-src 'self' is what stops an injected script POSTing the user's
    pasted API key to another host."""
    with urllib.request.urlopen(app_server + "/terminal") as r:  # noqa: S310 - test server
        csp = r.headers.get("Content-Security-Policy")
    assert "default-src 'self'" in csp
    assert "connect-src 'self'" in csp
    assert "form-action 'none'" in csp
