"""The contract every strategy implements.

Subclass `BaseStrategy`, implement `generate_signals`, and optionally override
`verify_on_option`. Drop the file into the strategies folder and the loader
finds it. That is the whole extension surface.

    from alpha_engine.strategy.base import BaseStrategy
    from alpha_engine.strategy.indicators import ema, crossed_above, crossed_below

    class MyStrategy(BaseStrategy):
        name = "My Strategy"
        params = {"fast": 9, "slow": 21}

        def generate_signals(self, candles):
            fast = ema(candles, self.params["fast"])
            slow = ema(candles, self.params["slow"])
            out = [0] * len(candles)
            for i in range(len(candles)):
                if crossed_above(fast, slow, i):
                    out[i] = 1
                elif crossed_below(fast, slow, i):
                    out[i] = -1
            return out

**The no-lookahead responsibility is yours.** The engine cannot enforce it here
the way `validation/backtest.py` does, because you are handed the whole series
at once — a `generate_signals` that reads `candles[i + 1]` will produce a
beautiful, meaningless equity curve. `run_strategy_backtest(check_lookahead=True)`
tests for it: it recomputes your signals on truncated history and flags any bar
whose signal changed once the future was removed. Leave it on.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from alpha_engine.cache.models import Candle
from alpha_engine.strategy.indicators import ema


class BaseStrategy(ABC):
    """A trading rule over one underlying, optionally confirmed on an option."""

    name: str = "BaseStrategy"
    description: str = "No description provided."
    #: Default parameters. Exposed by the loader so a UI or API caller can see
    #: what is tunable without importing the class.
    params: dict[str, Any] = {}

    def __init__(self, **overrides: Any) -> None:
        unknown = set(overrides) - set(type(self).params)
        if unknown:
            raise ValueError(
                f"{type(self).__name__} has no parameter(s): {', '.join(sorted(unknown))}. "
                f"Known: {', '.join(sorted(type(self).params)) or '(none)'}"
            )
        self.params = {**type(self).params, **overrides}

    @abstractmethod
    def generate_signals(self, candles: list[Candle]) -> list[int]:
        """Return one signal per candle, same length and order as the input:

            1  -> go long here
           -1  -> go short here
            0  -> no signal

        A signal is an *entry intent*, not a position. The engine holds the
        position until an opposite signal arrives.
        """
        raise NotImplementedError

    def verify_on_option(self, option_candles: list[Candle], t: int, signal: int) -> bool:
        """Confirm (or reject) an underlying signal using the option's own chart.

        This is the idea the rest of the engine does not have: a bullish read on
        Nifty means nothing if the call you would actually buy is bleeding. Only
        confirmed signals become trades.

        `option_candles` is already aligned to the underlying's bar index — bar
        `t` is the same moment in both series — so slicing `[: t + 1]` is the
        point-in-time view, and reading past `t` is lookahead.

        Default rule: confirm a long if the option closed at or above its 5-bar
        EMA, a short if at or below. Deliberately simple. Override it with
        volume, open-interest or IV logic for anything real.
        """
        visible = option_candles[: t + 1]
        if len(visible) < 5:
            return True  # not enough option history to reject on; don't veto blind

        ema5 = ema(visible, 5)[-1]
        if ema5 is None:
            return True
        last_close = visible[-1].close
        if signal == 1:
            return last_close >= ema5
        if signal == -1:
            return last_close <= ema5
        return True

    def describe(self) -> dict[str, Any]:
        """Serializable summary — what the API and the loader report."""
        return {
            "key": type(self).__name__,
            "name": self.name,
            "description": self.description,
            "params": dict(self.params),
            "defaults": dict(type(self).params),
        }
