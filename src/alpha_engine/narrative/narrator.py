"""Narrative layer. Writes the `thesis` string and NOTHING else. It receives a
fully-formed Signal with every number already decided, and explains it in prose.

Critical design choice for a free, clonable tool: the LLM is optional. With no API
key, a deterministic template produces a perfectly valid thesis. An LLM, if
configured, only upgrades the phrasing. There is never a paywall in the middle of
the pipeline, and the LLM is never allowed to change a number on the Signal.
"""

from __future__ import annotations

from alpha_engine.schema.signal import Direction, Signal


def _template_thesis(signal: Signal) -> str:
    """Deterministic, no-dependency thesis. Always available."""
    dir_word = {
        Direction.BULLISH: "a bullish",
        Direction.BEARISH: "a bearish",
        Direction.NEUTRAL: "a neutral",
    }[signal.direction]

    parts = [
        f"The engine reads {signal.asset} as {dir_word} setup on the "
        f"{signal.timeframe.value} timeframe, at {signal.confidence:.0%} confidence."
    ]

    if signal.signal_sources:
        contribs = ", ".join(
            f"{s.name} ({s.direction.value}, w={s.weight:.2f})"
            for s in signal.signal_sources
        )
        parts.append(f"Contributing inputs: {contribs}.")

    if signal.invalidation_level is not None:
        parts.append(
            f"The thesis is invalidated below/above {signal.invalidation_level:.2f}."
        )

    parts.append("Research output only, not investment advice.")
    return " ".join(parts)


def write_thesis(signal: Signal, use_llm: bool = False) -> Signal:
    """Return a copy of the signal with `thesis` populated. Always works offline.

    When `use_llm=True` and an LLM API key is configured (env `LLM_API_KEY`),
    the templated thesis is handed to the LLM for rephrasing. The LLM output
    is only accepted if re-validation confirms every numeric field is identical
    to the pre-LLM signal. If no key is set, the LLM call fails, or validation
    rejects the output, the template is used silently.
    """
    thesis = _template_thesis(signal)
    if use_llm:
        from alpha_engine.narrative.llm import rewrite_thesis

        thesis = rewrite_thesis(signal, thesis)
    return signal.model_copy(update={"thesis": thesis})
