"""No-lookahead backtesting: replay a cached price series through an analyzer as
if each historical bar were "now", then score every generated signal against
only the bars that came after it.

The most common backtesting bug is lookahead — letting the simulated past peek
at the future through a full-series indicator, an off-by-one slice, or an
invalidation level computed on later data. The guard here is structural:
`signal_at(series, t)` is the ONLY way the backtester generates a signal, and it
truncates the series to bars [0..t] before any analysis runs. A unit test pins
the guarantee by asserting the signal at bar t is identical whether or not the
future exists in the input series.

Expect the honest finding that the scaffold analyzer has little or no edge.
That is the point of this module: improvement happens against measured truth,
not vibes.
"""

from __future__ import annotations

from pydantic import BaseModel

from alpha_engine.analyzers.crypto_trend import analyze_trend, trend_invalidation
from alpha_engine.analyzers.equity_trend import analyze_equity_trend
from alpha_engine.analyzers.rsi import analyze_rsi
from alpha_engine.analyzers.bollinger import analyze_bollinger
from alpha_engine.cache.models import PriceSeries
from alpha_engine.schema.signal import Direction, Market, Signal, Timeframe
from alpha_engine.synthesis.synthesize import synthesize
from alpha_engine.validation.outcomes import (
    HORIZON_BARS,
    Outcome,
    OutcomeSummary,
    score_forward,
    summarize_outcomes,
)

# Bars the analyzer needs before its slow MA is defined (slow=30) plus margin.
DEFAULT_WARMUP = 35

# Which price-structure analyzer backs each market. Backtests replay only the
# trend source; macro context is excluded until point-in-time macro alignment
# exists (scoring it against today's revised series would be lookahead).
_TREND_ANALYZER = {
    Market.CRYPTO: analyze_trend,
    Market.US_EQUITY: analyze_equity_trend,
}


class BacktestReport(BaseModel):
    """The honest result of one backtest run. `signals_generated` counts every
    bar simulated; `directional` excludes neutrals (which are unscorable by
    definition); `summary` holds hit rate, average captured move, and the
    calibration curve over the directional signals."""

    asset: str
    market: Market
    timeframe: Timeframe
    bars: int
    warmup: int
    signals_generated: int
    directional: int
    summary: OutcomeSummary


def signal_at(
    series: PriceSeries,
    t: int,
    market: Market = Market.CRYPTO,
    timeframe: Timeframe = Timeframe.SWING,
) -> tuple[Signal, float]:
    """Generate the signal the engine WOULD have emitted at bar index t, seeing
    only bars [0..t]. Returns (signal, entry_price at bar t's close).

    This is the no-lookahead choke point: the truncation happens here, before
    any analysis, so no caller can accidentally leak the future in.

    Uses multiple analyzers (trend + RSI + Bollinger) for a richer synthesis,
    matching the live scan pipeline.
    """
    from alpha_engine.analyzers.indian_equity import analyze_indian_equity

    visible = series.candles[: t + 1]
    past = PriceSeries(asset=series.asset, interval=series.interval, candles=visible)

    sources = []
    if market is Market.CRYPTO:
        sources.append(analyze_trend(past))
        sources.append(analyze_rsi(past))
        sources.append(analyze_bollinger(past))
    elif market is Market.IN_EQUITY:
        sources.append(analyze_indian_equity(past))
        sources.append(analyze_rsi(past))
        sources.append(analyze_bollinger(past))
    else:
        sources.append(analyze_equity_trend(past))
        sources.append(analyze_rsi(past))
        sources.append(analyze_bollinger(past))

    signal = synthesize(
        asset=series.asset,
        market=market,
        sources=sources,
        timeframe=timeframe,
    )
    # Invalidation follows the SYNTHESIZED direction, not the raw source's: a
    # zero-weight bullish source synthesizes to neutral, which must carry no level.
    invalidation = trend_invalidation(visible, signal.direction)
    return signal.model_copy(update={"invalidation_level": invalidation}), visible[-1].close


def run_backtest(
    series: PriceSeries,
    market: Market = Market.CRYPTO,
    timeframe: Timeframe = Timeframe.SWING,
    warmup: int = DEFAULT_WARMUP,
    step: int = 1,
) -> BacktestReport:
    """Walk the series bar by bar, emit a signal at each step, score it against
    the future, and aggregate. `step` > 1 thins the walk (adjacent daily signals
    are heavily correlated; sparser sampling gives a less flattering, more
    honest read)."""
    candles = series.candles
    horizon = HORIZON_BARS[timeframe]

    scored: list[tuple[float, Outcome]] = []
    generated = 0
    directional = 0

    for t in range(warmup, len(candles) - 1, step):
        signal, entry = signal_at(series, t, market=market, timeframe=timeframe)
        generated += 1
        if signal.direction is Direction.NEUTRAL or entry == 0:
            continue
        directional += 1
        outcome = score_forward(
            direction=signal.direction,
            entry_price=entry,
            invalidation_level=signal.invalidation_level,
            future=candles[t + 1 :],
            horizon=horizon,
        )
        scored.append((signal.confidence, outcome))

    return BacktestReport(
        asset=series.asset,
        market=market,
        timeframe=timeframe,
        bars=len(candles),
        warmup=warmup,
        signals_generated=generated,
        directional=directional,
        summary=summarize_outcomes(scored),
    )
