"""Command-line interface. This is what a developer-user runs first. It wires the
whole pipeline end to end:

    ingest -> cache -> analyze -> synthesize -> narrate -> record -> print

Run:
    python -m alpha_engine.cli.main scan BTC          # crypto, no key needed
    python -m alpha_engine.cli.main scan AAPL         # US equity, no key needed
    python -m alpha_engine.cli.main backtest BTC --days 365
    python -m alpha_engine.cli.main record-stats

Market is auto-detected (mapped crypto symbols -> crypto; anything else -> US
equity) and can be forced with --market. Equity scans blend the trend read with
a macro-context tilt when FRED data is available; with no FRED_API_KEY they
degrade gracefully to trend-only. No command ever requires a key.
"""

from __future__ import annotations

import argparse
import os
import sys

from alpha_engine.analyzers.crypto_trend import analyze_trend, trend_invalidation
from alpha_engine.analyzers.equity_trend import analyze_equity_trend
from alpha_engine.analyzers.macro_context import analyze_macro
from alpha_engine.cache.interface import Cache
from alpha_engine.cache.models import MacroObservation, PriceSeries
from alpha_engine.ingestion import coingecko, fred, yahoo
from alpha_engine.narrative.narrator import write_thesis
from alpha_engine.schema.signal import Market, SignalSource, Timeframe
from alpha_engine.synthesis.synthesize import synthesize
from alpha_engine.validation.backtest import run_backtest
from alpha_engine.validation.outcomes import score_record, summarize_outcomes
from alpha_engine.validation.recorder import read_records, record_signal


def detect_market(asset: str, override: str | None = None) -> Market:
    """Mapped crypto symbols are crypto; everything else is a US equity ticker.
    Explicit --market always wins over detection."""
    if override:
        return Market(override)
    return Market.CRYPTO if coingecko.supports(asset) else Market.US_EQUITY


def _load_series(
    asset: str, market: Market, days: int, no_refresh: bool, cache: Cache
) -> PriceSeries:
    """Shared cache-or-fetch path for commands that need a daily series. A fresh
    cache still gets refetched if it clearly covers less history than requested
    (e.g. `backtest --days 365` right after a 90-day scan) — silently backtesting
    a quarter when the user asked for a year would be quietly dishonest."""
    series, stale = cache.get_price(asset, "1d")
    too_short = series is not None and len(series.candles) < (days * 3) // 5
    if series is None or ((stale or too_short) and not no_refresh):
        source = "CoinGecko" if market is Market.CRYPTO else "Yahoo Finance"
        print(f"[ingest] fetching {asset} daily from {source}...", file=sys.stderr)
        fetcher = coingecko.fetch_daily if market is Market.CRYPTO else yahoo.fetch_daily
        series = fetcher(asset, days=days, cache=cache)
    else:
        print(f"[cache] using cached {asset} ({len(series.candles)} bars)", file=sys.stderr)
    return series


def _load_macro(cache: Cache, no_refresh: bool) -> dict[str, list[MacroObservation]]:
    """Best-effort macro data: serve from cache, refresh stale series only when a
    FRED key exists, and never crash the scan over macro. Empty dict = no data."""
    data: dict[str, list[MacroObservation]] = {}
    have_key = bool(os.environ.get("FRED_API_KEY"))
    for series_id in fred.MACRO_SERIES:
        obs, stale = cache.get_macro(series_id)
        if (not obs or (stale and not no_refresh)) and have_key:
            try:
                print(f"[ingest] fetching {series_id} from FRED...", file=sys.stderr)
                obs = fred.fetch_series(series_id, cache=cache)
            except Exception as e:  # noqa: BLE001 - macro is optional context
                print(f"[macro] {series_id} fetch failed: {e}", file=sys.stderr)
        if obs:
            data[series_id] = obs
    if not data and not have_key:
        print(
            "[macro] FRED_API_KEY not set; scanning without macro context "
            "(free key: https://fred.stlouisfed.org)",
            file=sys.stderr,
        )
    return data


def cmd_scan(args: argparse.Namespace) -> int:
    cache = Cache()
    asset = args.asset.upper()
    market = detect_market(asset, args.market)

    try:
        series = _load_series(asset, market, args.days, args.no_refresh, cache)
    except Exception as e:  # noqa: BLE001 - surface any fetch issue clearly
        print(f"[error] fetch failed: {e}", file=sys.stderr)
        return 1

    sources: list[SignalSource] = []
    if market is Market.CRYPTO:
        sources.append(analyze_trend(series))
    else:
        sources.append(analyze_equity_trend(series))
        macro_data = _load_macro(cache, args.no_refresh)
        if macro_data:
            sources.append(analyze_macro(macro_data))

    signal = synthesize(
        asset=asset,
        market=market,
        sources=sources,
        timeframe=Timeframe.SWING,
    )
    # Invalidation is computed from the synthesized direction so the level always
    # matches the view the signal actually expresses.
    invalidation = trend_invalidation(series.candles, signal.direction)
    signal = signal.model_copy(update={"invalidation_level": invalidation})
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
    market = detect_market(asset, args.market)

    try:
        series = _load_series(asset, market, args.days, args.no_refresh, cache)
    except Exception as e:  # noqa: BLE001
        print(f"[error] fetch failed: {e}", file=sys.stderr)
        return 1

    report = run_backtest(series, market=market, step=args.step)
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


def _add_market_args(sub: argparse.ArgumentParser, default_days: int) -> None:
    sub.add_argument("asset", help="symbol, e.g. BTC, ETH, AAPL, MSFT")
    sub.add_argument(
        "--market",
        choices=[Market.CRYPTO.value, Market.US_EQUITY.value],
        default=None,
        help="force the market instead of auto-detecting",
    )
    sub.add_argument("--days", type=int, default=default_days, help="history window to fetch")
    sub.add_argument("--no-refresh", action="store_true", help="use cache even if stale")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="alpha-engine", description="Open research engine for market signals.")
    sub = p.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="generate a signal for one asset")
    _add_market_args(scan, default_days=90)
    scan.add_argument("--no-record", action="store_true", help="do not append to the signal log")
    scan.set_defaults(func=cmd_scan)

    bt = sub.add_parser("backtest", help="replay history through the analyzer, no lookahead")
    _add_market_args(bt, default_days=365)
    bt.add_argument("--step", type=int, default=1, help="bars between simulated signals")
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
