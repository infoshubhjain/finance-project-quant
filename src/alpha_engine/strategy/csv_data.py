"""Load your own OHLCV data from CSV.

Every other input to this engine comes from an API. This is the one that does
not, and it exists because the most interesting thing you can backtest here has
no free API at all: Indian index options. `strategy-backtest --option` is built
around cross-verifying a signal on the option's real price chart, and the only
way most people have that history is as a CSV out of a broker terminal.

Ported from `nifty_backtester/core/data_loader.py`, minus pandas. The original
app's whole point was "two CSVs — the index and the contract — checked against
each other", and porting the engine without the data path would have kept the
machinery and lost the use case.

Expected columns, case-insensitive, in any order:

    datetime, open, high, low, close, volume

`volume` is optional. The timestamp column may be named `datetime`, `date`,
`timestamp` or `time`. Anything else is rejected with a message naming what was
found, because a silent mis-parse here produces a plausible, wrong backtest —
the worst outcome this project has.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from alpha_engine.cache.models import Candle, Interval, PriceSeries

#: Accepted names for the timestamp column, in preference order.
_TS_COLUMNS = ("datetime", "date", "timestamp", "time")
_REQUIRED = ("open", "high", "low", "close")

#: Formats tried in order. ISO first because that is what every export produces
#: now; the rest cover broker terminals that still emit local conventions.
_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%d-%m-%Y %H:%M:%S",
    "%d-%m-%Y %H:%M",
    "%d-%m-%Y",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y",
)


class CsvFormatError(ValueError):
    """The file is not usable as a price series, and says why."""


def _parse_timestamp(raw: str) -> datetime:
    """Parse a timestamp, trying ISO first then common broker exports.

    Naive timestamps are assumed UTC. That is a real assumption and it is stated
    rather than hidden: the engine compares timestamps across sources, and a
    naive one silently treated as local time shifts an Indian session by five
    and a half hours — enough to misalign an index bar against its option bar,
    which is exactly the comparison this data exists to make.
    """
    text = raw.strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = None
        for fmt in _FORMATS:
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        raise CsvFormatError(
            f"could not parse timestamp {raw!r}. Use ISO (2024-01-02 09:15:00) "
            "or a common broker format."
        )
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _number(row: dict, key: str, fallback: float | None = None) -> float | None:
    raw = (row.get(key) or "").strip().replace(",", "")
    if not raw:
        return fallback
    try:
        return float(raw)
    except ValueError:
        return fallback


def load_candles(path: str | Path) -> list[Candle]:
    """Read one OHLCV CSV into candles, oldest first.

    Rows with an unparseable close are dropped rather than guessed at, matching
    every ingestion adapter. A file where NOTHING parses raises instead of
    returning an empty list: silently backtesting zero bars would look like a
    strategy that never traded.
    """
    path = Path(path)
    if not path.is_file():
        raise CsvFormatError(f"{path} does not exist")

    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise CsvFormatError(f"{path} is empty")

        # Normalise headers once; broker exports love stray spaces and capitals.
        headers = {(name or "").strip().lower(): name for name in reader.fieldnames}
        ts_key = next((headers[c] for c in _TS_COLUMNS if c in headers), None)
        if ts_key is None:
            raise CsvFormatError(
                f"{path} has no timestamp column. Expected one of "
                f"{', '.join(_TS_COLUMNS)}; found: {', '.join(sorted(headers))}"
            )
        missing = [c for c in _REQUIRED if c not in headers]
        if missing:
            raise CsvFormatError(
                f"{path} is missing required column(s): {', '.join(missing)}. "
                f"Found: {', '.join(sorted(headers))}"
            )

        candles: list[Candle] = []
        for row in reader:
            normalised = {k.strip().lower(): v for k, v in row.items() if k}
            close = _number(normalised, "close")
            if close is None:
                continue
            try:
                ts = _parse_timestamp(normalised.get(ts_key.strip().lower()) or "")
            except CsvFormatError:
                continue
            candles.append(
                Candle(
                    ts=ts,
                    open=_number(normalised, "open", close) or close,
                    high=_number(normalised, "high", close) or close,
                    low=_number(normalised, "low", close) or close,
                    close=close,
                    volume=_number(normalised, "volume"),
                )
            )

    if not candles:
        raise CsvFormatError(
            f"{path} produced no usable rows — every row had an unreadable close "
            "or timestamp. Check the header names and the date format."
        )

    candles.sort(key=lambda c: c.ts)
    return candles


def load_series(
    path: str | Path, asset: str | None = None, interval: Interval = Interval.DAY
) -> PriceSeries:
    """Read a CSV into a `PriceSeries` ready for `run_strategy_backtest`.

    `asset` defaults to the file's stem, so `NIFTY24500CE.csv` becomes
    `NIFTY24500CE`. `interval` matters for one reason and it is not cosmetic:
    it sets the annualisation constant behind Sharpe. Backtesting 5-minute bars
    while claiming `DAY` overstates the Sharpe ratio by roughly 9x.
    """
    path = Path(path)
    return PriceSeries(
        asset=(asset or path.stem).upper(),
        interval=interval,
        candles=load_candles(path),
    )
