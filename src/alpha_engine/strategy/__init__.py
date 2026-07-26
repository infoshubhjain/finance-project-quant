"""User-authored strategies: the trade-level backtester.

This layer answers a different question from `validation/`. That one asks "were
the engine's signals right?" (hit rate, calibration). This one asks "if I had
actually traded this rule, what would the equity curve and Sharpe have been?"
— entries, exits, transaction costs, drawdown.

Ported from the standalone `nifty_backtester` Streamlit app, with three changes:

1. **No pandas/numpy.** The original was a DataFrame app; this runs on the
   engine's own `Candle` list, so the whole repo stays at one runtime
   dependency (pydantic).
2. **Results are pydantic models**, so a backtest serializes straight over the
   MCP server and the HTTP API with no extra glue.
3. **Loading a strategy file executes Python.** That is fine locally — it is the
   same trust level as running the repo — and it is why no HTTP route in this
   project will ever accept strategy source. See `loader.py`.

The option cross-verification idea is kept intact because it is the genuinely
novel part: a signal on the underlying does not become a trade until the option
chart confirms it.
"""

from alpha_engine.strategy.base import BaseStrategy
from alpha_engine.strategy.engine import StrategyBacktest, Trade, run_strategy_backtest
from alpha_engine.strategy.loader import discover_strategies, load_strategy
from alpha_engine.strategy.metrics import StrategyMetrics, compute_metrics

__all__ = [
    "BaseStrategy",
    "StrategyBacktest",
    "StrategyMetrics",
    "Trade",
    "compute_metrics",
    "discover_strategies",
    "load_strategy",
    "run_strategy_backtest",
]
