from __future__ import annotations

from datetime import datetime, timezone

from alpha_engine.analyzers.fno_oi import (
    MAX_WEIGHT,
    analyze_fno,
    max_pain,
    oi_support_resistance,
    pcr,
)
from alpha_engine.cache.models import OptionQuote, OptionRight, OptionsChain
from alpha_engine.cli import main as cli_main
from alpha_engine.schema.signal import Direction

EXPIRY = datetime(2026, 7, 30, tzinfo=timezone.utc)


def _q(strike: float, right: OptionRight, oi: float, oi_change: float | None = None):
    return OptionQuote(strike=strike, right=right, oi=oi, oi_change=oi_change)


def _chain(quotes: list[OptionQuote], spot: float | None = None) -> OptionsChain:
    return OptionsChain(underlying="NIFTY", expiry=EXPIRY, spot=spot, quotes=quotes)


MAX_PAIN_FIXTURE = _chain(
    [
        _q(100, OptionRight.CALL, 100),
        _q(100, OptionRight.PUT, 50),
        _q(110, OptionRight.CALL, 80),
        _q(110, OptionRight.PUT, 120),
    ],
    spot=105.0,
)


def test_pcr_matches_hand_computation():
    res = pcr(MAX_PAIN_FIXTURE)
    assert res is not None and res == (170 / 180)


def test_pcr_is_none_without_call_oi():
    puts_only = _chain([_q(100, OptionRight.PUT, 500)])
    assert pcr(puts_only) is None
    assert pcr(_chain([])) is None


def test_max_pain_matches_hand_computation():
    res = max_pain(MAX_PAIN_FIXTURE)
    assert res == 110


def test_max_pain_empty_chain_is_none():
    res = max_pain(_chain([]))
    assert res is None


def test_max_pain_tie_resolves_to_lowest_strike():
    sym = _chain(
        [
            _q(100, OptionRight.CALL, 10),
            _q(110, OptionRight.PUT, 10),
        ]
    )
    res = max_pain(sym)
    assert res == 100


def test_call_wall_above_spot_is_bearish_invalidation():
    # spot (19000) is below the call strikes. The 20000 wall is the binding one.
    chain = _chain(
        [
            _q(19000, OptionRight.CALL, 100),
            _q(19500, OptionRight.CALL, 50),
            _q(20000, OptionRight.CALL, 2000),  # the wall
        ],
        spot=19000.0,
    )
    res = oi_support_resistance(chain, direction=Direction.BEARISH)
    assert res == 20000


def test_bullish_invalidates_below_put_wall():
    # spot (20000) is above the put strikes. The 19500 wall is the binding floor.
    chain = _chain(
        [
            _q(19000, OptionRight.PUT, 500),
            _q(19500, OptionRight.PUT, 2000),  # the wall
        ],
        spot=20000.0,
    )
    sup = oi_support_resistance(chain, direction=Direction.BULLISH)
    assert sup == 19500


class TestAnalyzeFno:
    def test_bullish_pcr_is_bullish_signal(self):
        chain = _chain(
            [
                _q(100, OptionRight.CALL, 100),
                _q(100, OptionRight.PUT, 150),
            ]
        )
        src = analyze_fno(chain)
        assert src.direction is Direction.BULLISH

    def test_bearish_pcr_is_bearish_signal(self):
        chain = _chain(
            [
                _q(100, OptionRight.CALL, 150),
                _q(100, OptionRight.PUT, 100),
            ]
        )
        src = analyze_fno(chain)
        assert src.direction is Direction.BEARISH

    def test_spot_above_max_pain_is_bearish(self):
        chain = _chain(MAX_PAIN_FIXTURE.quotes, spot=115.0)
        src = analyze_fno(chain)
        assert src.direction is Direction.BEARISH

    def test_spot_below_max_pain_is_bullish(self):
        chain = _chain(MAX_PAIN_FIXTURE.quotes, spot=105.0)
        src = analyze_fno(chain)
        assert src.direction is Direction.BULLISH

    def test_max_weight_capped(self):
        chain = _chain(
            [
                _q(100, OptionRight.PUT, 2000),
                _q(110, OptionRight.CALL, 100),
            ],
            spot=105,
        )
        src = analyze_fno(chain)
        assert src.weight <= MAX_WEIGHT

    def test_deterministic(self):
        a = analyze_fno(MAX_PAIN_FIXTURE)
        b = analyze_fno(MAX_PAIN_FIXTURE)
        assert a.model_dump() == b.model_dump()


class TestFullScan:
    def test_full_fno_signal_build(self):
        sig = cli_main._build_fno_signal("NIFTY", MAX_PAIN_FIXTURE)
        assert sig.direction in (Direction.BULLISH, Direction.BEARISH, Direction.NEUTRAL)
