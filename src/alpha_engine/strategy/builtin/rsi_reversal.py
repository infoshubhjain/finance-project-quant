"""RSI mean reversion, with a custom option-volume confirmation.

Long when RSI climbs back out of oversold, short when it falls back out of
overbought. The interesting part is `verify_on_option`: it shows how to tighten
the default confirmation rule with something only the option chart knows — here,
whether real volume backed the move.
"""

from __future__ import annotations

from alpha_engine.cache.models import Candle
from alpha_engine.strategy.base import BaseStrategy
from alpha_engine.strategy.indicators import rsi


class RSIReversal(BaseStrategy):
    name = "RSI Reversal"
    description = "Long when RSI exits oversold, short when RSI exits overbought."
    params = {"length": 14, "oversold": 30, "overbought": 70}

    def generate_signals(self, candles: list[Candle]) -> list[int]:
        values = rsi(candles, int(self.params["length"]))
        oversold = float(self.params["oversold"])
        overbought = float(self.params["overbought"])

        out = [0] * len(candles)
        for i in range(1, len(candles)):
            now, prev = values[i], values[i - 1]
            if now is None or prev is None:
                continue
            if now > oversold and prev <= oversold:
                out[i] = 1
            elif now < overbought and prev >= overbought:
                out[i] = -1
        return out

    def verify_on_option(self, option_candles: list[Candle], t: int, signal: int) -> bool:
        """The base 5-EMA check, AND option volume at or above its 10-bar mean.

        A premium moving the right way on no volume is usually a stale quote or
        a single lot, not participation.
        """
        if not super().verify_on_option(option_candles, t, signal):
            return False

        visible = option_candles[: t + 1]
        if len(visible) < 10:
            return True  # not enough option history to judge volume on
        volumes = [c.volume for c in visible[-10:] if c.volume is not None]
        if len(volumes) < 10 or visible[-1].volume is None:
            return True  # source doesn't report volume; don't veto on missing data
        return visible[-1].volume >= sum(volumes) / len(volumes)
