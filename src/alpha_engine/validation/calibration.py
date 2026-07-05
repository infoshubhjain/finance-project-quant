"""Confidence calibration. Uses recorded outcomes to recalibrate the raw
confidence scores so they better reflect actual hit rates.

The problem: the scaffold analyzers produce raw confidence scores that don't
track real hit rates. The highest-confidence bucket might underperform (as the
first backtest showed). This module measures the miscalibration and provides
a correction function.

The correction is a simple piecewise linear mapping from raw confidence to
calibrated confidence, learned from historical signal-vs-outcome data. No ML,
no optimization, just honest measurement and a transparent lookup table.

Cardinal rule compliance: pure function, no network, no LLM, deterministic.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from alpha_engine.validation.outcomes import CalibrationBin, OutcomeSummary


class CalibrationCurve(BaseModel):
    """The mapping from raw confidence buckets to calibrated confidence.

    Each entry says: "of the signals whose raw confidence fell in [lo, hi),
    the actual hit rate was X. Use X as the calibrated confidence for future
    signals in that bucket."
    """

    bins: list[CalibrationBin] = Field(default_factory=list)
    sample_size: int = 0
    overall_hit_rate: float | None = None

    def calibrate(self, raw_confidence: float) -> float:
        """Map a raw confidence score to a calibrated one.

        Uses the calibration bin that contains the raw score. If no bin
        exists or the bin has no data, returns the raw score unchanged
        (conservative: we don't degrade signals we haven't measured).
        """
        if not self.bins or self.sample_size < 10:
            return raw_confidence

        for bin in self.bins:
            if bin.lo <= raw_confidence < bin.hi or (
                bin.hi == 1.0 and raw_confidence == 1.0
            ):
                if bin.hit_rate is not None:
                    return bin.hit_rate
                # Bin exists but no data yet — return raw
                return raw_confidence

        # Outside all bins — return raw
        return raw_confidence


def build_calibration_curve(summary: OutcomeSummary) -> CalibrationCurve:
    """Build a calibration curve from an outcome summary.

    The curve is already computed by `summarize_outcomes` in the validation
    layer. This function just wraps it in the CalibrationCurve model for
    convenient use by the synthesis layer.
    """
    return CalibrationCurve(
        bins=summary.calibration,
        sample_size=summary.resolved,
        overall_hit_rate=summary.hit_rate,
    )


def apply_calibration(
    raw_confidence: float,
    curve: CalibrationCurve | None,
) -> float:
    """Apply calibration to a raw confidence score.

    If no curve is provided or calibration has insufficient data,
    returns the raw score unchanged. This is the safe default: we'd rather
    be honestly uncalibrated than falsely calibrated.
    """
    if curve is None:
        return raw_confidence
    return curve.calibrate(raw_confidence)
