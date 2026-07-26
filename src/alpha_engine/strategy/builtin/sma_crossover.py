"""Fast/slow moving-average crossover — the "hello world" of trading rules.

Long when the fast average crosses above the slow one, short on the reverse.
Ported from the nifty_backtester example. Read it as a template for the
`generate_signals` contract, not as a strategy worth trading.
"""

from __future__ import annotations

from alpha_engine.cache.models import Candle
from alpha_engine.strategy.base import BaseStrategy
from alpha_engine.strategy.indicators import crossed_above, crossed_below, sma


class SMACrossover(BaseStrategy):
    name = "SMA Crossover"
    description = "Long when the fast SMA crosses above the slow SMA, short on the reverse cross."
    params = {"fast_length": 9, "slow_length": 21}

    def generate_signals(self, candles: list[Candle]) -> list[int]:
        fast = sma(candles, int(self.params["fast_length"]))
        slow = sma(candles, int(self.params["slow_length"]))

        out = [0] * len(candles)
        for i in range(len(candles)):
            if crossed_above(fast, slow, i):
                out[i] = 1
            elif crossed_below(fast, slow, i):
                out[i] = -1
        return out
