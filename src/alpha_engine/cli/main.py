"""Command-line interface. This is what a developer-user runs first. It wires the
whole pipeline end to end:

    ingest -> cache -> analyze -> synthesize -> narrate -> record -> print

Run:
    python -m alpha_engine.cli.main scan BTC          # crypto, no key needed
    python -m alpha_engine.cli.main scan AAPL         # US equity, no key needed
    python -m alpha_engine.cli.main backtest BTC --days 365
    python -m alpha_engine.cli.main watch BTC AAPL NIFTY
    python -m alpha_engine.cli.main record-stats

Market is auto-detected (mapped crypto symbols -> crypto; Indian index symbols
-> F&O; `.NS` / `.BO` suffixes -> Indian equities; currency pairs like EURUSD
-> forex; everything else -> US equity) and can be forced with --market.
Equity scans blend the trend read with a macro-context tilt when FRED data is
available; with no FRED_API_KEY they degrade gracefully to trend-only. The
default crypto/US-equity paths never require a key; forex needs OANDA
credentials and live F&O chains need a broker key, both degrading to a clear
message without one.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from alpha_engine.analyzers.crypto_trend import analyze_trend, trend_invalidation
from alpha_engine.analyzers.equity_trend import analyze_equity_trend
from alpha_engine.analyzers.fno_oi import analyze_fno, oi_support_resistance
from alpha_engine.analyzers.forex_trend import analyze_forex_trend
from alpha_engine.analyzers.macd import analyze_macd
from alpha_engine.analyzers.macro_context import analyze_macro
from alpha_engine.analyzers.multi_timeframe import analyze_multi_timeframe
from alpha_engine.analyzers.rsi import analyze_rsi
from alpha_engine.analyzers.support_resistance import analyze_support_resistance
from alpha_engine.analyzers.volatility import analyze_volatility, volatility_scalar
from alpha_engine.analyzers.vwap import analyze_vwap
from alpha_engine.analyzers.bollinger import analyze_bollinger
from alpha_engine.analyzers.volume import analyze_volume
from alpha_engine.cache.interface import Cache
from alpha_engine.cache.models import MacroObservation, OptionsChain, PriceSeries
from alpha_engine.ingestion.breeze import BreezeLiveClient
from alpha_engine.ingestion import binance, coingecko, coingecko_pro, fred, oanda, yahoo
from alpha_engine.ingestion.indian_broker import BrokerNotConfiguredError
from alpha_engine.ingestion.indian_fno import load_indian_chain
from alpha_engine.narrative.narrator import write_thesis
from alpha_engine.quant.report import build_report, render_text
from alpha_engine.schema.signal import Market, Signal, SignalSource, Timeframe
from alpha_engine.synthesis.synthesize import synthesize
from alpha_engine.validation.backtest import run_backtest, run_per_analyzer_backtest
from alpha_engine.validation.outcomes import score_record, summarize_outcomes
from alpha_engine.validation.recorder import read_records, record_signal


# Indian index symbols that route to the F&O path. Extend as chains land.
_IN_FNO_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"}
_IN_EQUITY_SUFFIXES = (".NS", ".BO")


def detect_market(asset: str, override: str | None = None) -> Market:
    """Mapped crypto symbols are crypto, known Indian indexes are F&O,
    currency pairs (EURUSD, EUR/USD) are forex, and everything else is a US
    equity ticker. Explicit --market always wins."""
    if override:
        return Market(override)
    asset = asset.upper()
    if coingecko.supports(asset):
        return Market.CRYPTO
    if asset in _IN_FNO_SYMBOLS:
        return Market.IN_FNO
    if asset.endswith(_IN_EQUITY_SUFFIXES):
        return Market.IN_EQUITY
    if oanda.supports(asset):
        return Market.FOREX
    return Market.US_EQUITY


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
        if market is Market.CRYPTO:
            series = _fetch_crypto_daily(asset, days, cache)
        elif market is Market.FOREX:
            print(f"[ingest] fetching {asset} daily from OANDA...", file=sys.stderr)
            series = oanda.fetch_daily(asset, days=days, cache=cache)
        else:
            print(f"[ingest] fetching {asset} daily from Yahoo Finance...", file=sys.stderr)
            series = yahoo.fetch_daily(asset, days=days, cache=cache)
    else:
        print(f"[cache] using cached {asset} ({len(series.candles)} bars)", file=sys.stderr)
    return series


def _fetch_crypto_daily(asset: str, days: int, cache: Cache) -> PriceSeries:
    """Crypto fetch chain: CoinGecko Pro when a key exists, then keyless
    CoinGecko, then the keyless Binance fallback.

    Each step degrades loudly to the next so a CoinGecko 429 (rate limit)
    never kills a scan, and the last error propagates honestly if every
    source fails."""
    if coingecko_pro.has_key():
        try:
            print(f"[ingest] fetching {asset} daily from CoinGecko Pro...", file=sys.stderr)
            return coingecko_pro.fetch_daily(asset, days=days, cache=cache)
        except Exception as e:  # noqa: BLE001 - fall back to the keyless tier
            print(f"[ingest] CoinGecko Pro failed ({e}); trying keyless tier", file=sys.stderr)
    try:
        print(f"[ingest] fetching {asset} daily from CoinGecko...", file=sys.stderr)
        return coingecko.fetch_daily(asset, days=days, cache=cache)
    except Exception as e:  # noqa: BLE001 - fall back to Binance
        print(f"[ingest] CoinGecko failed ({e}); falling back to Binance", file=sys.stderr)
    return binance.fetch_daily(asset, days=days, cache=cache)


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


def _scan_fno(asset: str, cache: Cache, args: argparse.Namespace, use_llm: bool = False) -> int:
    """The Indian F&O path reads an options chain, not a price series, so it has
    its own flow. `scan` itself never fetches a chain: it reads whatever the
    cache holds (put there by `fetch-chain` with broker credentials, or by
    `scan-chain` from a JSON fixture). Missing data degrades to a clear
    message, never a crash."""
    chain, stale = cache.get_chain(asset)
    if chain is None:
        print(
            f"[error] no options chain cached for {asset}. Fetch one live with "
            f"`fetch-chain {asset} --expiry YYYY-MM-DD --broker breeze|angelone|dhan` "
            f"(needs broker credentials in .env), or run the analytics offline by "
            f"dropping a normalized OptionsChain JSON at data/cache/chain/{asset}.json "
            f"and using `scan-chain` (see cache/models.py for the shape).",
            file=sys.stderr,
        )
        return 1
    return _scan_fno_chain(asset, chain, stale, cache, args, use_llm=use_llm)


def _scan_fno_chain(
    asset: str,
    chain: OptionsChain,
    stale: bool,
    cache: Cache,
    args: argparse.Namespace,
    use_llm: bool = False,
) -> int:
    """Run the deterministic F&O pipeline on a normalized chain."""
    if stale:
        print(
            f"[cache] warning: {asset} chain is stale (fetched {chain.fetched_at}); "
            f"OI reads age quickly",
            file=sys.stderr,
        )

    signal = _build_fno_signal(asset, chain, use_llm=use_llm)

    if not args.no_record:
        record = record_signal(signal, entry_price=chain.spot)
        print(f"[record] appended {record.record_id} to data/signals/", file=sys.stderr)

    print(signal.model_dump_json(indent=2))
    return 0


def _fetch_fno_chain(
    asset: str,
    expiry_date: str,
    cache: Cache,
    args: argparse.Namespace,
) -> int:
    """Fetch a live Indian options chain and immediately run the deterministic
    F&O analyzer on it.

    Supports Breeze and Angel One. The broker is selected via --broker flag
    (default: breeze). The fetch is deliberately gated behind env credentials
    so the default repo still stays keyless.
    """
    broker = getattr(args, "broker", "breeze")

    try:
        if broker == "angelone":
            from alpha_engine.ingestion.angelone import AngelOneLiveClient

            client = AngelOneLiveClient.from_env()
        elif broker == "dhan":
            from alpha_engine.ingestion.dhan import DhanLiveClient

            client = DhanLiveClient.from_env()
        else:
            client = BreezeLiveClient.from_env()
        chain = client.fetch_chain(asset, expiry_date)
    except BrokerNotConfiguredError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001 - surface live broker issues clearly
        print(f"[error] {broker} fetch failed: {e}", file=sys.stderr)
        return 1

    cache.put_chain(chain)
    print(
        f"[cache] saved {asset} options chain for {expiry_date} to data/cache/chain/",
        file=sys.stderr,
    )
    return _scan_fno_chain(asset, chain, False, cache, args)


def _build_fno_signal(asset: str, chain: OptionsChain, use_llm: bool = False) -> Signal:
    """Build the deterministic F&O signal for one chain."""
    source = analyze_fno(chain)
    signal = synthesize(
        asset=asset,
        market=Market.IN_FNO,
        sources=[source],
        timeframe=Timeframe.SWING,
    )
    invalidation = oi_support_resistance(chain, signal.direction)
    signal = signal.model_copy(update={"invalidation_level": invalidation})
    signal = write_thesis(signal, use_llm=use_llm)
    return signal


def _load_chain_file(path: str | Path, underlying: str | None = None) -> OptionsChain:
    """Load a normalized options-chain fixture from disk."""
    return load_indian_chain(path, underlying=underlying)


def cmd_scan_chain(args: argparse.Namespace) -> int:
    """Analyze a normalized options-chain fixture from disk.

    This is the offline Phase 3 entry point: no broker credentials, no network,
    just the deterministic PCR / max-pain / OI-shift math against a fixture.
    The loaded chain is also written to the local cache so `scan NIFTY` can
    reuse the same normalized payload afterward.
    """
    cache = Cache()
    try:
        chain = _load_chain_file(args.chain_file, underlying=getattr(args, "underlying", None))
    except Exception as e:  # noqa: BLE001 - fixture loading should fail loudly
        print(f"[error] failed to load chain fixture: {e}", file=sys.stderr)
        return 1

    cache.put_chain(chain)
    return _scan_fno_chain(chain.underlying, chain, False, cache, args)


def cmd_fetch_chain(args: argparse.Namespace) -> int:
    """Fetch a live Indian options chain from Breeze and analyze it."""
    cache = Cache()
    asset = args.asset.upper()
    return _fetch_fno_chain(asset, args.expiry, cache, args)


def cmd_scan(args: argparse.Namespace) -> int:
    cache = Cache()
    asset = args.asset.upper()
    market = detect_market(asset, args.market)
    use_llm = getattr(args, "llm", False)

    if market is Market.IN_FNO:
        return _scan_fno(asset, cache, args, use_llm=use_llm)

    try:
        series = _load_series(asset, market, args.days, args.no_refresh, cache)
    except Exception as e:  # noqa: BLE001 - surface any fetch issue clearly
        print(f"[error] fetch failed: {e}", file=sys.stderr)
        return 1

    signal = _build_price_signal(asset, market, series, cache, args.no_refresh, use_llm=use_llm)

    if not args.no_record:
        entry = series.candles[-1].close if series.candles else None
        record = record_signal(signal, entry_price=entry)
        print(f"[record] appended {record.record_id} to data/signals/", file=sys.stderr)

    print(signal.model_dump_json(indent=2))
    return 0


def _build_price_signal(
    asset: str,
    market: Market,
    series: PriceSeries,
    cache: Cache,
    no_refresh: bool,
    use_llm: bool = False,
) -> Signal:
    """Build the deterministic price-series signal for crypto or equities.

    Uses multiple analyzers for a richer synthesis:
    - Trend (dual MA) — core directional read (market-specific)
    - RSI, MACD — momentum confirmation
    - Bollinger Bands — volatility/position context
    - Volume (OBV), VWAP — participation reads (skipped when volume is absent)
    - Support/resistance, multi-horizon alignment — structure reads
    - Macro context (equities only) — when FRED data available
    - Volatility regime — contextual; extreme tape dampens every other weight
    """
    from alpha_engine.analyzers.indian_equity import analyze_indian_equity

    sources: list[SignalSource] = []

    # Core directional read is market-specific; everything after is shared.
    if market is Market.CRYPTO:
        sources.append(analyze_trend(series))
    elif market is Market.IN_EQUITY:
        sources.append(analyze_indian_equity(series))
    elif market is Market.FOREX:
        sources.append(analyze_forex_trend(series))
    else:
        sources.append(analyze_equity_trend(series))

    sources.append(analyze_rsi(series))
    sources.append(analyze_macd(series))
    sources.append(analyze_bollinger(series))
    sources.append(analyze_multi_timeframe(series))
    sources.append(analyze_support_resistance(series))
    for volume_src in (analyze_volume(series), analyze_vwap(series)):
        if volume_src.weight > 0:
            sources.append(volume_src)

    if market in (Market.IN_EQUITY, Market.US_EQUITY):
        macro_data = _load_macro(cache, no_refresh)
        if macro_data:
            sources.append(analyze_macro(macro_data))

    # Volatility regime: extreme tape scales every directional weight down
    # (deterministically), and the regime itself lands in the audit trail.
    scalar = volatility_scalar(series)
    if scalar != 1.0:
        sources = [s.model_copy(update={"weight": round(s.weight * scalar, 4)}) for s in sources]
    sources.append(analyze_volatility(series))

    signal = synthesize(
        asset=asset,
        market=market,
        sources=sources,
        timeframe=Timeframe.SWING,
    )
    invalidation = trend_invalidation(series.candles, signal.direction)
    signal = signal.model_copy(update={"invalidation_level": invalidation})
    signal = write_thesis(signal, use_llm=use_llm)
    return signal


def cmd_backtest(args: argparse.Namespace) -> int:
    cache = Cache()
    asset = args.asset.upper()
    market = detect_market(asset, args.market)

    if market is Market.IN_FNO:
        print(
            "[error] F&O backtesting needs a history of options chains, which no "
            "free source provides yet. Price-series backtests cover crypto and "
            "US equities.",
            file=sys.stderr,
        )
        return 1

    try:
        series = _load_series(asset, market, args.days, args.no_refresh, cache)
    except Exception as e:  # noqa: BLE001
        print(f"[error] fetch failed: {e}", file=sys.stderr)
        return 1

    if getattr(args, "per_analyzer", False):
        reports = run_per_analyzer_backtest(series, market=market, step=args.step)
        print(
            json.dumps({name: r.model_dump(mode="json") for name, r in reports.items()}, indent=2)
        )
        return 0

    # Equity backtests replay macro context point-in-time (observations dated
    # after the simulated bar stay invisible — see backtest.py).
    macro_data = None
    if market in (Market.US_EQUITY, Market.IN_EQUITY):
        macro_data = _load_macro(cache, args.no_refresh) or None

    report = run_backtest(series, market=market, step=args.step, macro_data=macro_data)
    print(report.model_dump_json(indent=2))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Full quant metrics report: regime, scores, volatility forecast,
    fair-value distances, indicator values and a templated verdict. Same
    fetch path as scan/backtest; all numbers deterministic."""
    cache = Cache()
    asset = args.asset.upper()
    market = detect_market(asset, args.market)

    if market is Market.IN_FNO:
        print(
            "[error] the quant report needs a price series; F&O indexes have "
            "options chains, not candles. Try the underlying via Yahoo "
            "(e.g. ^NSEI with --market us_equity) or use scan-chain.",
            file=sys.stderr,
        )
        return 1

    try:
        series = _load_series(asset, market, args.days, args.no_refresh, cache)
    except Exception as e:  # noqa: BLE001 - surface any fetch issue clearly
        print(f"[error] fetch failed: {e}", file=sys.stderr)
        return 1

    try:
        report = build_report(series, market=market.value)
    except ValueError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 1

    if args.json:
        print(report.model_dump_json(indent=2))
    else:
        print(render_text(report))
    return 0


def _format_table(rows: list[dict[str, str]]) -> str:
    headers = ["asset", "market", "direction", "confidence", "status", "note"]
    widths = {h: len(h) for h in headers}
    for row in rows:
        for h in headers:
            widths[h] = max(widths[h], len(row.get(h, "")))

    def fmt(values: dict[str, str]) -> str:
        return "  ".join(values.get(h, "").ljust(widths[h]) for h in headers)

    lines = [fmt({h: h for h in headers})]
    lines.append("  ".join("-" * widths[h] for h in headers))
    for row in rows:
        lines.append(fmt(row))
    return "\n".join(lines)


def cmd_watch(args: argparse.Namespace) -> int:
    """Scan a batch of assets and print a compact table.

    This is a CLI convenience layer on top of the same deterministic analyzers
    used by `scan`; it is intentionally read-friendly, not a trading surface.
    """
    cache = Cache()
    rows: list[dict[str, str]] = []
    use_llm = getattr(args, "llm", False)

    for raw_asset in args.assets:
        asset = raw_asset.upper()
        market = detect_market(asset, args.market)
        try:
            if market is Market.IN_FNO:
                chain, stale = cache.get_chain(asset)
                if chain is None:
                    raise FileNotFoundError(
                        f"no cached chain for {asset}; run scan-chain first or drop a JSON file"
                    )
                signal = _build_fno_signal(asset, chain, use_llm=use_llm)
                status = "stale" if stale else "ok"
                if args.record:
                    record = record_signal(signal, entry_price=chain.spot)
                    print(
                        f"[record] appended {record.record_id} for {asset} to data/signals/",
                        file=sys.stderr,
                    )
            else:
                series = _load_series(asset, market, args.days, args.no_refresh, cache)
                signal = _build_price_signal(
                    asset, market, series, cache, args.no_refresh, use_llm=use_llm
                )
                status = "stale" if cache.get_price(asset, "1d")[1] else "ok"
                if args.record:
                    entry = series.candles[-1].close if series.candles else None
                    record = record_signal(signal, entry_price=entry)
                    print(
                        f"[record] appended {record.record_id} for {asset} to data/signals/",
                        file=sys.stderr,
                    )
        except Exception as e:  # noqa: BLE001 - batch view should keep going
            rows.append(
                {
                    "asset": asset,
                    "market": market.value,
                    "direction": "error",
                    "confidence": "-",
                    "status": str(e),
                    "note": "",
                }
            )
            continue

        note = signal.signal_sources[0].detail[:80] if signal.signal_sources else ""

        rows.append(
            {
                "asset": asset,
                "market": market.value,
                "direction": signal.direction.value,
                "confidence": f"{signal.confidence:.2f}",
                "status": status,
                "note": note,
            }
        )

    sort = getattr(args, "sort", None)
    if sort:
        if sort == "confidence":
            rows.sort(
                key=lambda row: float(row["confidence"]) if row["confidence"] != "-" else -1.0,
                reverse=True,
            )
        else:
            rows.sort(key=lambda row: row[sort])

    print(_format_table(rows))
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


def cmd_scan_all(args: argparse.Namespace) -> int:
    """Scan all configured assets across all markets. The batch entry point."""
    from alpha_engine.orchestrator import (
        load_config,
        run_batch,
    )

    config = load_config(
        config_path=getattr(args, "config", None),
        assets=getattr(args, "assets", None),
        days=args.days,
        record=not args.no_record,
        use_llm=getattr(args, "llm", False),
    )

    print(f"[scan-all] scanning {len(config.targets)} assets...", file=sys.stderr)
    report = run_batch(config)

    print(
        f"\n[scan-all] done: {report.ok}/{report.total} ok, {report.errors} errors", file=sys.stderr
    )
    print(json.dumps(report.summary(), indent=2))
    return 0 if report.errors == 0 else 1


def cmd_batch(args: argparse.Namespace) -> int:
    """Run a scheduled batch scan with report output. Cron-friendly."""

    from alpha_engine.orchestrator import (
        load_config,
        run_scheduled,
    )

    config = load_config(
        config_path=getattr(args, "config", None),
        assets=getattr(args, "assets", None),
        days=args.days,
        record=not args.no_record,
        use_llm=getattr(args, "llm", False),
    )

    output = getattr(args, "output", None)
    report = run_scheduled(config, output_path=output)

    if output:
        print(f"[batch] report written to {output}", file=sys.stderr)

    print(f"[batch] done: {report.ok}/{report.total} ok, {report.errors} errors", file=sys.stderr)
    return 0 if report.errors == 0 else 1


def cmd_dashboard(args: argparse.Namespace) -> int:
    """Launch the read-only web dashboard."""
    import sys as _sys

    host = getattr(args, "host", "127.0.0.1")
    port = getattr(args, "port", 8000)

    try:
        from web.server import main as web_main

        return web_main([f"--host={host}", f"--port={port}"])
    except ImportError:
        print(
            f"[error] web server module not found. Run directly:\n"
            f"  python -m web.server --host {host} --port {port}",
            file=_sys.stderr,
        )
        return 1


def _add_market_args(sub: argparse.ArgumentParser, default_days: int) -> None:
    sub.add_argument("asset", help="symbol, e.g. BTC, ETH, AAPL, NIFTY")
    sub.add_argument(
        "--market",
        choices=[
            Market.CRYPTO.value,
            Market.US_EQUITY.value,
            Market.IN_EQUITY.value,
            Market.IN_FNO.value,
            Market.FOREX.value,
        ],
        default=None,
        help="force the market instead of auto-detecting",
    )
    sub.add_argument("--days", type=int, default=default_days, help="history window to fetch")
    sub.add_argument("--no-refresh", action="store_true", help="use cache even if stale")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="alpha-engine", description="Open research engine for market signals."
    )
    sub = p.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="generate a signal for one asset")
    _add_market_args(scan, default_days=90)
    scan.add_argument("--no-record", action="store_true", help="do not append to the signal log")
    scan.add_argument(
        "--llm", action="store_true", help="use optional LLM to rephrase thesis (needs LLM_API_KEY)"
    )
    scan.set_defaults(func=cmd_scan)

    scan_chain = sub.add_parser(
        "scan-chain",
        help="generate an F&O signal from a normalized OptionsChain JSON fixture",
    )
    scan_chain.add_argument("chain_file", help="path to a normalized OptionsChain JSON file")
    scan_chain.add_argument(
        "--underlying",
        default=None,
        help="override the underlying symbol when the raw payload omits it",
    )
    scan_chain.add_argument(
        "--no-record", action="store_true", help="do not append to the signal log"
    )
    scan_chain.set_defaults(func=cmd_scan_chain)

    fetch_chain = sub.add_parser(
        "fetch-chain",
        help="fetch a live Indian options chain from Breeze or Angel One and analyze it",
    )
    fetch_chain.add_argument("asset", help="underlying symbol, e.g. NIFTY")
    fetch_chain.add_argument(
        "--expiry",
        required=True,
        help="expiry date to fetch, in YYYY-MM-DD format",
    )
    fetch_chain.add_argument(
        "--broker",
        choices=["breeze", "angelone", "dhan"],
        default="breeze",
        help="broker to fetch from (default: breeze)",
    )
    fetch_chain.add_argument(
        "--no-record", action="store_true", help="do not append to the signal log"
    )
    fetch_chain.set_defaults(func=cmd_fetch_chain)

    watch = sub.add_parser(
        "watch",
        help="scan multiple assets and print a compact table",
    )
    watch.add_argument("assets", nargs="+", help="symbols to scan, e.g. BTC AAPL NIFTY")
    watch.add_argument(
        "--market",
        choices=[m.value for m in Market],
        default=None,
        help="force one market for every asset",
    )
    watch.add_argument("--days", type=int, default=90, help="history window to fetch")
    watch.add_argument("--no-refresh", action="store_true", help="use cache even if stale")
    watch.add_argument("--record", action="store_true", help="append results to the signal log")
    watch.add_argument(
        "--llm", action="store_true", help="use optional LLM to rephrase thesis (needs LLM_API_KEY)"
    )
    watch.add_argument(
        "--sort",
        choices=["asset", "market", "confidence"],
        default=None,
        help="sort the batch output before printing",
    )
    watch.set_defaults(func=cmd_watch)

    bt = sub.add_parser("backtest", help="replay history through the analyzer, no lookahead")
    _add_market_args(bt, default_days=365)
    bt.add_argument("--step", type=int, default=1, help="bars between simulated signals")
    bt.add_argument(
        "--per-analyzer",
        action="store_true",
        help="backtest each analyzer in isolation plus the blend, for comparison",
    )
    bt.set_defaults(func=cmd_backtest)

    report = sub.add_parser(
        "report", help="full quant metrics report (regime, scores, vol forecast, indicators)"
    )
    _add_market_args(report, default_days=365)
    report.add_argument("--json", action="store_true", help="emit the full report as JSON")
    report.set_defaults(func=cmd_report)

    stats = sub.add_parser("record-stats", help="score recorded live signals against outcomes")
    stats.set_defaults(func=cmd_record_stats)

    scan_all = sub.add_parser("scan-all", help="scan all configured assets across all markets")
    scan_all.add_argument("--config", default=None, help="path to portfolio.json config file")
    scan_all.add_argument(
        "--assets", nargs="+", default=None, help="explicit asset list, e.g. BTC AAPL NIFTY"
    )
    scan_all.add_argument("--days", type=int, default=90, help="history window to fetch")
    scan_all.add_argument(
        "--no-record", action="store_true", help="do not append to the signal log"
    )
    scan_all.add_argument("--llm", action="store_true", help="use optional LLM to rephrase thesis")
    scan_all.set_defaults(func=cmd_scan_all)

    batch = sub.add_parser("batch", help="scheduled batch scan with report output (cron-friendly)")
    batch.add_argument("--config", default=None, help="path to portfolio.json config file")
    batch.add_argument("--assets", nargs="+", default=None, help="explicit asset list")
    batch.add_argument("--days", type=int, default=90, help="history window to fetch")
    batch.add_argument("--no-record", action="store_true", help="do not append to the signal log")
    batch.add_argument("--llm", action="store_true", help="use optional LLM to rephrase thesis")
    batch.add_argument("--output", default=None, help="write JSON report to this path")
    batch.set_defaults(func=cmd_batch)

    dashboard = sub.add_parser("dashboard", help="launch the read-only web dashboard")
    dashboard.add_argument("--host", default="127.0.0.1", help="bind address")
    dashboard.add_argument("--port", type=int, default=8000, help="port to listen on")
    dashboard.set_defaults(func=cmd_dashboard)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
