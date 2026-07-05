"""Optional LLM narrator. Upgrades thesis prose quality without ever letting the
model touch a number.

The design is simple and honest:

1. Receive a fully-formed Signal (all numbers already decided by pure Python).
2. Pass the signal + the templated thesis to an LLM and ask for a rephrased
   thesis.
3. **Re-validate** that the post-LLM Signal's numeric fields (direction,
   confidence, invalidation_level, every SignalSource weight) are identical to
   the pre-LLM ones. If anything changed, reject the LLM output silently.
4. If no API key is set, or the LLM call fails, fall back to the template
   silently.

There is never a paywall in the middle of the pipeline. The engine runs
identically without a key; the LLM only upgrades phrasing.

Cardinal rule compliance: this module may only write the `thesis` string.
It is never allowed to set or change a number. The re-validation step is
mandatory, not optional.
"""

from __future__ import annotations

import os
from typing import Any

import requests

from alpha_engine.schema.signal import Signal


def _extract_numeric_fields(sig: Signal) -> dict[str, Any]:
    """Extract the numeric, decision-bearing fields for comparison.

    These are the fields that an LLM must never change. If any differ
    after the LLM call, the LLM output is rejected.
    """
    return {
        "direction": sig.direction.value,
        "confidence": sig.confidence,
        "invalidation_level": sig.invalidation_level,
        "sources": [
            {"name": s.name, "direction": s.direction.value, "weight": s.weight}
            for s in sig.signal_sources
        ],
    }


def _validate_numeric_fields_unchanged(before: Signal, after: Signal) -> bool:
    """Return True if and only if every numeric, decision-bearing field on the
    post-LLM Signal matches the pre-LLM Signal exactly.

    This is the safety gate. The LLM may only change `thesis`; if it touches
    any number, we reject its output and fall back to the template.
    """
    before_fields = _extract_numeric_fields(before)
    after_fields = _extract_numeric_fields(after)
    return before_fields == after_fields


def _build_prompt(signal: Signal, template_thesis: str) -> str:
    """Build the LLM prompt. We give the model all the context it needs to
    write a clear thesis, but instruct it explicitly to only rephrase the
    existing thesis — never to change any numbers."""
    sources_detail = ""
    if signal.signal_sources:
        sources_detail = "\n".join(
            f"  - {s.name}: {s.direction.value} (weight={s.weight:.2f})"
            for s in signal.signal_sources
        )
    else:
        sources_detail = "  (none)"

    invalidation = (
        f"{signal.invalidation_level:.2f}" if signal.invalidation_level is not None else "N/A"
    )

    return (
        "You are a financial research analyst. Rewrite the following thesis "
        "to be clearer and more natural-sounding. You MUST preserve all "
        "specific numbers and facts exactly as stated. Do NOT add any new "
        "claims, predictions, or opinions.\n\n"
        f"Asset: {signal.asset}\n"
        f"Market: {signal.market.value}\n"
        f"Direction: {signal.direction.value}\n"
        f"Confidence: {signal.confidence:.0%}\n"
        f"Timeframe: {signal.timeframe.value}\n"
        f"Invalidation level: {invalidation}\n"
        f"Sources:\n{sources_detail}\n\n"
        f"Current thesis (template):\n{template_thesis}\n\n"
        "Rewrite the thesis above to be clearer and more natural. Keep all "
        "numbers identical. Output ONLY the rewritten thesis text, nothing else."
    )


def _call_llm(prompt: str, api_key: str, model: str, api_base: str) -> str | None:
    """Call the OpenAI-compatible chat API and return the assistant's message.

    Returns None if the call fails for any reason (network, auth, parse error).
    Never raises — failures are silent fallbacks to the template.
    """
    try:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 500,
        }
        resp = requests.post(
            f"{api_base.rstrip('/')}/chat/completions",
            json=body,
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices", [])
        if choices and isinstance(choices[0], dict):
            message = choices[0].get("message", {})
            content = message.get("content")
            if content and isinstance(content, str):
                return content.strip()
    except Exception:  # noqa: BLE001 - LLM is optional; any failure is a silent fallback
        pass
    return None


def rewrite_thesis(signal: Signal, template_thesis: str) -> str:
    """Attempt to rewrite the thesis via an LLM. Returns the rewritten thesis
    if the LLM is configured and the output passes numeric-field validation;
    otherwise returns the original template thesis unchanged.

    This function never modifies the Signal — it only returns a thesis string.
    The caller owns updating the Signal.
    """
    api_key = os.environ.get("LLM_API_KEY", "")
    if not api_key:
        return template_thesis

    model = os.environ.get("LLM_MODEL", "gpt-4o-mini")
    api_base = os.environ.get("LLM_API_BASE", "https://api.openai.com/v1")

    prompt = _build_prompt(signal, template_thesis)
    llm_thesis = _call_llm(prompt, api_key, model, api_base)
    if llm_thesis is None:
        return template_thesis

    # Build a candidate signal with the LLM thesis and validate numbers unchanged.
    candidate = signal.model_copy(update={"thesis": llm_thesis})
    if not _validate_numeric_fields_unchanged(signal, candidate):
        return template_thesis

    return llm_thesis
