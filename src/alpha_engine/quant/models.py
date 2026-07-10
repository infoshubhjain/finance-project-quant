"""Small statistical models, implemented dependency-free and deterministic.

Why hand-rolled instead of hmmlearn/arch/pykalman: the cardinal rule wants
every decision-bearing number reproducible from tested pure Python, and the
default clone must stay lightweight. These are textbook formulations sized to
this project's data (a few hundred daily bars), not research-grade fitters.

Determinism notes:
- GARCH is fit by an exhaustive grid search (no stochastic optimizer).
- The HMM uses a fixed, data-derived initialization (no random restarts).
- The Kalman filter is closed-form recursion; nothing to fit.
"""

from __future__ import annotations

import math

from pydantic import BaseModel

from alpha_engine.quant.features import _mean, _pstd, linreg

_ANNUAL = math.sqrt(252.0)
_VAR_FLOOR = 1e-12  # keeps likelihoods finite on degenerate (flat) data


# ---------------------------------------------------------------------------
# Kalman filter — local-level fair value
# ---------------------------------------------------------------------------


class KalmanResult(BaseModel):
    fair_value: float
    distance: float  # close vs fair value, as a fraction (+0.017 = +1.7%)
    slope: float  # fair-value drift over the last 5 bars, as a fraction


def kalman_fair_value(closes: list[float], q_ratio: float = 0.1) -> KalmanResult | None:
    """Local-level Kalman filter: price = hidden fair value + noise.

    R (measurement noise) is estimated from the variance of one-step price
    changes; Q (how fast fair value itself may drift) is R * q_ratio. A small
    q_ratio trusts the smooth line more; 0.1 tracks daily data closely enough
    to be a usable "fair value" while still filtering the day noise.
    """
    n = len(closes)
    if n < 20:
        return None
    diffs = [b - a for a, b in zip(closes, closes[1:])]
    r = max(_pstd(diffs) ** 2, _VAR_FLOOR)
    q = r * q_ratio
    x = closes[0]
    p = r
    filtered = [x]
    for z in closes[1:]:
        p += q
        k = p / (p + r)
        x += k * (z - x)
        p *= 1.0 - k
        filtered.append(x)
    fv = filtered[-1]
    if fv <= 0 or filtered[-6] <= 0:
        return None
    return KalmanResult(
        fair_value=fv,
        distance=closes[-1] / fv - 1.0,
        slope=fv / filtered[-6] - 1.0,
    )


# ---------------------------------------------------------------------------
# GARCH(1,1) — volatility forecast
# ---------------------------------------------------------------------------


class GarchResult(BaseModel):
    omega: float
    alpha: float  # reaction to yesterday's squared surprise
    beta: float  # persistence of yesterday's variance
    forecast_vol_daily: float  # next-bar vol as a fraction (0.021 = 2.1%)
    forecast_vol_annual: float
    log_likelihood: float


def _garch_ll(rets: list[float], omega: float, alpha: float, beta: float) -> tuple[float, float]:
    """Gaussian log-likelihood of returns under GARCH(1,1) plus the one-step
    variance forecast. Returns (ll, next_var)."""
    var = max(_mean([r * r for r in rets]), _VAR_FLOOR)
    ll = 0.0
    for r in rets:
        var = max(var, _VAR_FLOOR)
        ll += -0.5 * (math.log(2.0 * math.pi * var) + r * r / var)
        var = omega + alpha * r * r + beta * var
    return ll, max(var, _VAR_FLOOR)


def fit_garch(rets: list[float]) -> GarchResult | None:
    """Fit GARCH(1,1) by variance-targeted grid search.

    Variance targeting pins omega = long_run_var * (1 - alpha - beta), so the
    grid only spans (alpha, beta). ~600 likelihood evaluations on daily-sized
    data is instant, fully deterministic, and accurate enough for a report
    metric.
    # ponytail: grid fit; swap in MLE via scipy if forecast precision starts to matter
    """
    if len(rets) < 40:
        return None
    demeaned = [r - _mean(rets) for r in rets]
    long_var = max(_mean([r * r for r in demeaned]), _VAR_FLOOR)

    best: tuple[float, float, float, float, float] | None = None  # ll, o, a, b, next_var
    for ai in range(1, 16):  # alpha 0.02 .. 0.30
        alpha = ai * 0.02
        for bi in range(60, 99):  # beta 0.60 .. 0.98
            beta = bi * 0.01
            if alpha + beta >= 0.999:
                continue
            omega = long_var * (1.0 - alpha - beta)
            ll, next_var = _garch_ll(demeaned, omega, alpha, beta)
            if best is None or ll > best[0]:
                best = (ll, omega, alpha, beta, next_var)
    if best is None:
        return None
    ll, omega, alpha, beta, next_var = best
    vol = math.sqrt(next_var)
    return GarchResult(
        omega=omega,
        alpha=alpha,
        beta=beta,
        forecast_vol_daily=vol,
        forecast_vol_annual=vol * _ANNUAL,
        log_likelihood=ll,
    )


# ---------------------------------------------------------------------------
# 2-state Gaussian HMM — regime probability
# ---------------------------------------------------------------------------


class HmmResult(BaseModel):
    bull_prob: float  # P(recently in the higher-mean state), smoothed
    bull_mean: float
    bear_mean: float
    iterations: int


def fit_hmm(rets: list[float], max_iter: int = 100, tol: float = 1e-6) -> HmmResult | None:
    """2-state Gaussian HMM on returns, fit with Baum-Welch (EM).

    Initialization is deterministic: state means seeded from the mean of the
    below-median and above-median returns, sticky transitions (0.9 stay). The
    output is the filtered probability of the higher-mean ("bull") state
    averaged over the last 5 bars — a single bar's posterior whipsaws with
    every dip, and a regime read should not.
    """
    n = len(rets)
    if n < 40:
        return None
    srt = sorted(rets)
    mu = [_mean(srt[: n // 2]), _mean(srt[n // 2 :])]
    overall_var = max(_pstd(rets) ** 2, _VAR_FLOOR)
    var = [overall_var, overall_var]
    trans = [[0.9, 0.1], [0.1, 0.9]]
    pi = [0.5, 0.5]

    def dens(r: float, s: int) -> float:
        v = max(var[s], _VAR_FLOOR)
        return math.exp(-0.5 * (r - mu[s]) ** 2 / v) / math.sqrt(2.0 * math.pi * v)

    prev_ll = -math.inf
    iterations = 0
    alphas_final: list[list[float]] = [[0.5, 0.5]]
    for it in range(max_iter):
        iterations = it + 1
        # forward pass, scaled so probabilities never underflow
        alphas: list[list[float]] = []
        scales: list[float] = []
        a = [pi[s] * dens(rets[0], s) for s in (0, 1)]
        c = sum(a) or _VAR_FLOOR
        alphas.append([x / c for x in a])
        scales.append(c)
        for t in range(1, n):
            a = [
                dens(rets[t], s) * (alphas[-1][0] * trans[0][s] + alphas[-1][1] * trans[1][s])
                for s in (0, 1)
            ]
            c = sum(a) or _VAR_FLOOR
            alphas.append([x / c for x in a])
            scales.append(c)
        ll = sum(math.log(c) for c in scales)

        # backward pass
        betas = [[1.0, 1.0] for _ in range(n)]
        for t in range(n - 2, -1, -1):
            for s in (0, 1):
                betas[t][s] = sum(
                    trans[s][j] * dens(rets[t + 1], j) * betas[t + 1][j] for j in (0, 1)
                ) / (scales[t + 1] or _VAR_FLOOR)

        # E-step: state and transition responsibilities
        gamma = []
        for t in range(n):
            g = [alphas[t][s] * betas[t][s] for s in (0, 1)]
            tot = sum(g) or _VAR_FLOOR
            gamma.append([x / tot for x in g])
        xi_num = [[0.0, 0.0], [0.0, 0.0]]
        for t in range(n - 1):
            denom = scales[t + 1] or _VAR_FLOOR
            for s in (0, 1):
                for j in (0, 1):
                    xi_num[s][j] += (
                        alphas[t][s] * trans[s][j] * dens(rets[t + 1], j) * betas[t + 1][j] / denom
                    )

        # M-step
        pi = gamma[0][:]
        for s in (0, 1):
            occup = sum(gamma[t][s] for t in range(n - 1)) or _VAR_FLOOR
            for j in (0, 1):
                trans[s][j] = xi_num[s][j] / occup
            row = sum(trans[s]) or _VAR_FLOOR
            trans[s] = [x / row for x in trans[s]]
            weight = sum(gamma[t][s] for t in range(n)) or _VAR_FLOOR
            mu[s] = sum(gamma[t][s] * rets[t] for t in range(n)) / weight
            var[s] = max(
                sum(gamma[t][s] * (rets[t] - mu[s]) ** 2 for t in range(n)) / weight,
                _VAR_FLOOR,
            )

        alphas_final = alphas
        if abs(ll - prev_ll) < tol:
            break
        prev_ll = ll

    bull_state = 0 if mu[0] >= mu[1] else 1
    recent = alphas_final[-5:]
    return HmmResult(
        bull_prob=_mean([a[bull_state] for a in recent]),
        bull_mean=mu[bull_state],
        bear_mean=mu[1 - bull_state],
        iterations=iterations,
    )


# ---------------------------------------------------------------------------
# rolling regression — trend strength and stability
# ---------------------------------------------------------------------------


class TrendStrength(BaseModel):
    slope: float  # % per bar, from the latest window
    r2: float  # 0..1, how line-like the latest window is
    stability: float  # 0..1, how consistent the slope sign was across windows


def rolling_trend_strength(
    closes: list[float], window: int = 20, samples: int = 5
) -> TrendStrength | None:
    """Fit a regression line on each of the last `samples` overlapping windows
    (stepped one bar apart). The latest fit gives slope/R²; sign agreement
    across fits gives a stability score — a trend that keeps flipping sign is
    weak no matter how steep today's line is."""
    if len(closes) < window + samples - 1:
        return None
    fits = []
    for k in range(samples):
        end = len(closes) - (samples - 1 - k)
        fit = linreg(closes[end - window : end])
        if fit is None:
            return None
        fits.append(fit)
    slope, _, r2 = fits[-1]
    mean_p = _mean(closes[-window:])
    if mean_p <= 0:
        return None
    dominant = 1.0 if slope >= 0 else -1.0
    agree = sum(1 for s, _, _ in fits if (s >= 0) == (dominant > 0))
    return TrendStrength(slope=slope / mean_p, r2=r2, stability=agree / samples)
