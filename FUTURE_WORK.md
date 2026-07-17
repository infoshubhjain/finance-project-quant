# FUTURE_WORK.md — Everything Left To Build

The original build plan (Phases 0–6, all shipped) took the project from an empty folder to a working, validated,
multi-market signal engine (Phases 0–6, all shipped). This file picks up from
there and covers everything still on the table: the rest of the AI Analyst, the
whole of AlphaX, and the QuantHQ platform the engine eventually plugs into.

Read [context.md](context.md) first. The cardinal rule (numbers come from tested
pure Python; the LLM writes prose only) applies to every phase below without
exception. If a phase here appears to conflict with it, the phase is wrong.

**How to use this file.** Phases are ordered by leverage, not by excitement. Each
one is independently shippable and testable. Build top to bottom. The
"Why this order" section explains why the fun parts are late — the same reason
the orchestrator was last in the original plan.

---

## PART 0 — The foundation: what is already built

Before planning what's next, here is what exists today, from the ground up, in
plain terms. Everything below is working, tested, and shipped.

### The one idea the whole repo is organized around

**Every number is computed by plain, tested Python. An AI model may only write
the prose paragraph, and may never touch a number.**

"Deterministic" means: same input, same output, always. No randomness, no live
network call, no AI in the decision path. This is what makes the engine
*backtestable* — if a language model set the confidence score, you could never
replay history and check whether the engine was right, because the model might
answer differently tomorrow. That single rule dictates the directory layout, and
every phase in this file inherits it.

### The pipeline

```text
ingestion/  →  cache/  →  analyzers/  →  synthesis/  →  narrative/  →  Signal  →  validation/
(network)     (disk)     (pure math)    (weighted vote)  (prose only)            (log, score, backtest)
```

Each arrow is a directory under `src/alpha_engine/`. Data only flows one way.

**1. `ingestion/` — the only layer allowed to touch the internet.**
Adapters that call an external API and translate its messy format into one clean
shape. CoinGecko + Binance (crypto), Yahoo (US and Indian stocks), OANDA (forex),
FRED (US macro), and three Indian brokers (Breeze, Angel One, Dhan) for options
chains. The default path needs **zero API keys** — keys only unlock extras, and
the fetch chain falls back loudly (CoinGecko Pro → keyless CoinGecko → Binance)
so a rate-limit error never kills a scan.

**2. `cache/` — a folder of JSON files under `data/cache/`.**
Analyzers read from here, never from the internet. Two reasons: free APIs
rate-limit you if you hammer them, and pure functions can't do I/O anyway. Every
kind of data has a TTL ("time to live" — how long before it's considered stale):
a daily price bar goes stale in 12 hours, an options chain in 15 minutes because
open interest moves fast. The cache never lies about freshness — it returns
`(data, is_stale)` and lets the caller decide whether to refetch.

The normalized shapes live in `cache/models.py`:

- `Candle` — one bar of OHLCV (open, high, low, close, volume) for one day.
- `PriceSeries` — a list of candles for one asset.
- `OptionsChain` — every strike, call and put for one expiry.
- `MacroObservation` — one datapoint of an economic series (e.g. US CPI for March).

Whatever the source, it lands in these shapes. That's the whole point of the
cache layer: an analyzer never learns what a CoinGecko response looks like.

**3. `analyzers/` — eleven pure functions, each an independent specialist.**
Each looks at the data and returns a `SignalSource`: a name, a direction, and a
weight from 0 to 1 saying how strongly it feels. None of them can see each other.

| Analyzer | What it reads |
| -------- | ------------- |
| `crypto_trend` / `equity_trend` / `forex_trend` / `indian_equity` | Dual moving average — is the 10-day average above the 30-day? Above = uptrend. |
| `rsi` | Relative Strength Index — a 0-100 overbought/oversold meter. |
| `macd` | Momentum crossovers between two moving averages. |
| `bollinger` | Bands ±2 standard deviations around the mean; where is price inside them? |
| `volume` | On-balance volume — is volume confirming the move? |
| `vwap` | Volume-weighted average price — what participants actually paid. |
| `support_resistance` | Clusters of recent swing highs and lows. |
| `multi_timeframe` | Do the short, medium and long horizons agree? |
| `volatility` | ATR regime. Special: in a wild tape it scales *every other analyzer's weight down*. |
| `macro_context` | Fed funds trend, CPI, unemployment → a tightening/easing tilt, capped at 0.35 weight because it is context, not the main read. |
| `fno_oi` | The Indian options math: PCR, max pain, OI shifts, put/call walls. |

The F&O analyzer is the most distinctive thing in the repo. **PCR** = put open
interest ÷ call open interest (lots of puts written below spot reads as a floor).
**Max pain** = the expiry price at which option *buyers* collectively get paid
the least — price tends to gravitate toward it into expiry. Both are
hand-checkable arithmetic, not black boxes.

**4. `synthesis/` — folds all those votes into one verdict.**
Bullish sources add their weight, bearish subtract, and the net (−1 to +1)
decides direction, with a deadband so tiny nets stay neutral. Confidence comes
from three ingredients: **agreement** (what fraction of weight agrees),
**reliability** (each analyzer's historical accuracy — currently ~0.50, i.e.
coin-flip, because the backtests say so), and a **source-count cap** (one source
can never exceed 0.45 confidence; five caps at 0.78; never 1.0). One indicator
agreeing with itself is not conviction.

**5. `narrative/` — writes the `thesis` sentence and nothing else.**
A deterministic template by default. With `--llm` and a key, a model rephrases
it — and then `narrative/llm.py` re-checks that not one number moved. If any did,
the AI text is silently discarded and the template kept.

**6. The `Signal` — the contract everything compiles against** (`schema/signal.py`):

```text
asset, market, direction, confidence (0-1), timeframe,
signal_sources[]    ← the audit trail: who voted what, how hard
invalidation_level  ← the price that proves this view wrong
thesis              ← the only field an LLM may write
timestamp, schema_version
```

`invalidation_level` is the most important field. Anyone can say "bullish."
Saying "bullish, and I'm wrong below $58,400" is *falsifiable* — and that is what
makes the next layer possible at all.

**7. `validation/` — the part that makes the whole thing trustworthy.**

- `recorder.py` — every scan appends one line to `data/signals/signals.jsonl`
  with the price at that moment. **Append-only**: the code has no path that can
  rewrite an old line. Signals are recorded *before* anyone knows the outcome, so
  the track record can't be quietly cleaned up later.
- `outcomes.py` — scores those recorded signals against what actually happened.
  Strict on purpose: a swing signal gets 10 trading days; if price touches the
  invalidation level first it's a **miss immediately**, even if it recovers.
  Neutral signals aren't scored (they're not claims). Produces a **calibration
  curve**: of the signals you called 60–80% confident, how many were right?
- `backtest.py` — replays history bar by bar. The classic backtesting bug is
  *lookahead*: letting the simulated past peek at the future and producing
  gorgeous fake results. The defence is structural — `signal_at(series, t)` is the
  **only** way to generate a historical signal, and the first thing it does is
  chop the series to bars `[0..t]`. A test pins it: the signal at bar *t* must be
  identical whether or not the future exists in the input.

**And the honest finding? Roughly a coin flip on BTC.** The README says so out
loud. That is not a failure — it is the measured baseline every future
improvement gets judged against. Most quant side-projects never build the
machinery that would let them discover this about themselves.

**8. `quant/` — the deeper analytics track (the `report` command).**
53 deterministic features plus three statistical models, all hand-rolled in
dependency-free Python so they stay reproducible:

- **Kalman filter** — treats "fair value" as a hidden smooth line and the daily
  price as that line plus noise. Tells you how stretched price is from it.
- **GARCH(1,1)** — forecasts tomorrow's volatility. Volatility clusters (calm
  follows calm, chaos follows chaos) and GARCH models exactly that. Fit by
  exhaustive grid search rather than a random optimizer, specifically to stay
  deterministic.
- **2-state HMM** — infers whether you're in a bull or bear *regime* from the
  return pattern, with a fixed deterministic initialization.

**9. The shells.** `cli/main.py` wires it all together and auto-detects the market
from the symbol (`BTC` → crypto, `NIFTY` → F&O, `.NS` suffix → Indian equity,
`EURUSD` → forex, else US equity). Commands: `scan`, `scan-all`, `watch`,
`backtest`, `report`, `record-stats`, `batch`, `scan-chain`, `fetch-chain`,
`dashboard`. `orchestrator/` runs batches over `portfolio.json`, fault-isolated so
one asset failing never blocks the rest. `web/server.py` is a read-only local
dashboard on Python's stdlib HTTP server — no auth, no writes, no trading buttons.

**325 tests, all network-free.**

### Scored against the original plan

| Original plan area | Built | Missing |
| ------------------ | ----- | ------- |
| **Part-1: AI Analyst** | ~45% | news/sentiment, on-chain, fundamentals, risk agent, real orchestrator, closed feedback loop |
| **Part-2: AlphaX** | ~10% | factor ranking, factor testing (IC), scale to 500+ factors, ML layer |
| **QuantHQ platform** | 0% | all of it — no users, no auth, no database, no server anyone else can reach |

The technical and derivatives specialists are strong. The crypto "specialist" is
really just a moving-average crossover with a different name — the on-chain agent
the blueprint described does not exist. There is no fundamentals agent at all.
And two pieces of **finished code are written but wired into nothing**:
`analyzers/correlation.py` and `analyzers/portfolio_signal.py`. Nothing imports
them in the scan path. Phase 8 fixes that.

---

## Why this order

The instinct is to chase breadth: add news, add on-chain, add fundamentals, get
to 1000 factors. That is the wrong first move, and here is the concrete reason.

You currently have 53 factors and **no way to tell which of them predict
anything**. Adding 950 more produces 1000 factors you also can't rank. Every new
data source has the same problem: you will not be able to prove the news agent
helped, because there is no measurement that would show it.

So the ranking layer comes first (Phase 7). Once a factor can be scored against
forward returns, every subsequent addition — new factors, new data domains, new
analyzers — arrives with a built-in verdict on whether it earned its place. That
is the same logic that put the validation harness first in the original plan, applied one
level up.

Order: **measure → wire up what already exists → close the loop → then grow.**

---

# PART A — Finishing the Engine

## Phase 7 — Factor Ranking: the actual AlphaX core `NEXT`

**Goal.** Turn 53 dead numbers into a ranking engine. Given any price series,
answer the question AlphaX was invented to answer: *which factors actually
predict this asset's forward returns, and how strongly?*

This is the single highest-leverage phase in the file. It is also small — the
factors and the backtester already exist; what is missing is the scoring.

**Concepts you'll need (plain terms).**

- **Forward return**: what the asset did over the next N bars, measured from
  bar *t*. This is the thing a factor is trying to predict.
- **Information Coefficient (IC)**: the correlation between a factor's value at
  bar *t* and the forward return from bar *t*. IC = 0 means the factor is noise.
  IC = 0.05 on daily data is a *real* signal in practice; IC = 0.3 means you
  have a bug (almost certainly lookahead).
- **Rank IC (Spearman)**: the same thing computed on ranks instead of raw
  values. It is robust to outliers and fat tails, which is exactly what
  financial data has. **Prefer rank IC as the headline metric.**
- **IC decay**: IC measured at several horizons (1, 5, 10, 20 bars). A factor
  whose IC peaks at 1 bar and dies is an intraday factor; one that peaks at 20
  is a position factor. The shape tells you what timeframe to use it on.
- **Hit rate**: fraction of bars where the factor's sign matched the forward
  return's sign. Easier to read than IC; less informative.

**Build.**

- `quant/factors.py` — a **factor panel**, not a snapshot. Today
  `compute_features()` returns one dict for the latest bar. Add
  `compute_factor_panel(series) -> dict[str, list[float | None]]`: the value of
  every factor *at every bar*, aligned to the series index. This is the input
  the ranking layer needs, and it is a refactor of existing code, not new math.
  - **The lookahead trap lives here.** A factor at bar *t* must use only bars
    `[0..t]`. Any rolling window, z-score, or normalization computed over the
    full series leaks the future. Compute forward, never backward.
- `quant/ranking.py` — the scoring engine:
  - `forward_returns(closes, horizon) -> list[float | None]` — the last
    `horizon` entries are None (no future exists yet). Never fill them.
  - `rank_ic(factor_values, fwd_returns) -> float | None` — Spearman
    correlation, dropping index positions where either side is None.
  - `ic_decay(factor_values, closes, horizons=(1,5,10,20))` — IC at each horizon.
  - `FactorScore` (Pydantic): `name`, `rank_ic`, `ic_by_horizon`, `hit_rate`,
    `coverage` (fraction of bars where the factor was computable — a factor
    defined on 12% of bars is not usable no matter how good its IC),
    `t_stat` (IC × sqrt(n), the crude "is this distinguishable from luck" check).
  - `rank_factors(series, horizon=10) -> list[FactorScore]` — sorted by |rank_ic|
    descending.
- `quant/factors.py` also gets `factor_correlation(panel)` — a factor whose IC is
  0.04 but which is 0.95-correlated with your best factor adds nothing. Report
  it, so the ranking is of *independent* information, not fifty flavours of
  momentum.
- CLI: `python -m alpha_engine.cli.main factors BTC [--horizon 10] [--json]`
  — prints the ranked table:
  ```
  factor                rank_ic   t_stat   hit_rate   coverage   corr_w_top
  mom_20                  0.061     1.94       54.1%      98.2%       1.000
  vol_ratio_20_60        -0.048    -1.52       53.0%      97.0%      -0.210
  ...
  ```
- Tests (`tests/test_ranking.py`), and they matter more than usual here:
  - **The synthetic-signal test**: build a series where a known factor is
    constructed to predict forward returns (e.g. returns generated *from* the
    factor plus noise). Assert its rank IC comes out strongly positive. If your
    IC code is wrong, this is the test that catches it.
  - **The pure-noise test**: random-walk closes (fixed seed, in the test only —
    never in `analyzers/` or `synthesis/`). Assert every factor's |rank IC| is
    small. A ranking engine that finds edge in noise is broken.
  - **The lookahead pin**: `compute_factor_panel(series)[name][t]` must equal
    `compute_factor_panel(series.truncated_to(t))[name][t]`. This is the same
    guarantee `signal_at` gives the backtester, at the factor level. Non-negotiable.

**Done when.** `factors BTC` prints a ranked table, the synthetic-signal test
passes, the noise test passes, and the lookahead pin passes. You can point at
any factor and say what its measured predictive value on this asset is.

**Notes.** Expect the honest finding — as with the analyzers — that most factors
have near-zero IC. That is the correct and useful answer. The value of this
phase is not that it finds alpha; it is that from now on, *nothing gets added to
the engine without a number attached to whether it helped.*

Do **not** use the ranking output to auto-select factors into the live signal
yet. Ranking on one asset's history, then trading that same history, is
in-sample overfitting — the exact failure mode the original plan warned about. Ranking is
a research tool in this phase. Phase 9 handles feeding it back safely.

---

## Phase 8 — The Risk Agent: wire up what you already wrote

**Goal.** Layer 4 of the original blueprint called for a risk agent alongside
signal synthesis. Two thirds of it is already sitting in the repo, unimported.

**Build.**

- Wire `analyzers/correlation.py` and `analyzers/portfolio_signal.py` into the
  batch path. After `scan-all` / `batch` produces N signals, run them through
  `build_portfolio_view()` and emit a portfolio-level block alongside the
  per-asset ones.
- `analyzers/risk.py` — the missing third:
  - **Position sizing** from volatility. Inverse-vol sizing: an asset with 2×
    the volatility of another gets half the notional for the same risk budget.
    Use the existing ATR / GARCH forecast — the plumbing exists in
    `quant/models.py`. Output a *fraction of risk budget*, never a rupee/dollar
    amount and never an order (see the disclaimer rules).
  - **Correlation clustering.** Five bullish signals across five names that are
    all 0.9-correlated is one bet, not five. Flag it. `correlation.py` already
    computes the matrix.
  - **Tail-risk flag.** Deterministic and simple: historical VaR/CVaR at 95% on
    the trailing window, plus a "drawdown regime" flag when the asset is more
    than X% below its trailing high. No Monte Carlo, no simulated distributions.
  - **Regime gate.** The HMM in `quant/models.py` already returns a bull
    probability. Surface it as a risk overlay: a bullish signal fired inside a
    high-confidence bear regime is a different animal than one fired in a bull
    regime, and the audit trail should say so.
- New CLI: `risk` (portfolio view over the recorded signal log + cached prices),
  and the same block appended to `batch --output`.
- Extend the dashboard with a portfolio/risk panel — it reads the same JSON.

**Done when.** `scan-all` reports concentration and correlation warnings, every
directional signal carries a suggested risk fraction, and the tests pin the
sizing math on fixed inputs.

**Notes.** Sizing output must stay framed as *research context* ("this position
represents 3.2× the volatility of that one"), never as instruction. The
disclaimer rules in context.md apply with full force here — sizing advice is
closer to the regulated line than directional research is.

---

## Phase 9 — Close the Feedback Loop

**Goal.** The original Layer-5 promise: *"the backtest results tell the agents
what actually worked."* Today they don't.
[`SOURCE_RELIABILITY`](src/alpha_engine/synthesis/synthesize.py) is a hand-typed
dict of 0.50s. A human types those numbers. That is the loop, unclosed.

**Build.**

- `validation/calibrate.py` — a **deterministic, offline, explicit** calibration
  step:
  - Read the recorded signal log (`data/signals/signals.jsonl`) and/or run
    `run_per_analyzer_backtest` over a held-out window.
  - Compute each analyzer's realized hit rate, with a **shrinkage prior**: an
    analyzer with 12 resolved signals and a 75% hit rate has not earned 0.75.
    Shrink toward 0.50 by sample size —
    `reliability = (hits + k*0.5) / (n + k)` with k ≈ 30. This is the single
    line that prevents the whole phase from overfitting to noise.
  - Enforce a **minimum sample floor**: below ~50 resolved signals, an analyzer
    keeps the default 0.50 and the tool says so out loud.
  - Write the result to `data/calibration.json`, and have `synthesize.py` load
    it **if present**, falling back to the hardcoded defaults if absent (the
    fresh-clone path must still work with zero data files).
- CLI: `calibrate [--min-samples 50] [--dry-run]`. **Never runs automatically.**
  It is a command a human invokes and reviews, and its output is a file that gets
  committed deliberately, not a number that mutates behind your back.
- The calibration file records `generated_at`, the window used, the sample count
  per analyzer, and the shrinkage k — so any signal's confidence is traceable to
  the exact calibration that produced it.

**Done when.** `calibrate` produces a reviewed `data/calibration.json`,
synthesis consumes it, the no-file path is tested, and the shrinkage behaviour is
pinned by a test (12 samples at 75% must not yield 0.75).

**Notes.** This is the phase most likely to quietly make the track record
dishonest, which is why it is deliberately clunky: offline, human-invoked,
sample-floored, shrunk, and version-controlled. Resist every urge to make it
automatic. An engine that silently retunes its own confidence from its own
history is an engine whose track record means nothing.

**Hard prerequisite:** you need months of recorded scans. The daily cron is what
generates that dataset. Keep it running. This phase is gated on data, not on
code.

---

## Phase 10 — Scale the Factor Library (53 → 500+)

**Goal.** AlphaX's stated target was 1000–2000 factors. With Phase 7's ranking
engine in place, factors can now be added *and immediately judged*, so this
becomes safe, mechanical volume work.

**Build.** Generate factors by systematic parameterization rather than by hand,
grouped by family so the ranking output stays legible:

- **Momentum / reversal** — return over N bars, for N in {1,2,3,5,10,15,20,30,
  40,60,90,120,180,252}. Both raw and volatility-normalized. (~40)
- **Moving-average structure** — SMA/EMA/WMA over the same N grid; price-to-MA
  distance; MA crossover spreads for every (fast, slow) pair. (~80)
- **Volatility** — realized vol, Parkinson (high-low), Garman-Klass (OHLC),
  Yang-Zhang, ATR, over the N grid; vol-of-vol; vol ratios between horizons. (~60)
- **Volume / participation** — OBV, volume z-score, volume-price correlation,
  Amihud illiquidity, dollar-volume trend, VWAP distance across horizons. (~40)
- **Distribution / statistical** — rolling skew, kurtosis, Hurst exponent,
  autocorrelation at lags 1..10, variance ratio tests, z-scores across horizons. (~60)
- **Trend quality** — regression slope, R², slope stability, ADX, efficiency
  ratio (net move ÷ path length), across horizons. (~40)
- **Range / structure** — distance to N-bar high/low, position within range,
  consecutive up/down bars, gap statistics, candle-body ratios. (~40)
- **Cross-sectional** (requires a universe, not one asset) — an asset's rank
  within its universe on any of the above; beta and residual vol vs. a
  benchmark; correlation to BTC / SPY / NIFTY. (~60)
- **Derived-model factors** — Kalman distance, GARCH vol forecast, HMM bull
  probability, and their rates of change. (~15) *(Kalman/GARCH/HMM already exist
  in `quant/models.py` — this is wiring, not new math.)*

**Rules that keep this from becoming garbage:**

1. **A factor family ships with its ranking output.** If a family's whole set
   has |rank IC| < 0.02 across your test assets, it goes in the library but gets
   flagged `low_signal` — you keep it for completeness, you don't pretend it's
   useful.
2. **No factor may look ahead.** The Phase-7 lookahead pin must run over every
   new factor automatically, as a parameterized test over the whole registry.
3. **Coverage matters.** A factor needing 252 bars is unusable on a 90-day
   series. Every factor declares its `min_bars`, and the panel returns `None`
   rather than a wrong number.
4. **Registry, not a god-function.** `FACTOR_REGISTRY: dict[str, FactorSpec]`
   with name, family, `min_bars`, and the pure function. Adding a factor should
   be one dict entry, and it should immediately appear in `factors <ASSET>`
   output with no other change anywhere.

**Done when.** `factors BTC` ranks 500+ factors, the parameterized lookahead
test covers every entry in the registry, and the factor-correlation report
identifies the independent clusters.

**Notes.** Do **not** chase 2000 for the number's own sake. 500 genuinely
independent, correctly-computed, coverage-honest factors beat 2000 where 1500 are
near-duplicates of each other. The correlation report is what tells you when
you've saturated a family.

---

## Phase 11 — Data Breadth: the missing ingestion domains

**Goal.** Fill Layer-2 holes from the original blueprint. Each is a self-contained
adapter following the existing `ingestion/` pattern: fetch → normalize into a
`cache/models.py` shape → done. Analyzers never learn the source's native format.

Ordered by value-per-unit-of-pain. **Each one ships with the analyzer that
consumes it — an ingestion adapter with no consumer is dead weight.**

### 11a — News & sentiment
- `cache/models.py`: new `NewsItem` (ts, headline, source, url, asset_tags,
  sentiment_score | None).
- `ingestion/rss.py` — keyless, and therefore first: NSE/BSE announcements, SEC
  EDGAR filings, Fed/RBI press releases. Pure stdlib parsing, no new dependency.
- `ingestion/finnhub_news.py` — company-tagged news, free key, gated.
- `analyzers/sentiment.py` — deterministic scoring. **Not** an LLM. Start with a
  finance-specific keyword/lexicon score, count-based, fully testable. The LLM
  cannot touch this: sentiment feeds a weight, and weights are numbers.
  - If you eventually want a model-based sentiment score, it must be a *local,
    pinned, deterministic* classifier whose output is cached per-headline —
    never a live API call inside the analyze path.
- Event-window logic: a headline three weeks old is not news. Decay the weight.

### 11b — Crypto on-chain (finally makes the "crypto agent" real)
Today the crypto analyzer is a moving-average crossover with a different name.
The blueprint's crypto agent was on-chain flows, funding rates, open interest,
and BTC dominance.
- `cache/models.py`: `OnChainObservation` (metric, ts, value, chain, source).
- `ingestion/glassnode.py` — free tier, Tier-1 daily metrics, key-gated.
- `ingestion/binance_futures.py` — funding rate + open interest, **keyless**.
  This is the highest-value/lowest-cost item in the whole phase: funding rate is
  a genuine positioning signal and Binance serves it without a key.
- `ingestion/coingecko.py` — extend for BTC dominance (already talking to
  CoinGecko; this is a second endpoint, not a new adapter).
- `analyzers/crypto_onchain.py` — exchange net-flow direction, funding-rate
  extremes (crowded longs pay to stay long — a contrarian read), OI build-up,
  dominance as a risk-on/off gauge.

### 11c — Fundamentals (the biggest missing domain)
- `cache/models.py`: `Fundamentals` (asset, period, revenue, earnings, margins,
  debt, equity, shares_out, …), `FilingEvent`.
- `ingestion/fmp.py` or `ingestion/finnhub_fundamentals.py` — free tier, gated.
- `ingestion/nse_disclosures.py` — Indian specifics the blueprint explicitly
  called for: promoter holding patterns, FII/DII flow data, corporate governance
  flags. Scraped, fragile, worth it — this is genuinely differentiated.
- `analyzers/fundamentals.py` — earnings quality (accruals vs. cash flow),
  balance-sheet strength, valuation percentile vs. the asset's own history
  (which needs no peer universe, unlike comps). Deterministic ratios only.
- **Deliberately deferred inside this sub-phase:** DCF and comps. Both require
  assumptions (discount rate, terminal growth, peer set) that are judgment
  calls, not computations. A DCF whose inputs you pick is a number you invented
  wearing a suit. Add only if you can make the assumptions explicit config with
  sensitivity ranges.

### 11d — Macro breadth
- `ingestion/rbi.py` — DBIE scrape: repo rate, CPI, WPI, forex reserves, credit
  growth. The Indian half of the macro agent currently does not exist.
- `ingestion/worldbank.py` — free, no key, global indicators for cross-market
  regime work.
- Extend `analyzers/macro_context.py` to be region-aware: an Indian equity should
  tilt on RBI posture, not the Fed's (though it should see both — DXY and Fed
  policy absolutely transmit to Indian markets).
- **Macro calendar** — the blueprint's "macro calendar agent". Upcoming RBI MPC /
  FOMC / CPI dates. Its use is defensive: a signal fired the day before a
  policy decision should carry lower confidence, deterministically.

### 11e — Forex depth
The current forex analyzer is trend + z-score. The blueprint wanted carry
dynamics, DXY correlation, and INR-specific RBI intervention zones and
CAD/oil sensitivity. Interest-rate differentials come free from FRED + RBI once
11d lands — carry is just that differential.

**Done when.** Each sub-phase's adapter caches normalized data, its analyzer
produces a `SignalSource`, `factors`/`backtest` show whether it improved
anything, and the whole thing still runs keyless with the new sources simply
absent.

**Notes.** The order is deliberate: keyless before key-gated, cheap before
fragile. And every new source must answer the Phase-7 question — *did it change
the measured result?* If a news analyzer's addition doesn't move hit rate or
calibration, you have learned something real and should say so in the README
rather than quietly shipping it as a feature.

---

## Phase 12 — A Real Orchestrator

**Goal.** The current `orchestrator/` is a for-loop with error handling over
`portfolio.json`. Honest name: a batch runner. The blueprint's Layer 1 was an
event-driven brain: triggers, priority, and shared context.

Only build this once there are enough data domains for scheduling to be a real
problem. With one cron job and eleven analyzers, it is a traffic system for a
town with one car (the original plan's phrasing, still true).

**Build.**
- **Triggers** — scheduled scan, new-data arrival, breaking-news event,
  user query. Each declares which analyzers it wakes.
- **Priority** — a breaking earnings headline preempts the 9am routine scan.
- **Shared context** — one fetch of BTC's series serves the trend, RSI, MACD,
  volatility, and factor-panel consumers within a run, rather than each
  re-reading the cache. (Today this is cheap because everything is local files.
  It stops being cheap when there are twenty analyzers and network sources.)
- **Freshness-driven ingestion** — the orchestrator refreshes what is stale,
  rather than refetching a fixed list.

**Done when.** A news event can trigger a targeted re-scan of the affected asset
without a full portfolio sweep, and a single run fetches each series once.

**Notes.** Determinism holds: the orchestrator decides *what* runs and *when*,
never *how* anything is analyzed. Scheduled batch remains the default. Do not
build always-on infrastructure — it is a large ops bill for an architecture you
have already demonstrated.

---

## Phase 13 — The ML Layer `GATED — DO NOT START EARLY`

**Goal.** The third layer of the AlphaX diagram: learn a combination of ranked
factors rather than hand-weighting them.

**The gate (all three, not two):**
1. Phase 7 shipped, so factors have measured IC.
2. Phase 9 has been running long enough that you have **12+ months** of recorded
   signal-vs-outcome data — real, live, out-of-sample-by-construction.
3. You have a genuine **held-out period** you have never looked at. Not
   "held out" after you already ran twenty experiments on it.

If any of the three is missing, this phase makes the track record *worse* while
making the charts prettier. That is the failure mode the original plan permanently warned
about, and it is not hypothetical — it is the standard outcome.

**Build (when the gate opens).**
- Walk-forward validation only. Fit on `[0..t]`, predict `t+1`, roll forward.
  A single train/test split on financial time series is not evidence.
- Start with the most boring model that could work: a regularized linear
  regression (ridge/lasso) over the top-ranked, low-correlation factors. If a
  linear model on your factors has no edge, gradient boosting will find edge that
  is not there and you will believe it.
- Report the model's IC and hit rate **against the existing blended pipeline's**.
  A model that ties the weighted-vote baseline is a negative result, and shipping
  it anyway is how projects start lying.
- The model outputs a `SignalSource` like any other analyzer. It gets no special
  status, no override, and no exemption from the reliability shrinkage in Phase 9.
- **Determinism**: pinned seeds, pinned library versions, model artifact
  committed or content-hashed. A signal must be reproducible from its inputs
  forever. If the model can't guarantee that, it doesn't go in the decision path.

**Done when.** A walk-forward-validated model beats the blended baseline on a
never-touched holdout, and the README says by how much — including if the answer
is "it doesn't."

---

## Phase 14 — MCP Server: the engine as a tool an AI can call `CHEAP — SHIP ANY TIME`

**Goal.** Expose the engine over **MCP** (Model Context Protocol — the standard by
which an AI assistant like Claude Code or Cursor discovers and calls external
tools) so a quant can ask "backtest a momentum read on RELIANCE" in their editor
and the engine answers inline.

This phase is out of dependency order on purpose: it blocks nothing, depends on
nothing after Phase 7, and is the **cheapest distribution channel available.**
"Clone this repo and set up a venv" is a wall. "Add this MCP server" is not.

**Why it fits this architecture unusually well — and it isn't luck.** MCP means the
LLM *calls* deterministic tools and *reads* their results. It never computes the
numbers. That is precisely this repo's cardinal rule, and it means the engine is
already shaped correctly for MCP without a single compromise. Most quant MCP
servers get this backwards: they let the model do the reasoning and the math, so
the output is unreproducible. Ours structurally cannot — the model can only ask
the engine questions and relay what tested Python answered.

**Build.**

- `mcp/server.py` (or a top-level `mcp_server.py` — it lives *outside* the
  installed package, like `web/`, so the library stays pure).
- Tools exposed, each a thin wrapper over an existing CLI command that already
  returns JSON:
  - `scan(asset, market?)` → the full `Signal` object.
  - `report(asset)` → the quant metrics report.
  - `backtest(asset, days?, step?)` → the `BacktestReport`.
  - `factors(asset, horizon?)` → the Phase-7 ranked factor table.
  - `record_stats()` → the live track record.
- **The work is almost entirely wrapping, not writing.** The CLI already emits
  structured JSON and the pipeline has no side effects beyond the cache and the
  append-only log. Budget ~100 lines.

**Non-negotiables for this surface:**

1. **The disclaimer travels with every payload.** Not in the README — in the tool
   response itself. An MCP tool result gets pasted into other people's contexts;
   the research-only framing must be inseparable from the data.
2. **Rate-limit the upstream sources hard.** An MCP server that gets popular will
   get your IP banned by CoinGecko within a day if the cache doesn't absorb the
   traffic. The cache layer already exists for exactly this; make the MCP path
   `no_refresh`-biased and serve stale-but-labelled data rather than hammering
   free APIs on every call.
3. **Read-only by default.** No tool writes to the signal log unless explicitly
   asked. The log is the compounding asset; a chatty assistant should not be able
   to pollute it with exploratory scans.
4. **Never expose a tool that lets the caller set a number.** No
   `set_confidence`, no `override_weight`. The tools answer questions; they do not
   accept opinions.

**Done when.** A user with no Python environment can add the MCP server to their
assistant, ask for a backtest of any supported asset, and get the same JSON the
CLI would produce — disclaimer attached.

**Notes.** This is also the natural precursor to Part B's API. The tool surface you
design here — what a caller is allowed to ask, and what comes back — is the same
surface the platform will eventually expose over HTTP. Get it right once.

---

# PART B — QuantHQ: the Platform

## B.0 — The architectural decision that comes before any code

**QuantHQ is a different codebase.** A web app with users, sessions, a database,
and eventually payments. It must not live in this repo.

This repo stays what it is: an importable, deterministic, keyless-by-default
research library that the platform *calls*. The moment user accounts and billing
enter it, the "clone it and run it with zero setup" property dies — and that
property is the entire reason anyone believes the engine. A backtest engine whose
source you can read and re-run yourself is credible. One behind a login is a
claim.

So the shape is:

```text
alpha-engine  (this repo)          quanthq  (new repo)
  pure, deterministic, keyless  →  web app: auth, DB, feed, profiles, payments
  importable Python library        calls the engine in a sandboxed worker
  the thing that is TRUSTED        the thing that is USED
```

**The engine's role in the six tabs you sketched:**

| Tab | Powered by the engine? |
| --- | --- |
| **Feed** | No — standard social app. |
| **Reach** | No — matching/messaging. |
| **Research** (Analyst + AlphaX) | **Yes** — `scan`, `report`, and the Phase-7 `factors` ranking. |
| **Create** | Partly — the engine is the runtime the code targets. |
| **Backtest** | **Yes** — this is the whole moat. See B.1. |
| **Chat** | No. |

Your own note said the USP is the community, not the analysis. That's right about
what *sells*. But a community needs something to be a community *about*, and it
needs a reason to trust what its members claim. The engine is both.

---

## Phase A — Identity & Verified Backtests

**Goal.** Establish the one thing QuantHQ can offer that LinkedIn, GitHub, and
Kaggle structurally cannot: **a backtest result that a stranger can believe.**

**Why this is the moat (and the only real one in the business doc).**
Today, a quant's evidence of skill is a screenshot of an equity curve. Everyone
knows screenshots are worthless — they hide the lookahead bugs, the survivorship
bias, the fifty strategies tried before the one shown. There is currently **no
neutral referee** in quant hiring. That is the gap.

If QuantHQ runs the backtest *itself*, on its own infrastructure, on data the
user never touched, with the no-lookahead guarantee this engine already enforces
structurally — then a QuantHQ-verified result is not a claim, it is a fact. That
is worth paying for, and it is what every later revenue line (QuantScore,
recruiters, marketplace) is secretly built on top of.

Everything else in the QuantHQ doc is a feature. This is the product.

**Build.**

- **Accounts & profiles.** Auth, user records, public profile pages.
- **The verified-backtest service.** The core piece:
  - A user submits a strategy (code, or a declarative signal spec).
  - QuantHQ runs it **server-side, in a sandboxed worker**, against
    QuantHQ-held price data the user never had access to.
  - The engine's [`signal_at`](src/alpha_engine/validation/backtest.py)
    truncation choke point guarantees no lookahead — this is not a promise the
    platform makes, it is a property of the code path.
  - The result is written immutably (same discipline as
    `validation/recorder.py`), stamped with the engine's `schema_version`, the
    data window, and a content hash of the strategy.
  - **Every run is recorded — including the failures.** A user who ran 50
    strategies and publishes the best one is doing exactly what the screenshot
    problem lets people do today. QuantHQ knows they ran 50, and the profile can
    say so. That single fact is more honest than any existing quant credential.
- **Sandboxing is a hard requirement, not a nice-to-have.** You are executing
  strangers' code on your servers. Container isolation, no network egress from
  the worker, CPU/memory/wall-clock caps, a filesystem the job cannot escape.
  Treat this as the security boundary of the entire business — because it is.
- **Profiles as living portfolios**: verified backtests, published factor
  research, strategies, competition results. Not "5 years experience"; the actual
  measured work.
- **Public/private repositories** for strategies, notebooks, factor libraries.

**Done when.** A user publishes a strategy, QuantHQ re-runs its backtest
server-side on held-out data, and the number on their profile is one **QuantHQ
computed** — not one the user typed in. And a recruiter looking at it can see how
many attempts it took.

**Notes.** Do not weaken the engine's no-lookahead guarantees to make the
platform's life easier. They are not an implementation detail you are working
around — they are the asset you are selling.

---

## Phase A2 — The Verified-Backtest API: QuantHQ as infrastructure

**Goal.** Expose the Phase-A verification service as a public API, so that
QuantHQ stops being a *destination* and becomes **infrastructure other products
depend on.**

**The reframe.** If the product is "a backtest a stranger can believe," then the
highest-leverage form of it is not a website with a login. It is an endpoint:

```text
POST /v1/backtest
  { strategy: <code|spec>, universe: ..., window: ... }
→ { result: {...}, data_window: ..., engine_version: ..., signature: ... }
```

Your strategy runs in QuantHQ's sandbox, against data you never saw, through the
engine's no-lookahead choke point, and comes back as a **signed, tamper-evident
result.**

**Think Stripe, not LinkedIn.** Nobody *visits* Stripe; everybody depends on it.
A fund's internal tooling calls the API. A university's quant course calls it to
grade assignments. A competing platform embeds it because building a credible
backtester is harder than paying for one. Every one of those callers is now
distributing QuantHQ's verification as a trusted primitive — which is a far
stronger position than being one more website quants have to remember to visit.

**Build.**

- **The signed result.** This is the piece that makes it infrastructure rather
  than a hosted script. The response carries a cryptographic signature over
  `{strategy_hash, data_window, engine_version, result}`. Anyone can verify a
  QuantHQ result is genuine and unaltered **without trusting the party showing it
  to them.** A screenshot can be faked; a signed result cannot. This is what lets
  a third-party site display "QuantHQ-verified Sharpe 1.4" and be believed.
- **Public verification endpoint.** `GET /v1/verify/<result_id>` — anyone, with no
  account, can confirm a result is real. Free, forever. It costs nothing and it is
  the entire trust mechanism.
- **The sandbox** (shared with Phase A — one runner, one guarantee): container
  isolation, **no network egress**, CPU/memory/wall-clock caps, an escape-proof
  filesystem. You are executing strangers' code. This is the security boundary of
  the whole business.
- **Held-out data as a service.** The API's value is that the caller *cannot* see
  the evaluation data. Data licensing and access control is therefore not an ops
  detail — it is the product. Budget for it.
- Rate limits, API keys, usage-based pricing. This is the one place in the whole
  project where a paywall is correct, because the cost is real compute.

**Done when.** A third party can call the API, get a signed result, display it on
their own site, and a skeptic can verify it against QuantHQ without an account.

**Notes — read before starting.**

**Do not build this before Phase A has real users asking for it.** An API with no
callers is the most expensive kind of nothing: you will spend months on sandbox
hardening, data licensing, and key management to serve zero requests. The
sequence is: verify backtests for your own users (A) → notice people asking to
embed or automate it → *then* build A2.

**The engine's MCP server (Phase 14) is the cheap rehearsal for this.** Same
question — what may a caller ask, and what comes back — at a fraction of the cost
and with no security surface. Design the tool contract there, learn what people
actually want, and let A2 inherit it.

**What must never be an API:** the feed, profiles, chat, the social graph. Zero
value, real cost, and it hands scrapers your community. Social APIs get built when
partners demand them, and not one day sooner.

---

## Phase B — Collaboration & Community

**Goal.** The tabs that make it a place people return to daily rather than a tool
they visit when job-hunting.

**Build.** Feed (posts), Reach (collab/outreach — the "Quant Match" cofounder
idea lives here), Chat, teams, project workspaces, discussions, notifications.

**Notes.** This is ordinary social-app engineering with no engine dependency, and
it is where the doc's USP claim actually lands. Two honest observations:

- **A feed with no content is a ghost town, and a ghost town kills a launch.**
  Phase A's verified backtests are the content that seeds it: "X published a
  verified strategy, Sharpe 1.4, 3-year holdout" is a post worth reading. Build
  the feed *after* there is something to put in it, or it launches empty and
  never recovers.
- **"Quant Research Dating" / Quant Match is genuinely a strong idea** and it is
  cheap once profiles exist — matching on complementary skills (ML × options ×
  C++ × macro) is a query, not a research project. It's the highest
  value-per-line-of-code item in Part B.

---

## Phase C — Competitions, QuantScore & StrategyScore

**Goal.** Turn verified results into a *ranking* the industry trusts.

**Build.**

- **Competitions.** Industry-sponsored problems (alpha generation, vol
  forecasting, regime prediction, options pricing).
  - The evaluation period is **held out and never shown to participants.**
    Without this, the leaderboard ranks overfitting skill, and everyone
    sophisticated will know it does. This is the single design decision that
    determines whether competitions mean anything.
  - Submissions run through the same sandboxed, no-lookahead runner as Phase A.
    One code path, one guarantee.
- **QuantScore — the reputation engine.** Only trustworthy if every input is
  verified:
  - Server-run backtest quality (not self-reported Sharpe — **never** accept a
    self-reported number into the score, it poisons the whole thing).
  - Competition results on hidden data.
  - Peer review and open-source contribution.
  - **Robustness, not just returns.** Reward out-of-sample stability; penalize
    strategies that look brilliant in-sample and die out. The engine's calibration
    curve is already the right mental model for this.
  - Publish the formula. An opaque reputation score gets gamed *and* distrusted —
    a transparent one only gets gamed, and you can patch that.
- **StrategyScore — "Moody's for quant models."** The strongest idea in your whole
  document, and mostly *engine* work rather than platform work:
  - Out-of-sample performance, drawdown profile, turnover, **capacity** (at what
    AUM does the edge die?), regime robustness (the HMM in `quant/models.py`
    already classifies regimes), survivorship-bias checks.
  - "QuantHQ AA+ rated" is a real product for allocators, who today genuinely
    cannot compare strategies on any common basis.
  - Note the conflict-of-interest trap Moody's actually fell into: **never let
    the entity being rated pay for its rating.** Whoever pays, someone else must
    grade.

**Notes.** Order is load-bearing: verification (A) → competitions on hidden data
(C) → score. Build the score first and it is decoration on unverified claims, and
you only get one shot at being believed.

---

## Phase D — Recruiter Platform & Marketplace

**Goal.** Monetize the trust built in A–C. This is where the revenue actually is.

**Build.**

- **Recruiter search** over *verified* attributes: QuantScore, strategy
  performance, competition rank, factor research, language/domain skills.
  Subscription-priced, LinkedIn-Recruiter-style. This is the largest and most
  reliable revenue line in the doc.
- **Quant University / apprenticeship** (your idea 2): firms post real problems,
  students solve them, the work is verified by the same runner, the best get
  hired. This is *the recruiter product with a better funnel* — the candidate has
  already done the job before being interviewed. It should probably ship as part
  of D, not as a separate thing.
- **Job board**, application flow.
- **Marketplace**: research reports, strategies, factor libraries, datasets,
  backtest frameworks. Platform takes a cut.

**Notes.** Both lines are entirely downstream of trust. A recruiter pays for
search only if the scores mean something; a buyer pays for a strategy only if its
backtest is real. **Do not attempt to monetize before Phase A ships** — selling
access to unverified profiles is just a worse LinkedIn.

One hard regulatory line: the marketplace sells **research and tools**, not
signals or advice. The moment QuantHQ takes a cut of someone selling buy/sell
calls to retail Indians, it is in SEBI's regulated space and the whole
research-only framing that protects this project collapses. Enforce it in the
marketplace's terms *and* in its listing categories, from day one.

---

## Phase E — Enterprise

**Goal.** Recurring revenue from funds and prop shops.

**Build.** Team repositories, experiment tracking, model governance, institutional
knowledge management, private compute. Essentially Phase A + B behind a firm's
own walls.

**Notes.** Last, and only when demand is *pulling* — an enterprise customer asking
for it, not a slide predicting they will. Enterprise features built on
speculation are the most expensive possible way to discover nobody wanted them,
and they distort the roadmap for years.

---

## The one-line strategy for Part B

**Everything QuantHQ sells is downstream of one thing: a backtest a stranger can
believe.** Build that first, in a sandbox, on held-out data, with the
no-lookahead guarantee the engine already gives you for free. The feed, the
score, the recruiters, and the marketplace are all just ways of monetizing that
trust — and none of them work without it.

---

## Permanently deferred / handle with extreme care

- **Anything resembling execution, order placement, or managed money.** Out of
  scope by design. It changes the regulatory profile entirely and destroys the
  research-only framing that keeps the project clear of SEBI RA registration.
- **Auto-tuning confidence from live results without human review.** See Phase 9.
  The clunkiness is the safety.
- **An LLM anywhere in the decision path.** Not for sentiment scoring, not for
  factor selection, not for "just the weights". If a number depends on a model
  that could answer differently tomorrow, the backtest is a work of fiction.
- **Real-time / always-on infrastructure.** Scheduled batch demonstrates the same
  architecture for a fraction of the cost and stays friendly to anyone cloning
  the repo.
- **Selling signals.** The moment money changes hands for a directional view, the
  entire regulatory framing collapses.

---

## Cross-cutting rules (unchanged from the original plan, restated because they still bind)

1. **Determinism in the decision path.** Non-negotiable.
2. **Keyless default path stays keyless.** New sources are additive and gated.
3. **Every deterministic change ships with tests.** Analyzer or synthesis changes
   without tests are incomplete.
4. **No lookahead.** Now enforced at three levels: `signal_at` for backtests,
   point-in-time macro, and (from Phase 7) the factor-panel pin.
5. **Honesty over hype.** Heuristics are scaffolds until validation proves edge.
   Publish the losing results. Never weaken the disclaimer.
6. **Smallest testable increment.** A working slice of one thing beats a
   half-built abstraction over five.

---

## The immediate next step

**Phase 7.** It is small, it is the stated core of AlphaX, and every phase after
it is measurably better for its existing. Concretely, the first commit:
`compute_factor_panel()` + `rank_ic()` + the three tests (synthetic signal, pure
noise, lookahead pin). Everything else in Phase 7 is presentation on top of those.

And keep the daily cron running. Phase 9 and Phase 13 are both gated on recorded
history, and that history only accumulates while scans happen. Every day the cron
doesn't run is a day added to the wait.
