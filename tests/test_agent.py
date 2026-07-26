"""Tests for the BYO-key AI terminal.

No network and no API key: `net.post` is monkeypatched with a scripted provider,
so the agent loop, the two provider dialects, and the key-handling rules are all
exercised offline — same as every other test in this suite.

The properties worth pinning:

1. The agent actually calls engine tools and relays their results.
2. Both provider dialects (OpenAI and Anthropic) parse into the same shape.
3. Every tool call ends up in the audit trail — that is the only real guarantee
   the terminal offers, so it must not be droppable.
4. The API key never appears in a reply, a transcript, or an error message.
5. Provider failures surface as readable errors rather than silence.
"""

from __future__ import annotations

import json

import pytest

from alpha_engine import net, toolkit
from alpha_engine.narrative import agent, providers
from alpha_engine.narrative.providers import (
    Provider,
    ProviderError,
    ToolCall,
    redact,
    resolve,
    tool_result_message,
    tools_for,
)

KEY = "sk-test-abcdefghijklmnopqrstuvwxyz0123456789"


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def _openai_turn(text="", tool_calls=None):
    message = {"role": "assistant", "content": text}
    if tool_calls:
        message["tool_calls"] = [
            {
                "id": f"call_{i}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
            for i, (name, args) in enumerate(tool_calls)
        ]
    return {"choices": [{"message": message, "finish_reason": "stop"}]}


def _anthropic_turn(text="", tool_calls=None):
    content = []
    if text:
        content.append({"type": "text", "text": text})
    for i, (name, args) in enumerate(tool_calls or []):
        content.append({"type": "tool_use", "id": f"toolu_{i}", "name": name, "input": args})
    return {"content": content, "stop_reason": "end_turn"}


@pytest.fixture()
def scripted(monkeypatch):
    """Queue up provider responses; returns the list of requests made."""
    calls: list[dict] = []
    queue: list = []

    def fake_post(url, *, json=None, headers=None, timeout=20, **kwargs):
        calls.append({"url": url, "body": json, "headers": headers or {}})
        if not queue:
            raise AssertionError("provider called more times than the test scripted")
        return queue.pop(0)

    monkeypatch.setattr(net, "post", fake_post)
    return type("Scripted", (), {"calls": calls, "queue": queue})()


# --------------------------------------------------------------------------
# 1. The loop calls tools and relays them
# --------------------------------------------------------------------------


def test_agent_calls_a_tool_then_answers(scripted):
    scripted.queue.append(FakeResponse(_openai_turn(tool_calls=[("health", {})])))
    scripted.queue.append(FakeResponse(_openai_turn(text="All sources are healthy.")))

    reply = agent.ask("is anything broken?", api_key=KEY, provider_key="openai")

    assert reply.error is None
    assert reply.answer == "All sources are healthy."
    assert [c.name for c in reply.tool_calls] == ["health"]
    # The real `health` tool ran — the result is engine output, not a fixture.
    assert "degraded" in reply.tool_calls[0].result
    assert reply.steps == 2


def test_agent_answers_directly_when_no_tool_is_needed(scripted):
    scripted.queue.append(FakeResponse(_openai_turn(text="Ask me about an asset.")))
    reply = agent.ask("hello", api_key=KEY)
    assert reply.tool_calls == []
    assert reply.steps == 1


def test_every_tool_call_lands_in_the_audit_trail(scripted):
    """The audit trail is the terminal's only hard guarantee — a reader checks
    the prose against it. It must never be silently dropped."""
    scripted.queue.append(
        FakeResponse(
            _openai_turn(
                tool_calls=[("list_strategies", {}), ("health", {})],
            )
        )
    )
    scripted.queue.append(FakeResponse(_openai_turn(text="done")))

    reply = agent.ask("what can you do?", api_key=KEY)
    assert [c.name for c in reply.tool_calls] == ["list_strategies", "health"]
    assert all(isinstance(c.result, dict) for c in reply.tool_calls)


def test_a_failing_tool_is_reported_to_the_model_not_raised(scripted):
    scripted.queue.append(
        FakeResponse(_openai_turn(tool_calls=[("scan", {"asset": "NOTREAL_XYZ"})]))
    )
    scripted.queue.append(FakeResponse(_openai_turn(text="No cached data for that.")))

    reply = agent.ask("scan NOTREAL_XYZ", api_key=KEY)
    assert reply.error is None
    assert "error" in reply.tool_calls[0].result


def test_malformed_tool_arguments_come_back_as_a_readable_tool_error(scripted, monkeypatch):
    bad = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "function": {"name": "scan", "arguments": "{not json"},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    }
    scripted.queue.append(FakeResponse(bad))
    scripted.queue.append(FakeResponse(_openai_turn(text="retrying")))

    reply = agent.ask("scan BTC", api_key=KEY)
    assert "not valid JSON" in reply.tool_calls[0].result["error"]


def test_loop_stops_at_max_steps_and_keeps_what_it_gathered(scripted):
    for _ in range(3):
        scripted.queue.append(FakeResponse(_openai_turn(tool_calls=[("health", {})])))

    reply = agent.ask("loop forever", api_key=KEY, max_steps=3)
    assert reply.truncated is True
    assert reply.steps == 3
    assert len(reply.tool_calls) == 3


# --------------------------------------------------------------------------
# 2. Both provider dialects
# --------------------------------------------------------------------------


def test_anthropic_dialect_parses_into_the_same_shape(scripted):
    scripted.queue.append(FakeResponse(_anthropic_turn(tool_calls=[("health", {})])))
    scripted.queue.append(FakeResponse(_anthropic_turn(text="Healthy.")))

    reply = agent.ask("status?", api_key=KEY, provider_key="anthropic")
    assert reply.answer == "Healthy."
    assert [c.name for c in reply.tool_calls] == ["health"]


def test_anthropic_uses_its_own_auth_header_and_system_field(scripted):
    scripted.queue.append(FakeResponse(_anthropic_turn(text="hi")))
    agent.ask("hi", api_key=KEY, provider_key="anthropic")

    request = scripted.calls[0]
    assert request["headers"]["x-api-key"] == KEY
    assert "Authorization" not in request["headers"]
    # Anthropic takes the system prompt as a top-level field, not a message.
    assert "system" in request["body"]
    assert all(m["role"] != "system" for m in request["body"]["messages"])


def test_openai_uses_a_bearer_header_and_a_system_message(scripted):
    scripted.queue.append(FakeResponse(_openai_turn(text="hi")))
    agent.ask("hi", api_key=KEY, provider_key="openai")

    request = scripted.calls[0]
    assert request["headers"]["Authorization"] == f"Bearer {KEY}"
    assert request["body"]["messages"][0]["role"] == "system"


def test_tool_schemas_are_translated_per_provider():
    tools = [{"name": "scan", "description": "d", "inputSchema": {"type": "object"}}]

    openai = tools_for(resolve("openai"), tools)
    assert openai[0]["type"] == "function"
    assert openai[0]["function"]["parameters"] == {"type": "object"}

    anthropic = tools_for(resolve("anthropic"), tools)
    assert anthropic[0]["input_schema"] == {"type": "object"}
    assert "function" not in anthropic[0]


def test_tool_results_are_shaped_per_provider():
    call = ToolCall(id="c1", name="scan", arguments={})
    openai = tool_result_message(resolve("openai"), call, {"ok": True})
    assert openai["role"] == "tool" and openai["tool_call_id"] == "c1"

    anthropic = tool_result_message(resolve("anthropic"), call, {"ok": True})
    assert anthropic["role"] == "user"
    assert anthropic["content"][0]["type"] == "tool_result"
    assert anthropic["content"][0]["tool_use_id"] == "c1"


def test_unknown_provider_is_a_clean_error():
    reply = agent.ask("hi", api_key=KEY, provider_key="notaprovider")
    assert "unknown provider" in reply.error
    assert reply.tool_calls == []


# --------------------------------------------------------------------------
# 3. The system prompt encodes the cardinal rule
# --------------------------------------------------------------------------


def test_system_prompt_forbids_the_model_producing_numbers(scripted):
    scripted.queue.append(FakeResponse(_openai_turn(text="ok")))
    agent.ask("hi", api_key=KEY)
    system = scripted.calls[0]["body"]["messages"][0]["content"]
    assert "never produce a number yourself" in system
    assert "noise floor" in system
    assert "RESEARCH ONLY" in system


def test_the_model_is_never_shown_a_write_capable_argument(scripted):
    """The agent may read the engine; it may not append to the signal log.

    Enforced twice: the argument is absent from the schema the model sees, and
    stripped again at call time. A model that cannot see `record` cannot decide
    that recording its findings would be helpful.
    """
    scripted.queue.append(FakeResponse(_openai_turn(text="ok")))
    agent.ask("hi", api_key=KEY)

    sent = {
        t["function"]["name"]: t["function"]["parameters"]
        for t in scripted.calls[0]["body"]["tools"]
    }
    assert set(sent) == set(toolkit.tool_names())  # every tool is still offered
    for tool_name, blocked_args in toolkit.WRITE_ARGS.items():
        offered = set(sent[tool_name].get("properties", {}))
        assert not (offered & blocked_args), f"{tool_name} exposed {offered & blocked_args}"


def test_a_write_argument_is_stripped_even_if_the_model_sends_it_anyway(monkeypatch):
    seen: dict = {}
    monkeypatch.setitem(toolkit.HANDLERS, "scan", lambda a: seen.update(a) or {"ok": True})

    toolkit.call_tool("scan", {"asset": "BTC", "record": True}, read_only=True)
    assert "record" not in seen

    seen.clear()
    toolkit.call_tool("scan", {"asset": "BTC", "record": True})
    assert seen["record"] is True  # not read-only: the argument survives


def test_read_only_tools_leaves_non_write_tools_untouched():
    read_only = {t["name"]: t for t in toolkit.read_only_tools()}
    for tool in toolkit.TOOLS:
        if tool["name"] not in toolkit.WRITE_ARGS:
            assert read_only[tool["name"]] is tool


def test_stripping_does_not_mutate_the_shared_tool_table():
    """read_only_tools() must copy — TOOLS is shared with the MCP server, where
    `record` is a legitimate opt-in."""
    toolkit.read_only_tools()
    scan = next(t for t in toolkit.TOOLS if t["name"] == "scan")
    assert "record" in scan["inputSchema"]["properties"]


# --------------------------------------------------------------------------
# 4. Key handling
# --------------------------------------------------------------------------


def test_the_key_never_appears_in_the_reply(scripted):
    scripted.queue.append(FakeResponse(_openai_turn(tool_calls=[("health", {})])))
    scripted.queue.append(FakeResponse(_openai_turn(text="fine")))

    reply = agent.ask("status", api_key=KEY)
    assert KEY not in agent.transcript_json(reply)
    assert KEY not in json.dumps(reply.to_dict())


def test_a_provider_error_echoing_the_key_is_redacted(scripted):
    scripted.queue.append(
        FakeResponse({"error": {"message": f"Incorrect API key provided: {KEY}"}}, status_code=401)
    )
    reply = agent.ask("hi", api_key=KEY)
    assert reply.error is not None
    assert KEY not in reply.error
    assert "[redacted]" in reply.error


def test_redact_leaves_ordinary_prose_alone():
    assert redact("the sharpe ratio was 1.4") == "the sharpe ratio was 1.4"


def test_missing_key_is_refused_before_any_request():
    with pytest.raises(ProviderError, match="no API key"):
        providers.chat(resolve("openai"), "", "gpt-4o-mini", [{"role": "user", "content": "hi"}])


# --------------------------------------------------------------------------
# 5. Provider failures are loud, not silent
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "hint"),
    [
        (401, "check the API key"),
        (404, "check the model name"),
        (429, "rate limited"),
    ],
)
def test_provider_errors_carry_an_actionable_hint(scripted, status, hint):
    scripted.queue.append(FakeResponse({"error": {"message": "nope"}}, status_code=status))
    reply = agent.ask("hi", api_key=KEY)
    assert hint in reply.error


def test_network_failure_is_a_readable_error(monkeypatch):
    def explode(*args, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(net, "post", explode)
    reply = agent.ask("hi", api_key=KEY)
    assert "could not reach" in reply.error


def test_provider_failure_keeps_tools_already_gathered(scripted):
    scripted.queue.append(FakeResponse(_openai_turn(tool_calls=[("health", {})])))
    scripted.queue.append(FakeResponse({"error": {"message": "boom"}}, status_code=500))

    reply = agent.ask("status", api_key=KEY)
    assert reply.error is not None
    assert len(reply.tool_calls) == 1  # the verified engine output is not thrown away


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def test_terminal_summary_shows_the_tool_trail_above_the_answer(scripted):
    scripted.queue.append(FakeResponse(_openai_turn(tool_calls=[("scan", {"asset": "BTC"})])))
    scripted.queue.append(FakeResponse(_openai_turn(text="Here is the read.")))

    text = agent.summarize_for_terminal(agent.ask("scan BTC", api_key=KEY))
    assert "scan(asset='BTC')" in text
    assert text.strip().endswith("Here is the read.")


def test_terminal_summary_of_an_error_is_just_the_error():
    reply = agent.AgentReply(answer="", provider="openai", model="m", steps=0, error="bad key")
    assert agent.summarize_for_terminal(reply) == "error: bad key"


def test_provider_catalogue_lists_where_to_get_a_key():
    for entry in providers.list_providers():
        assert entry["keys_url"].startswith("https://")
        assert entry["default_model"]


def test_provider_dataclass_is_frozen():
    """Provider config is shared module state; a mutable one is a foot-gun."""
    with pytest.raises(Exception):
        resolve("openai").base_url = "http://evil.test"


def test_all_providers_declare_a_known_dialect():
    for provider in providers.PROVIDERS.values():
        assert isinstance(provider, Provider)
        assert provider.style in ("openai", "anthropic")


# --------------------------------------------------------------------------
# HTTP status classification
#
# Everything used to return 502. A mistyped provider or model name is the
# caller's mistake, and answering "Bad Gateway" sends them looking for an
# outage that is not happening. Found by testing against a live OpenRouter key.
# --------------------------------------------------------------------------


def test_an_unknown_provider_is_a_client_error():
    reply = agent.ask("hi", api_key=KEY, provider_key="notaprovider")
    assert reply.error_status == 400


def test_a_missing_key_is_a_client_error():
    with pytest.raises(ProviderError) as e:
        providers.chat(resolve("openai"), "", "m", [{"role": "user", "content": "hi"}])
    assert e.value.http_status == 400


@pytest.mark.parametrize(
    ("upstream", "expected"),
    [
        (400, 400),  # bad model name — the caller's to fix
        (401, 401),  # bad key — the caller's
        (403, 403),
        (404, 404),
        (429, 429),  # surfaced so a client can back off
        (500, 502),  # a genuine upstream failure
        (503, 502),
    ],
)
def test_upstream_status_is_classified_not_flattened(scripted, upstream, expected):
    scripted.queue.append(FakeResponse({"error": {"message": "x"}}, status_code=upstream))
    assert agent.ask("hi", api_key=KEY).error_status == expected


def test_an_unreachable_endpoint_is_a_gateway_error(monkeypatch):
    def explode(*a, **kw):
        raise OSError("connection refused")

    monkeypatch.setattr(net, "post", explode)
    assert agent.ask("hi", api_key=KEY).error_status == 502


def test_a_successful_reply_carries_no_error_status_meaning(scripted):
    scripted.queue.append(FakeResponse(_openai_turn(text="ok")))
    reply = agent.ask("hi", api_key=KEY)
    assert reply.error is None
