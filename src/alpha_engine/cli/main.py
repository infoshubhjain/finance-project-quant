"""Command-line interface. This is what a developer-user runs first. It wires the
whole pipeline end to end on the zero-key default path:

    ingest (CoinGecko) -> cache -> analyze (trend) -> synthesize -> narrate -> print

Run:
    python -m alpha_engine.cli.main scan BTC
    python -m alpha_engine.cli.main scan ETH --no-refresh

`scan` produces one Signal and prints it as JSON. No API key required.
"""

from __future__ import annotations

import argparse
import sys

from alpha_engine.analyzers.crypto_trend import analyze_trend
from alpha_engine.cache.interface import Cache
from alpha_engine.ingestion.coingecko import fetch_daily
from alpha_engine.narrative.narrator import write_thesis
from alpha_engine.schema.signal import Market, Timeframe
from alpha_engine.synthesis.synthesize import synthesize


def cmd_scan(args: argparse.Namespace) -> int:
    cache = Cache()
    asset = args.asset.upper()

    series, stale = cache.get_price(asset, "1d")
    if series is None or (stale and not args.no_refresh):
        print(f"[ingest] fetching {asset} daily from CoinGecko...", file=sys.stderr)
        try:
            series = fetch_daily(asset, days=args.days, cache=cache)
        except Exception as e:  # noqa: BLE001 - surface any fetch issue clearly
            print(f"[error] fetch failed: {e}", file=sys.stderr)
            return 1
    else:
        print(f"[cache] using cached {asset} ({len(series.candles)} bars)", file=sys.stderr)

    trend = analyze_trend(series)

    # A simple, honest invalidation level for a trend read: the most recent low.
    lows = [c.low for c in series.candles[-10:]] if series.candles else []
    invalidation = min(lows) if lows else None

    signal = synthesize(
        asset=asset,
        market=Market.CRYPTO,
        sources=[trend],
        timeframe=Timeframe.SWING,
        invalidation_level=invalidation,
    )
    signal = write_thesis(signal)

    print(signal.model_dump_json(indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="alpha-engine", description="Open research engine for market signals.")
    sub = p.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="generate a signal for one asset")
    scan.add_argument("asset", help="symbol, e.g. BTC, ETH, SOL")
    scan.add_argument("--days", type=int, default=90, help="history window to fetch")
    scan.add_argument("--no-refresh", action="store_true", help="use cache even if stale")
    scan.set_defaults(func=cmd_scan)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
