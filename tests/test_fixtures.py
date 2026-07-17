from __future__ import annotations

import json
from pathlib import Path

from alpha_engine.analyzers.fno_oi import (
    analyze_fno,
    max_pain,
    oi_support_resistance,
    pcr,
)
from alpha_engine.cache.models import OptionsChain
from alpha_engine.schema.signal import Direction

FIXTURES_ROOT = Path(__file__).parent / "fixtures"


def test_load_breeze_fno_fixture():
    path = FIXTURES_ROOT / "breeze_fno_nifty.json"
    raw = json.loads(path.read_text())
    assert isinstance(raw, list)


def test_load_angelone_fno_fixture():
    path = FIXTURES_ROOT / "angelone_fno_nifty.json"
    raw = json.loads(path.read_text())
    assert isinstance(raw, dict)


def test_load_dhan_fno_fixture():
    path = FIXTURES_ROOT / "dhan_fno_nifty.json"
    raw = json.loads(path.read_text())
    assert isinstance(raw, dict)


class TestFNODeterminism:
    chain = OptionsChain.model_validate(
        {
            "underlying": "NIFTY",
            "spot": 17800.5,
            "expiry": "2026-07-30T00:00:00Z",
            "quotes": [
                {
                    "strike": 17800,
                    "oi": 1000,
                    "right": "call",
                },
                {
                    "strike": 17800,
                    "oi": 1500,
                    "right": "put",
                },
            ],
        }
    )

    def test_pcr_is_deterministic(self):
        a = pcr(self.chain)
        b = pcr(self.chain)
        assert a == b

    def test_max_pain_is_deterministic(self):
        a = max_pain(self.chain)
        b = max_pain(self.chain)
        assert a == b

    def test_oi_shifts_is_deterministic(self):
        a = oi_support_resistance(self.chain, Direction.BULLISH)
        b = oi_support_resistance(self.chain, Direction.BULLISH)
        assert a == b

    def test_deterministic(self):
        a = analyze_fno(self.chain)
        b = analyze_fno(self.chain)
        assert a.model_dump() == b.model_dump()


class TestEdgeCases:
    def test_empty_chain_is_neutral(self):
        chain = OptionsChain.model_validate(
            {
                "underlying": "NIFTY",
                "expiry": "2026-07-30T00:00:00Z",
                "spot": 24500.0,
                "quotes": [],
            }
        )
        src = analyze_fno(chain)
        assert src.direction is Direction.NEUTRAL
        assert src.weight == 0.0

    def test_single_strike_chain(self):
        chain = OptionsChain.model_validate(
            {
                "underlying": "NIFTY",
                "expiry": "2026-07-30T00:00:00Z",
                "spot": 24500.0,
                "quotes": [
                    {"strike": 24500, "right": "call", "oi": 1000},
                    {"strike": 24500, "right": "put", "oi": 1500},
                ],
            }
        )
        src = analyze_fno(chain)
        assert src.direction in (Direction.BULLISH, Direction.BEARISH, Direction.NEUTRAL)
        res = pcr(chain)
        assert res is not None and res == 1.5

    def test_heavily_call_heavy_chain_is_bearish(self):
        chain = OptionsChain.model_validate(
            {
                "underlying": "NIFTY",
                "expiry": "2026-07-30T00:00:00Z",
                "spot": 24500.0,
                "quotes": [
                    {"strike": 24000, "right": "call", "oi": 5000, "oi_change": 1000},
                    {"strike": 24500, "right": "call", "oi": 4000, "oi_change": 800},
                    {"strike": 25000, "right": "call", "oi": 3000, "oi_change": 600},
                    {"strike": 24000, "right": "put", "oi": 500, "oi_change": 100},
                    {"strike": 24500, "right": "put", "oi": 400, "oi_change": 50},
                ],
            }
        )
        src = analyze_fno(chain)
        assert src.direction is Direction.BEARISH
        assert src.weight > 0

    def test_heavily_put_heavy_chain_is_bullish(self):
        chain = OptionsChain.model_validate(
            {
                "underlying": "NIFTY",
                "expiry": "2026-07-30T00:00:00Z",
                "spot": 24500.0,
                "quotes": [
                    {"strike": 24000, "right": "put", "oi": 5000, "oi_change": 1000},
                    {"strike": 24500, "right": "put", "oi": 4000, "oi_change": 800},
                    {"strike": 25000, "right": "put", "oi": 3000, "oi_change": 600},
                    {"strike": 24000, "right": "call", "oi": 500, "oi_change": 100},
                    {"strike": 24500, "right": "call", "oi": 400, "oi_change": 50},
                ],
            }
        )
        src = analyze_fno(chain)
        assert src.direction is Direction.BULLISH
        assert src.weight > 0
