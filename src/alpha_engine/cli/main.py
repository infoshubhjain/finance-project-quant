"""Command-line interface. This is what a developer-user runs first. It wires the
whole pipeline end to end on the zero-key default path:

    ingest (CoinGecko) -> cache -> analyze (trend) -> synthesize -> narrate
        -> record (immutable log) -> print

Run:
    python -m alpha_engine.cli.main scan BTC
    python -m alpha_engine.cli.main scan ETH --no-refresh
    python -m alpha_engine.cli.main backtest BTC --days 365
    python -m alpha_engine.cli.main record-stats

`scan` produces one Signal, appends it to the immutable signal log, and prints it
as JSON. `backtest` replays cached history through the analyzer with no lookahead
and prints the honest hit-rate/calibration report. `record-stats` scores every
recorded live signal against the freshest cached prices. No API key required.
"""

from __future__ import annotations

import argparse
import sys

from alpha_engine.analyzers.crypto_trend import analyze_trend, trend_invalidation
from alpha_engine.cache.interface import Cache
from alpha_engine.ingestion.coingecko import fetch_daily
from alpha_engine.narrative.narrator import write_thesis
from alpha_engine.schema.signal import Market, Timeframe
from alpha_engine.synthesis.synthesize import synthesize
from alpha_engine.validation.backtest import run_backtest
from alpha_engine.validation.outcomes import score_record, summarize_outcomes
from alpha_engine.validation.recorder import read_records, record_signal


def _load_series(asset: str, days: int, no_refresh: bool, cache: Cache):
    """Shared cache-or-fetch path for commands that need a daily series."""
    series, stale = cache.get_price(asset, "1d")
    if series is None or (stale and not no_refresh):
        print(f"[ingest] fetching {asset} daily from CoinGecko...", file=sys.stderr)
        series = fetch_daily(asset, days=days, cache=cache)
    else:
        print(f"[cache] using cached {asset} ({len(series.candles)} bars)", file=sys.stderr)
    return series


def cmd_scan(args: argparse.Namespace) -> int:
    cache = Cache()
    asset = args.asset.upper()

    try:
        series = _load_series(asset, args.days, args.no_refresh, cache)
    except Exception as e:  # noqa: BLE001 - surface any fetch issue clearly
        print(f"[error] fetch failed: {e}", file=sys.stderr)
        return 1

    trend = analyze_trend(series)
    invalidation = trend_invalidation(series.candles, trend.direction)

    signal = synthesize(
        asset=asset,
        market=Market.CRYPTO,
        sources=[trend],
        timeframe=Timeframe.SWING,
        invalidation_level=invalidation,
    )
    signal = write_thesis(signal)

    if not args.no_record:
        entry = series.candles[-1].close if series.candles else None
        record = record_signal(signal, entry_price=entry)
        print(f"[record] appended {record.record_id} to data/signals/", file=sys.stderr)

    print(signal.model_dump_json(indent=2))
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    cache = Cache()
    asset = args.asset.upper()

    try:
        series = _load_series(asset, args.days, args.no_refresh, cache)
    except Exception as e:  # noqa: BLE001
        print(f"[error] fetch failed: {e}", file=sys.stderr)
        return 1

    report = run_backtest(series, step=args.step)
    print(report.model_dump_json(indent=2))
    return 0


def cmd_record_stats(args: argparse.Namespace) -> int:
    """Score every recorded live signal against cached prices. Reads only the
    local cache — run a fresh `scan` first if you want newer price data."""
    records = read_records()
    if not records:
        print("[record-stats] no recorded signals yet; run `scan` first.", file=sys.stderr)
        return 0

    cache = Cache()
    scored = []
    for record in records:
        series, _stale = cache.get_price(record.signal.asset, "1d")
        if series is None:
            continue  # asset no longer cached; counted below as skipped
        scored.append((record.signal.confidence, score_record(record, series)))

    skipped = len(records) - len(scored)
    if skipped:
        print(f"[record-stats] {skipped} record(s) skipped: no cached prices", file=sys.stderr)

    summary = summarize_outcomes(scored)
    print(summary.model_dump_json(indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="alpha-engine", description="Open research engine for market signals.")
    sub = p.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="generate a signal for one asset")
    scan.add_argument("asset", help="symbol, e.g. BTC, ETH, SOL")
    scan.add_argument("--days", type=int, default=90, help="history window to fetch")
    scan.add_argument("--no-refresh", action="store_true", help="use cache even if stale")
    scan.add_argument("--no-record", action="store_true", help="do not append to the signal log")
    scan.set_defaults(func=cmd_scan)

    bt = sub.add_parser("backtest", help="replay history through the analyzer, no lookahead")
    bt.add_argument("asset", help="symbol, e.g. BTC, ETH, SOL")
    bt.add_argument("--days", type=int, default=365, help="history window to fetch")
    bt.add_argument("--step", type=int, default=1, help="bars between simulated signals")
    bt.add_argument("--no-refresh", action="store_true", help="use cache even if stale")
    bt.set_defaults(func=cmd_backtest)

    stats = sub.add_parser("record-stats", help="score recorded live signals against outcomes")
    stats.set_defaults(func=cmd_record_stats)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
