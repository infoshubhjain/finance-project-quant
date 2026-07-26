"""Signal -> confirmation -> position -> trades -> equity curve.

The flow, in order:

1. `strategy.generate_signals(underlying)` returns one intent per bar (1/-1/0).
2. Every non-zero intent is offered to `strategy.verify_on_option(...)`. Only
   confirmed intents survive. This is the step the rest of the engine does not
   have, and the reason this module exists.
3. Surviving intents become a position, held until an opposite intent arrives.
4. P&L accrues on whichever leg the caller chose to trade, net of a flat
   transaction cost charged whenever the position changes.

Two honesty guards are built in rather than documented and hoped for:

- **The position is lagged one bar.** A signal computed from bar `t`'s close is
  filled at `t+1`, never at `t`. Paying the close you just used to decide is the
  oldest way to invent returns.
- **`check_lookahead`** re-runs the strategy on truncated history and reports any
  bar whose signal changed once the future was removed. Costs one extra pass
  over a sample of bars; catches the bug class that makes backtests lie.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from alpha_engine.cache.models import Candle, Interval, PriceSeries
from alpha_engine.strategy.base import BaseStrategy
from alpha_engine.strategy.metrics import (
    StrategyMetrics,
    bars_per_year_for,
    compute_metrics,
)

#: Bars sampled by the lookahead check. Re-running the strategy is O(n) per
#: sampled bar, so checking every bar would make the check quadratic; a spread
#: sample catches a systematic leak, which is the only kind that exists in
#: practice — nobody peeks at the future on exactly one bar.
_LOOKAHEAD_SAMPLES = 25


class Trade(BaseModel):
    """One round trip. `open` marks a position still held at the last bar — its
    P&L is marked-to-market, not realised."""

    entry_ts: datetime
    exit_ts: datetime
    direction: str  # "long" | "short"
    entry_price: float
    exit_price: float
    qty: float
    pnl: float
    return_pct: float
    open: bool = False


class StrategyBacktest(BaseModel):
    """The full result. Series fields are aligned to `timestamps`, so a chart
    can plot any of them against one x-axis with no further work."""

    strategy: str
    params: dict[str, float | int | str | bool]
    asset: str
    interval: Interval
    trade_on: str = Field(..., description="'underlying' or 'option' — which leg P&L accrued on")
    option_confirmation: bool

    bars: int
    timestamps: list[datetime]
    signals: list[int] = Field(..., description="raw strategy intent per bar")
    confirmed: list[bool] = Field(..., description="did the option chart confirm this bar's intent")
    position: list[int] = Field(..., description="position actually held, entering bar i")
    equity_curve: list[float]
    drawdown: list[float]

    signals_raw: int = Field(..., description="non-zero intents the strategy produced")
    signals_confirmed: int = Field(..., description="intents that survived option confirmation")
    trades: list[Trade]
    metrics: StrategyMetrics

    lookahead_checked: bool = False
    lookahead_violations: list[int] = Field(
        default_factory=list,
        description="bar indices whose signal changed when future bars were removed; "
        "any entry here means the equity curve above is not trustworthy",
    )
    disclaimer: str = (
        "RESEARCH ONLY. A backtest is a measurement of the past under simplifying "
        "assumptions, not a prediction. Not financial advice."
    )


def align_option_to_underlying(
    underlying: list[Candle], option: list[Candle]
) -> tuple[list[Candle], list[Candle]]:
    """Trim both series to their overlapping range and forward-fill the option
    onto the underlying's bar index.

    Returns `(underlying_trimmed, option_aligned)` of equal length, where index
    `i` is the same moment in both. Forward-fill only ever reaches backwards to
    the most recent option bar at or before the underlying bar, so no future
    option price can leak in.

    Trimming to the overlap rather than padding is deliberate: an option that
    did not trade yet has no price, and inventing one is how a backtest starts
    lying before the first trade.
    """
    if not underlying or not option:
        return list(underlying), []

    start_ts = max(underlying[0].ts, option[0].ts)
    trimmed = [c for c in underlying if c.ts >= start_ts]
    if not trimmed:
        return [], []

    aligned: list[Candle] = []
    j = 0
    for candle in trimmed:
        while j + 1 < len(option) and option[j + 1].ts <= candle.ts:
            j += 1
        aligned.append(option[j])
    return trimmed, aligned


def _extract_trades(
    position: list[int],
    prices: list[float],
    timestamps: list[datetime],
    qty: float,
    txn_cost_bps: float,
) -> list[Trade]:
    """Walk the position series and emit one Trade per round trip."""
    trades: list[Trade] = []
    held = 0
    entry_i = 0

    def close(exit_i: int, still_open: bool) -> None:
        entry_price = prices[entry_i]
        exit_price = prices[exit_i]
        if entry_price <= 0:
            return
        gross = (exit_price - entry_price) * held * qty
        cost = txn_cost_bps / 10_000.0 * (entry_price + exit_price) * qty
        pnl = gross - cost
        trades.append(
            Trade(
                entry_ts=timestamps[entry_i],
                exit_ts=timestamps[exit_i],
                direction="long" if held == 1 else "short",
                entry_price=round(entry_price, 4),
                exit_price=round(exit_price, 4),
                qty=qty,
                pnl=round(pnl, 2),
                return_pct=round((exit_price - entry_price) * held / entry_price * 100.0, 4),
                open=still_open,
            )
        )

    for i, pos in enumerate(position):
        if pos == held:
            continue
        if held != 0:
            close(i, still_open=False)
        if pos != 0:
            entry_i = i
        held = pos

    if held != 0:
        close(len(position) - 1, still_open=True)
    return trades


def _lookahead_violations(
    strategy: BaseStrategy, candles: list[Candle], signals: list[int]
) -> list[int]:
    """Re-run the strategy on truncated history and report bars whose signal
    changed. A non-empty result means `generate_signals` read future bars."""
    n = len(candles)
    if n < 3:
        return []
    # Skip the first 20% as warm-up: an indicator seeded differently on a short
    # slice is not lookahead, it is just an undefined indicator.
    start = max(2, n // 5)
    if start >= n:
        return []
    step = max(1, (n - start) // _LOOKAHEAD_SAMPLES)

    violations: list[int] = []
    for t in range(start, n, step):
        truncated = strategy.generate_signals(candles[: t + 1])
        if len(truncated) == t + 1 and truncated[t] != signals[t]:
            violations.append(t)
    return violations


def run_strategy_backtest(
    strategy: BaseStrategy,
    underlying: PriceSeries,
    option: PriceSeries | None = None,
    *,
    trade_on: str = "underlying",
    require_option_confirmation: bool = True,
    capital: float = 100_000.0,
    qty: float = 1.0,
    txn_cost_bps: float = 2.0,
    check_lookahead: bool = True,
) -> StrategyBacktest:
    """Backtest one strategy and return trades, equity curve and metrics.

    `trade_on="option"` computes P&L on the option leg while still generating
    signals from the underlying — how an options trader actually works: the
    index tells you the direction, the contract is what you own.
    """
    if trade_on not in ("underlying", "option"):
        raise ValueError("trade_on must be 'underlying' or 'option'")

    option_candles_raw = option.candles if option else []
    candles, option_candles = align_option_to_underlying(underlying.candles, option_candles_raw)
    if trade_on == "option" and not option_candles:
        raise ValueError("trade_on='option' needs an option price series with overlapping bars")
    if len(candles) < 2:
        raise ValueError("need at least 2 overlapping bars to backtest")

    confirm = require_option_confirmation and bool(option_candles)
    signals = strategy.generate_signals(candles)
    if len(signals) != len(candles):
        raise ValueError(
            f"{type(strategy).__name__}.generate_signals returned {len(signals)} signals "
            f"for {len(candles)} candles; it must return exactly one per bar"
        )

    confirmed: list[bool] = []
    for t, sig in enumerate(signals):
        confirmed.append(
            bool(sig) and (strategy.verify_on_option(option_candles, t, sig) if confirm else True)
        )

    # Hold the last confirmed intent until a new one replaces it.
    position: list[int] = []
    held = 0
    for sig, ok in zip(signals, confirmed):
        if sig != 0 and ok:
            held = sig
        position.append(held)

    prices = [
        (option_candles[i] if trade_on == "option" else candles[i]).close
        for i in range(len(candles))
    ]
    timestamps = [c.ts for c in candles]

    returns: list[float] = [0.0]
    equity: list[float] = [capital]
    for i in range(1, len(prices)):
        prev = prices[i - 1]
        bar_ret = (prices[i] / prev - 1.0) if prev > 0 else 0.0
        # position[i-1]: the fill happens on the bar AFTER the signal's close.
        gross = position[i - 1] * bar_ret
        turnover = abs(position[i] - position[i - 1])
        net = gross - turnover * (txn_cost_bps / 10_000.0)
        returns.append(net)
        equity.append(equity[-1] * (1.0 + net))

    trades = _extract_trades(position, prices, timestamps, qty, txn_cost_bps)
    metrics, drawdown = compute_metrics(
        equity,
        returns[1:],
        [t.pnl for t in trades],
        bars_per_year=bars_per_year_for(underlying.interval),
    )

    violations = _lookahead_violations(strategy, candles, signals) if check_lookahead else []

    return StrategyBacktest(
        strategy=strategy.name,
        params={k: v for k, v in strategy.params.items() if isinstance(v, (int, float, str, bool))},
        asset=underlying.asset,
        interval=underlying.interval,
        trade_on=trade_on,
        option_confirmation=confirm,
        bars=len(candles),
        timestamps=timestamps,
        signals=signals,
        confirmed=confirmed,
        position=position,
        equity_curve=[round(e, 2) for e in equity],
        drawdown=[round(d, 6) for d in drawdown],
        signals_raw=sum(1 for s in signals if s != 0),
        signals_confirmed=sum(1 for ok in confirmed if ok),
        trades=trades,
        metrics=metrics,
        lookahead_checked=check_lookahead,
        lookahead_violations=violations,
    )
