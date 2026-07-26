"""Bring-your-own-key LLM providers, normalized to one tool-calling interface.

The terminal lets whoever runs it paste their own API key. That means this
module has to speak more than one vendor's dialect, because the two that matter
disagree on nearly everything: OpenAI puts tool calls in
`choices[0].message.tool_calls` with JSON-encoded argument *strings*; Anthropic
puts them in `content[]` blocks of `type: "tool_use"` with argument *objects*.
Tool results go back as `role: "tool"` messages in one and as `tool_result`
content blocks in the other.

`chat()` hides all of that and returns an `AssistantTurn`. Everything above this
module — the agent loop, the terminal — sees one shape.

No SDK dependency, by the same argument as `net.py` and `mcp_server.py`: this is
two POST endpoints and some JSON reshaping. Adding `openai` + `anthropic` would
add two dependency trees to a repo that has exactly one runtime dependency.

Key handling
------------
The key arrives as a function argument, is used for one request, and is never
stored, cached, written to disk, or logged. `redact()` exists so error paths
cannot accidentally echo one back to a caller. There is no server-side key
store, and adding one would change the security model of the whole terminal —
the point of BYO key is that the operator never holds anyone else's credential.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from alpha_engine import net


@dataclass(frozen=True)
class Provider:
    """One vendor endpoint and the dialect it speaks."""

    key: str
    label: str
    base_url: str
    #: "openai" or "anthropic" — which request/response shape to use.
    style: str
    default_model: str
    #: Where a user gets a key, shown in the terminal's help.
    keys_url: str = ""


PROVIDERS: dict[str, Provider] = {
    "openai": Provider(
        key="openai",
        label="OpenAI",
        base_url="https://api.openai.com/v1",
        style="openai",
        default_model="gpt-4o-mini",
        keys_url="https://platform.openai.com/api-keys",
    ),
    "anthropic": Provider(
        key="anthropic",
        label="Anthropic (Claude)",
        base_url="https://api.anthropic.com/v1",
        style="anthropic",
        default_model="claude-sonnet-4-5",
        keys_url="https://console.anthropic.com/settings/keys",
    ),
    "openrouter": Provider(
        key="openrouter",
        label="OpenRouter (many models, one key)",
        base_url="https://openrouter.ai/api/v1",
        style="openai",
        default_model="anthropic/claude-sonnet-4.5",
        keys_url="https://openrouter.ai/keys",
    ),
    "groq": Provider(
        key="groq",
        label="Groq",
        base_url="https://api.groq.com/openai/v1",
        style="openai",
        default_model="llama-3.3-70b-versatile",
        keys_url="https://console.groq.com/keys",
    ),
}

ANTHROPIC_VERSION = "2023-06-01"


class ProviderError(RuntimeError):
    """A provider call failed in a way the user needs to see (bad key, no
    credit, unknown model). Distinct from a bug in this code."""


@dataclass
class ToolCall:
    """One tool the model asked to run."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class AssistantTurn:
    """What the model said, in provider-neutral form."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    #: The raw provider message, needed verbatim in the next request's history.
    raw: Any = None
    finish_reason: str = ""


def redact(text: str) -> str:
    """Strip anything that looks like an API key out of a string before it is
    shown or logged. Cheap insurance on error paths — a provider that echoes the
    key back in its error message must not get it into a transcript."""
    out = []
    for token in text.split():
        if len(token) > 20 and (
            token.startswith(("sk-", "sk_", "gsk_", "xai-")) or token.count("-") > 3
        ):
            out.append("[redacted]")
        else:
            out.append(token)
    return " ".join(out)


def resolve(provider_key: str) -> Provider:
    provider = PROVIDERS.get((provider_key or "").lower())
    if provider is None:
        raise ProviderError(
            f"unknown provider '{provider_key}'. Available: {', '.join(sorted(PROVIDERS))}"
        )
    return provider


def list_providers() -> list[dict[str, str]]:
    """Serializable catalogue for the terminal's `/providers` command."""
    return [
        {
            "key": p.key,
            "label": p.label,
            "default_model": p.default_model,
            "keys_url": p.keys_url,
        }
        for p in PROVIDERS.values()
    ]


# ---------------------------------------------------------------------------
# Tool schema translation
# ---------------------------------------------------------------------------


def tools_for(provider: Provider, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate the engine's MCP-shaped tool table into the provider's format.

    MCP and Anthropic both use `input_schema`-style JSON Schema, so Anthropic is
    nearly a pass-through. OpenAI wraps each tool in a `function` envelope.
    """
    if provider.style == "anthropic":
        return [
            {
                "name": t["name"],
                "description": t["description"],
                "input_schema": t["inputSchema"],
            }
            for t in tools
        ]
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["inputSchema"],
            },
        }
        for t in tools
    ]


def tool_result_message(provider: Provider, call: ToolCall, payload: dict[str, Any]) -> Any:
    """Build the message that hands a tool's output back to the model."""
    text = json.dumps(payload, indent=2, default=str)
    if provider.style == "anthropic":
        return {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": call.id, "content": text}],
        }
    return {"role": "tool", "tool_call_id": call.id, "content": text}


# ---------------------------------------------------------------------------
# The call
# ---------------------------------------------------------------------------


def chat(
    provider: Provider,
    api_key: str,
    model: str,
    messages: list[Any],
    tools: list[dict[str, Any]] | None = None,
    system: str = "",
    max_tokens: int = 2000,
    timeout: float = 60.0,
) -> AssistantTurn:
    """One round trip to the model. Raises ProviderError on any failure.

    Unlike `llm.rewrite_thesis`, failures here are NOT silent. That function is
    an optional prose upgrade where falling back to a template is correct; this
    one is the user's terminal, where swallowing "your key is invalid" would
    leave them staring at nothing.
    """
    if not api_key:
        raise ProviderError("no API key supplied")

    if provider.style == "anthropic":
        headers = {
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            body["system"] = system
        if tools:
            body["tools"] = tools_for(provider, tools)
        url = f"{provider.base_url.rstrip('/')}/messages"
    else:
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        full: list[Any] = ([{"role": "system", "content": system}] if system else []) + messages
        body = {"model": model, "messages": full, "max_tokens": max_tokens}
        if tools:
            body["tools"] = tools_for(provider, tools)
        url = f"{provider.base_url.rstrip('/')}/chat/completions"

    try:
        resp = net.post(url, json=body, headers=headers, timeout=timeout)
    except Exception as e:  # noqa: BLE001 - network failure must surface as a clean message
        raise ProviderError(f"could not reach {provider.label}: {redact(str(e))}") from e

    if resp.status_code >= 400:
        raise ProviderError(_error_message(provider, resp))

    try:
        data = resp.json()
    except ValueError as e:
        raise ProviderError(f"{provider.label} returned a non-JSON response") from e

    return _parse(provider, data)


def _error_message(provider: Provider, resp: Any) -> str:
    """Turn a provider's error body into one readable line."""
    detail = ""
    try:
        payload = resp.json()
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                detail = error.get("message", "")
            elif isinstance(error, str):
                detail = error
    except Exception:  # noqa: BLE001 - the body may be HTML or empty
        detail = resp.text[:200] if hasattr(resp, "text") else ""

    hint = ""
    if resp.status_code in (401, 403):
        hint = " — check the API key is valid and has credit"
    elif resp.status_code == 404:
        hint = " — check the model name exists for this provider"
    elif resp.status_code == 429:
        hint = " — rate limited by the provider; wait and retry"
    return f"{provider.label} error {resp.status_code}{hint}: {redact(detail)}".strip()


def _parse(provider: Provider, data: dict[str, Any]) -> AssistantTurn:
    if provider.style == "anthropic":
        blocks = data.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        calls = [
            ToolCall(id=b.get("id", ""), name=b.get("name", ""), arguments=b.get("input") or {})
            for b in blocks
            if b.get("type") == "tool_use"
        ]
        return AssistantTurn(
            text=text.strip(),
            tool_calls=calls,
            raw={"role": "assistant", "content": blocks},
            finish_reason=data.get("stop_reason", ""),
        )

    choices = data.get("choices") or []
    if not choices:
        raise ProviderError(f"{provider.label} returned no choices")
    message = choices[0].get("message") or {}
    calls = []
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        # OpenAI sends arguments as a JSON *string*; a model can emit malformed
        # JSON there, and that must be a tool error the loop can report rather
        # than an exception that kills the request.
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except json.JSONDecodeError:
            arguments = {"__malformed_arguments": function.get("arguments", "")}
        calls.append(
            ToolCall(id=call.get("id", ""), name=function.get("name", ""), arguments=arguments)
        )

    return AssistantTurn(
        text=(message.get("content") or "").strip(),
        tool_calls=calls,
        raw=message,
        finish_reason=choices[0].get("finish_reason", ""),
    )
