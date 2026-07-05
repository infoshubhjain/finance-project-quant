"""Tests for the confidence calibration module.

Key properties:
- With insufficient data, raw confidence passes through unchanged.
- Calibration bins correctly map raw to calibrated confidence.
- The curve is built from outcome summary data.
"""

from __future__ import annotations

from alpha_engine.validation.calibration import (
    CalibrationCurve,
    apply_calibration,
    build_calibration_curve,
)
from alpha_engine.validation.outcomes import CalibrationBin, OutcomeSummary


def test_calibration_curve_with_insufficient_data():
    curve = CalibrationCurve(bins=[], sample_size=5)
    assert curve.calibrate(0.7) == 0.7  # passes through


def test_calibration_curve_maps_to_hit_rate():
    bins = [
        CalibrationBin(lo=0.0, hi=0.2, count=10, hits=5, hit_rate=0.5),
        CalibrationBin(lo=0.2, hi=0.4, count=8, hits=4, hit_rate=0.5),
        CalibrationBin(lo=0.4, hi=0.6, count=12, hits=9, hit_rate=0.75),
        CalibrationBin(lo=0.6, hi=0.8, count=6, hits=3, hit_rate=0.5),
        CalibrationBin(lo=0.8, hi=1.0, count=4, hits=2, hit_rate=0.5),
    ]
    curve = CalibrationCurve(bins=bins, sample_size=40)

    # Raw 0.5 falls in [0.4, 0.6) -> calibrated to 0.75
    assert curve.calibrate(0.5) == 0.75
    # Raw 0.1 falls in [0.0, 0.2) -> calibrated to 0.5
    assert curve.calibrate(0.1) == 0.5
    # Raw 0.9 falls in [0.8, 1.0] -> calibrated to 0.5
    assert curve.calibrate(0.9) == 0.5


def test_calibration_curve_boundary_values():
    bins = [
        CalibrationBin(lo=0.0, hi=0.5, count=10, hits=5, hit_rate=0.5),
        CalibrationBin(lo=0.5, hi=1.0, count=10, hits=8, hit_rate=0.8),
    ]
    curve = CalibrationCurve(bins=bins, sample_size=20)

    assert curve.calibrate(0.0) == 0.5
    assert curve.calibrate(0.5) == 0.8
    assert curve.calibrate(1.0) == 0.8  # hi=1.0 inclusive for 1.0


def test_calibration_curve_none_hit_rate_passes_through():
    bins = [
        CalibrationBin(lo=0.0, hi=0.5, count=0, hits=0, hit_rate=None),
        CalibrationBin(lo=0.5, hi=1.0, count=5, hits=3, hit_rate=0.6),
    ]
    curve = CalibrationCurve(bins=bins, sample_size=20)

    # Bin with no data -> passes through raw
    assert curve.calibrate(0.3) == 0.3
    # Bin with data -> calibrated
    assert curve.calibrate(0.7) == 0.6


def test_build_calibration_curve_from_summary():
    summary = OutcomeSummary(
        total=50,
        resolved=40,
        pending=5,
        not_applicable=5,
        hits=20,
        hit_rate=0.5,
        calibration=[
            CalibrationBin(lo=0.0, hi=0.2, count=10, hits=5, hit_rate=0.5),
            CalibrationBin(lo=0.2, hi=0.4, count=8, hits=4, hit_rate=0.5),
            CalibrationBin(lo=0.4, hi=0.6, count=12, hits=9, hit_rate=0.75),
            CalibrationBin(lo=0.6, hi=0.8, count=6, hits=3, hit_rate=0.5),
            CalibrationBin(lo=0.8, hi=1.0, count=4, hits=2, hit_rate=0.5),
        ],
    )
    curve = build_calibration_curve(summary)
    assert curve.sample_size == 40
    assert curve.overall_hit_rate == 0.5
    assert len(curve.bins) == 5


def test_apply_calibration_no_curve():
    assert apply_calibration(0.7, None) == 0.7


def test_apply_calibration_with_curve():
    bins = [
        CalibrationBin(lo=0.0, hi=0.5, count=10, hits=5, hit_rate=0.5),
        CalibrationBin(lo=0.5, hi=1.0, count=10, hits=8, hit_rate=0.8),
    ]
    curve = CalibrationCurve(bins=bins, sample_size=20)
    assert apply_calibration(0.6, curve) == 0.8
    assert apply_calibration(0.3, curve) == 0.5
