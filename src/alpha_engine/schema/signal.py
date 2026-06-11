"""The signal schema. This is the contract between every layer of the system.

Nothing in this file should change casually. Every analyzer produces parts of a
Signal, synthesis assembles the final object, the narrator fills in `thesis`, and
the validation layer stores and scores it. If you change a field here, you change
every layer downstream, so the version is bumped deliberately.

Design rule that this schema enforces: the numeric, decision-bearing fields
(direction, confidence, invalidation_level) are produced by deterministic Python.
`thesis` is the only free-text field and is the only thing an LLM is allowed to
write. The LLM never sets a number.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator

SCHEMA_VERSION = "0.1.0"


class Market(str, Enum):
    """The market a signal applies to. Drives which analyzer produced it and
    which data sources fed it. New markets are added here as analyzers land."""

    CRYPTO = "crypto"
    US_EQUITY = "us_equity"
    IN_EQUITY = "in_equity"
    IN_FNO = "in_fno"
    FOREX = "forex"


class Direction(str, Enum):
    """Directional bias. Deliberately not 'buy'/'sell' so the system reads as
    research output rather than advice. NEUTRAL is a real, common answer."""

    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class Timeframe(str, Enum):
    INTRADAY = "intraday"
    SWING = "swing"  # days to weeks
    POSITION = "position"  # weeks to months


class SignalSource(BaseModel):
    """A single contributing input to a signal, with the partial view it gave.
    The list of these on a Signal is what makes synthesis auditable: you can see
    exactly which analyzer said what and how strongly."""

    name: str = Field(..., description="e.g. 'crypto.trend', 'fno.pcr'")
    direction: Direction
    weight: float = Field(..., ge=0.0, le=1.0, description="contribution weight")
    detail: str = Field("", description="short machine-generated note, not prose")


class Signal(BaseModel):
    """The unified output of the engine for one asset at one point in time.

    This object is immutable once recorded by the validation layer. Treat it as a
    timestamped fact: 'at this instant, given these inputs, the engine's view was X'.
    """

    schema_version: str = Field(default=SCHEMA_VERSION)

    asset: str = Field(..., description="ticker or symbol, e.g. 'BTC', 'AAPL', 'NIFTY'")
    market: Market
    direction: Direction
    confidence: float = Field(
        ..., ge=0.0, le=1.0, description="0=no conviction, 1=maximal. Calibrated, not hype."
    )
    timeframe: Timeframe

    signal_sources: list[SignalSource] = Field(
        default_factory=list, description="every input that fed this signal"
    )

    invalidation_level: Optional[float] = Field(
        None,
        description="price at which the thesis is wrong. The most honest field in "
        "the schema. None only if genuinely not applicable.",
    )

    thesis: str = Field(
        "",
        description="human-readable rationale. The ONLY field an LLM may write. "
        "Filled by the narrative layer; templated fallback if no LLM configured.",
    )

    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC instant the signal was generated",
    )

    @field_validator("asset")
    @classmethod
    def asset_not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("asset must be a non-empty symbol")
        return v.strip().upper()

    @field_validator("timestamp")
    @classmethod
    def timestamp_is_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware (UTC)")
        return v.astimezone(timezone.utc)

    def is_actionable(self) -> bool:
        """A convenience read: neutral or near-zero-confidence signals are real
        outputs but not things you'd act on. Kept out of the data; computed here."""
        return self.direction is not Direction.NEUTRAL and self.confidence >= 0.55
