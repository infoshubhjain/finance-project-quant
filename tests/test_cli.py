"""CLI smoke tests: the argument parser wires every subcommand, market
auto-detection routes correctly, and the scan/report commands run end to end
against a synthetic cached series — no network, no writes outside tmp_path.

These exist to catch wiring regressions (a renamed flag, a dropped default, a
subcommand losing its handler) that the layer-level unit tests can't see.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone

from alpha_engine.cache.models import Candle, Interval, PriceSeries
from alpha_engine.cli import main as cli
from alpha_engine.schema.signal import Market


def _series(n: int = 150) -> PriceSeries:
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    candles = []
    prev = 100.0
    for i in range(n):
        close = 100.0 * math.exp(0.002 * i) + math.sin(i * 0.5)
        candles.append(
            Candle(
                ts=t0 + timedelta(days=i),
                open=prev,
                high=max(prev, close) * 1.004,
                low=min(prev, close) * 0.996,
                close=close,
                volume=1000.0 + 5.0 * i,
            )
        )
        prev = close
    return PriceSeries(asset="BTC", interval=Interval.DAY, candles=candles)


# --- parser wiring -----------------------------------------------------------

# (argv, expected handler) — one line per subcommand keeps drift visible.
_SUBCOMMANDS = [
    (["scan", "BTC"], cli.cmd_scan),
    (["scan-chain", "chain.json"], cli.cmd_scan_chain),
    (["fetch-chain", "NIFTY", "--expiry", "2026-07-30"], cli.cmd_fetch_chain),
    (["watch", "BTC", "AAPL"], cli.cmd_watch),
    (["backtest", "BTC"], cli.cmd_backtest),
    (["report", "BTC"], cli.cmd_report),
    (["record-stats"], cli.cmd_record_stats),
    (["scan-all"], cli.cmd_scan_all),
    (["batch"], cli.cmd_batch),
]


def test_every_subcommand_parses_and_routes():
    parser = cli.build_parser()
    for argv, handler in _SUBCOMMANDS:
        args = parser.parse_args(argv)
        assert args.func is handler, f"{argv[0]} routed to {args.func.__name__}"


def test_report_defaults():
    args = cli.build_parser().parse_args(["report", "btc"])
    assert args.days == 365
    assert args.json is False
    assert args.market is None


def test_scan_defaults():
    args = cli.build_parser().parse_args(["scan", "BTC"])
    assert args.days == 90
    assert args.no_record is False
    assert args.no_refresh is False


# --- market auto-detection ---------------------------------------------------


def test_detect_market_routing():
    assert cli.detect_market("BTC") is Market.CRYPTO
    assert cli.detect_market("NIFTY") is Market.IN_FNO
    assert cli.detect_market("RELIANCE.NS") is Market.IN_EQUITY
    assert cli.detect_market("EURUSD") is Market.FOREX
    assert cli.detect_market("AAPL") is Market.US_EQUITY
    # explicit override always wins
    assert cli.detect_market("BTC", "us_equity") is Market.US_EQUITY


# --- end-to-end command runs (loader monkeypatched, no network) --------------


def test_cmd_scan_end_to_end(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "_load_series", lambda *a, **k: _series())
    monkeypatch.setattr(cli, "_load_macro", lambda *a, **k: {})
    monkeypatch.chdir(tmp_path)  # data/ writes (cache dirs) land in tmp

    args = cli.build_parser().parse_args(["scan", "BTC", "--no-record"])
    assert args.func(args) == 0

    signal = json.loads(capsys.readouterr().out)
    assert signal["asset"] == "BTC"
    assert signal["direction"] in {"bullish", "bearish", "neutral"}
    assert 0.0 <= signal["confidence"] <= 1.0
    assert signal["thesis"]  # narrator filled it


def test_cmd_scan_records_to_the_log(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "_load_series", lambda *a, **k: _series())
    monkeypatch.setattr(cli, "_load_macro", lambda *a, **k: {})
    monkeypatch.chdir(tmp_path)

    args = cli.build_parser().parse_args(["scan", "BTC"])
    assert args.func(args) == 0
    log = tmp_path / "data" / "signals" / "signals.jsonl"
    assert log.exists() and log.read_text().count("\n") == 1


def test_cmd_report_end_to_end_json(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "_load_series", lambda *a, **k: _series())
    monkeypatch.chdir(tmp_path)

    args = cli.build_parser().parse_args(["report", "BTC", "--json"])
    assert args.func(args) == 0

    report = json.loads(capsys.readouterr().out)
    assert report["asset"] == "BTC"
    assert report["overall_score"] is None or 0 <= report["overall_score"] <= 100
    assert len(report["features"]) == 51
    assert "not investment advice" in report["disclaimer"]


def test_cmd_report_short_history_fails_cleanly(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "_load_series", lambda *a, **k: _series(30))
    monkeypatch.chdir(tmp_path)

    args = cli.build_parser().parse_args(["report", "BTC"])
    assert args.func(args) == 1  # clean error exit, not a traceback
    assert "60" in capsys.readouterr().err


# --- strategy + terminal commands --------------------------------------------


def test_new_subcommands_parse_and_route():
    parser = cli.build_parser()
    for argv, handler in [
        (["strategies"], cli.cmd_strategies),
        (["strategy-backtest", "BTC", "--strategy", "SMACrossover"], cli.cmd_strategy_backtest),
        (["terminal"], cli.cmd_terminal),
    ]:
        assert parser.parse_args(argv).func is handler


def test_strategy_backtest_defaults():
    args = cli.build_parser().parse_args(["strategy-backtest", "BTC", "--strategy", "SMACrossover"])
    assert args.days == 365
    assert args.trade_on == "underlying"
    assert args.no_confirmation is False
    assert args.capital == 100_000.0
    assert args.cost_bps == 2.0


def test_cmd_strategies_lists_the_builtins(capsys):
    assert cli.cmd_strategies(cli.build_parser().parse_args(["strategies"])) == 0
    out = capsys.readouterr().out
    assert "SMACrossover" in out
    assert "RSIReversal" in out
    assert "fast_length=9" in out  # default params are shown


def test_cmd_strategy_backtest_end_to_end(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "_load_series", lambda *a, **k: _series())
    monkeypatch.chdir(tmp_path)

    args = cli.build_parser().parse_args(
        ["strategy-backtest", "BTC", "--strategy", "SMACrossover", "--json"]
    )
    assert args.func(args) == 0

    report = json.loads(capsys.readouterr().out)
    assert report["strategy"] == "SMA Crossover"
    assert report["lookahead_violations"] == []
    assert "sharpe" in report["metrics"]
    assert report["disclaimer"]


def test_cmd_strategy_backtest_param_overrides_are_typed(monkeypatch, capsys, tmp_path):
    """--param fast_length=4 must reach the strategy as int 4, not "4"."""
    monkeypatch.setattr(cli, "_load_series", lambda *a, **k: _series())
    monkeypatch.chdir(tmp_path)

    args = cli.build_parser().parse_args(
        [
            "strategy-backtest",
            "BTC",
            "--strategy",
            "SMACrossover",
            "--param",
            "fast_length=4",
            "--param",
            "slow_length=30",
            "--json",
        ]
    )
    assert args.func(args) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["params"] == {"fast_length": 4, "slow_length": 30}


def test_cmd_strategy_backtest_rejects_a_malformed_param(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_load_series", lambda *a, **k: _series())
    monkeypatch.chdir(tmp_path)
    args = cli.build_parser().parse_args(
        ["strategy-backtest", "BTC", "--strategy", "SMACrossover", "--param", "nonsense"]
    )
    assert args.func(args) == 2


def test_cmd_strategy_backtest_unknown_strategy_lists_the_known_ones(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "_load_series", lambda *a, **k: _series())
    monkeypatch.chdir(tmp_path)
    args = cli.build_parser().parse_args(["strategy-backtest", "BTC", "--strategy", "Nope"])
    assert args.func(args) == 1
    assert "SMACrossover" in capsys.readouterr().err


def test_cmd_terminal_without_a_key_explains_how_to_bring_one(monkeypatch, capsys):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    args = cli.build_parser().parse_args(["terminal", "hello"])
    assert args.func(args) == 2
    err = capsys.readouterr().err
    assert "uses YOUR key" in err
    assert "platform.openai.com" in err  # tells them where to get one
