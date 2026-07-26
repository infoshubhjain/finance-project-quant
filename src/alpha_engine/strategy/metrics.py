"""Trade-level performance metrics.

These are the numbers `validation/outcomes.py` deliberately does not compute.
Outcomes scores *signals* (was the direction right?); this scores a *position
book* (what did the money do?). Both are needed and they are not the same
question — a strategy can be right 60% of the time and still lose money.

Every metric is a plain arithmetic function of the equity curve and the trade
list, so the cardinal rule holds trivially: no network, no randomness, no model.

Annualisation is explicit, not guessed. Sharpe on 5-minute bars annualised with
252 is wrong by a factor of ~9, and that mistake is the single most common way a
backtest flatters itself. `bars_per_year_for` maps the engine's `Interval` to the
right constant; pass `bars_per_year` yourself if your data is irregular.
"""

from __future__ import annotations

import math
from statistics import pstdev

from pydantic import BaseModel, Field

from alpha_engine.cache.models import Interval

# Trading days per year, and the session lengths behind the intraday constants.
# 6.5h is a US cash session; NSE is 6.25h. The difference moves annualised vol
# by ~2%, which is noise next to the error of using 252 for minute bars.
TRADING_DAYS = 252
_BARS_PER_YEAR = {
    Interval.DAY: float(TRADING_DAYS),
    Interval.HOUR: TRADING_DAYS * 6.5,
    Interval.MINUTE: TRADING_DAYS * 390.0,
}


def bars_per_year_for(interval: Interval) -> float:
    """Annualisation constant for a bar interval."""
    return _BARS_PER_YEAR.get(interval, float(TRADING_DAYS))


class StrategyMetrics(BaseModel):
    """The performance report for one strategy run.

    Ratios are unitless; `*_pct` fields are percentages (12.5 = +12.5%); P&L
    fields are in account currency. `profit_factor` is None rather than
    infinity when there were no losing trades — infinity is not a number a
    reader should ever see next to a two-trade sample.
    """

    total_return_pct: float
    cagr_pct: float
    annual_volatility_pct: float
    sharpe: float
    sortino: float
    calmar: float
    max_drawdown_pct: float

    trades: int
    win_rate_pct: float
    profit_factor: float | None = Field(
        None, description="gross profit / gross loss; None when there were no losses"
    )
    expectancy: float = Field(0.0, description="mean P&L per trade, account currency")
    avg_win: float = 0.0
    avg_loss: float = 0.0
    best_trade: float = 0.0
    worst_trade: float = 0.0

    bars_per_year: float
    note: str = (
        "Backtested on historical data with no execution modelling beyond a flat "
        "transaction cost. Slippage, liquidity and fills are not simulated."
    )


def drawdown_series(equity: list[float]) -> list[float]:
    """Fractional drawdown at each bar (-0.25 = 25% below the running peak)."""
    out: list[float] = []
    peak = equity[0] if equity else 0.0
    for value in equity:
        peak = max(peak, value)
        out.append(value / peak - 1.0 if peak > 0 else 0.0)
    return out


def compute_metrics(
    equity: list[float],
    returns: list[float],
    trade_pnls: list[float],
    bars_per_year: float = float(TRADING_DAYS),
) -> tuple[StrategyMetrics, list[float]]:
    """Compute the full report. Returns `(metrics, drawdown_series)`.

    `returns` are per-bar fractional returns net of costs; `equity` is the
    resulting curve; `trade_pnls` is one realised P&L per closed trade.
    """
    dd = drawdown_series(equity)

    total_return_pct = 0.0
    cagr = 0.0
    if len(equity) > 1 and equity[0] > 0:
        growth = equity[-1] / equity[0]
        total_return_pct = (growth - 1.0) * 100.0
        years = max(len(returns) / bars_per_year, 1e-9)
        # A wiped-out account has no real CAGR; report -100% rather than a
        # complex root of a negative growth factor.
        cagr = growth ** (1.0 / years) - 1.0 if growth > 0 else -1.0

    vol = pstdev(returns) if len(returns) > 1 else 0.0
    annual_vol = vol * math.sqrt(bars_per_year)
    mean_ret = sum(returns) / len(returns) if returns else 0.0
    sharpe = (mean_ret * bars_per_year) / annual_vol if annual_vol > 0 else 0.0

    downside = [r for r in returns if r < 0]
    dstd = pstdev(downside) if len(downside) > 1 else 0.0
    annual_downside = dstd * math.sqrt(bars_per_year)
    sortino = (mean_ret * bars_per_year) / annual_downside if annual_downside > 0 else 0.0

    max_dd = min(dd) if dd else 0.0
    calmar = cagr / abs(max_dd) if max_dd < 0 else 0.0

    wins = [p for p in trade_pnls if p > 0]
    losses = [p for p in trade_pnls if p <= 0]
    n = len(trade_pnls)
    gross_loss = abs(sum(losses))

    return (
        StrategyMetrics(
            total_return_pct=round(total_return_pct, 2),
            cagr_pct=round(cagr * 100.0, 2),
            annual_volatility_pct=round(annual_vol * 100.0, 2),
            sharpe=round(sharpe, 3),
            sortino=round(sortino, 3),
            calmar=round(calmar, 3),
            max_drawdown_pct=round(max_dd * 100.0, 2),
            trades=n,
            win_rate_pct=round(len(wins) / n * 100.0, 2) if n else 0.0,
            profit_factor=round(sum(wins) / gross_loss, 3) if gross_loss > 0 else None,
            expectancy=round(sum(trade_pnls) / n, 2) if n else 0.0,
            avg_win=round(sum(wins) / len(wins), 2) if wins else 0.0,
            avg_loss=round(sum(losses) / len(losses), 2) if losses else 0.0,
            best_trade=round(max(trade_pnls), 2) if n else 0.0,
            worst_trade=round(min(trade_pnls), 2) if n else 0.0,
            bars_per_year=bars_per_year,
        ),
        dd,
    )
