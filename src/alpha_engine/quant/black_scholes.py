"""Black-Scholes-Merton European option pricing — pure-Python, deterministic.

Indian index options (NIFTY, BANKNIFTY, FINNIFTY) are European-exercise, so the
Black-Scholes closed form is exactly right — no binomial tree needed. Everything
here is a plain formula: same inputs, same output, always. That keeps it inside
the project's cardinal rule (decision-bearing numbers come from deterministic
Python) and needs no dependency — `math.erf` gives the normal CDF exactly.

Honesty note: we MODEL option prices from the underlying price plus a volatility
estimate. This is NOT a substitute for real historical option quotes. A true
market price carries an implied-vol smile, a bid/ask spread, and demand skew that
this does not. It is an honest, replayable approximation for backtesting when
tick-level option history isn't available for free. Label any output as such.
"""

from __future__ import annotations

import math

from alpha_engine.cache.models import OptionRight

_SQRT2 = math.sqrt(2.0)


def norm_cdf(x: float) -> float:
    """Standard normal cumulative distribution, exact via the error function."""
    return 0.5 * (1.0 + math.erf(x / _SQRT2))


def bs_price(
    spot: float,
    strike: float,
    t_years: float,
    vol: float,
    right: OptionRight,
    rate: float = 0.06,
) -> float:
    """Black-Scholes price of one European option.

    Args:
        spot: underlying price now.
        strike: option strike.
        t_years: time to expiry in years (e.g. 21 trading days = 21/252).
        vol: annualized volatility of the underlying (e.g. 0.20 = 20%).
        right: CALL or PUT.
        rate: annual risk-free rate (default 6%, roughly the Indian short rate).

    At or past expiry (`t_years <= 0`) or with zero vol, returns intrinsic value —
    the option is worth exactly what it would pay if exercised now.
    """
    if spot <= 0 or strike <= 0:
        return 0.0

    intrinsic = max(0.0, spot - strike) if right is OptionRight.CALL else max(0.0, strike - spot)
    if t_years <= 0 or vol <= 0:
        return intrinsic

    sqrt_t = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * vol * vol) * t_years) / (vol * sqrt_t)
    d2 = d1 - vol * sqrt_t
    disc = math.exp(-rate * t_years)

    if right is OptionRight.CALL:
        return spot * norm_cdf(d1) - strike * disc * norm_cdf(d2)
    return strike * disc * norm_cdf(-d2) - spot * norm_cdf(-d1)


def _demo() -> None:
    """Self-check: put-call parity and monotonicity must hold exactly."""
    # Put-call parity: C - P == S - K*exp(-rT), for any inputs.
    s, k, t, v, r = 100.0, 105.0, 0.25, 0.2, 0.06
    c = bs_price(s, k, t, v, OptionRight.CALL, r)
    p = bs_price(s, k, t, v, OptionRight.PUT, r)
    parity = s - k * math.exp(-r * t)
    assert abs((c - p) - parity) < 1e-9, (c - p, parity)

    # ATM with zero rate: call and put are equal.
    c0 = bs_price(100.0, 100.0, 0.25, 0.2, OptionRight.CALL, rate=0.0)
    p0 = bs_price(100.0, 100.0, 0.25, 0.2, OptionRight.PUT, rate=0.0)
    assert abs(c0 - p0) < 1e-9, (c0, p0)

    # Higher vol -> higher price (a long option likes volatility).
    lo = bs_price(100.0, 100.0, 0.25, 0.1, OptionRight.CALL)
    hi = bs_price(100.0, 100.0, 0.25, 0.4, OptionRight.CALL)
    assert hi > lo, (lo, hi)

    # At expiry, price == intrinsic.
    assert bs_price(110.0, 100.0, 0.0, 0.2, OptionRight.CALL) == 10.0
    assert bs_price(90.0, 100.0, 0.0, 0.2, OptionRight.CALL) == 0.0
    print("black_scholes self-check passed")


if __name__ == "__main__":
    _demo()
