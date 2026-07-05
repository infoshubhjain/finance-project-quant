"""Tests for Indian market fixtures. These verify the F&O analyzer produces
reasonable, deterministic results on realistic options chain data.

The fixtures are small enough to verify by hand but representative enough
to catch regressions in the normalization or analysis pipeline.
"""

from __future__ import annotations

from pathlib import Path

from alpha_engine.analyzers.fno_oi import analyze_fno, max_pain, oi_support_resistance, pcr
from alpha_engine.cache.models import OptionsChain
from alpha_engine.ingestion.indian_fno import load_indian_chain
from alpha_engine.schema.signal import Direction

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_chain(name: str) -> OptionsChain:
    return load_indian_chain(FIXTURES_DIR / f"{name}_chain.json")


# --- NIFTY fixture tests --------------------------------------------------------


class TestNiftyFixture:
    def setup_method(self):
        self.chain = _load_chain("nifty")

    def test_chain_loads_correctly(self):
        assert self.chain.underlying == "NIFTY"
        assert self.chain.spot == 24500.0
        assert len(self.chain.quotes) == 10  # 5 strikes * 2 rights

    def test_pcr_is_reasonable(self):
        pcr_val = pcr(self.chain)
        assert pcr_val is not None
        # Put OI > Call OI in this fixture, so PCR > 1.0
        assert pcr_val > 1.0
        assert pcr_val < 2.0  # sanity check

    def test_max_pain_is_near_spot(self):
        mp = max_pain(self.chain)
        assert mp is not None
        # Max pain should be one of the strikes
        strikes = {q.strike for q in self.chain.quotes}
        assert mp in strikes
        # Should be within 1000 points of spot
        assert abs(mp - 24500) <= 1000

    def test_analyzer_produces_direction(self):
        src = analyze_fno(self.chain)
        assert src.direction in (Direction.BULLISH, Direction.BEARISH, Direction.NEUTRAL)
        assert src.weight >= 0

    def test_invalidation_level_is_a_strike(self):
        src = analyze_fno(self.chain)
        invalidation = oi_support_resistance(self.chain, src.direction)
        if src.direction is not Direction.NEUTRAL:
            strikes = {q.strike for q in self.chain.quotes}
            assert invalidation in strikes

    def test_deterministic(self):
        a = analyze_fno(self.chain)
        b = analyze_fno(self.chain)
        assert a.model_dump() == b.model_dump()


# --- BANKNIFTY fixture tests ----------------------------------------------------


class TestBankniftyFixture:
    def setup_method(self):
        self.chain = _load_chain("banknifty")

    def test_chain_loads_correctly(self):
        assert self.chain.underlying == "BANKNIFTY"
        assert self.chain.spot == 52000.0
        assert len(self.chain.quotes) == 10

    def test_pcr_is_reasonable(self):
        pcr_val = pcr(self.chain)
        assert pcr_val is not None
        # Put OI > Call OI in this fixture
        assert pcr_val > 1.0

    def test_max_pain_is_near_spot(self):
        mp = max_pain(self.chain)
        assert mp is not None
        strikes = {q.strike for q in self.chain.quotes}
        assert mp in strikes
        assert abs(mp - 52000) <= 2000

    def test_analyzer_produces_direction(self):
        src = analyze_fno(self.chain)
        assert src.direction in (Direction.BULLISH, Direction.BEARISH, Direction.NEUTRAL)
        assert src.weight >= 0

    def test_deterministic(self):
        a = analyze_fno(self.chain)
        b = analyze_fno(self.chain)
        assert a.model_dump() == b.model_dump()


# --- Edge cases -----------------------------------------------------------------


class TestEdgeCases:
    def test_empty_chain_is_neutral(self):
        chain = OptionsChain(
            underlying="NIFTY",
            expiry="2026-07-30T00:00:00Z",
            spot=24500.0,
            quotes=[],
        )
        src = analyze_fno(chain)
        assert src.direction is Direction.NEUTRAL
        assert src.weight == 0.0

    def test_single_strike_chain(self):
        chain = OptionsChain(
            underlying="NIFTY",
            expiry="2026-07-30T00:00:00Z",
            spot=24500.0,
            quotes=[
                {"strike": 24500, "right": "call", "oi": 1000},
                {"strike": 24500, "right": "put", "oi": 1500},
            ],
        )
        src = analyze_fno(chain)
        assert src.direction in (Direction.BULLISH, Direction.BEARISH, Direction.NEUTRAL)
        # PCR should be 1.5
        assert pcr(chain) == 1.5

    def test_heavily_call_heavy_chain_is_bearish(self):
        chain = OptionsChain(
            underlying="NIFTY",
            expiry="2026-07-30T00:00:00Z",
            spot=24500.0,
            quotes=[
                {"strike": 24000, "right": "call", "oi": 5000, "oi_change": 1000},
                {"strike": 24500, "right": "call", "oi": 4000, "oi_change": 800},
                {"strike": 25000, "right": "call", "oi": 3000, "oi_change": 600},
                {"strike": 24000, "right": "put", "oi": 500, "oi_change": 100},
                {"strike": 24500, "right": "put", "oi": 400, "oi_change": 50},
            ],
        )
        src = analyze_fno(chain)
        assert src.direction is Direction.BEARISH
        assert src.weight > 0

    def test_heavily_put_heavy_chain_is_bullish(self):
        chain = OptionsChain(
            underlying="NIFTY",
            expiry="2026-07-30T00:00:00Z",
            spot=24500.0,
            quotes=[
                {"strike": 24000, "right": "put", "oi": 5000, "oi_change": 1000},
                {"strike": 24500, "right": "put", "oi": 4000, "oi_change": 800},
                {"strike": 25000, "right": "put", "oi": 3000, "oi_change": 600},
                {"strike": 24000, "right": "call", "oi": 500, "oi_change": 100},
                {"strike": 24500, "right": "call", "oi": 400, "oi_change": 50},
            ],
        )
        src = analyze_fno(chain)
        assert src.direction is Direction.BULLISH
        assert src.weight > 0
