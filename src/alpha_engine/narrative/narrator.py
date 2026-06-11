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

    `use_llm=True` is a placeholder hook for the optional LLM upgrade. Until that's
    wired (and gated behind a user-supplied key), it falls back to the template so
    behavior is identical with or without a key.
    """
    thesis = _template_thesis(signal)
    # When the LLM path lands, it will take `signal` + `thesis` and rephrase,
    # then we re-validate that it changed no numeric field before accepting.
    return signal.model_copy(update={"thesis": thesis})
