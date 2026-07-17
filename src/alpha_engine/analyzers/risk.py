"""Risk agent: portfolio-level risk reads layered on top of per-asset signals.

This is Layer 4 of the original blueprint — the piece that sits alongside
signal synthesis and answers "how big is the bet, and how correlated is it?"

What it computes, all deterministic:
- Inverse-volatility position sizing: an asset with 2× the volatility of
  another gets half the notional for the same risk budget. Output is a
  *fraction of risk budget*, never a dollar amount and never an order.
- Historical VaR (Value at Risk) and CVaR (Conditional VaR / expected
  shortfall) at 95% confidence over a trailing window.
- Drawdown regime flag: how far below the trailing high the asset sits.
- Regime gate: the HMM bull probability surfaces as a risk overlay so a
  bullish signal fired inside a bear regime carries a warning.

Framing: all outputs are *research context*, not instructions. "This position
represents 3.2× the volatility of that one" is research. "Buy X" is not.

Cardinal rule compliance: pure functions, no network, no LLM, deterministic.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from pydantic import BaseModel, Field

from alpha_engine.cache.models import PriceSeries
from alpha_engine.quant.features import _mean, _pstd
from alpha_engine.quant.models import HmmResult
from alpha_engine.schema.signal import Direction, Signal


# ---------------------------------------------------------------------------
# Output models
# ---------------------------------------------------------------------------


class PositionSize(BaseModel):
    """Inverse-volatility weight for one asset."""

    asset: str
    weight: float = Field(
        ...,
        ge=0.0,
        description="fraction of risk budget; sums to ~1 across assets after normalization",
    )
    daily_vol: float = Field(..., description="annualized daily volatility of recent returns")
    annualized_vol: float = Field(..., description="daily_vol × sqrt(252)")
    note: str = ""


class TailRisk(BaseModel):
    """Historical tail-risk metrics for one asset."""

    asset: str
    var_95: float = Field(..., description="95% historical VaR; negative = loss (e.g. -0.03 = -3%)")
    cvar_95: float = Field(..., description="95% CVaR (expected shortfall); worse than VaR")
    max_drawdown: float = Field(
        ..., ge=-1.0, le=0.0, description="worst peak-to-trough decline in window"
    )
    current_drawdown: float = Field(
        ..., ge=-1.0, le=0.0, description="how far below trailing high right now"
    )
    window: int
    note: str = ""


class RiskReport(BaseModel):
    """Aggregate risk reads for a portfolio of signals."""

    position_sizes: list[PositionSize] = Field(default_factory=list)
    tail_risks: list[TailRisk] = Field(default_factory=list)
    concentration_warnings: list[str] = Field(default_factory=list)
    regime_gate: str = Field(
        "",
        description="portfolio-level regime overlay from the HMM (if available)",
    )
    regime_confidence: float = Field(
        0.0, ge=0.0, le=1.0, description="strength of the regime read (0 = no data)"
    )
    risk_score: int = Field(
        50,
        ge=0,
        le=100,
        description="0 = max risk (everything concentrated + extreme vol), 100 = minimal risk",
    )


# ---------------------------------------------------------------------------
# Inverse-volatility position sizing
# ---------------------------------------------------------------------------

_ANNUAL = math.sqrt(252.0)
_MIN_VOLS = 20  # need at least this many returns for a meaningful vol estimate


def vol_position_size(
    closes: Sequence[float],
    asset: str,
    risk_budget: float = 1.0,
) -> PositionSize | None:
    """Inverse-volatility weight for one asset. None when history is too short.

    The output is a *fraction of risk budget*: an asset with 2× the vol of
    another gets ½ the weight. The caller normalizes across assets; this
    function handles one asset at a time.

    Output is research context, not an order. Framing: "this position
    represents X% of the risk budget," never "buy this much."
    """
    if len(closes) < _MIN_VOLS + 1:
        return None

    rets = [(closes[i] / closes[i - 1]) - 1.0 for i in range(1, len(closes)) if closes[i - 1] > 0]
    if len(rets) < _MIN_VOLS:
        return None

    # Use recent window for responsiveness, but at least _MIN_VOLS bars
    window = min(60, len(rets))
    recent = rets[-window:]

    daily_vol = _pstd(recent)
    if daily_vol is None or daily_vol <= 0:
        return None

    ann_vol = daily_vol * _ANNUAL
    # Inverse vol: higher vol → lower weight
    inv_vol = 1.0 / daily_vol
    # Weight as fraction of risk budget (un-normalized; caller normalizes)
    weight = inv_vol * risk_budget

    return PositionSize(
        asset=asset,
        weight=weight,
        daily_vol=daily_vol,
        annualized_vol=ann_vol,
        note=f"inv-vol weight (un-normalized) over {window}-bar window",
    )


def normalize_positions(positions: list[PositionSize]) -> list[PositionSize]:
    """Normalize un-normalized position weights so they sum to 1.0.

    Returns a new list; the original is not mutated.
    """
    total = sum(p.weight for p in positions)
    if total <= 0:
        return positions
    return [p.model_copy(update={"weight": round(p.weight / total, 4)}) for p in positions]


# ---------------------------------------------------------------------------
# Historical VaR and CVaR
# ---------------------------------------------------------------------------


def historical_var(
    closes: Sequence[float],
    confidence: float = 0.95,
    window: int = 60,
) -> float | None:
    """Historical Value at Risk at `confidence` level over trailing `window`.

    VaR is the loss threshold: with 95% confidence, the daily loss will not
    exceed this number. Returns a *negative* number for losses (e.g. -0.03
    means a 3% daily loss). None when history is too short.
    """
    if len(closes) < window + 1:
        return None

    rets = [
        (closes[i] / closes[i - 1]) - 1.0
        for i in range(len(closes) - window, len(closes))
        if closes[i - 1] > 0
    ]
    if not rets:
        return None

    sorted_rets = sorted(rets)
    idx = int((1.0 - confidence) * len(sorted_rets))
    idx = max(0, min(idx, len(sorted_rets) - 1))
    return round(sorted_rets[idx], 6)


def historical_cvar(
    closes: Sequence[float],
    confidence: float = 0.95,
    window: int = 60,
) -> float | None:
    """Conditional VaR (expected shortfall): average loss beyond the VaR threshold.

    CVaR answers "when it does go wrong, how bad is it on average?" Always
    worse (more negative) than VaR. None when history is too short.
    """
    if len(closes) < window + 1:
        return None

    rets = [
        (closes[i] / closes[i - 1]) - 1.0
        for i in range(len(closes) - window, len(closes))
        if closes[i - 1] > 0
    ]
    if not rets:
        return None

    sorted_rets = sorted(rets)
    cutoff = int((1.0 - confidence) * len(sorted_rets))
    cutoff = max(1, min(cutoff, len(sorted_rets)))
    tail = sorted_rets[:cutoff]
    return round(_mean(tail), 6)


# ---------------------------------------------------------------------------
# Drawdown regime
# ---------------------------------------------------------------------------


def drawdown_metrics(closes: Sequence[float], window: int = 252) -> tuple[float, float] | None:
    """Trailing max drawdown and current drawdown over the last `window` bars.

    Returns (max_drawdown, current_drawdown), both negative or zero.
    None when history is too short.
    """
    if len(closes) < 2:
        return None

    lookback = min(window, len(closes))
    recent = closes[-lookback:]

    peak = recent[0]
    max_dd = 0.0
    for c in recent:
        if c > peak:
            peak = c
        dd = (c / peak) - 1.0 if peak > 0 else 0.0
        if dd < max_dd:
            max_dd = dd

    current_peak = max(recent)
    current_dd = (recent[-1] / current_peak) - 1.0 if current_peak > 0 else 0.0

    return (round(max_dd, 6), round(current_dd, 6))


def tail_risk_flag(closes: Sequence[float], window: int = 60) -> TailRisk | None:
    """Full tail-risk read for one asset. None when history is too short."""
    if len(closes) < window + 1:
        return None

    var = historical_var(closes, 0.95, window)
    cvar = historical_cvar(closes, 0.95, window)
    dd = drawdown_metrics(closes, window)

    if var is None or cvar is None or dd is None:
        return None

    max_dd, current_dd = dd
    notes: list[str] = []
    if current_dd < -0.10:
        notes.append("in significant drawdown")
    if abs(cvar) > 0.05:
        notes.append("severe tail risk")

    return TailRisk(
        asset="",  # filled by caller
        var_95=var,
        cvar_95=cvar,
        max_drawdown=max_dd,
        current_drawdown=current_dd,
        window=window,
        note="; ".join(notes),
    )


# ---------------------------------------------------------------------------
# Regime gate
# ---------------------------------------------------------------------------


def regime_gate(hmm: HmmResult | None) -> tuple[str, float]:
    """HMM bull probability as a risk overlay. Returns (label, confidence).

    A bullish signal in a bear regime is a different animal than one in a
    bull regime. The label says which; the confidence says how strong the
    read is. When no HMM data exists, returns ("unknown", 0.0).
    """
    if hmm is None:
        return ("unknown", 0.0)

    bp = hmm.bull_prob
    if bp >= 0.7:
        return ("bull_regime", bp)
    if bp <= 0.3:
        return ("bear_regime", 1.0 - bp)
    return ("neutral_regime", max(bp, 1.0 - bp))


# ---------------------------------------------------------------------------
# Aggregate risk report
# ---------------------------------------------------------------------------


def build_risk_report(
    signals: list[Signal],
    series_by_asset: dict[str, PriceSeries],
    hmm: HmmResult | None = None,
    window: int = 60,
) -> RiskReport:
    """Aggregate risk reads across the portfolio's signals.

    This is the main entry point for the risk agent. It layers:
    1. Inverse-vol position sizing (per asset)
    2. Historical VaR/CVaR + drawdown (per asset)
    3. Concentration warnings (from correlation, if series available)
    4. Regime gate (from HMM, if available)

    All outputs are research context, not trading instructions.
    """
    positions: list[PositionSize] = []
    tails: list[TailRisk] = []

    directional = [s for s in signals if s.direction is not Direction.NEUTRAL]

    for signal in directional:
        asset = signal.asset
        series = series_by_asset.get(asset)
        if series is None or not series.candles:
            continue

        closes = [c.close for c in series.candles]

        # Position sizing
        ps = vol_position_size(closes, asset)
        if ps is not None:
            positions.append(ps)

        # Tail risk
        tr = tail_risk_flag(closes, window=window)
        if tr is not None:
            tails.append(tr.model_copy(update={"asset": asset}))

    # Normalize position weights
    if positions:
        positions = normalize_positions(positions)

    # Concentration warnings (from correlation matrix if ≥2 assets)
    concentration: list[str] = []
    if len(directional) >= 2 and len(series_by_asset) >= 2:
        from alpha_engine.analyzers.correlation import correlation_matrix, CONCENTRATION_MIN_CORR

        covered = {
            s.asset: series_by_asset[s.asset] for s in directional if s.asset in series_by_asset
        }
        if len(covered) >= 2:
            matrix = correlation_matrix(covered, window=window)
            for i, a in enumerate(matrix.assets):
                for j in range(i + 1, len(matrix.assets)):
                    b = matrix.assets[j]
                    corr = matrix.matrix[i][j]
                    if corr is not None and abs(corr) >= CONCENTRATION_MIN_CORR:
                        concentration.append(
                            f"{a} and {b} have {corr:+.2f} return correlation "
                            f"({'effectively one position' if corr > 0 else 'natural hedge'})"
                        )

    # Regime gate
    rg_label, rg_conf = regime_gate(hmm)

    # Risk score: composite of concentration + tail risk + regime
    risk_score = _compute_risk_score(positions, tails, concentration, rg_label, rg_conf)

    return RiskReport(
        position_sizes=positions,
        tail_risks=tails,
        concentration_warnings=concentration,
        regime_gate=rg_label,
        regime_confidence=rg_conf,
        risk_score=risk_score,
    )


def _compute_risk_score(
    positions: list[PositionSize],
    tails: list[TailRisk],
    concentration: list[str],
    regime_label: str,
    regime_conf: float,
) -> int:
    """Deterministic risk score 0-100 (100 = minimal risk).

    Components (each 0-100, averaged):
    - Diversification: fewer concentration warnings → higher score
    - Tail safety: less severe CVaR → higher score
    - Regime: bull regime → higher score, bear regime → lower
    """
    # Diversification score
    max_warnings = max(len(positions), 1)
    div_score = max(0, 100 - (len(concentration) / max_warnings) * 100)

    # Tail risk score: based on average |CVaR| across assets
    if tails:
        avg_cvar = sum(abs(t.cvar_95) for t in tails) / len(tails)
        # 0% CVaR → 100, 10% CVaR → 0
        tail_score = max(0, min(100, 100 - avg_cvar * 1000))
    else:
        tail_score = 50.0  # no data → neutral

    # Regime score
    if regime_label == "bull_regime":
        regime_score = 60 + regime_conf * 40
    elif regime_label == "bear_regime":
        regime_score = 60 - regime_conf * 40
    else:
        regime_score = 50.0

    overall = div_score * 0.4 + tail_score * 0.4 + regime_score * 0.2
    return max(0, min(100, round(overall)))
