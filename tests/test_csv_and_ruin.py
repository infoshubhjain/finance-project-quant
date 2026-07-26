"""CSV loading, supertrend, and the account-ruin guard.

These three landed together because loading real option data is what exposed the
third. Running the ported engine on a synthetic Nifty + option CSV pair produced
"total return +609%" beside "max drawdown -293%" and a negative Sharpe — not a
bad result, not a result at all. An account cannot lose more than everything,
and a compounding model will happily let it: a short through a bar that rises
300% gives net = -3.0, so equity *= (1 - 3.0) and the curve goes negative.

Options make that ordinary rather than exotic, and this engine is built to trade
the option leg.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from alpha_engine.cache.models import Candle, Interval, PriceSeries
from alpha_engine.strategy.base import BaseStrategy
from alpha_engine.strategy.csv_data import CsvFormatError, load_candles, load_series
from alpha_engine.strategy.engine import run_strategy_backtest
from alpha_engine.strategy.indicators import supertrend

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

HEADER = "datetime,open,high,low,close,volume\n"
ROWS = "2026-01-02 09:15:00,100,101,99,100.5,1000\n2026-01-03 09:15:00,100.5,102,100,101.5,1200\n"


def _write(tmp_path, text, name="data.csv"):
    path = tmp_path / name
    path.write_text(text)
    return path


def _candles(closes, start=T0):
    return [
        Candle(ts=start + timedelta(days=i), open=c, high=c, low=c, close=c, volume=1)
        for i, c in enumerate(closes)
    ]


# --------------------------------------------------------------------------
# CSV loading
# --------------------------------------------------------------------------


def test_a_plain_csv_loads(tmp_path):
    candles = load_candles(_write(tmp_path, HEADER + ROWS))
    assert len(candles) == 2
    assert candles[0].close == 100.5
    assert candles[0].volume == 1000


def test_headers_are_case_and_space_insensitive(tmp_path):
    text = " Datetime , Open , High , Low , Close \n2026-01-02,1,2,0.5,1.5\n"
    assert load_candles(_write(tmp_path, text))[0].close == 1.5


@pytest.mark.parametrize("column", ["datetime", "date", "timestamp", "time"])
def test_any_accepted_timestamp_column_works(tmp_path, column):
    text = f"{column},open,high,low,close\n2026-01-02,1,2,0.5,1.5\n"
    assert len(load_candles(_write(tmp_path, text))) == 1


@pytest.mark.parametrize(
    "stamp",
    ["2026-01-02", "2026-01-02 09:15:00", "2026-01-02T09:15:00", "02-01-2026 09:15", "02/01/2026"],
)
def test_common_broker_date_formats_parse(tmp_path, stamp):
    text = f"datetime,open,high,low,close\n{stamp},1,2,0.5,1.5\n"
    assert load_candles(_write(tmp_path, text))[0].ts.year == 2026


def test_naive_timestamps_are_treated_as_utc(tmp_path):
    """Stated rather than hidden: a naive stamp read as local time shifts an
    Indian session by 5.5h, which misaligns an index bar against its option bar
    — the exact comparison this data exists to make."""
    candles = load_candles(_write(tmp_path, HEADER + ROWS))
    assert candles[0].ts.tzinfo is timezone.utc


def test_rows_are_sorted_oldest_first(tmp_path):
    text = HEADER + "2026-01-05,5,5,5,5,1\n2026-01-02,2,2,2,2,1\n"
    assert [c.close for c in load_candles(_write(tmp_path, text))] == [2.0, 5.0]


def test_volume_is_optional(tmp_path):
    text = "datetime,open,high,low,close\n2026-01-02,1,2,0.5,1.5\n"
    assert load_candles(_write(tmp_path, text))[0].volume is None


def test_thousands_separators_are_tolerated(tmp_path):
    text = 'datetime,open,high,low,close\n2026-01-02,"1,200","1,300","1,100","1,250"\n'
    assert load_candles(_write(tmp_path, text))[0].close == 1250.0


def test_a_missing_timestamp_column_names_what_was_found(tmp_path):
    with pytest.raises(CsvFormatError, match="no timestamp column"):
        load_candles(_write(tmp_path, "open,high,low,close\n1,2,0.5,1.5\n"))


def test_a_missing_price_column_is_named(tmp_path):
    with pytest.raises(CsvFormatError, match="close"):
        load_candles(_write(tmp_path, "datetime,open,high,low\n2026-01-02,1,2,0.5\n"))


def test_a_file_where_nothing_parses_raises_rather_than_returning_empty(tmp_path):
    """Backtesting zero bars silently looks like a strategy that never traded."""
    text = HEADER + "not-a-date,x,x,x,x,x\n"
    with pytest.raises(CsvFormatError, match="no usable rows"):
        load_candles(_write(tmp_path, text))


def test_a_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(CsvFormatError, match="does not exist"):
        load_candles(tmp_path / "absent.csv")


def test_load_series_names_the_asset_from_the_filename(tmp_path):
    series = load_series(_write(tmp_path, HEADER + ROWS, name="nifty_24000ce.csv"))
    assert series.asset == "NIFTY_24000CE"
    assert series.interval is Interval.DAY


def test_interval_is_carried_through_because_sharpe_depends_on_it(tmp_path):
    """5-minute bars annualised as daily overstate Sharpe by roughly 9x."""
    series = load_series(_write(tmp_path, HEADER + ROWS), interval=Interval.MINUTE)
    assert series.interval is Interval.MINUTE


# --------------------------------------------------------------------------
# Supertrend
# --------------------------------------------------------------------------


def test_supertrend_follows_a_sustained_trend():
    _line, up = supertrend(_candles([100 + i * 2 for i in range(40)]))
    _line2, down = supertrend(_candles([180 - i * 2 for i in range(40)]))
    assert up[-1] == 1
    assert down[-1] == -1


def test_supertrend_is_index_aligned_with_a_warmup():
    candles = _candles([100 + i for i in range(30)])
    line, direction = supertrend(candles, period=10)
    assert len(line) == len(direction) == len(candles)
    assert line[0] is None and direction[0] is None


def test_supertrend_reads_no_future_bars():
    """Truncating the series must not change earlier values."""
    candles = _candles([100 + (i % 9) * 3 for i in range(60)])
    full, _ = supertrend(candles)
    partial, _ = supertrend(candles[:40])
    assert full[:40] == pytest.approx(partial, nan_ok=True)


# --------------------------------------------------------------------------
# Ruin
# --------------------------------------------------------------------------


class _AlwaysShort(BaseStrategy):
    name = "Always Short"
    params: dict = {}

    def generate_signals(self, candles):
        out = [0] * len(candles)
        out[0] = -1
        return out


def _run(closes, **kw):
    return run_strategy_backtest(
        _AlwaysShort(),
        PriceSeries(asset="X", interval=Interval.DAY, candles=_candles(closes)),
        txn_cost_bps=0.0,
        check_lookahead=False,
        **kw,
    )


def test_equity_can_never_go_negative():
    """The bug: net = -1 * (+3.0) = -3.0, so equity *= (1 - 3.0)."""
    report = _run([100.0, 100.0, 400.0, 400.0])
    assert min(report.equity_curve) >= 0.0


def test_ruin_is_reported_with_the_bar_it_happened_on():
    report = _run([100.0, 100.0, 400.0, 400.0])
    assert report.ruined_at_bar == 2


def test_a_wiped_out_account_reports_minus_one_hundred_percent():
    """Not -300%. An account cannot lose more than everything."""
    report = _run([100.0, 100.0, 400.0, 400.0])
    assert report.metrics.total_return_pct == pytest.approx(-100.0)
    assert report.metrics.max_drawdown_pct == pytest.approx(-100.0)


def test_nothing_accrues_after_ruin():
    report = _run([100.0, 100.0, 400.0, 100.0, 50.0])
    assert report.equity_curve[2:] == [0.0, 0.0, 0.0]


def test_no_position_is_held_open_after_ruin():
    report = _run([100.0, 100.0, 400.0, 400.0])
    assert not any(t.open for t in report.trades)


def test_a_survivable_loss_is_not_treated_as_ruin():
    """A short through a +50% bar hurts and does not wipe the account out."""
    report = _run([100.0, 100.0, 150.0, 150.0])
    assert report.ruined_at_bar is None
    assert report.metrics.total_return_pct == pytest.approx(-50.0)


def test_an_ordinary_long_backtest_is_unaffected():
    report = run_strategy_backtest(
        _AlwaysShort(),
        PriceSeries(asset="X", interval=Interval.DAY, candles=_candles([100.0, 100.0, 90.0])),
        txn_cost_bps=0.0,
        check_lookahead=False,
    )
    assert report.ruined_at_bar is None
    assert report.metrics.total_return_pct == pytest.approx(10.0)
