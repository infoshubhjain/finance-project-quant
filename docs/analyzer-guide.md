# Writing a New Analyzer

An analyzer is a **pure function**: it takes normalized data from the cache and
returns one `SignalSource` — a vote with a direction, a weight, and a short
machine-readable note. Same input, same output, every time. No network calls,
no randomness, no LLM. That's the cardinal rule, and it's what makes every
number in this engine backtestable.

This guide walks through the whole life of an analyzer, using the real ones in
`src/alpha_engine/analyzers/` as templates.

## 1. Decide what the analyzer reads

| Input | Model | Example analyzers |
|---|---|---|
| Daily candles | `PriceSeries` (`cache/models.py`) | `rsi.py`, `macd.py`, `support_resistance.py` |
| Options chain | `OptionsChain` | `fno_oi.py` |
| Macro series | `dict[str, list[MacroObservation]]` | `macro_context.py` |
| Several assets | `dict[str, PriceSeries]` | `correlation.py` (feeds the portfolio view) |

Analyzers never fetch. If the data you want isn't in a cache model yet, that's
an *ingestion* task first (see `ingestion/` and its adapters).

## 2. Write the pure function

The skeleton every analyzer follows:

```python
"""One paragraph: what market intuition this encodes, and its honest limits."""

from __future__ import annotations

from alpha_engine.cache.models import PriceSeries
from alpha_engine.schema.signal import Direction, SignalSource

_NAME = "my_analyzer"


def analyze_my_thing(series: PriceSeries, period: int = 14) -> SignalSource:
    closes = series.closes()

    # 1. Not enough data? Degrade to weight 0 — never guess, never crash.
    if len(closes) < period + 1:
        return SignalSource(
            name=_NAME, direction=Direction.NEUTRAL, weight=0.0,
            detail=f"insufficient history for {_NAME}({period})",
        )

    # 2. Compute the indicator with plain deterministic Python.
    value = ...

    # 3. Map it to a vote. Direction from the read; weight 0..1 from how
    #    strong the read is, capped so one input can't dominate synthesis.
    return SignalSource(
        name=_NAME,
        direction=Direction.BULLISH,
        weight=round(min(strength, 0.6), 4),
        detail=f"{_NAME}({period})={value:.2f}",
    )
```

Conventions that keep the codebase coherent:

- **Degrade, don't raise.** Insufficient history or missing volume returns a
  neutral, zero-weight source with a self-explanatory `detail`.
- **Cap your weight.** A context read (VWAP, volatility) caps around 0.3–0.5;
  a structural read (support/resistance) around 0.6; only a fresh, strong
  event (MACD crossover) earns up to 0.8.
- **`detail` is for machines and audits**, not prose — pack the numbers in
  (`"rsi(14)=27.31 [oversold]"`). The narrator writes the human sentence.
- **Round weights** (`round(w, 4)`) so serialized signals are stable.

## 3. Pin it with tests

Every analyzer ships with fixture tests in `tests/test_analyzers.py`. The
minimum set, mirroring the existing pattern:

1. **Insufficient data** → weight 0, neutral.
2. **A bullish fixture** → crafted closes where the expected read is obvious.
3. **A bearish fixture** → the mirror.
4. **Determinism** → run twice on the same input, `model_dump()`s are equal.

Fixtures are hand-built lists of floats, never random. If the trigger
condition is fiddly (a crossover, a threshold), compute the fixture
numerically once and pin it with a comment — see
`test_macd_fresh_bullish_crossover` for the pattern.

```python
def test_my_thing_bullish_fixture():
    closes = [100.0, 98.0, 96.0, ...]        # crafted, commented
    src = analyze_my_thing(_series(closes))
    assert src.direction == Direction.BULLISH
    assert src.weight > 0
```

## 4. Wire it into synthesis

Two places consume analyzers:

- **Live scans** — add your call to `_build_price_signal()` in
  `cli/main.py`. Market-specific reads go in the per-market branch; shared
  reads go in the common block.
- **Backtests** — add the same call to `signal_at()` in
  `validation/backtest.py`, and register it in `ANALYZER_REGISTRY` so
  `backtest --per-analyzer` scores it in isolation. If your indicator needs
  more warmup bars than the current `DEFAULT_WARMUP`, raise that constant.

The synthesis layer (`synthesis/synthesize.py`) does the rest: it folds all
sources into one Signal via a weighted vote and calibrates confidence using
per-analyzer reliability factors. You do not touch synthesis to add an
analyzer.

## 5. Prove it (or at least measure it)

Run the honest loop before claiming anything:

```bash
pytest -q && ruff check .
python -m alpha_engine.cli.main backtest BTC --days 365 --per-analyzer
```

The per-analyzer report shows your analyzer's isolated hit rate and
calibration next to the blend. A ~50% hit rate means it adds nothing yet —
which is a fine, honest place for a first version to land. Say so in the
module docstring; the validation layer, not the docs, decides what has edge.
