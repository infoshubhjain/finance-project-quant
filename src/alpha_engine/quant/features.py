"""The feature library: ~50 deterministic statistics computed from a candle
series. Each feature is a plain number describing one aspect of recent price
action (returns, trend, stretch, volatility, ranges, volume, candle shape,
statistical structure).

Design rules:
- Pure functions of the candles. No network, no randomness, no state.
- A feature that cannot be computed (not enough history, missing volume,
  degenerate prices) is None, never a made-up zero. Consumers must handle None.
- Interval-agnostic: the same functions work on daily or intraday candles,
  which is why the "intraday alpha factors" set is just a named subset
  (INTRADAY_FACTORS) rather than separate code.

Windows are bar counts, so "20" means 20 bars of whatever interval the series
holds.
"""

from __future__ import annotations

import math
from statistics import median

from alpha_engine.cache.models import Candle, PriceSeries

# Annualization assumes daily bars and a 252-session year. For intraday bars
# the absolute level is off but rankings/percentiles remain meaningful.
_ANNUAL = math.sqrt(252.0)


# ---------------------------------------------------------------------------
# small numeric helpers
# ---------------------------------------------------------------------------


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def _pstd(xs: list[float]) -> float:
    """Population standard deviation (divide by n, not n-1)."""
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))


def _svar(xs: list[float]) -> float:
    """Sample variance (n-1), used where the estimator is defined that way."""
    n = len(xs)
    if n < 2:
        return 0.0
    m = _mean(xs)
    return sum((x - m) ** 2 for x in xs) / (n - 1)


def log_returns(closes: list[float]) -> list[float]:
    """Log returns ln(p_t / p_{t-1}); log so multi-day returns add up."""
    out: list[float] = []
    for a, b in zip(closes, closes[1:]):
        if a > 0 and b > 0:
            out.append(math.log(b / a))
    return out


def linreg(ys: list[float]) -> tuple[float, float, float] | None:
    """Least-squares line over the sequence index. Returns (slope, intercept,
    r2). r2 says how straight the move was; slope says how steep."""
    n = len(ys)
    if n < 3:
        return None
    xs = list(range(n))
    mx, my = _mean([float(x) for x in xs]), _mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    intercept = my - slope * mx
    ss_tot = sum((y - my) ** 2 for y in ys)
    if ss_tot == 0:
        return slope, intercept, 0.0
    ss_res = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    r2 = max(0.0, 1.0 - ss_res / ss_tot)
    return slope, intercept, r2


def _corr(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    sx, sy = _pstd(xs), _pstd(ys)
    if sx == 0 or sy == 0:
        return None
    mx, my = _mean(xs), _mean(ys)
    cov = _mean([(x - mx) * (y - my) for x, y in zip(xs, ys)])
    return max(-1.0, min(1.0, cov / (sx * sy)))


def _pct_rank(x: float, xs: list[float]) -> float:
    """Share of values <= x. 1.0 means x is the largest seen."""
    if not xs:
        return 0.5
    return sum(1 for v in xs if v <= x) / len(xs)


def _vwap_of(candles: list[Candle]) -> float | None:
    """Volume-weighted average price of a candle slice. None when volume is
    missing anywhere (a zero-volume VWAP is undefined, not zero)."""
    num = den = 0.0
    for c in candles:
        if c.volume is None:
            return None
        num += (c.high + c.low + c.close) / 3.0 * c.volume
        den += c.volume
    return num / den if den > 0 else None


def _true_ranges(candles: list[Candle]) -> list[float]:
    trs: list[float] = []
    for prev, c in zip(candles, candles[1:]):
        trs.append(max(c.high - c.low, abs(c.high - prev.close), abs(c.low - prev.close)))
    return trs


def ema_series(values: list[float], period: int) -> list[float]:
    """Standard EMA seeded with the SMA of the first `period` values. The
    single EMA implementation in the codebase; analyzers import it too."""
    if len(values) < period:
        return []
    k = 2.0 / (period + 1.0)
    out = [sum(values[:period]) / period]
    for v in values[period:]:
        out.append(v * k + out[-1] * (1.0 - k))
    return out


def _slope_pct(values: list[float], lookback: int = 5) -> float | None:
    """Percent change of a line over the last `lookback` steps — a cheap,
    readable slope measure for EMAs and VWAP."""
    if len(values) <= lookback or values[-1 - lookback] == 0:
        return None
    return values[-1] / values[-1 - lookback] - 1.0


# ---------------------------------------------------------------------------
# volatility estimators (each uses a different part of the candle, which is
# why several exist: close-to-close wastes the intrabar information)
# ---------------------------------------------------------------------------


def _parkinson(candles: list[Candle]) -> float | None:
    terms = []
    for c in candles:
        if c.low <= 0 or c.high < c.low:
            return None
        terms.append(math.log(c.high / c.low) ** 2)
    # all-zero ranges mean the source has no real high/low (synthesized bars):
    # that's "unknown", not "zero volatility"
    if not terms or sum(terms) <= 0:
        return None
    return math.sqrt(_mean(terms) / (4.0 * math.log(2.0))) * _ANNUAL


def _garman_klass(candles: list[Candle]) -> float | None:
    terms = []
    for c in candles:
        if c.low <= 0 or c.open <= 0 or c.high < c.low:
            return None
        hl = math.log(c.high / c.low) ** 2
        co = math.log(c.close / c.open) ** 2
        terms.append(0.5 * hl - (2.0 * math.log(2.0) - 1.0) * co)
    if not terms:
        return None
    m = _mean(terms)
    if m <= 0:  # degenerate/synthesized bars: unknown, not zero vol
        return None
    return math.sqrt(m) * _ANNUAL


def _yang_zhang(candles: list[Candle]) -> float | None:
    """Yang-Zhang combines overnight gaps, open-to-close moves and the
    Rogers-Satchell range term; it is the most complete of the range-based
    estimators and robust to both drift and gaps."""
    n = len(candles) - 1
    if n < 2:
        return None
    overnight, openclose, rs_terms = [], [], []
    for prev, c in zip(candles, candles[1:]):
        if min(prev.close, c.open, c.close, c.high, c.low) <= 0:
            return None
        overnight.append(math.log(c.open / prev.close))
        openclose.append(math.log(c.close / c.open))
        rs_terms.append(
            math.log(c.high / c.close) * math.log(c.high / c.open)
            + math.log(c.low / c.close) * math.log(c.low / c.open)
        )
    k = 0.34 / (1.34 + (n + 1) / (n - 1))
    yz_var = _svar(overnight) + k * _svar(openclose) + (1.0 - k) * _mean(rs_terms)
    if yz_var < 0:
        return None
    return math.sqrt(yz_var) * _ANNUAL


def _ewma_vol(rets: list[float], lam: float = 0.94) -> float | None:
    """RiskMetrics-style exponentially weighted vol: recent squared returns
    count more. Seeded with the plain variance so it is deterministic."""
    if len(rets) < 10:
        return None
    var = _mean([r * r for r in rets[:10]])
    for r in rets[10:]:
        var = lam * var + (1.0 - lam) * r * r
    return math.sqrt(var) * _ANNUAL


def realized_vol(rets: list[float]) -> float | None:
    if len(rets) < 2:
        return None
    return _pstd(rets) * _ANNUAL


# ---------------------------------------------------------------------------
# statistical structure
# ---------------------------------------------------------------------------


def hurst_exponent(closes: list[float], max_points: int = 64) -> float | None:
    """Variance-scaling Hurst estimate on log prices. H > 0.5 suggests moves
    tend to continue (trending), H < 0.5 suggests they tend to reverse.

    We regress log(std of lag-k differences) on log(k) for k in 2,4,8,16 —
    the slope is H. Crude but deterministic and dependency-free."""
    logs = [math.log(c) for c in closes[-max_points:] if c > 0]
    if len(logs) < 32:
        return None
    pts: list[tuple[float, float]] = []
    for lag in (2, 4, 8, 16):
        diffs = [logs[i + lag] - logs[i] for i in range(len(logs) - lag)]
        s = _pstd(diffs)
        if s <= 0:
            return None
        pts.append((math.log(lag), math.log(s)))
    # lag spacing is not uniform, so regress on the actual log-lags:
    xs = [x for x, _ in pts]
    ys = [y for _, y in pts]
    mx, my = _mean(xs), _mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    h = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return max(0.0, min(1.0, h))


def shannon_entropy(rets: list[float], bins: int = 5) -> float | None:
    """Entropy of the return histogram, normalized to [0, 1]. High = returns
    spread across many sizes (noisy); low = concentrated (structured)."""
    if len(rets) < bins:
        return None
    lo, hi = min(rets), max(rets)
    if hi == lo:
        return 0.0
    counts = [0] * bins
    for r in rets:
        idx = min(int((r - lo) / (hi - lo) * bins), bins - 1)
        counts[idx] += 1
    n = len(rets)
    ent = -sum((c / n) * math.log(c / n) for c in counts if c > 0)
    return ent / math.log(bins)


def max_drawdown(closes: list[float]) -> float | None:
    """Worst peak-to-trough drop, as a negative fraction (-0.25 = -25%)."""
    if len(closes) < 2:
        return None
    peak = closes[0]
    worst = 0.0
    for c in closes:
        peak = max(peak, c)
        if peak > 0:
            worst = min(worst, c / peak - 1.0)
    return worst


def half_life(closes: list[float]) -> float | None:
    """Half-life of mean reversion in bars, from an AR(1) fit of price changes
    on lagged price (Ornstein-Uhlenbeck discretization). None when price shows
    no pull back toward a mean (b >= 0), i.e. reversion is undefined."""
    if len(closes) < 20:
        return None
    lagged = closes[:-1]
    deltas = [b - a for a, b in zip(closes, closes[1:])]
    ml = _mean(lagged)
    sxx = sum((x - ml) ** 2 for x in lagged)
    if sxx == 0:
        return None
    md = _mean(deltas)
    b = sum((x - ml) * (d - md) for x, d in zip(lagged, deltas)) / sxx
    if b >= 0 or b <= -1:
        return None
    return -math.log(2.0) / math.log(1.0 + b)


# ---------------------------------------------------------------------------
# the feature table
# ---------------------------------------------------------------------------

FEATURE_GROUPS: dict[str, list[str]] = {
    "return_dynamics": [
        "log_return_1d",
        "cum_return_5d",
        "cum_return_20d",
        "rolling_mean_return",
        "return_acceleration",
        "return_asymmetry",
    ],
    "trend": [
        "lr_slope",
        "lr_r2",
        "ema20_slope",
        "ema50_slope",
        "dist_ema20",
        "dist_ema50",
        "kaufman_er",
        "trend_persistence",
    ],
    "mean_reversion": [
        "z_score",
        "dist_median",
        "dist_vwap",
        "bollinger_pct_b",
        "mr_half_life",
        "price_percentile",
    ],
    "volatility": [
        "atr_pct",
        "realized_vol",
        "parkinson_vol",
        "garman_klass_vol",
        "yang_zhang_vol",
        "ewma_vol",
        "vol_of_vol",
        "vol_percentile",
    ],
    "range_expansion": [
        "range_percentile",
        "donchian_width",
        "bb_width",
        "keltner_width",
        "atr_breakout_ratio",
        "compression_score",
    ],
    "volume_liquidity": [
        "relative_volume",
        "volume_z",
        "volume_trend",
        "volume_ema_ratio",
        "obv_slope",
        "price_volume_corr",
    ],
    "candle_structure": [
        "body_pct",
        "upper_wick_pct",
        "lower_wick_pct",
        "clv",
        "gap_pct",
    ],
    "statistical": [
        "hurst",
        "autocorr_lag1",
        "skewness",
        "entropy",
        "max_drawdown",
    ],
    "extra": ["vwap_slope"],
}

# The requested intraday alpha-factor set. Same functions, intraday candles.
INTRADAY_FACTORS: list[str] = [
    "dist_vwap",
    "vwap_slope",
    "ema20_slope",
    "lr_slope",
    "lr_r2",
    "kaufman_er",
    "trend_persistence",
    "atr_pct",
    "realized_vol",
    "vol_percentile",
    "bb_width",
    "relative_volume",
    "volume_z",
    "price_volume_corr",
    "body_pct",
    "clv",
    "hurst",
    "autocorr_lag1",
    "entropy",
    "range_percentile",
]


def compute_features(series: PriceSeries, window: int = 20) -> dict[str, float | None]:
    """Compute the full feature table for the most recent bar.

    `window` is the default lookback (20 bars ≈ one trading month on dailies).
    Reliable output wants >= 60 bars; anything a feature can't support is None.
    """
    candles = series.candles
    closes = [c.close for c in candles]
    n = len(closes)
    rets = log_returns(closes)
    f: dict[str, float | None] = {k: None for keys in FEATURE_GROUPS.values() for k in keys}
    if n < 2:
        return f

    last = candles[-1]
    c_now = last.close
    w_rets = rets[-window:]

    # --- 1. return dynamics ---
    f["log_return_1d"] = rets[-1] if rets else None
    if n > 5 and closes[-6] > 0:
        f["cum_return_5d"] = c_now / closes[-6] - 1.0
    if n > window and closes[-window - 1] > 0:
        f["cum_return_20d"] = c_now / closes[-window - 1] - 1.0
    if len(w_rets) >= window:
        f["rolling_mean_return"] = _mean(w_rets)
    if len(rets) >= 10:
        f["return_acceleration"] = _mean(rets[-5:]) - _mean(rets[-10:-5])
    ups = [r for r in w_rets if r > 0]
    downs = [r for r in w_rets if r < 0]
    if ups and downs:
        f["return_asymmetry"] = _mean(ups) / abs(_mean(downs))

    # --- 2. trend ---
    w_closes = closes[-window:]
    fit = linreg(w_closes) if len(w_closes) >= window else None
    if fit is not None:
        slope, _, r2 = fit
        mean_p = _mean(w_closes)
        f["lr_slope"] = slope / mean_p if mean_p > 0 else None  # % per bar
        f["lr_r2"] = r2
    e20 = ema_series(closes, 20)
    e50 = ema_series(closes, 50)
    f["ema20_slope"] = _slope_pct(e20)
    f["ema50_slope"] = _slope_pct(e50)
    if e20 and e20[-1] > 0:
        f["dist_ema20"] = c_now / e20[-1] - 1.0
    if e50 and e50[-1] > 0:
        f["dist_ema50"] = c_now / e50[-1] - 1.0
    if n > 10:
        net = abs(c_now - closes[-11])
        path = sum(abs(b - a) for a, b in zip(closes[-11:], closes[-10:]))
        f["kaufman_er"] = net / path if path > 0 else None
    if len(w_rets) >= window:
        net_sign = 1.0 if sum(w_rets) > 0 else -1.0 if sum(w_rets) < 0 else 0.0
        if net_sign == 0.0:
            f["trend_persistence"] = 0.5
        else:
            agree = sum(1 for r in w_rets if (r > 0) == (net_sign > 0))
            f["trend_persistence"] = agree / len(w_rets)

    # --- 3. mean reversion ---
    if len(w_closes) >= window:
        m, s = _mean(w_closes), _pstd(w_closes)
        f["z_score"] = (c_now - m) / s if s > 0 else 0.0
        med = median(w_closes)
        f["dist_median"] = c_now / med - 1.0 if med > 0 else None
        lower = m - 2.0 * s
        upper = m + 2.0 * s
        f["bollinger_pct_b"] = 0.5 if upper == lower else (c_now - lower) / (upper - lower)
        f["bb_width"] = (upper - lower) / m if m > 0 else None
    vwap_now = _vwap_of(candles[-window:]) if n >= window else None
    if vwap_now:
        f["dist_vwap"] = c_now / vwap_now - 1.0
    if n >= window + 5:
        vwap_prev = _vwap_of(candles[-window - 5 : -5])
        if vwap_now and vwap_prev:
            f["vwap_slope"] = vwap_now / vwap_prev - 1.0
    f["mr_half_life"] = half_life(closes[-60:])
    if n >= 60:
        f["price_percentile"] = _pct_rank(c_now, closes[-60:])

    # --- 4. volatility ---
    trs = _true_ranges(candles)
    if len(trs) >= 14 and c_now > 0:
        f["atr_pct"] = _mean(trs[-14:]) / c_now
    f["realized_vol"] = realized_vol(w_rets) if len(w_rets) >= window else None
    if n >= window:
        f["parkinson_vol"] = _parkinson(candles[-window:])
        f["garman_klass_vol"] = _garman_klass(candles[-window:])
    if n >= window + 1:
        f["yang_zhang_vol"] = _yang_zhang(candles[-window - 1 :])
    f["ewma_vol"] = _ewma_vol(rets)
    if len(rets) >= window + 10:
        rolling_10 = [
            _pstd(rets[i - 10 : i]) * _ANNUAL for i in range(len(rets) - window + 1, len(rets) + 1)
        ]
        f["vol_of_vol"] = _pstd(rolling_10)
    if len(rets) >= 2 * window:
        history = [_pstd(rets[i - window : i]) * _ANNUAL for i in range(window, len(rets) + 1)]
        f["vol_percentile"] = _pct_rank(history[-1], history)

    # --- 5. range expansion ---
    ranges = [(c.high - c.low) / c.close for c in candles if c.close > 0]
    if len(ranges) >= 60:
        f["range_percentile"] = _pct_rank(ranges[-1], ranges[-60:])
    if n >= window and c_now > 0:
        hi = max(c.high for c in candles[-window:])
        lo = min(c.low for c in candles[-window:])
        f["donchian_width"] = (hi - lo) / c_now
    if len(trs) >= 14:
        atr14 = _mean(trs[-14:])
        f["atr_breakout_ratio"] = trs[-1] / atr14 if atr14 > 0 else None
    if len(trs) >= 10 and e20 and e20[-1] > 0:
        f["keltner_width"] = 4.0 * _mean(trs[-10:]) / e20[-1]
    if len(trs) >= window:
        avg5 = _mean(trs[-5:])
        avg20 = _mean(trs[-window:])
        # positive = recent ranges tighter than the month (compression),
        # negative = expansion
        f["compression_score"] = 1.0 - avg5 / avg20 if avg20 > 0 else None

    # --- 6. volume & liquidity ---
    vols = [c.volume for c in candles]
    if all(v is not None for v in vols) and n >= window:
        v = [float(x) for x in vols if x is not None]
        v_now = v[-1]
        vm = _mean(v[-window:])
        vs = _pstd(v[-window:])
        if vm > 0:
            f["relative_volume"] = v_now / vm
            f["volume_trend"] = _mean(v[-5:]) / vm
        f["volume_z"] = (v_now - vm) / vs if vs > 0 else 0.0
        ve = ema_series(v, 20)
        if ve and ve[-1] > 0:
            f["volume_ema_ratio"] = v_now / ve[-1]
        obv = []
        acc = 0.0
        for i in range(1, n):
            if closes[i] > closes[i - 1]:
                acc += v[i]
            elif closes[i] < closes[i - 1]:
                acc -= v[i]
            obv.append(acc)
        if len(obv) >= window and vm > 0:
            obv_fit = linreg(obv[-window:])
            if obv_fit is not None:
                f["obv_slope"] = obv_fit[0] / vm  # in "average days of volume" per bar
        # pairing returns with volumes assumes no return was dropped for a
        # non-positive price; a shifted pairing would be silently wrong
        if len(w_rets) >= window and len(rets) == n - 1:
            f["price_volume_corr"] = _corr(w_rets, v[-len(w_rets) :])

    # --- 7. candle structure (the most recent bar) ---
    full = last.high - last.low
    if full > 0:
        f["body_pct"] = abs(last.close - last.open) / full
        f["upper_wick_pct"] = (last.high - max(last.open, last.close)) / full
        f["lower_wick_pct"] = (min(last.open, last.close) - last.low) / full
        f["clv"] = ((last.close - last.low) - (last.high - last.close)) / full
    if n >= 2 and candles[-2].close > 0:
        f["gap_pct"] = last.open / candles[-2].close - 1.0

    # --- 8. statistical properties ---
    f["hurst"] = hurst_exponent(closes)
    if len(rets) >= window + 1:
        f["autocorr_lag1"] = _corr(rets[-window - 1 : -1], rets[-window:])
    if len(w_rets) >= window:
        s = _pstd(w_rets)
        if s > 0:
            m = _mean(w_rets)
            f["skewness"] = _mean([((r - m) / s) ** 3 for r in w_rets])
        else:
            f["skewness"] = 0.0
    f["entropy"] = shannon_entropy(w_rets) if len(w_rets) >= window else None
    f["max_drawdown"] = max_drawdown(closes[-60:])

    return f
