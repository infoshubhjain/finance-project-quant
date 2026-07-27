"""Deterministic, offline calibration of per-analyzer source reliability.

Reads the recorded signal log, scores each signal against cached prices,
groups by analyzer name, computes per-analyzer hit rates with Bayesian
shrinkage toward 0.50, and writes the result to ``data/calibration.json``.

This is deliberately offline and human-invoked. It never runs automatically.
The clunkiness is the safety: sample-floored, shrunk, version-controlled.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from alpha_engine.cache.interface import Cache
from alpha_engine.schema.signal import Direction, Market, Timeframe
from alpha_engine.validation.outcomes import OutcomeStatus, score_record
from alpha_engine.validation.recorder import SignalRecord, read_records

from alpha_engine.config import data_dir

# Shrinkage parameter: controls how quickly an analyzer's hit rate converges
# to the default 0.50. Higher k means more shrinkage (more conservative).
# With k=30, an analyzer needs ~30 resolved signals before its empirical
# hit rate starts to meaningfully override the default.
_DEFAULT_SHRINKAGE_K = 30.0

# Minimum number of resolved signals for an analyzer to use its empirical
# hit rate. Below this, the analyzer keeps the default 0.50 and the tool
# says so out loud.
_DEFAULT_MIN_SAMPLES = 50

# Where calibration is persisted
CALIBRATION_PATH = data_dir() / "calibration.json"


class AnalyzerCalibration(BaseModel):
    """Calibration result for a single analyzer."""

    name: str
    empirical_hit_rate: float | None = Field(
        None, description="Raw hit rate from resolved signals (None if no data)"
    )
    shrunk_reliability: float = Field(..., description="Shrunk reliability toward 0.50")
    resolved_count: int = Field(
        ..., description="Number of resolved signals this analyzer contributed to"
    )
    hit_count: int = Field(..., description="Number of hits")
    used_default: bool = Field(
        ..., description="True if analyzer had fewer than min_samples signals"
    )


class CalibrationResult(BaseModel):
    """Full calibration result written to data/calibration.json."""

    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = Field(
        "live_log",
        description="'live_log' — scored from recorded signals, out of sample. "
        "'backtest' — replayed from history, IN SAMPLE on the same bars the "
        "analyzers will run on again. The two must never be silently mixed: a "
        "system that grades its own homework and then trusts the grade is worse "
        "than one with no grades at all.",
    )
    assets: list[str] = Field(
        default_factory=list, description="assets replayed, when source is 'backtest'"
    )
    window_records: int = Field(..., description="Total number of records in the signal log")
    window_resolved: int = Field(
        ..., description="Number of records that were resolved (not pending)"
    )
    shrinkage_k: float = Field(..., description="Shrinkage parameter used")
    min_samples: int = Field(..., description="Minimum samples threshold used")
    analyzers: list[AnalyzerCalibration] = Field(
        default_factory=list,
        description="Per-analyzer calibration results",
    )


def _shrink(hits: int, total: int, k: float) -> float:
    """Apply Bayesian shrinkage to a hit rate.

    Formula: ``(hits + k * 0.5) / (total + k)``

    This pulls the empirical hit rate toward 0.50 (the baseline).
    With k=30, an analyzer with 12 signals at 75% hit rate gets:
        (9 + 30 * 0.5) / (12 + 30) = 24 / 42 = 0.5714
    """
    return (hits + k * 0.5) / (total + k)


def calibrate(
    records: list[SignalRecord] | None = None,
    *,
    min_samples: int = _DEFAULT_MIN_SAMPLES,
    shrinkage_k: float = _DEFAULT_SHRINKAGE_K,
    cache: Cache | None = None,
) -> CalibrationResult:
    """Compute per-analyzer reliability from the recorded signal log.

    This is an offline, deterministic operation. It does not write anything;
    the caller decides whether to persist the result via ``write_calibration``.
    """
    if records is None:
        records = read_records()
    if cache is None:
        cache = Cache()

    # Score each record and collect per-analyzer hit data
    analyzer_hits: dict[str, int] = {}
    analyzer_totals: dict[str, int] = {}
    resolved_count = 0

    for record in records:
        if record.entry_price is None:
            continue
        series, _stale = cache.get_price(record.signal.asset, "1d")
        if series is None:
            continue
        outcome = score_record(record, series)
        if outcome.status != OutcomeStatus.RESOLVED:
            continue

        resolved_count += 1
        hit = outcome.hit is True

        # Each signal may have contributed multiple analyzer sources.
        # A signal is a "hit" if the overall call was right; each analyzer
        # that voted with the winning direction gets credit for that hit.
        # Analyzers that voted against the winning direction are not counted
        # (their low reliability is already captured by the agreement penalty
        # in synthesize.py).
        direction = record.signal.direction
        for source in record.signal.signal_sources:
            if source.direction is direction:
                name = source.name
                analyzer_totals[name] = analyzer_totals.get(name, 0) + 1
                if hit:
                    analyzer_hits[name] = analyzer_hits.get(name, 0) + 1

    # Build calibration entries
    analyzers: list[AnalyzerCalibration] = []
    for name in sorted(analyzer_totals):
        total = analyzer_totals[name]
        hits_count = analyzer_hits.get(name, 0)
        empirical = hits_count / total if total > 0 else None
        used_default = total < min_samples
        if used_default or total == 0:
            shrunk = 0.5
        else:
            shrunk = _shrink(hits_count, total, shrinkage_k)
        analyzers.append(
            AnalyzerCalibration(
                name=name,
                empirical_hit_rate=empirical,
                shrunk_reliability=shrunk,
                resolved_count=total,
                hit_count=hits_count,
                used_default=used_default,
            )
        )

    return CalibrationResult(
        window_records=len(records),
        window_resolved=resolved_count,
        shrinkage_k=shrinkage_k,
        min_samples=min_samples,
        analyzers=analyzers,
    )


def calibrate_from_backtest(
    assets: dict[str, Market],
    *,
    days: int = 1825,
    timeframe: Timeframe | None = None,
    step: int = 1,
    min_samples: int = _DEFAULT_MIN_SAMPLES,
    shrinkage_k: float = _DEFAULT_SHRINKAGE_K,
    cache: Cache | None = None,
) -> CalibrationResult:
    """Calibrate per-analyzer reliability by replaying history, not by waiting.

    The live log needs ten trading days before a single swing signal resolves,
    so a fresh install has nothing to learn from for weeks. Replaying cached
    history through the same `signal_at()` choke point produces thousands of
    scored calls in minutes, which is what makes the feedback loop usable at all.

    Two deliberate differences from `calibrate()`:

    **Direction-matched scoring.** An analyzer is credited when its OWN
    direction was right, measured against how often the asset moved that way
    anyway. Scoring against a flat 50% flatters every bullish call on an asset
    that spent the window rising — AAPL rose in 56% of 10-bar windows, so 50%
    there is worse than doing nothing.

    **No invalidation stop.** The live scorer counts a touched stop as an
    immediate miss, which is right for judging a *signal* and wrong for judging
    an *analyzer*: measurement showed the stop killing ~30% of calls before the
    thesis could play out, so it swamps the thing being measured.

    The result is tagged `source="backtest"` because it is IN SAMPLE. It is an
    honest prior, not evidence — `source="live_log"` should replace it as soon
    as real resolved signals exist.
    """
    from alpha_engine.cache.models import PriceSeries
    from alpha_engine.synthesis.synthesize import synthesize
    from alpha_engine.validation.backtest import ANALYZER_REGISTRY, DEFAULT_WARMUP
    from alpha_engine.validation.outcomes import HORIZON_BARS

    timeframe = timeframe or Timeframe.SWING
    horizon = HORIZON_BARS[timeframe]
    cache = cache or Cache()

    hits: dict[str, int] = {}
    totals: dict[str, int] = {}
    scored_total = 0
    used: list[str] = []

    for asset, market in assets.items():
        series, _stale = cache.get_price(asset, "1d")
        if series is None or len(series.candles) < DEFAULT_WARMUP + horizon + 10:
            continue
        used.append(asset)
        candles = series.candles

        for name, analyzer in ANALYZER_REGISTRY.items():
            for t in range(DEFAULT_WARMUP, len(candles) - horizon, step):
                past = PriceSeries(asset=asset, interval=series.interval, candles=candles[: t + 1])
                call = synthesize(
                    asset=asset, market=market, sources=[analyzer(past)], timeframe=timeframe
                )
                if call.direction is Direction.NEUTRAL:
                    continue
                rose = candles[t + horizon].close > candles[t].close
                correct = rose if call.direction is Direction.BULLISH else not rose
                totals[name] = totals.get(name, 0) + 1
                if correct:
                    hits[name] = hits.get(name, 0) + 1
                scored_total += 1

    analyzers: list[AnalyzerCalibration] = []
    for name in sorted(totals):
        total = totals[name]
        hit_count = hits.get(name, 0)
        used_default = total < min_samples
        analyzers.append(
            AnalyzerCalibration(
                name=name,
                empirical_hit_rate=hit_count / total if total else None,
                shrunk_reliability=0.5 if used_default else _shrink(hit_count, total, shrinkage_k),
                resolved_count=total,
                hit_count=hit_count,
                used_default=used_default,
            )
        )

    return CalibrationResult(
        source="backtest",
        assets=sorted(used),
        window_records=scored_total,
        window_resolved=scored_total,
        shrinkage_k=shrinkage_k,
        min_samples=min_samples,
        analyzers=analyzers,
    )


def write_calibration(
    result: CalibrationResult,
    path: str | Path = CALIBRATION_PATH,
) -> Path:
    """Write calibration result to a JSON file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_calibration(
    path: str | Path = CALIBRATION_PATH,
) -> dict[str, float]:
    """Load calibration from a JSON file, returning ``{name: reliability}``.

    Returns an empty dict if the file doesn't exist (the fresh-clone path).
    """
    path = Path(path)
    if not path.exists():
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    analyzers = data.get("analyzers", [])
    result: dict[str, float] = {}
    for entry in analyzers:
        result[entry["name"]] = entry["shrunk_reliability"]
    return result
