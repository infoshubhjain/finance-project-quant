"""Joint underlying + options backtest.

Replay the SAME no-lookahead signals the price backtest uses, but for each
directional signal simulate taking the matching at-the-money option and report
BOTH P&Ls side by side:

    bullish signal  -> buy an ATM CALL
    bearish signal  -> buy an ATM PUT

The point is to make the two things a trader actually feels visible at once:
- **Leverage**: a +2% underlying move can be a +40% option move.
- **Time decay (theta)**: holding a long option through a flat tape bleeds
  premium even when the underlying does nothing.

Honesty boundaries (documented, not hidden):
- The underlying P&L is REAL — it comes straight from cached candles.
- The option P&L is MODEL-PRICED with Black-Scholes from the underlying close
  and a trailing realized-vol estimate (see quant/black_scholes.py). Free
  tick-level option history doesn't exist, so this is an approximation: no IV
  smile, no bid/ask, no demand skew. Read it as "what a textbook option would
  have done," not "what you'd have filled."

No-lookahead is inherited, not re-implemented: signals come only from
`signal_at` (bars [0..t]), and the entry-vol estimate uses only bars [0..t].
Exit prices use future bars — that is the outcome being measured, not a leak.

ponytail: v1 holds each option a fixed `horizon` bars (no early exit on the
signal's invalidation level). Add invalidation-based exit when a backtest needs
to compare stop behaviour; the fixed hold keeps entry/exit symmetric and simple.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, Field

from alpha_engine.cache.models import OptionRight, PriceSeries
from alpha_engine.quant.black_scholes import bs_price
from alpha_engine.quant.features import _pstd
from alpha_engine.schema.signal import Direction, Market, Timeframe
from alpha_engine.validation.backtest import DEFAULT_WARMUP, signal_at
from alpha_engine.validation.outcomes import HORIZON_BARS

# A month of trading days: long enough that a swing hold still leaves real time
# value at exit, short enough that theta actually bites. Configurable per run.
DEFAULT_DTE_BARS = 21
_TRADING_DAYS = 252.0
_VOL_WINDOW = 20  # bars of returns behind each entry-vol estimate


class OptionsBacktestReport(BaseModel):
    """Side-by-side result of simulating the option vs the underlying for every
    directional signal. Returns are per-trade means, expressed as fractions
    (0.10 = +10%). Option returns dwarf underlying returns because options are
    leveraged — that spread, up AND down, is the whole point of looking."""

    asset: str
    market: Market
    timeframe: Timeframe
    bars: int
    warmup: int
    dte_bars: int
    trades: int = Field(..., description="directional signals taken (neutrals skipped)")
    option_win_rate: float = Field(..., description="fraction of option trades that finished green")
    avg_option_return: float = Field(..., description="mean per-trade option P&L, as a fraction")
    avg_underlying_return: float = Field(
        ..., description="mean per-trade DIRECTIONAL underlying move (bullish=+, bearish=-)"
    )
    avg_vol_used: float = Field(..., description="mean annualized vol fed to the pricer")


def _annualized_vol(closes: list[float], window: int = _VOL_WINDOW) -> float | None:
    """Trailing annualized volatility from log returns of the last `window` bars.
    None when there isn't enough history to estimate it."""
    rets = [
        math.log(closes[i] / closes[i - 1])
        for i in range(1, len(closes))
        if closes[i - 1] > 0 and closes[i] > 0
    ]
    recent = rets[-window:]
    if len(recent) < 2:
        return None
    sd = _pstd(recent)
    if sd is None or sd <= 0:
        return None
    return sd * math.sqrt(_TRADING_DAYS)


def run_options_backtest(
    series: PriceSeries,
    market: Market = Market.IN_EQUITY,
    timeframe: Timeframe = Timeframe.SWING,
    warmup: int = DEFAULT_WARMUP,
    step: int = 1,
    dte_bars: int = DEFAULT_DTE_BARS,
    rate: float = 0.06,
) -> OptionsBacktestReport:
    """Backtest the ATM option matching each directional signal, alongside the
    underlying. Uses the exact same no-lookahead signals as `run_backtest`."""
    candles = series.candles
    horizon = HORIZON_BARS[timeframe]
    # The option must outlive the hold, or exit is at expiry (pure intrinsic).
    if dte_bars <= horizon:
        dte_bars = horizon + 5

    opt_returns: list[float] = []
    und_returns: list[float] = []
    vols_used: list[float] = []
    wins = 0

    for t in range(warmup, len(candles) - 1, step):
        signal, spot_entry = signal_at(series, t, market=market, timeframe=timeframe)
        if signal.direction is Direction.NEUTRAL or spot_entry <= 0:
            continue

        vol = _annualized_vol([c.close for c in candles[: t + 1]])
        if vol is None:
            continue

        right = OptionRight.CALL if signal.direction is Direction.BULLISH else OptionRight.PUT
        strike = spot_entry  # at-the-money

        exit_idx = min(t + horizon, len(candles) - 1)
        held = exit_idx - t
        spot_exit = candles[exit_idx].close

        entry_prem = bs_price(spot_entry, strike, dte_bars / _TRADING_DAYS, vol, right, rate)
        exit_prem = bs_price(spot_exit, strike, (dte_bars - held) / _TRADING_DAYS, vol, right, rate)
        if entry_prem <= 0:
            continue

        opt_ret = exit_prem / entry_prem - 1.0
        raw_move = spot_exit / spot_entry - 1.0
        und_ret = raw_move if signal.direction is Direction.BULLISH else -raw_move

        opt_returns.append(opt_ret)
        und_returns.append(und_ret)
        vols_used.append(vol)
        if opt_ret > 0:
            wins += 1

    n = len(opt_returns)
    return OptionsBacktestReport(
        asset=series.asset,
        market=market,
        timeframe=timeframe,
        bars=len(candles),
        warmup=warmup,
        dte_bars=dte_bars,
        trades=n,
        option_win_rate=round(wins / n, 4) if n else 0.0,
        avg_option_return=round(sum(opt_returns) / n, 6) if n else 0.0,
        avg_underlying_return=round(sum(und_returns) / n, 6) if n else 0.0,
        avg_vol_used=round(sum(vols_used) / n, 6) if n else 0.0,
    )
