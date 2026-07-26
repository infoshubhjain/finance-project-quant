"""Index-aligned indicator series for strategy authors.

Every function returns a list the SAME length as the input candles, with `None`
for bars where the indicator is not yet defined (the warm-up period). That
alignment is the whole point: strategy code says `if fast[i] > slow[i]` without
ever doing offset arithmetic, which is where lookahead bugs are born.

`quant/features.py` stays the home of the analytics the engine itself uses; its
series helpers return *trimmed* lists because that suits vectorised scoring.
These are the ergonomic wrappers for hand-written strategies. EMA is not
reimplemented here — it delegates to `features.ema_series`, which remains the
single EMA implementation in the codebase.
"""

from __future__ import annotations

from alpha_engine.cache.models import Candle
from alpha_engine.quant.features import ema_series

Series = list[float | None]


def _align(values: list[float], length: int) -> Series:
    """Right-align a trimmed indicator onto a full-length bar index, padding the
    warm-up with None."""
    pad = length - len(values)
    if pad < 0:
        return list(values[-length:])
    return [None] * pad + list(values)


def sma(candles: list[Candle], length: int, field: str = "close") -> Series:
    """Simple moving average. None until `length` bars exist."""
    values = [getattr(c, field) or 0.0 for c in candles]
    out: Series = [None] * len(values)
    if length <= 0 or len(values) < length:
        return out
    window = sum(values[:length])
    out[length - 1] = window / length
    for i in range(length, len(values)):
        window += values[i] - values[i - length]
        out[i] = window / length
    return out


def ema(candles: list[Candle], length: int, field: str = "close") -> Series:
    """Exponential moving average, seeded with the SMA of the first `length`
    bars (the engine's convention, via `features.ema_series`)."""
    values = [getattr(c, field) or 0.0 for c in candles]
    return _align(ema_series(values, length), len(values))


def rsi(candles: list[Candle], length: int = 14) -> Series:
    """Wilder's RSI, 0-100. None until `length` deltas exist."""
    closes = [c.close for c in candles]
    out: Series = [None] * len(closes)
    if len(closes) <= length or length <= 0:
        return out

    gains = losses = 0.0
    for i in range(1, length + 1):
        delta = closes[i] - closes[i - 1]
        gains += max(delta, 0.0)
        losses += max(-delta, 0.0)
    avg_gain = gains / length
    avg_loss = losses / length
    out[length] = _rsi_from(avg_gain, avg_loss)

    for i in range(length + 1, len(closes)):
        delta = closes[i] - closes[i - 1]
        # Wilder smoothing: the running average decays rather than dropping a bar.
        avg_gain = (avg_gain * (length - 1) + max(delta, 0.0)) / length
        avg_loss = (avg_loss * (length - 1) + max(-delta, 0.0)) / length
        out[i] = _rsi_from(avg_gain, avg_loss)
    return out


def _rsi_from(avg_gain: float, avg_loss: float) -> float:
    # No losses in the window means maximally overbought, not a divide-by-zero.
    if avg_loss == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def atr(candles: list[Candle], length: int = 14) -> Series:
    """Average true range, Wilder-smoothed. None until `length` bars exist."""
    out: Series = [None] * len(candles)
    if len(candles) <= length or length <= 0:
        return out

    trs: list[float] = []
    for i in range(1, len(candles)):
        prev_close = candles[i - 1].close
        c = candles[i]
        trs.append(max(c.high - c.low, abs(c.high - prev_close), abs(c.low - prev_close)))

    running = sum(trs[:length]) / length
    out[length] = running
    for i in range(length, len(trs)):
        running = (running * (length - 1) + trs[i]) / length
        out[i + 1] = running
    return out


def crossed_above(fast: Series, slow: Series, i: int) -> bool:
    """True when `fast` crosses up through `slow` on bar i. Reads bars i and
    i-1 only — never a future bar."""
    if i < 1:
        return False
    a, b, pa, pb = fast[i], slow[i], fast[i - 1], slow[i - 1]
    if None in (a, b, pa, pb):
        return False
    return a > b and pa <= pb  # type: ignore[operator]


def crossed_below(fast: Series, slow: Series, i: int) -> bool:
    """True when `fast` crosses down through `slow` on bar i."""
    if i < 1:
        return False
    a, b, pa, pb = fast[i], slow[i], fast[i - 1], slow[i - 1]
    if None in (a, b, pa, pb):
        return False
    return a < b and pa >= pb  # type: ignore[operator]
