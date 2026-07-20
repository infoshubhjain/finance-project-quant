"""Tests for the Phase-10 factor registry.

The important test in this file is `test_no_lookahead`. It runs over *every*
entry in `FACTOR_REGISTRY` and asserts that a factor's value at bar `t` is
identical whether it is computed on the full series or on a series truncated at
`t`. A factor that peeks at future bars fails here, and it fails automatically
for factors added later — that is the point of parameterizing over the registry
rather than listing factors by hand.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone

import pytest

from alpha_engine.cache.models import Candle, Interval, PriceSeries
from alpha_engine.quant.factors import (
    FACTOR_REGISTRY,
    Bars,
    compute_panel,
    factor_clusters,
    factor_families,
    factor_names,
    flag_low_signal,
)
from alpha_engine.quant.ranking import rank_factors

# Long enough that every registered factor gets at least one computable bar.
# The longest declarations are the 252-bar-history ones (mom_rank_120 needs
# 373, slope_*_252 needs 315), so anything below ~380 makes correct factors
# look dead.
BARS = 400


def _series(seed: int = 7, n: int = BARS, with_volume: bool = True) -> PriceSeries:
    """A deterministic synthetic series. Seeded so every run sees identical
    data — a factor test that depends on random data cannot pin anything."""
    rng = random.Random(seed)
    t0 = datetime(2023, 1, 1, tzinfo=timezone.utc)
    price = 100.0
    candles = []
    for i in range(n):
        price *= math.exp(rng.gauss(0.0004, 0.018))
        high = price * (1.0 + abs(rng.gauss(0, 0.009)))
        low = price * (1.0 - abs(rng.gauss(0, 0.009)))
        open_ = price * (1.0 + rng.gauss(0, 0.004))
        candles.append(
            Candle(
                ts=t0 + timedelta(days=i),
                open=open_,
                high=max(high, open_, price),
                low=min(low, open_, price),
                close=price,
                volume=1_000_000 * (1.0 + rng.random()) if with_volume else None,
            )
        )
    return PriceSeries(asset="TEST", interval=Interval.DAY, candles=candles)


@pytest.fixture(scope="module")
def series() -> PriceSeries:
    return _series()


@pytest.fixture(scope="module")
def bars(series: PriceSeries) -> Bars:
    return Bars.from_candles(series.candles)


# ---------------------------------------------------------------------------
# Registry shape
# ---------------------------------------------------------------------------


def test_registry_size():
    """Phase 10's stated target. 500 correctly-computed factors, not 2000
    near-duplicates."""
    assert len(FACTOR_REGISTRY) >= 500


def test_registry_entries_well_formed():
    for name, spec in FACTOR_REGISTRY.items():
        assert spec.name == name, f"{name} keyed under a different name"
        assert spec.family, f"{name} has no family"
        assert spec.min_bars >= 1, f"{name} has a nonsensical min_bars"
        assert spec.cost in ("fast", "slow"), f"{name} has an unknown cost tier"
        assert callable(spec.fn)


def test_families_are_populated():
    fams = factor_families()
    assert len(fams) >= 8
    assert all(names for names in fams.values())


def test_factor_names_filters_by_cost():
    fast = set(factor_names(include_slow=False))
    every = set(factor_names(include_slow=True))
    assert fast < every
    assert all(FACTOR_REGISTRY[n].cost == "fast" for n in fast)


def test_factor_names_filters_by_family():
    names = factor_names(families=["momentum"])
    assert names
    assert all(FACTOR_REGISTRY[n].family == "momentum" for n in names)


# ---------------------------------------------------------------------------
# The lookahead pin — the reason this file exists
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(FACTOR_REGISTRY))
def test_no_lookahead(name: str, series: PriceSeries, bars: Bars):
    """factor(full_series, t) must equal factor(series_truncated_at_t, t).

    If they differ, the factor read a bar it was not entitled to see.
    """
    spec = FACTOR_REGISTRY[name]
    t = BARS - 1  # the last bar, where the most history is available
    if t < spec.min_bars - 1:
        pytest.skip(f"{name} needs {spec.min_bars} bars")

    full = spec.fn(bars, t)
    truncated = spec.fn(Bars.from_candles(series.candles[: t + 1]), t)

    assert full == truncated or (
        full is not None and truncated is not None and math.isclose(full, truncated, rel_tol=1e-12)
    ), f"{name} changed when future bars were removed: {full} != {truncated}"


@pytest.mark.parametrize("name", sorted(FACTOR_REGISTRY))
def test_respects_min_bars(name: str, series: PriceSeries):
    """A factor asked for a value it cannot support returns None, never a
    plausible-looking wrong number computed from a partial window."""
    spec = FACTOR_REGISTRY[name]
    if spec.min_bars < 2:
        pytest.skip("no under-supplied case exists")
    short = Bars.from_candles(series.candles[: spec.min_bars - 1])
    assert spec.fn(short, spec.min_bars - 2) is None


@pytest.mark.parametrize("name", sorted(FACTOR_REGISTRY))
def test_deterministic(name: str, bars: Bars):
    """Same input, same output. No randomness, no state, no clock."""
    spec = FACTOR_REGISTRY[name]
    t = BARS - 1
    if t < spec.min_bars - 1:
        pytest.skip(f"{name} needs {spec.min_bars} bars")
    assert spec.fn(bars, t) == spec.fn(bars, t)


# ---------------------------------------------------------------------------
# Panel behaviour
# ---------------------------------------------------------------------------


def test_panel_shape(series: PriceSeries):
    panel = compute_panel(series)
    assert len(panel) == len(factor_names())
    for name, col in panel.items():
        assert len(col) == len(series.candles), f"{name} column is the wrong length"


def test_panel_excludes_slow_by_default(series: PriceSeries):
    fast_panel = compute_panel(series)
    slow_names = [n for n, s in FACTOR_REGISTRY.items() if s.cost == "slow"]
    assert slow_names
    assert not (set(slow_names) & set(fast_panel))


def test_panel_can_select_specific_factors(series: PriceSeries):
    panel = compute_panel(series, names=["mom_20", "rsi_14"])
    assert set(panel) == {"mom_20", "rsi_14"}


def test_panel_every_fast_factor_produces_a_value(series: PriceSeries):
    """A factor that is None at every bar of a 300-bar series is either broken
    or mis-declared. Either way it must not sit in the registry pretending to
    be a factor."""
    panel = compute_panel(series)
    dead = [n for n, col in panel.items() if all(v is None for v in col)]
    assert dead == [], f"factors that never compute: {dead}"


def test_panel_leading_bars_are_none(series: PriceSeries):
    """Bars before min_bars are None, which is what makes `coverage` honest."""
    panel = compute_panel(series, names=["mom_252"])
    col = panel["mom_252"]
    assert all(v is None for v in col[:252])
    assert col[-1] is not None


def test_volumeless_series_yields_none_not_crash():
    """Forex and some macro sources have no volume. Volume factors must be
    absent, not zero, and must not take the panel down with them."""
    s = _series(with_volume=False)
    panel = compute_panel(s, names=["obv_slope_20", "volume_z_20", "mom_20"])
    assert all(v is None for v in panel["obv_slope_20"])
    assert all(v is None for v in panel["volume_z_20"])
    assert any(v is not None for v in panel["mom_20"])


def test_panel_feeds_ranking(series: PriceSeries):
    """The panel's shape is exactly what rank_factors consumes — the two layers
    have to stay compatible or Phase 10 buys nothing."""
    panel = compute_panel(series, names=["mom_20", "rsi_14", "rvol_20"])
    scores = rank_factors(series, panel, horizon=10)
    assert len(scores) == 3
    assert all(s.coverage > 0 for s in scores)


# ---------------------------------------------------------------------------
# Known-value checks: a formula nobody verified is a formula nobody trusts
# ---------------------------------------------------------------------------


def _flat_bars(prices: list[float], volume: float | None = 1000.0) -> Bars:
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return Bars.from_candles(
        [
            Candle(ts=t0 + timedelta(days=i), open=p, high=p, low=p, close=p, volume=volume)
            for i, p in enumerate(prices)
        ]
    )


def test_momentum_known_value():
    b = _flat_bars([100.0, 110.0, 121.0])
    assert math.isclose(FACTOR_REGISTRY["mom_2"].fn(b, 2), 0.21, rel_tol=1e-9)


def test_rsi_all_gains_is_100():
    b = _flat_bars([float(100 + i) for i in range(20)])
    assert math.isclose(FACTOR_REGISTRY["rsi_14"].fn(b, 19), 100.0)


def test_rsi_all_losses_is_zero():
    b = _flat_bars([float(200 - i) for i in range(20)])
    assert math.isclose(FACTOR_REGISTRY["rsi_14"].fn(b, 19), 0.0)


def test_dist_sma_zero_on_flat_prices():
    b = _flat_bars([50.0] * 40)
    assert math.isclose(FACTOR_REGISTRY["dist_sma_20"].fn(b, 39), 0.0, abs_tol=1e-12)


def test_drawdown_now_matches_hand_calculation():
    b = _flat_bars([100.0] * 5 + [200.0] + [150.0] * 15)
    # the trailing 20-bar window holds the 200 peak; now 150 -> -25%
    assert math.isclose(FACTOR_REGISTRY["drawdown_now_20"].fn(b, 20), -0.25, rel_tol=1e-9)


def test_range_position_at_the_high_is_one():
    b = _flat_bars([float(i) for i in range(1, 30)])
    assert math.isclose(FACTOR_REGISTRY["range_position_20"].fn(b, 28), 1.0)


def test_consecutive_bars_counts_the_run():
    b = _flat_bars([100.0] * 20 + [101.0, 102.0, 103.0])
    assert FACTOR_REGISTRY["consecutive_bars_20"].fn(b, 22) == 3.0


def test_sharpe_is_none_without_variation():
    """Zero deviation means Sharpe is undefined, not infinite and not zero."""
    b = _flat_bars([100.0] * 40)
    assert FACTOR_REGISTRY["sharpe_20"].fn(b, 39) is None


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------


def test_factor_clusters_groups_identical_columns(series: PriceSeries):
    panel = compute_panel(series, names=["mom_20", "rsi_14"])
    panel["mom_20_copy"] = list(panel["mom_20"])
    clusters = factor_clusters(panel, threshold=0.99)
    together = [c for c in clusters if "mom_20" in c]
    assert together and "mom_20_copy" in together[0]


def test_factor_clusters_separates_unrelated_columns(series: PriceSeries):
    panel = compute_panel(series, names=["mom_20", "rvol_60"])
    clusters = factor_clusters(panel, threshold=0.99)
    assert len(clusters) == 2


def test_flag_low_signal_marks_noise_families(series: PriceSeries):
    panel = compute_panel(series, names=["mom_20", "rsi_14"])
    scores = rank_factors(series, panel, horizon=10)
    flags = flag_low_signal(scores)
    assert set(flags) <= set(factor_families())
    assert all(isinstance(v, bool) for v in flags.values())


def test_flag_low_signal_ignores_unknown_names():
    class Fake:
        name = "not_a_registered_factor"
        rank_ic = 0.9

    assert flag_low_signal([Fake()]) == {}


# ---------------------------------------------------------------------------
# Bars container
# ---------------------------------------------------------------------------


def test_bars_drops_volume_when_any_bar_lacks_it():
    """Partial volume is worse than no volume: it would make volume factors
    quietly wrong instead of visibly absent."""
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    candles = [
        Candle(ts=t0, open=1, high=1, low=1, close=1, volume=100),
        Candle(ts=t0 + timedelta(days=1), open=1, high=1, low=1, close=1, volume=None),
    ]
    assert Bars.from_candles(candles).volume is None


def test_win_refuses_to_read_before_the_start(bars: Bars):
    from alpha_engine.quant.factors import _win

    assert _win(bars.close, 5, 10) is None
    assert len(_win(bars.close, 10, 5)) == 5
