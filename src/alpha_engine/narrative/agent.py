"""The AI terminal: a chat loop where every number comes from a tool.

The user asks a question in plain language. The model may not answer it from
memory — it answers by calling the engine's deterministic tools and relaying
what they returned. `scan`, `factors`, `strategy_backtest` and the rest compute
in tested Python; the model's only job is choosing which to call and writing the
paragraph around the results.

How this stays inside the cardinal rule
---------------------------------------
The rule is that decision-bearing numbers come from tested pure Python and the
LLM only writes prose. This module honours it in three ways, and it is worth
being precise about which are *enforced* and which are *instructed*, because
overstating that would itself be a kind of lie:

- **Enforced.** The agent has no write path. It cannot record a signal, place an
  order, change a weight, or modify anything on disk. Its tools are the
  read-only ones in `toolkit.py`, and `web/api.py` gates the one write-capable
  argument separately. Nothing this model outputs re-enters the engine.
- **Enforced.** Every tool call and every raw tool result is returned in the
  transcript alongside the prose. A reader can check any number in the answer
  against the payload it came from, without trusting the model at all. This is
  the real guarantee: not that the model cannot be wrong, but that being wrong
  is *visible*.
- **Instructed.** The system prompt forbids computing, estimating or
  recalling numbers. A language model can still ignore an instruction, which is
  exactly why the audit trail above is not optional.

So: treat the prose as a readable index into the tool results, not as an
authority. That framing is in the system prompt too, and it is why this is a
research terminal and not an advisor.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from alpha_engine.narrative.providers import (
    Provider,
    ProviderError,
    ToolCall,
    chat,
    resolve,
    tool_result_message,
)
from alpha_engine.toolkit import DISCLAIMER, call_tool, read_only_tools

#: What the model is allowed to see. `read_only_tools()` strips every
#: write-capable argument from the schemas, so `record` is not merely refused —
#: it is absent from the model's view of the API.
AGENT_TOOLS = read_only_tools()

#: How many model round trips one question may take. Each step is one provider
#: call plus any tools it asked for. Six is enough for "scan these three assets
#: and compare them"; past that a model is usually looping, and the user is
#: paying for it with their own key.
DEFAULT_MAX_STEPS = 6

SYSTEM_PROMPT = f"""\
You are the research terminal for Alpha Engine, a deterministic market-research
system. You help the user investigate assets, factors, strategies and backtests.

THE ONE RULE THAT MATTERS: you never produce a number yourself.

Every figure you state — a price, a confidence, a Sharpe ratio, a hit rate, a
return, an information coefficient — must come from a tool call you made in this
conversation. You may quote tool output, round it, and explain it. You may not
compute it, estimate it, average it, extrapolate it, or recall it from training
data. If you need a number you do not have, call a tool. If no tool provides it,
say plainly that the engine does not measure it.

How to work:
- Call `list_strategies` before `strategy_backtest`, so you use a real name.
- If a tool returns "no cached data", tell the user to run `alpha-engine scan
  <ASSET>` once to populate the cache. Do not substitute your own knowledge.
- When you report a top-ranked factor, always report the noise floor next to it.
  A factor below the floor is indistinguishable from randomness, and presenting
  one without that context is how backtests lie.
- If a `strategy_backtest` returns lookahead_violations, lead with that. Every
  other number in that result is void, and the user needs to know before they
  read the Sharpe ratio.
- Prefer showing the user the actual numbers over summarising the vibe of them.

On what to invest in:
The user may ask you what to buy. Answer in research terms: what the engine
measured, how confident it is, what would invalidate it, and what the track
record says about signals like this one. That is genuinely useful. What you must
not do is dress it up as advice, imply an edge the backtests do not show, or
soften the fact that these analyzers score close to a coin flip on most assets.
The honest answer to "will this go up" is what the measurements say and how
weak they are. Never manufacture confidence the tools did not report.

{DISCLAIMER}
"""


@dataclass
class ToolInvocation:
    """One tool call and what it returned — the audit trail entry."""

    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]


@dataclass
class AgentReply:
    """The answer plus everything needed to check it."""

    answer: str
    provider: str
    model: str
    steps: int
    tool_calls: list[ToolInvocation] = field(default_factory=list)
    truncated: bool = False
    error: str | None = None
    #: What an HTTP caller should be told. 400 for a malformed request, 401 for
    #: a bad key, 502 only for a genuine upstream failure.
    error_status: int = 502
    disclaimer: str = DISCLAIMER

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "provider": self.provider,
            "model": self.model,
            "steps": self.steps,
            "tool_calls": [asdict(t) for t in self.tool_calls],
            "truncated": self.truncated,
            "error": self.error,
            "disclaimer": self.disclaimer,
        }


def _history_message(provider: Provider, turn_raw: Any) -> Any:
    """The assistant's own turn, in the shape its provider expects back."""
    return turn_raw if turn_raw is not None else {"role": "assistant", "content": ""}


def _run_tool(call: ToolCall) -> dict[str, Any]:
    """Execute one tool the model asked for. Never raises — a failure is a
    result the model must be able to read and explain."""
    if "__malformed_arguments" in call.arguments:
        return {
            "error": "the arguments you sent were not valid JSON; retry with a well-formed object",
            "received": str(call.arguments["__malformed_arguments"])[:200],
        }
    return call_tool(call.name, call.arguments, read_only=True)


def ask(
    question: str,
    api_key: str,
    provider_key: str = "openai",
    model: str | None = None,
    history: list[Any] | None = None,
    max_steps: int = DEFAULT_MAX_STEPS,
) -> AgentReply:
    """Answer one question by letting the model drive the engine's tools.

    `api_key` is the caller's own credential. It is passed to the provider and
    then dropped — never stored, never logged, never written to disk.

    `history` is the provider-native message list from previous turns, so the
    terminal can hold a conversation. It is the caller's job to keep it; this
    function is stateless on purpose, because holding conversation state
    server-side would mean holding it *next to* other people's keys.
    """
    try:
        provider = resolve(provider_key)
    except ProviderError as e:
        return AgentReply(
            answer="",
            provider=provider_key,
            model=model or "",
            steps=0,
            error=str(e),
            error_status=e.http_status,
        )

    chosen_model = model or provider.default_model
    messages: list[Any] = list(history or [])
    messages.append({"role": "user", "content": question})

    invocations: list[ToolInvocation] = []
    truncated = False

    for step in range(1, max_steps + 1):
        try:
            turn = chat(
                provider,
                api_key,
                chosen_model,
                messages,
                tools=AGENT_TOOLS,
                system=SYSTEM_PROMPT,
            )
        except ProviderError as e:
            return AgentReply(
                answer="",
                provider=provider.key,
                model=chosen_model,
                steps=step - 1,
                tool_calls=invocations,
                error=str(e),
                error_status=e.http_status,
            )

        if not turn.tool_calls:
            return AgentReply(
                answer=turn.text,
                provider=provider.key,
                model=chosen_model,
                steps=step,
                tool_calls=invocations,
            )

        messages.append(_history_message(provider, turn.raw))
        for call in turn.tool_calls:
            result = _run_tool(call)
            invocations.append(
                ToolInvocation(name=call.name, arguments=call.arguments, result=result)
            )
            messages.append(tool_result_message(provider, call, result))

        if step == max_steps:
            truncated = True

    # Ran out of steps while the model was still calling tools. Return what was
    # gathered rather than nothing — the tool results are the valuable part, and
    # they are all verified engine output.
    return AgentReply(
        answer=(
            f"Stopped after {max_steps} tool-calling rounds without a final answer. "
            "The tool results gathered so far are below; ask a narrower question "
            "to get a written summary."
        ),
        provider=provider.key,
        model=chosen_model,
        steps=max_steps,
        tool_calls=invocations,
        truncated=truncated,
    )


def summarize_for_terminal(reply: AgentReply) -> str:
    """Plain-text rendering for the CLI terminal, with the audit trail folded in
    so a command-line user sees the same evidence a web user does."""
    lines: list[str] = []
    if reply.error:
        lines.append(f"error: {reply.error}")
        return "\n".join(lines)

    for call in reply.tool_calls:
        args = ", ".join(f"{k}={v!r}" for k, v in call.arguments.items())
        marker = "!" if "error" in call.result else "*"
        lines.append(f"  {marker} {call.name}({args})")
        if "error" in call.result:
            lines.append(f"      {call.result['error']}")
    if lines:
        lines.append("")

    lines.append(reply.answer)
    return "\n".join(lines)


def transcript_json(reply: AgentReply) -> str:
    return json.dumps(reply.to_dict(), indent=2, default=str)
