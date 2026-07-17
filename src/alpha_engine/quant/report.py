"""Turns the feature table + models into the scored report:

    Regime: Trending Bull (78%)
    Trend Score: 82/100 ... Overall Asset Score: 79/100

Every score here is a documented heuristic blend of deterministic features —
transparent scaffolding, not proven alpha (see context.md: honesty over hype).
The 0-100 scales read as: 50 = neutral/no evidence, 100 = strongly bullish
evidence, 0 = strongly bearish evidence. Volume is the exception: it measures
conviction (0 = no participation), not direction.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from alpha_engine.analyzers.rsi import _rsi
from alpha_engine.cache.models import PriceSeries
from alpha_engine.quant.features import (
    _mean,
    _true_ranges,
    _vwap_of,
    compute_features,
    ema_series,
    log_returns,
)
from alpha_engine.quant.models import (
    GarchResult,
    HmmResult,
    KalmanResult,
    TrendStrength,
    fit_garch,
    fit_hmm,
    kalman_fair_value,
    rolling_trend_strength,
)

MIN_BARS = 60

DISCLAIMER = "Research output only, not investment advice."


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


# ---------------------------------------------------------------------------
# indicators not already covered by the feature table
# ---------------------------------------------------------------------------


def adx(series: PriceSeries, period: int = 14) -> float | None:
    """Average Directional Index (Wilder). 0-100; above ~25 usually reads as
    'a trend exists' regardless of direction."""
    candles = series.candles
    if len(candles) < 2 * period + 1:
        return None
    plus_dm, minus_dm, trs = [], [], []
    for prev, c in zip(candles, candles[1:]):
        up = c.high - prev.high
        down = prev.low - c.low
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        trs.append(max(c.high - c.low, abs(c.high - prev.close), abs(c.low - prev.close)))

    def wilder(vals: list[float]) -> list[float]:
        out = [sum(vals[:period]) / period]
        for v in vals[period:]:
            out.append(out[-1] + (v - out[-1]) / period)
        return out

    sm_tr = wilder(trs)
    sm_plus = wilder(plus_dm)
    sm_minus = wilder(minus_dm)
    dxs = []
    for tr, p, m in zip(sm_tr, sm_plus, sm_minus):
        if tr <= 0:
            return None
        di_p, di_m = 100.0 * p / tr, 100.0 * m / tr
        total = di_p + di_m
        dxs.append(100.0 * abs(di_p - di_m) / total if total > 0 else 0.0)
    if len(dxs) < period:
        return None
    return wilder(dxs)[-1]


def volume_profile(
    series: PriceSeries, lookback: int = 60, bins: int = 10
) -> dict[str, float | list[float]] | None:
    """Where the volume traded, by price. Returns the point of control (the
    single busiest price level) and the top-3 levels — crude support/
    resistance zones built from participation, not chart patterns."""
    candles = series.candles[-lookback:]
    if len(candles) < 20 or any(c.volume is None for c in candles):
        return None
    typicals = [(c.high + c.low + c.close) / 3.0 for c in candles]
    lo, hi = min(typicals), max(typicals)
    if hi <= lo:
        return None
    buckets = [0.0] * bins
    for t, c in zip(typicals, candles):
        idx = min(int((t - lo) / (hi - lo) * bins), bins - 1)
        buckets[idx] += float(c.volume or 0.0)
    centers = [lo + (i + 0.5) * (hi - lo) / bins for i in range(bins)]
    ranked = sorted(zip(buckets, centers), reverse=True)
    return {"poc": ranked[0][1], "levels": [c for _, c in ranked[:3]]}


def compute_indicators(series: PriceSeries) -> dict[str, float | None]:
    """The classic chart indicators, as raw values for the report."""
    closes = series.closes()
    c_now = closes[-1]
    e20 = ema_series(closes, 20)
    e50 = ema_series(closes, 50)
    e200 = ema_series(closes, 200)
    trs = _true_ranges(series.candles)
    out: dict[str, float | None] = {
        "close": c_now,
        "ema20": e20[-1] if e20 else None,
        "ema50": e50[-1] if e50 else None,
        "ema200": e200[-1] if e200 else None,
        "vwap20": _vwap_of(series.candles[-20:]) if len(closes) >= 20 else None,
        "atr14": _mean(trs[-14:]) if len(trs) >= 14 else None,
        "rsi14": _rsi(closes, 14),
        "adx14": adx(series, 14),
    }
    if len(closes) >= 20:
        window = closes[-20:]
        mid = _mean(window)
        sd = (sum((x - mid) ** 2 for x in window) / len(window)) ** 0.5
        out["bb_lower"], out["bb_mid"], out["bb_upper"] = mid - 2 * sd, mid, mid + 2 * sd
    else:
        out["bb_lower"] = out["bb_mid"] = out["bb_upper"] = None
    if e20 and len(trs) >= 10:
        atr10 = _mean(trs[-10:])
        out["keltner_lower"] = e20[-1] - 2 * atr10
        out["keltner_mid"] = e20[-1]
        out["keltner_upper"] = e20[-1] + 2 * atr10
    else:
        out["keltner_lower"] = out["keltner_mid"] = out["keltner_upper"] = None
    return out


# ---------------------------------------------------------------------------
# scores
# ---------------------------------------------------------------------------


def _score_from(components: list[float | None]) -> int | None:
    """Average the signed components (-1..+1 each) into a 0-100 score.
    Missing components are simply left out rather than faked as neutral."""
    present = [c for c in components if c is not None]
    if not present:
        return None
    return round(_clamp(50.0 + 50.0 * _mean(present), 0.0, 100.0))


def _trend_sign(f: dict[str, float | None]) -> float:
    cum = f.get("cum_return_20d")
    if cum is None or cum == 0:
        return 0.0
    return 1.0 if cum > 0 else -1.0


def trend_score(f: dict[str, float | None], strength: TrendStrength | None) -> int | None:
    sign = _trend_sign(f)
    comps: list[float | None] = []
    if f["lr_slope"] is not None and f["lr_r2"] is not None:
        # steepness capped at ±1, discounted by how line-like the move was
        comps.append(_clamp(f["lr_slope"] * 300.0) * f["lr_r2"])
    if f["ema20_slope"] is not None:
        comps.append(_clamp(f["ema20_slope"] * 40.0))
    if f["ema50_slope"] is not None:
        comps.append(_clamp(f["ema50_slope"] * 40.0))
    if f["dist_ema50"] is not None:
        comps.append(_clamp(f["dist_ema50"] * 10.0))
    if f["kaufman_er"] is not None and sign != 0:
        comps.append(f["kaufman_er"] * sign)
    if f["trend_persistence"] is not None and sign != 0:
        comps.append((f["trend_persistence"] - 0.5) * 2.0 * sign)
    if strength is not None:
        comps.append((strength.stability - 0.5) * 2.0 * (1.0 if strength.slope >= 0 else -1.0))
    return _score_from(comps)


def momentum_score(f: dict[str, float | None], rsi14: float | None) -> int | None:
    comps: list[float | None] = []
    if rsi14 is not None:
        comps.append(_clamp((rsi14 - 50.0) / 30.0))
    if f["cum_return_5d"] is not None:
        comps.append(_clamp(f["cum_return_5d"] * 15.0))
    if f["cum_return_20d"] is not None:
        comps.append(_clamp(f["cum_return_20d"] * 5.0))
    if f["return_acceleration"] is not None:
        comps.append(_clamp(f["return_acceleration"] * 200.0))
    return _score_from(comps)


def volume_score(f: dict[str, float | None]) -> int | None:
    """Participation/conviction, 0-100 (not directional). Components are
    scaled 0..1 then averaged, so a missing volume feed yields None."""
    sign = _trend_sign(f)
    comps: list[float] = []
    if f["relative_volume"] is not None:
        comps.append(_clamp((f["relative_volume"] - 0.5) / 1.5, 0.0, 1.0))
    if f["obv_slope"] is not None and sign != 0:
        comps.append(_clamp(f["obv_slope"] * sign * 10.0 + 0.5, 0.0, 1.0))
    if f["price_volume_corr"] is not None and sign != 0:
        comps.append((f["price_volume_corr"] * sign + 1.0) / 2.0)
    if not comps:
        return None
    return round(_clamp(100.0 * _mean(comps), 0.0, 100.0))


def classify_regime(
    f: dict[str, float | None], hmm: HmmResult | None, adx14: float | None
) -> tuple[str, float, float, float]:
    """Returns (label, confidence 0-1, trending_prob, bull_prob).

    Trending-vs-ranging comes from structure features (R², efficiency ratio,
    ADX). Bull-vs-bear blends the HMM state posterior 50/50 with the 20-bar
    drift: the HMM reacts to the freshest returns while the drift anchors the
    read, so one down week inside an uptrend doesn't flip the regime call.
    """
    t_comps: list[float] = []
    if f["lr_r2"] is not None:
        t_comps.append(f["lr_r2"])
    if f["kaufman_er"] is not None:
        t_comps.append(f["kaufman_er"])
    if adx14 is not None:
        t_comps.append(_clamp((adx14 - 15.0) / 25.0, 0.0, 1.0))
    trending = _mean(t_comps) if t_comps else 0.5

    cum = f["cum_return_20d"]
    tilt = 0.5 + _clamp(cum * 5.0, -0.45, 0.45) if cum is not None else None
    if hmm is not None and tilt is not None:
        bull = 0.5 * hmm.bull_prob + 0.5 * tilt
    elif hmm is not None:
        bull = hmm.bull_prob
    elif tilt is not None:
        bull = tilt
    else:
        bull = 0.5

    label = f"{'Trending' if trending >= 0.5 else 'Ranging'} {'Bull' if bull >= 0.5 else 'Bear'}"
    confidence = (max(trending, 1.0 - trending) * max(bull, 1.0 - bull)) ** 0.5
    return label, confidence, trending, bull


def _extension_penalty(f: dict[str, float | None], kalman: KalmanResult | None) -> float:
    """Being very stretched above/below fair value caps the overall score:
    strong trend + extreme extension is a worse entry than the trend alone
    suggests. Max 15 points."""
    pen = 0.0
    z = f.get("z_score")
    if z is not None:
        pen += _clamp((abs(z) - 2.0) * 10.0, 0.0, 10.0)
    if kalman is not None:
        pen += _clamp((abs(kalman.distance) - 0.05) * 100.0, 0.0, 5.0)
    return pen


# ---------------------------------------------------------------------------
# the report
# ---------------------------------------------------------------------------


class QuantReport(BaseModel):
    asset: str
    market: str
    interval: str
    bars: int
    as_of: datetime

    regime_label: str
    regime_confidence: float
    trending_prob: float
    bull_prob: float

    trend_score: int | None
    momentum_score: int | None
    volume_score: int | None
    volume_label: str
    relative_strength_label: str
    overall_score: int | None

    forecast_vol_daily: float | None
    vol_percentile: float | None
    dist_fair_value: float | None
    fair_value: float | None
    dist_vwap: float | None
    z_score: float | None

    indicators: dict[str, float | None]
    profile: dict[str, float | list[float]] | None
    features: dict[str, float | None]
    verdict: str
    disclaimer: str = DISCLAIMER


def _tier(value: float | None, strong: float, weak: float) -> str:
    if value is None:
        return "Unknown"
    if value >= strong:
        return "Strong"
    if value <= weak:
        return "Weak"
    return "Moderate"


def _verdict(rep_vals: dict[str, object]) -> str:
    """Deterministic template prose. Numbers decide the words, never the
    other way around."""
    overall = rep_vals["overall_score"]
    label = rep_vals["regime_label"]
    lean = (
        "constructive"
        if isinstance(overall, int) and overall >= 60
        else "cautious/bearish"
        if isinstance(overall, int) and overall <= 40
        else "neutral"
    )
    parts = [f"Structure reads {str(label).lower()}; the overall evidence blend is {lean}."]
    vp = rep_vals["vol_percentile"]
    if isinstance(vp, float):
        if vp >= 0.8:
            parts.append(
                "Volatility sits in the top quintile of its own history — size accordingly."
            )
        elif vp <= 0.3:
            parts.append("Volatility is subdued versus its own history.")
    z = rep_vals["z_score"]
    if isinstance(z, float) and abs(z) > 2.0:
        side = "above" if z > 0 else "below"
        parts.append(f"Price is stretched more than 2 standard deviations {side} its 20-bar mean.")
    dfv = rep_vals["dist_fair_value"]
    if isinstance(dfv, float) and abs(dfv) > 0.03:
        side = "above" if dfv > 0 else "below"
        parts.append(f"Price trades {abs(dfv) * 100:.1f}% {side} the Kalman fair-value line.")
    parts.append("Scores are transparent heuristics, not proven alpha; validate before acting.")
    return " ".join(parts)


def build_report(series: PriceSeries, market: str) -> QuantReport:
    """Compute everything from one candle series. Deterministic end to end."""
    if len(series.candles) < MIN_BARS:
        raise ValueError(
            f"need at least {MIN_BARS} candles for a quant report, got {len(series.candles)}; "
            f"fetch more history (--days)"
        )
    closes = series.closes()
    if any(c <= 0 for c in closes):
        # a non-positive close is broken source data; refusing beats quietly
        # computing statistics on a series with holes in it
        raise ValueError(f"{series.asset} series contains non-positive closes; source data is bad")
    rets = log_returns(closes)

    f = compute_features(series)
    ind = compute_indicators(series)
    strength = rolling_trend_strength(closes)
    kalman = kalman_fair_value(closes)
    garch: GarchResult | None = fit_garch(rets)
    hmm = fit_hmm(rets)
    prof = volume_profile(series)

    label, conf, trending, bull = classify_regime(f, hmm, ind["adx14"])
    t_score = trend_score(f, strength)
    m_score = momentum_score(f, ind["rsi14"])
    v_score = volume_score(f)

    penalty = _extension_penalty(f, kalman)
    weighted: list[tuple[float, float]] = []  # (weight, value)
    if t_score is not None:
        weighted.append((0.35, float(t_score)))
    if m_score is not None:
        weighted.append((0.30, float(m_score)))
    if v_score is not None:
        weighted.append((0.15, float(v_score)))
    weighted.append((0.20, conf * 100.0))
    total_w = sum(w for w, _ in weighted)
    overall = (
        round(_clamp(sum(w * v for w, v in weighted) / total_w - penalty, 0.0, 100.0))
        if total_w > 0
        else None
    )

    values: dict[str, object] = {
        "overall_score": overall,
        "regime_label": label,
        "vol_percentile": f["vol_percentile"],
        "z_score": f["z_score"],
        "dist_fair_value": kalman.distance if kalman else None,
    }
    return QuantReport(
        asset=series.asset,
        market=market,
        interval=series.interval.value,
        bars=len(series.candles),
        as_of=series.candles[-1].ts,
        regime_label=label,
        regime_confidence=conf,
        trending_prob=trending,
        bull_prob=bull,
        trend_score=t_score,
        momentum_score=m_score,
        volume_score=v_score,
        volume_label=_tier(float(v_score) if v_score is not None else None, 65.0, 40.0),
        relative_strength_label=_tier(ind["rsi14"], 60.0, 40.0),
        overall_score=overall,
        forecast_vol_daily=garch.forecast_vol_daily if garch else None,
        vol_percentile=f["vol_percentile"],
        dist_fair_value=kalman.distance if kalman else None,
        fair_value=kalman.fair_value if kalman else None,
        dist_vwap=f["dist_vwap"],
        z_score=f["z_score"],
        indicators=ind,
        profile=prof,
        features=f,
        verdict=_verdict(values),
    )


# ---------------------------------------------------------------------------
# text rendering
# ---------------------------------------------------------------------------


def _fmt(v: float | None, spec: str = ".2f", suffix: str = "") -> str:
    return "n/a" if v is None else f"{v:{spec}}{suffix}"


def _pct(v: float | None, signed: bool = True, decimals: int = 0) -> str:
    if v is None:
        return "n/a"
    return f"{v * 100:+.1f}%" if signed else f"{v * 100:.{decimals}f}%"


def _score_str(v: int | None) -> str:
    return "n/a" if v is None else f"{v}/100"


def render_text(r: QuantReport) -> str:
    """The human-readable report block."""
    rows: list[tuple[str, str]] = [
        ("Regime", f"{r.regime_label} ({r.regime_confidence * 100:.0f}%)"),
        ("", ""),
        ("Trend Score", _score_str(r.trend_score)),
        ("Momentum Score", _score_str(r.momentum_score)),
        ("Volume Score", f"{_score_str(r.volume_score)}  ({r.volume_label})"),
        ("Relative Strength", r.relative_strength_label),
        ("", ""),
        (
            "Forecast Volatility",
            _pct(r.forecast_vol_daily, signed=False, decimals=1) + " daily (GARCH)",
        ),
        ("Volatility Percentile", _pct(r.vol_percentile, signed=False)),
        ("", ""),
        ("Fair Value (Kalman)", _pct(r.dist_fair_value) + " vs price"),
        ("Distance from VWAP", _pct(r.dist_vwap)),
        ("Z-Score (20 bar)", _fmt(r.z_score, "+.2f")),
        ("", ""),
        ("Overall Asset Score", _score_str(r.overall_score)),
    ]
    width = max(len(k) for k, _ in rows) + 3
    head = f"{r.asset}  ({r.market}, {r.bars} {r.interval} bars, as of {r.as_of:%Y-%m-%d})"
    lines = [head, "=" * len(head)]
    for k, v in rows:
        lines.append(f"{k:<{width}}{v}".rstrip())

    i = r.indicators
    lines += [
        "",
        "Indicators",
        "-" * 10,
        f"{'Close':<{width}}{_fmt(i['close'])}",
        f"{'EMA 20 / 50 / 200':<{width}}"
        f"{_fmt(i['ema20'])} / {_fmt(i['ema50'])} / {_fmt(i['ema200'])}",
        f"{'VWAP (20)':<{width}}{_fmt(i['vwap20'])}",
        f"{'Bollinger (20, 2σ)':<{width}}"
        f"{_fmt(i['bb_lower'])} | {_fmt(i['bb_mid'])} | {_fmt(i['bb_upper'])}",
        f"{'Keltner (20, 2×ATR10)':<{width}}"
        f"{_fmt(i['keltner_lower'])} | {_fmt(i['keltner_mid'])} | {_fmt(i['keltner_upper'])}",
        f"{'ATR 14':<{width}}{_fmt(i['atr14'])}",
        f"{'RSI 14':<{width}}{_fmt(i['rsi14'], '.1f')}",
        f"{'ADX 14':<{width}}{_fmt(i['adx14'], '.1f')}",
    ]
    if r.profile:
        poc = r.profile["poc"]
        levels = r.profile["levels"]
        if isinstance(poc, float) and isinstance(levels, list):
            lvl_txt = ", ".join(f"{lv:.2f}" for lv in levels)
            lines.append(f"{'Volume Profile POC':<{width}}{poc:.2f}  (top levels: {lvl_txt})")

    lines += ["", "Verdict", "-" * 7, r.verdict, "", r.disclaimer]
    return "\n".join(lines)
