# Measured findings

What the engine has actually been shown to do, as opposed to what it was built
to do. Every number here came from running the code; the commands are included
so anyone can reproduce or refute them.

Update this file whenever a measurement changes. It is the answer to "does this
work?", and it is meant to be uncomfortable when the answer is no.

---

## 2026-07-27 — The engine has no measurable directional edge

**Verdict: +0.0% edge over 6,788 signals, 7 assets, 2.7–5 years of history.**

### What was measured

For every bar, the full live pipeline was replayed through `signal_at()` — the
same no-lookahead choke point `scan` uses — and each directional call was scored
against what price actually did over the next 10 bars (the swing horizon).

```bash
# Backfill deep history first: the cache holds ~90 bars by default, which
# leaves ~10 scorable signals after the 80-bar warmup.
python -c "from alpha_engine.ingestion import yahoo, binance
from alpha_engine.cache.interface import Cache
for a in ['AAPL','MSFT','GOOGL','NVDA']: yahoo.fetch_daily(a, days=1825, cache=Cache())
for a in ['BTC','ETH','SOL']: binance.fetch_daily(a, days=1825, cache=Cache())"

alpha-engine backtest BTC --days 1000 --per-analyzer --step 1 --no-refresh
```

### The result

Edge is measured against a **direction-matched base rate**: how often the asset
moved that way anyway. A bullish call on an asset that rises 56% of the time has
to beat 56%, not 50%.

| Asset | Signals | Bullish correct | Bearish correct | Base rate | **Edge** |
|---|---:|---:|---:|---:|---:|
| BTC | 830 | 51.3% | 45.2% | 53.3% | **−1.7%** |
| ETH | 854 | 46.8% | 51.9% | 47.0% | **−0.7%** |
| SOL | 829 | 49.9% | 49.5% | 50.9% | **−0.3%** |
| AAPL | 1080 | 58.2% | 45.8% | 56.1% | **+2.0%** |
| MSFT | 1051 | 52.6% | 46.4% | 52.5% | **−0.5%** |
| GOOGL | 1067 | 57.3% | 42.5% | 56.6% | **+0.1%** |
| NVDA | 1077 | 58.4% | 42.7% | 57.1% | **+0.7%** |
| **All** | **6,788** | | | | **+0.0%** |

The spread from −1.7% to +2.0% is noise around zero. This is exactly what
`AGENTS.md` has always claimed ("analyzers are honest scaffolds, ~coin-flip");
it is now measured at scale rather than asserted.

### Two things that nearly got reported as edge

Recording these because both are easy to fall for, and one of them was caught
only on a second look.

**1. Conditioning on survival.** Scoring only the signals that were *not*
stopped out gives a +10% edge across all four assets tested. It is an artifact:
a bullish call that drops gets stopped out and removed from the sample, so
"survivors finished up more often" is close to tautological. Never score a
subset selected by the outcome.

**2. Comparing against 50%.** Several analyzers look near-50% and therefore
harmless. But AAPL rose in 56.1% of 10-bar windows over this period, so 50% is
*worse than doing nothing*. The base rate is the bar, not a coin.

### The invalidation level is destroying value

Scored with the stop, the blended signal hits 42.2% on BTC against a 53.3% base
rate. Scored without it, direction is ~50/50. The stop is not protecting the
strategy, it is converting a coin flip into a loss:

| | BTC | AAPL |
|---|---:|---:|
| Stopped out before the horizon | 34.2% | 28.6% |
| Survived, finished in the right direction | 42.2% | 47.7% |
| Survived, finished wrong | 23.6% | 23.7% |

Roughly a third of all signals are killed by their own invalidation level before
the thesis has time to play out. That is the most concrete, actionable defect
this measurement found — and unlike the edge question, it is a *fixable* one.

### Per-analyzer, without the stop (AAPL, base rate 56.1%)

| Analyzer | Signals | Correct | vs base |
|---|---:|---:|---:|
| multi_timeframe | 1103 | 52.6% | −3.6% |
| vwap | 1163 | 52.3% | −3.9% |
| volume | 1163 | 51.9% | −4.2% |
| macd | 1163 | 51.2% | −4.9% |
| support_resistance | 953 | 49.9% | −6.2% |
| bollinger | 795 | 45.9% | −10.2% |
| rsi | 146 | 45.2% | −10.9% |

Read with care: this column compares against a *raw* base rate, which penalises
bearish calls on an asset that spent the window rising. The direction-matched
number in the table above (+0.0%) is the fair one. What survives either reading
is the ordering — `rsi` and `bollinger` are the weakest inputs by a clear
margin, and both are worth removing or re-thinking before anything else is added.

---

## What this does and does not mean

**It does not mean the project failed.** The engine was built to answer this
question honestly, and it did. Most systems like this never find out, because
they are never measured against a base rate on enough samples to tell.

**It does mean nothing here should be traded**, and that the next work is
subtractive rather than additive: fix the invalidation level, drop or repair the
two worst analyzers, and re-measure. Adding a ninth analyzer to eight that carry
no signal produces nine that carry no signal.

**The measurement is now cheap to repeat.** That is the real asset. Any change
to an analyzer can be scored against 6,788 samples in minutes, so the next
version of this file can say something different — and be believed.
