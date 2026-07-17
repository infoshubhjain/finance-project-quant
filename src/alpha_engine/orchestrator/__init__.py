"""Multi-agent orchestrator. Decides which analyzers to fire and when.

This is the "always-on" brain of the engine: a scheduled process that keeps
the cache fresh and produces signals across all configured markets without
manual per-asset invocation.

Design principles:
- Scheduled batch, not always-on services. Same architecture, far less ops cost.
- Each asset scan is independent and fault-isolated — one failure never blocks
  another.
- The orchestrator owns configuration (which assets to scan, how often) but
  never owns analysis — analyzers remain pure functions in their own layer.
- Determinism in the decision path is preserved: the orchestrator only decides
  WHAT to scan and WHEN, never HOW to analyze.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from alpha_engine.cache.interface import Cache
from alpha_engine.schema.signal import Market, Signal

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Default portfolio: keyless assets that work on a fresh clone.
# Users override this via a JSON config file or env vars.
DEFAULT_PORTFOLIO: list[dict[str, Any]] = [
    {"asset": "BTC", "market": "crypto"},
    {"asset": "ETH", "market": "crypto"},
    {"asset": "SOL", "market": "crypto"},
    {"asset": "AAPL", "market": "us_equity"},
    {"asset": "MSFT", "market": "us_equity"},
    {"asset": "GOOGL", "market": "us_equity"},
    {"asset": "NVDA", "market": "us_equity"},
]

# Config file locations searched in order
_CONFIG_FILENAMES = ("portfolio.json",)


@dataclass(frozen=True, slots=True)
class AssetTarget:
    """One asset the orchestrator should scan."""

    asset: str
    market: Market
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class OrchestratorConfig:
    """Runtime configuration for a batch scan run."""

    targets: list[AssetTarget]
    days: int = 90
    record: bool = True
    use_llm: bool = False
    no_refresh: bool = False


@dataclass(slots=True)
class ScanResult:
    """Outcome of scanning one asset."""

    asset: str
    market: str
    status: str  # "ok", "error", "skipped"
    signal: Signal | None = None
    error: str | None = None
    duration_ms: float = 0.0


@dataclass(slots=True)
class BatchReport:
    """Summary of a full orchestrator run."""

    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    results: list[ScanResult] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        from collections import Counter

        counts = Counter(r.status for r in self.results)

        # Portfolio view: aggregate directional signals with correlation reads
        ok_signals = [r.signal for r in self.results if r.signal is not None]
        portfolio_data: dict[str, Any] | None = None
        if ok_signals:
            from alpha_engine.analyzers.portfolio_signal import build_portfolio_view

            cache = Cache()
            series_by_asset: dict[str, Any] = {}
            for r in self.results:
                if r.signal is not None and r.signal.direction is not None:
                    series, _stale = cache.get_price(r.asset, "1d")
                    if series is not None:
                        series_by_asset[r.asset] = series
            pv = build_portfolio_view(ok_signals, series_by_asset)
            portfolio_data = pv.model_dump(mode="json")

        return {
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "total": len(self.results),
            "ok": counts["ok"],
            "errors": counts["error"],
            "skipped": counts["skipped"],
            "results": [
                {
                    "asset": r.asset,
                    "market": r.market,
                    "status": r.status,
                    "error": r.error,
                    "duration_ms": round(r.duration_ms, 1),
                    "direction": r.signal.direction.value if r.signal else None,
                    "confidence": r.signal.confidence if r.signal else None,
                }
                for r in self.results
            ],
            "portfolio": portfolio_data,
        }


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def load_config(
    config_path: str | Path | None = None,
    assets: list[str] | None = None,
    days: int = 90,
    record: bool = True,
    use_llm: bool = False,
) -> OrchestratorConfig:
    """Build the orchestrator config from a JSON file, CLI overrides, or defaults.

    Priority: explicit config_path > assets list > default portfolio.
    """
    targets: list[AssetTarget] = []

    if config_path:
        targets = _load_targets_from_file(config_path)
    elif assets:
        targets = [_parse_asset_string(a) for a in assets]
    else:
        targets = _load_targets_from_defaults()

    enabled = [t for t in targets if t.enabled]
    return OrchestratorConfig(
        targets=enabled,
        days=days,
        record=record,
        use_llm=use_llm,
    )


def _load_targets_from_file(path: str | Path) -> list[AssetTarget]:
    """Load targets from a JSON file. Expected format:
    {"assets": [{"asset": "BTC", "market": "crypto"}, ...]}
    or just a list: [{"asset": "BTC", "market": "crypto"}, ...]
    """
    raw = json.loads(Path(path).read_text())
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        items = raw.get("assets", raw.get("targets", []))
    else:
        items = []

    targets = []
    for item in items:
        asset = item.get("asset", "").upper()
        market_str = item.get("market", "crypto").lower()
        enabled = item.get("enabled", True)
        try:
            market = Market(market_str)
        except ValueError:
            market = Market.CRYPTO
        targets.append(AssetTarget(asset=asset, market=market, enabled=enabled))
    return targets


def _parse_asset_string(raw: str) -> AssetTarget:
    """Parse 'BTC' or 'BTC:crypto' into an AssetTarget."""
    if ":" in raw:
        asset, market_str = raw.split(":", 1)
        try:
            market = Market(market_str.lower())
        except ValueError:
            market = Market.US_EQUITY
    else:
        asset = raw
        # Default market detection will happen at scan time
        market = Market.US_EQUITY
    return AssetTarget(asset=asset.upper(), market=market)


def _load_targets_from_defaults() -> list[AssetTarget]:
    """Load the default portfolio."""
    targets = []
    for item in DEFAULT_PORTFOLIO:
        asset = item["asset"].upper()
        market = Market(item["market"])
        targets.append(AssetTarget(asset=asset, market=market))
    return targets


# ---------------------------------------------------------------------------
# Batch scanning
# ---------------------------------------------------------------------------


def scan_target(
    target: AssetTarget,
    cache: Cache,
    config: OrchestratorConfig,
) -> ScanResult:
    """Scan a single asset target. Returns a ScanResult with the signal or error.

    This imports the CLI builder functions to reuse the existing pipeline
    without duplicating analysis logic.
    """
    from alpha_engine.cli.main import (
        _build_fno_signal,
        _build_price_signal,
        _load_series,
    )
    from alpha_engine.validation.recorder import record_signal

    start = time.monotonic()
    asset = target.asset
    market = target.market

    try:
        entry = None
        if market is Market.IN_FNO:
            chain, _stale = cache.get_chain(asset)
            if chain is None:
                return ScanResult(
                    asset=asset,
                    market=market.value,
                    status="skipped",
                    error="no options chain cached",
                    duration_ms=(time.monotonic() - start) * 1000,
                )
            signal = _build_fno_signal(asset, chain, use_llm=config.use_llm)
            entry = chain.spot
        else:
            series = _load_series(asset, market, config.days, config.no_refresh, cache)
            signal = _build_price_signal(
                asset, market, series, cache, config.no_refresh, use_llm=config.use_llm
            )
            if series.candles:
                entry = series.candles[-1].close

        if config.record:
            record_signal(signal, entry_price=entry)

        return ScanResult(
            asset=asset,
            market=market.value,
            status="ok",
            signal=signal,
            duration_ms=(time.monotonic() - start) * 1000,
        )

    except Exception as e:  # noqa: BLE001 - fault isolation: one error must not block others
        return ScanResult(
            asset=asset,
            market=market.value,
            status="error",
            error=str(e),
            duration_ms=(time.monotonic() - start) * 1000,
        )


def run_batch(config: OrchestratorConfig) -> BatchReport:
    """Scan all configured targets and return a batch report.

    Scans run sequentially for simplicity and to respect rate limits. Each
    scan is fault-isolated — a failure on one asset never blocks another.
    """
    cache = Cache()
    report = BatchReport(started_at=datetime.now(timezone.utc))

    for target in config.targets:
        print(
            f"[orchestrator] scanning {target.asset} ({target.market.value})...",
            file=sys.stderr,
        )
        result = scan_target(target, cache, config)
        report.results.append(result)

        status_msg = f"{result.status}"
        if result.error:
            status_msg += f" ({result.error})"
        if result.signal:
            status_msg += f" -> {result.signal.direction.value} @ {result.signal.confidence:.0%}"
        print(
            f"[orchestrator]   {result.asset}: {status_msg} [{result.duration_ms:.0f}ms]",
            file=sys.stderr,
        )

    report.finished_at = datetime.now(timezone.utc)
    return report


def run_scheduled(
    config: OrchestratorConfig,
    output_path: str | Path | None = None,
) -> BatchReport:
    """Run a batch scan and optionally write the report to disk.

    This is the cron-friendly entry point. It writes a JSON report that
    monitoring tools can pick up, and exits with code 0 if all scans
    succeeded, 1 if any failed.
    """
    report = run_batch(config)

    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report.summary(), indent=2))

    return report
