"""Tests for the F&O open-interest analyzer. The max-pain and PCR fixtures are
small enough to verify by hand, which is the point: every decision-bearing
number in this analyzer should be checkable with a calculator and the chain.

Hand-check for the max-pain fixture (two strikes, 100 and 110):
  expire at 100 -> calls pay 0; puts pay (110-100)*120 = 1200 -> total 1200
  expire at 110 -> calls pay (110-100)*100 = 1000; puts pay 0 -> total 1000
  so max pain = 110 (the cheaper day for option writers).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from argparse import Namespace

from alpha_engine.analyzers.fno_oi import (
    MAX_WEIGHT,
    analyze_fno,
    max_pain,
    oi_support_resistance,
    pcr,
    summarize_chain,
)
from alpha_engine.cache.interface import Cache, LocalStore
from alpha_engine.cache.models import OptionQuote, OptionRight, OptionsChain
from alpha_engine.cli import main as cli_main
from alpha_engine.cli.main import detect_market
from alpha_engine.ingestion.indian_broker import BrokerNotConfiguredError
from alpha_engine.ingestion.indian_fno import load_indian_chain, parse_indian_chain_payload
from alpha_engine.schema.signal import Direction, Market, Signal, Timeframe

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


# --- pcr ----------------------------------------------------------------------


def test_pcr_matches_hand_computation():
    # puts (50 + 120) / calls (100 + 80) = 170 / 180
    assert pcr(MAX_PAIN_FIXTURE) == (170 / 180)


def test_pcr_is_none_without_call_oi():
    puts_only = _chain([_q(100, OptionRight.PUT, 500)])
    assert pcr(puts_only) is None
    assert pcr(_chain([])) is None


# --- max pain -------------------------------------------------------------------


def test_max_pain_matches_hand_computation():
    assert max_pain(MAX_PAIN_FIXTURE) == 110


def test_max_pain_empty_chain_is_none():
    assert max_pain(_chain([])) is None


def test_max_pain_tie_resolves_to_lowest_strike():
    # Symmetric chain: both strikes produce the same payout, lowest must win
    # so the answer is deterministic.
    sym = _chain(
        [
            _q(100, OptionRight.CALL, 10),
            _q(110, OptionRight.PUT, 10),
        ]
    )
    # at 100: calls 0, puts (110-100)*10 = 100 ; at 110: calls (110-100)*10 = 100, puts 0
    assert max_pain(sym) == 100


# --- OI support/resistance ------------------------------------------------------


def test_put_wall_is_bullish_invalidation():
    chain = _chain(
        [
            _q(19000, OptionRight.PUT, 500),
            _q(19500, OptionRight.PUT, 2000),  # the wall
            _q(20500, OptionRight.CALL, 1800),
        ],
        spot=20000.0,
    )
    assert oi_support_resistance(chain, Direction.BULLISH) == 19500


def test_call_wall_is_bearish_invalidation():
    chain = _chain(
        [
            _q(20500, OptionRight.CALL, 3000),  # the wall
            _q(21000, OptionRight.CALL, 900),
            _q(19500, OptionRight.PUT, 2000),
        ],
        spot=20000.0,
    )
    assert oi_support_resistance(chain, Direction.BEARISH) == 20500


def test_walls_beyond_spot_do_not_count():
    # The biggest put pile sits ABOVE spot; a floor above you is not a floor.
    chain = _chain(
        [
            _q(21000, OptionRight.PUT, 5000),
            _q(19500, OptionRight.PUT, 1000),
        ],
        spot=20000.0,
    )
    assert oi_support_resistance(chain, Direction.BULLISH) == 19500


def test_neutral_direction_has_no_invalidation():
    assert oi_support_resistance(MAX_PAIN_FIXTURE, Direction.NEUTRAL) is None


# --- the combined source ---------------------------------------------------------


def _put_heavy_chain() -> OptionsChain:
    """PCR ~1.67 (bullish vote), max pain tied to spot (neutral vote), fresh
    put writing far outpacing calls (bullish vote) -> score +0.67, bullish."""
    return _chain(
        [
            _q(19500, OptionRight.PUT, 3000, oi_change=800),
            _q(20000, OptionRight.PUT, 2000, oi_change=400),
            _q(20500, OptionRight.CALL, 1500, oi_change=200),
            _q(21000, OptionRight.CALL, 1500, oi_change=100),
        ],
        spot=20000.0,
    )


def test_put_heavy_chain_reads_bullish():
    src = analyze_fno(_put_heavy_chain())
    assert src.name == "fno.oi"
    assert src.direction is Direction.BULLISH
    assert 0 < src.weight <= MAX_WEIGHT
    assert "pcr=" in src.detail and "max_pain=" in src.detail and "put_wall=" in src.detail


def test_summarize_chain_reports_key_levels():
    summary = summarize_chain(_put_heavy_chain())
    assert summary["pcr"] is not None
    assert summary["max_pain"] is not None
    assert summary["put_wall"] is not None
    assert summary["call_wall"] is not None


def test_call_heavy_chain_reads_bearish():
    chain = _chain(
        [
            _q(19500, OptionRight.PUT, 800, oi_change=100),
            _q(20000, OptionRight.CALL, 3000, oi_change=900),
            _q(19800, OptionRight.CALL, 2500, oi_change=700),
        ],
        spot=20000.0,
    )
    src = analyze_fno(chain)
    assert src.direction is Direction.BEARISH
    assert src.weight > 0


def test_empty_chain_is_neutral_zero():
    src = analyze_fno(_chain([]))
    assert src.direction is Direction.NEUTRAL
    assert src.weight == 0.0


def test_missing_oi_change_skips_that_vote():
    no_change = _chain(
        [
            _q(19500, OptionRight.PUT, 3000),
            _q(20500, OptionRight.CALL, 1500),
        ],
        spot=20000.0,
    )
    src = analyze_fno(no_change)
    assert "oi_new_puts" not in src.detail  # the vote simply didn't happen


def test_analyze_fno_is_deterministic():
    a = analyze_fno(_put_heavy_chain())
    b = analyze_fno(_put_heavy_chain())
    assert a.model_dump() == b.model_dump()


# --- cache round trip -------------------------------------------------------------


def test_chain_survives_cache_round_trip(tmp_path):
    cache = Cache(store=LocalStore(root=tmp_path))
    original = _put_heavy_chain()
    cache.put_chain(original)
    loaded, stale = cache.get_chain("NIFTY")
    assert loaded is not None
    assert not stale  # freshly written
    assert loaded.model_dump() == original.model_dump()
    # And the analyzer gives the same answer on the reloaded chain: the cache
    # is a faithful courier, not an editor.
    assert analyze_fno(loaded).model_dump() == analyze_fno(original).model_dump()


def test_missing_chain_reads_as_none_and_stale(tmp_path):
    cache = Cache(store=LocalStore(root=tmp_path))
    chain, stale = cache.get_chain("NIFTY")
    assert chain is None
    assert stale


def test_scan_chain_fixture_uses_offline_fno_pipeline(tmp_path, monkeypatch, capsys):
    cache = Cache(store=LocalStore(root=tmp_path))
    chain = _put_heavy_chain()
    fixture = tmp_path / "nifty_chain.json"
    fixture.write_text(chain.model_dump_json(indent=2))

    monkeypatch.setattr(cli_main, "Cache", lambda: cache)
    rc = cli_main.cmd_scan_chain(Namespace(chain_file=str(fixture), no_record=True))

    assert rc == 0
    out = capsys.readouterr().out
    assert '"asset": "NIFTY"' in out
    assert '"market": "in_fno"' in out
    loaded, stale = cache.get_chain("NIFTY")
    assert loaded is not None
    assert not stale
    assert loaded.model_dump() == chain.model_dump()


def test_raw_broker_style_payload_normalizes_into_chain():
    payload = {
        "underlying": "NIFTY",
        "expiry": "2026-07-30T00:00:00Z",
        "spot": 20000,
        "records": [
            {
                "strikePrice": 19500,
                "CE": {
                    "openInterest": 1500,
                    "changeinOpenInterest": 200,
                    "totalTradedVolume": 90,
                    "lastPrice": 72.5,
                },
                "PE": {
                    "openInterest": 3000,
                    "changeinOpenInterest": 800,
                    "totalTradedVolume": 120,
                    "lastPrice": 98.0,
                },
            }
        ],
    }

    chain = parse_indian_chain_payload(payload)
    assert chain.underlying == "NIFTY"
    assert chain.spot == 20000.0
    assert len(chain.quotes) == 2
    assert {q.right for q in chain.quotes} == {OptionRight.CALL, OptionRight.PUT}
    assert chain.quotes[0].oi_change is not None


def test_load_indian_chain_handles_raw_payload(tmp_path):
    raw = {
        "underlying": "NIFTY",
        "expiry": "2026-07-30T00:00:00Z",
        "records": [
            {
                "strikePrice": 20000,
                "CE": {"openInterest": 1000},
                "PE": {"openInterest": 2000},
            }
        ],
    }
    path = tmp_path / "raw_chain.json"
    path.write_text(json.dumps(raw))

    loaded = load_indian_chain(path)
    assert loaded.underlying == "NIFTY"
    assert len(loaded.quotes) == 2


def test_scan_chain_underlying_override_handles_raw_payload(tmp_path, monkeypatch, capsys):
    cache = Cache(store=LocalStore(root=tmp_path))
    raw = {
        "expiry": "2026-07-30T00:00:00Z",
        "records": [
            {
                "strikePrice": 20000,
                "CE": {"openInterest": 1000},
                "PE": {"openInterest": 2000},
            }
        ],
    }
    path = tmp_path / "raw_chain.json"
    path.write_text(json.dumps(raw))

    monkeypatch.setattr(cli_main, "Cache", lambda: cache)
    rc = cli_main.cmd_scan_chain(
        Namespace(chain_file=str(path), underlying="NIFTY", no_record=True)
    )

    assert rc == 0
    out = capsys.readouterr().out
    assert '"asset": "NIFTY"' in out
    loaded, stale = cache.get_chain("NIFTY")
    assert loaded is not None
    assert not stale


def test_fetch_chain_uses_breeze_client_and_caches_chain(tmp_path, monkeypatch, capsys):
    cache = Cache(store=LocalStore(root=tmp_path))
    chain = _put_heavy_chain()

    class FakeBreezeClient:
        def fetch_chain(self, underlying: str, expiry_date: str):
            assert underlying == "NIFTY"
            assert expiry_date == "2026-07-30"
            return chain

        @classmethod
        def from_env(cls):
            return cls()

    monkeypatch.setattr(cli_main, "Cache", lambda: cache)
    monkeypatch.setattr(cli_main, "BreezeLiveClient", FakeBreezeClient)

    rc = cli_main.cmd_fetch_chain(Namespace(asset="NIFTY", expiry="2026-07-30", no_record=True))

    assert rc == 0
    out = capsys.readouterr().out
    assert '"asset": "NIFTY"' in out
    assert '"market": "in_fno"' in out
    loaded, stale = cache.get_chain("NIFTY")
    assert loaded is not None
    assert not stale
    assert loaded.model_dump() == chain.model_dump()


def test_fetch_chain_reports_missing_credentials(tmp_path, monkeypatch, capsys):
    cache = Cache(store=LocalStore(root=tmp_path))

    class FakeMissingClient:
        @classmethod
        def from_env(cls):
            raise BrokerNotConfiguredError("BREEZE_API_KEY is required for Breeze")

    monkeypatch.setattr(cli_main, "Cache", lambda: cache)
    monkeypatch.setattr(cli_main, "BreezeLiveClient", FakeMissingClient)

    rc = cli_main.cmd_fetch_chain(Namespace(asset="NIFTY", expiry="2026-07-30", no_record=True))

    assert rc == 1
    err = capsys.readouterr().err
    assert "BREEZE_API_KEY" in err


def test_watch_prints_a_summary_table(monkeypatch, capsys):
    dummy_chain = _put_heavy_chain()
    dummy_series = _chain([])
    crypto_signal = Signal(
        asset="BTC",
        market=Market.CRYPTO,
        direction=Direction.BULLISH,
        confidence=0.83,
        timeframe=Timeframe.SWING,
    )
    fno_signal = Signal(
        asset="NIFTY",
        market=Market.IN_FNO,
        direction=Direction.BEARISH,
        confidence=0.61,
        timeframe=Timeframe.SWING,
    )

    class FakeCache:
        def get_chain(self, underlying: str):
            return (dummy_chain if underlying == "NIFTY" else None, False)

        def get_price(self, asset: str, interval: str):
            return (dummy_series, False)

    monkeypatch.setattr(cli_main, "Cache", lambda: FakeCache())
    monkeypatch.setattr(cli_main, "_load_series", lambda *args, **kwargs: dummy_series)
    monkeypatch.setattr(cli_main, "_build_price_signal", lambda *args, **kwargs: crypto_signal)
    monkeypatch.setattr(cli_main, "_build_fno_signal", lambda *args, **kwargs: fno_signal)

    rc = cli_main.cmd_watch(
        Namespace(assets=["BTC", "NIFTY"], market=None, days=7, no_refresh=True, record=False)
    )

    assert rc == 0
    out = capsys.readouterr().out
    assert "asset" in out and "market" in out and "direction" in out
    assert "BTC" in out and "NIFTY" in out


# --- market detection ---------------------------------------------------------------


def test_indian_indexes_route_to_fno():
    assert detect_market("NIFTY") is Market.IN_FNO
    assert detect_market("banknifty") is Market.IN_FNO
    assert detect_market("AAPL") is Market.US_EQUITY
    assert detect_market("RELIANCE.NS") is Market.IN_EQUITY
