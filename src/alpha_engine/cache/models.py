"""Normalized data shapes. Every external source, however messy, is mapped into
these before anything downstream sees it. An NSE quote and a FRED series land in
compatible shapes here, which is what lets analyzers stay source-agnostic.

If you add a source, you write an ingestion adapter that outputs these types. You
do NOT teach the analyzer about the source's native format. That separation is the
whole point of the cache layer.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class Interval(str, Enum):
    MINUTE = "1m"
    HOUR = "1h"
    DAY = "1d"


class Candle(BaseModel):
    """One OHLCV bar, normalized. Volume optional because some macro/forex
    sources don't provide it."""

    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None


class PriceSeries(BaseModel):
    """A run of candles for one asset at one interval. The bread-and-butter input
    for a technical/trend analyzer."""

    asset: str
    interval: Interval
    candles: list[Candle] = Field(default_factory=list)
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def closes(self) -> list[float]:
        return [c.close for c in self.candles]


class OptionRight(str, Enum):
    """Whether an option is a call (right to buy) or a put (right to sell)."""

    CALL = "call"
    PUT = "put"


class OptionQuote(BaseModel):
    """One row of an options chain: a single strike + right for one expiry.
    `oi` (open interest) is the number of contracts currently open there — the
    footprint of where market participants have positioned. `oi_change` is the
    day-over-day shift when the source provides it; None means unknown, which
    analyzers must treat differently from zero."""

    strike: float
    right: OptionRight
    oi: float = Field(..., ge=0.0, description="open interest, in contracts")
    oi_change: float | None = None
    volume: float | None = None
    last_price: float | None = None


class OptionsChain(BaseModel):
    """A full options chain for one underlying and one expiry, normalized. This
    is the input for the F&O analyzers (PCR, max pain, OI structure). Whatever
    broker it came from (Breeze, Angel One, a hand-dropped JSON), it lands in
    this one shape."""

    underlying: str  # e.g. 'NIFTY'
    expiry: datetime
    spot: float | None = Field(None, description="underlying price when fetched")
    quotes: list[OptionQuote] = Field(default_factory=list)
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def calls(self) -> list[OptionQuote]:
        return [q for q in self.quotes if q.right is OptionRight.CALL]

    def puts(self) -> list[OptionQuote]:
        return [q for q in self.quotes if q.right is OptionRight.PUT]


class MacroObservation(BaseModel):
    """A single value of a macro series at a date, e.g. US CPI for a month.
    Normalized from FRED, World Bank, RBI, etc."""

    series_id: str  # e.g. 'CPIAUCSL'
    ts: datetime
    value: float
    source: str  # e.g. 'fred'


class CacheKey(BaseModel):
    """How a cached item is addressed. Kept explicit so the store stays debuggable
    and a human can reason about what's cached."""

    kind: str  # 'price' | 'macro'
    asset: str  # asset symbol or series id
    interval: str = ""  # only for price

    def as_str(self) -> str:
        return f"{self.kind}:{self.asset}:{self.interval}".rstrip(":")
