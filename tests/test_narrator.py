"""Tests for the narrative layer: template thesis generation and the optional
LLM rephraser. The LLM path is tested by mocking the HTTP call so no real API
key is needed — the point is to verify the re-validation gate, not to test an
external service.

Key properties tested:
- Template thesis always populates all required fields.
- LLM path falls back to template when no API key is set.
- LLM path falls back when the HTTP call fails.
- LLM path rejects output that changes numeric fields.
- LLM path accepts output that only changes prose.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


from alpha_engine.narrative.llm import (
    _build_prompt,
    _extract_numeric_fields,
    _validate_numeric_fields_unchanged,
    rewrite_thesis,
)
from alpha_engine.narrative.narrator import _template_thesis, write_thesis
from alpha_engine.schema.signal import (
    Direction,
    Market,
    Signal,
    SignalSource,
    Timeframe,
)

T0 = datetime(2024, 6, 15, tzinfo=timezone.utc)


def _signal(
    direction: Direction = Direction.BULLISH,
    confidence: float = 0.72,
    invalidation: float | None = 95.0,
) -> Signal:
    return Signal(
        asset="BTC",
        market=Market.CRYPTO,
        direction=direction,
        confidence=confidence,
        timeframe=Timeframe.SWING,
        signal_sources=[
            SignalSource(name="crypto.trend", direction=Direction.BULLISH, weight=0.8),
            SignalSource(name="macro.ctx", direction=Direction.BULLISH, weight=0.3),
        ],
        invalidation_level=invalidation,
        timestamp=T0,
    )


# --- template thesis --------------------------------------------------------


def test_template_thesis_contains_asset_and_direction():
    sig = _signal()
    thesis = _template_thesis(sig)
    assert "BTC" in thesis
    assert "bullish" in thesis
    assert "72%" in thesis


def test_template_thesis_lists_sources():
    sig = _signal()
    thesis = _template_thesis(sig)
    assert "crypto.trend" in thesis
    assert "macro.ctx" in thesis


def test_template_thesis_mentions_invalidation():
    sig = _signal(invalidation=95.0)
    thesis = _template_thesis(sig)
    assert "95.00" in thesis


def test_template_thesis_neutral_direction():
    sig = _signal(direction=Direction.NEUTRAL, confidence=0.0)
    thesis = _template_thesis(sig)
    assert "neutral" in thesis


def test_template_thesis_no_invalidation():
    sig = _signal(invalidation=None)
    thesis = _template_thesis(sig)
    assert "invalidated" not in thesis


# --- write_thesis (template only, no LLM) -----------------------------------


def test_write_thesis_without_llm_populates_thesis():
    sig = _signal()
    result = write_thesis(sig, use_llm=False)
    assert result.thesis != ""
    assert "BTC" in result.thesis


def test_write_thesis_preserves_all_numeric_fields():
    sig = _signal()
    result = write_thesis(sig, use_llm=False)
    assert result.direction is sig.direction
    assert result.confidence == sig.confidence
    assert result.invalidation_level == sig.invalidation_level
    assert len(result.signal_sources) == len(sig.signal_sources)
    for orig, got in zip(sig.signal_sources, result.signal_sources):
        assert orig.direction is got.direction
        assert orig.weight == got.weight


# --- numeric field extraction ------------------------------------------------


def test_extract_numeric_fields_captures_direction_and_confidence():
    sig = _signal()
    fields = _extract_numeric_fields(sig)
    assert fields["direction"] == "bullish"
    assert fields["confidence"] == 0.72
    assert fields["invalidation_level"] == 95.0
    assert len(fields["sources"]) == 2
    assert fields["sources"][0]["weight"] == 0.8


def test_validate_unchanged_returns_true_for_identical_signals():
    sig = _signal()
    assert _validate_numeric_fields_unchanged(sig, sig) is True


def test_validate_unchanged_detects_confidence_change():
    sig = _signal()
    changed = sig.model_copy(update={"confidence": 0.99})
    assert _validate_numeric_fields_unchanged(sig, changed) is False


def test_validate_unchanged_detects_direction_change():
    sig = _signal()
    changed = sig.model_copy(update={"direction": Direction.BEARISH})
    assert _validate_numeric_fields_unchanged(sig, changed) is False


def test_validate_unchanged_detects_source_weight_change():
    sig = _signal()
    new_sources = [
        SignalSource(name="crypto.trend", direction=Direction.BULLISH, weight=0.99),
        SignalSource(name="macro.ctx", direction=Direction.BULLISH, weight=0.3),
    ]
    changed = sig.model_copy(update={"signal_sources": new_sources})
    assert _validate_numeric_fields_unchanged(sig, changed) is False


def test_validate_unchanged_allows_thesis_change():
    sig = _signal()
    changed = sig.model_copy(update={"thesis": "completely different text"})
    assert _validate_numeric_fields_unchanged(sig, changed) is True


# --- LLM prompt building -----------------------------------------------------


def test_build_prompt_contains_signal_context():
    sig = _signal()
    prompt = _build_prompt(sig, "template text here")
    assert "BTC" in prompt
    assert "bullish" in prompt
    assert "72%" in prompt
    assert "95.00" in prompt
    assert "template text here" in prompt
    assert "crypto.trend" in prompt


# --- rewrite_thesis (LLM path) -----------------------------------------------


def test_rewrite_thesis_falls_back_without_api_key(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    sig = _signal()
    result = rewrite_thesis(sig, "original template")
    assert result == "original template"


def test_rewrite_thesis_falls_back_on_http_failure(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key-123")

    def fake_post(*args: Any, **kwargs: Any) -> Any:
        raise ConnectionError("no network")

    import alpha_engine.narrative.llm as llm_mod
    monkeypatch.setattr(llm_mod.requests, "post", fake_post)

    sig = _signal()
    result = rewrite_thesis(sig, "original template")
    assert result == "original template"


def test_rewrite_thesis_falls_back_on_malformed_response(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key-123")

    class FakeResponse:
        status_code = 200
        def raise_for_status(self) -> None:
            pass
        def json(self) -> dict:
            return {"unexpected": "shape"}

    def fake_post(*args: Any, **kwargs: Any) -> FakeResponse:
        return FakeResponse()

    import alpha_engine.narrative.llm as llm_mod
    monkeypatch.setattr(llm_mod.requests, "post", fake_post)

    sig = _signal()
    result = rewrite_thesis(sig, "original template")
    assert result == "original template"


def test_rewrite_thesis_rejects_llm_output_that_changes_numbers(monkeypatch):
    """The re-validation gate catches corruption of the Signal's numeric fields.

    In practice the LLM can only change the thesis string (the Signal object is
    built by pure Python and never given to the LLM). But if a pipeline bug
    somehow mutated a numeric field during the rewrite step, this gate would
    catch it. We simulate that by injecting a post-LLM validation that returns
    a different signal."""
    monkeypatch.setenv("LLM_API_KEY", "test-key-123")

    class FakeResponse:
        status_code = 200
        def raise_for_status(self) -> None:
            pass
        def json(self) -> dict:
            return {
                "choices": [
                    {"message": {"content": "rephrased thesis"}}
                ]
            }

    def fake_post(*args: Any, **kwargs: Any) -> FakeResponse:
        return FakeResponse()

    import alpha_engine.narrative.llm as llm_mod
    monkeypatch.setattr(llm_mod.requests, "post", fake_post)

    # Simulate a pipeline bug: after the LLM call, the candidate signal
    # has a different confidence. The validation should catch this.
    original = _signal()
    monkeypatch.setattr(llm_mod, "_validate_numeric_fields_unchanged", lambda a, b: False)

    result = rewrite_thesis(original, "original template")
    assert result == "original template"


def test_rewrite_thesis_accepts_llm_output_that_only_rephrases(monkeypatch):
    """When the LLM only changes thesis prose and numeric fields are untouched,
    the rewritten thesis is accepted."""
    monkeypatch.setenv("LLM_API_KEY", "test-key-123")

    good_thesis = (
        "The engine views BTC with a bullish outlook on the swing timeframe, "
        "carrying 72% confidence. Contributing inputs: crypto.trend (bullish, "
        "w=0.80), macro.ctx (bullish, w=0.30). Thesis invalidated below 95.00. "
        "Research output only, not investment advice."
    )

    class FakeResponse:
        status_code = 200
        def raise_for_status(self) -> None:
            pass
        def json(self) -> dict:
            return {"choices": [{"message": {"content": good_thesis}}]}

    def fake_post(*args: Any, **kwargs: Any) -> FakeResponse:
        return FakeResponse()

    import alpha_engine.narrative.llm as llm_mod
    monkeypatch.setattr(llm_mod.requests, "post", fake_post)

    sig = _signal()
    result = rewrite_thesis(sig, "original template")
    assert result == good_thesis
    assert "72%" in result


def test_rewrite_thesis_uses_custom_model_and_api_base(monkeypatch):
    captured: dict[str, Any] = {}

    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "custom-model")
    monkeypatch.setenv("LLM_API_BASE", "https://custom.api.com/v1")

    class FakeResponse:
        status_code = 200
        def raise_for_status(self) -> None:
            pass
        def json(self) -> dict:
            return {"choices": [{"message": {"content": "rephrased"}}]}

    def fake_post(url: str, **kwargs: Any) -> FakeResponse:
        captured["url"] = url
        captured["model"] = kwargs.get("json", {}).get("model")
        return FakeResponse()

    import alpha_engine.narrative.llm as llm_mod
    monkeypatch.setattr(llm_mod.requests, "post", fake_post)

    sig = _signal()
    result = rewrite_thesis(sig, "template")
    assert result == "rephrased"
    assert captured["url"] == "https://custom.api.com/v1/chat/completions"
    assert captured["model"] == "custom-model"


def test_write_thesis_with_llm_false_never_calls_llm(monkeypatch):
    """Even with a key set, use_llm=False must not touch the LLM path."""
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    def fail_post(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("LLM should not be called when use_llm=False")

    import alpha_engine.narrative.llm as llm_mod
    monkeypatch.setattr(llm_mod.requests, "post", fail_post)

    sig = _signal()
    result = write_thesis(sig, use_llm=False)
    assert result.thesis != ""
    assert "BTC" in result.thesis
