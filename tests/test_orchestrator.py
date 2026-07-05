"""Tests for the orchestrator module.

Key properties:
- Default portfolio loads correctly.
- Config from JSON file works.
- Asset string parsing handles market suffixes.
- Batch report tracks ok/error/skipped counts.
- scan_target faults are isolated (one failure doesn't block others).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone


from alpha_engine.orchestrator import (
    BatchReport,
    ScanResult,
    load_config,
    _parse_asset_string,
)
from alpha_engine.schema.signal import Direction, Market, Signal, Timeframe

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


# --- AssetTarget parsing ------------------------------------------------------


def test_parse_asset_string_bare():
    t = _parse_asset_string("BTC")
    assert t.asset == "BTC"
    assert t.market == Market.US_EQUITY  # default


def test_parse_asset_string_with_market():
    t = _parse_asset_string("NIFTY:in_fno")
    assert t.asset == "NIFTY"
    assert t.market == Market.IN_FNO


def test_parse_asset_string_is_uppercased():
    t = _parse_asset_string("aapl")
    assert t.asset == "AAPL"


def test_parse_asset_string_unknown_market_falls_back():
    t = _parse_asset_string("BTC:unknown_market")
    assert t.asset == "BTC"
    assert t.market == Market.US_EQUITY


# --- load_config --------------------------------------------------------------


def test_load_config_from_file(tmp_path):
    config_data = {
        "assets": [
            {"asset": "BTC", "market": "crypto"},
            {"asset": "AAPL", "market": "us_equity"},
        ]
    }
    config_file = tmp_path / "portfolio.json"
    config_file.write_text(json.dumps(config_data))

    config = load_config(config_path=config_file, days=30)
    assert len(config.targets) == 2
    assert config.targets[0].asset == "BTC"
    assert config.targets[0].market == Market.CRYPTO
    assert config.days == 30


def test_load_config_from_assets_list():
    config = load_config(assets=["BTC", "AAPL:us_equity"], days=60)
    assert len(config.targets) == 2
    assert config.targets[0].asset == "BTC"
    assert config.targets[1].asset == "AAPL"
    assert config.days == 60


def test_load_config_defaults():
    config = load_config()
    assert len(config.targets) > 0
    assert all(t.enabled for t in config.targets)
    assert config.record is True


def test_load_config_disabled_assets_excluded(tmp_path):
    config_data = {
        "assets": [
            {"asset": "BTC", "market": "crypto", "enabled": True},
            {"asset": "SOL", "market": "crypto", "enabled": False},
        ]
    }
    config_file = tmp_path / "portfolio.json"
    config_file.write_text(json.dumps(config_data))

    config = load_config(config_path=config_file)
    assert len(config.targets) == 1
    assert config.targets[0].asset == "BTC"


def test_load_config_list_format(tmp_path):
    config_data = [
        {"asset": "BTC", "market": "crypto"},
        {"asset": "ETH", "market": "crypto"},
    ]
    config_file = tmp_path / "portfolio.json"
    config_file.write_text(json.dumps(config_data))

    config = load_config(config_path=config_file)
    assert len(config.targets) == 2


# --- BatchReport --------------------------------------------------------------


def test_batch_report_counts():
    report = BatchReport()
    report.results = [
        ScanResult(asset="BTC", market="crypto", status="ok"),
        ScanResult(asset="ETH", market="crypto", status="error", error="fetch failed"),
        ScanResult(asset="SOL", market="crypto", status="skipped", error="no chain"),
        ScanResult(asset="AAPL", market="us_equity", status="ok"),
    ]
    report.finished_at = datetime.now(timezone.utc)

    assert report.total == 4
    assert report.ok == 2
    assert report.errors == 1
    assert report.skipped == 1


def test_batch_report_summary_shape():
    report = BatchReport()
    report.results = [
        ScanResult(
            asset="BTC",
            market="crypto",
            status="ok",
            signal=Signal(
                asset="BTC",
                market=Market.CRYPTO,
                direction=Direction.BULLISH,
                confidence=0.8,
                timeframe=Timeframe.SWING,
                signal_sources=[],
            ),
            duration_ms=123.4,
        ),
    ]
    report.finished_at = datetime.now(timezone.utc)

    summary = report.summary()
    assert summary["total"] == 1
    assert summary["ok"] == 1
    assert summary["results"][0]["asset"] == "BTC"
    assert summary["results"][0]["direction"] == "bullish"
    assert summary["results"][0]["confidence"] == 0.8
    assert summary["results"][0]["duration_ms"] == 123.4


def test_batch_report_empty():
    report = BatchReport()
    assert report.total == 0
    assert report.ok == 0
    assert report.errors == 0
    summary = report.summary()
    assert summary["total"] == 0
    assert summary["results"] == []


# --- ScanResult ---------------------------------------------------------------


def test_scan_result_ok():
    sig = Signal(
        asset="BTC",
        market=Market.CRYPTO,
        direction=Direction.BULLISH,
        confidence=0.7,
        timeframe=Timeframe.SWING,
    )
    result = ScanResult(asset="BTC", market="crypto", status="ok", signal=sig, duration_ms=50.0)
    assert result.status == "ok"
    assert result.signal is not None
    assert result.signal.direction is Direction.BULLISH


def test_scan_result_error():
    result = ScanResult(asset="ETH", market="crypto", status="error", error="timeout")
    assert result.status == "error"
    assert result.signal is None
    assert result.error == "timeout"


def test_scan_result_skipped():
    result = ScanResult(asset="NIFTY", market="in_fno", status="skipped", error="no chain cached")
    assert result.status == "skipped"
    assert result.error == "no chain cached"
