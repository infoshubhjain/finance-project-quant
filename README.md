# Alpha Engine

An open, deterministic research engine that turns market data into structured,
confidence-scored signals across **crypto, US equities, Indian equities, Indian
F&O, and forex** — with **zero API keys required** for the default path.

> **This is a research and education project, not investment advice.** It produces
> directional *research views*, not buy/sell recommendations. See [Disclaimer](#disclaimer).

---

## The core design rule

**Every number is computed by deterministic, tested Python.** A language model may
write ONLY the `thesis` prose string, and may never set or change a number.

Concretely:

- `direction`, `confidence`, `invalidation_level`, and all source weights are
  pure functions — same input, same output, always.
- No analyzer makes a network call. Analyzers read from the `Cache`. Fetching
  lives in `ingestion/`.
- No randomness in `analyzers/` or `synthesis/`.
- The LLM lives only in `narrative/`, is optional, and is gated behind a
  user-supplied key. With no key, a deterministic template writes the thesis.

This rule is what makes the engine **backtestable**. If a language model set the
confidence score, you could never replay history and check whether the engine was
right, because the model might answer differently tomorrow.

---

## Pipeline

```text
ingestion/  →  cache/  →  analyzers/  →  synthesis/  →  narrative/  →  Signal  →  validation/
(network)     (disk)     (pure math)    (weighted vote)  (prose only)            (log, score, backtest)
```

Data flows one way. Each stage may only look left.

---

## Markets & capability

| Market | Default (zero-key) | With free key | With broker account |
|---|---|---|---|
| Crypto | CoinGecko + Binance fallback | CoinGecko Pro (raise rate limits) | — |
| US equities | Yahoo Finance | FRED macro context added | — |
| Indian equities | Yahoo via `.NS` / `.BO` | — | — |
| Indian F&O | Analytics on cached/fixture chains | — | Breeze / Angel One / Dhan |
| Forex | — | — | OANDA (free practice account) |
| US macro | — | FRED (free key, fred.stlouisfed.org) | — |

The LLM narrator is optional. With no model key, a deterministic template writes
the thesis. A configured model only upgrades the phrasing.

---

## Quickstart

```bash
git clone <repo-url> alpha-engine
cd alpha-engine
pip install -e ".[dev]"

# No API key needed — crypto and US equities both work keyless.
python -m alpha_engine.cli.main scan BTC
python -m alpha_engine.cli.main scan AAPL

# Run the tests to confirm everything behaves:
pytest -q

# Or use the zero-setup wrapper:
./start.sh scan BTC
```

See [all CLI commands](#cli-commands) below.

---

## CLI commands

| Command | Description |
|---|---|
| `scan <ASSET>` | Generate a signal for one asset (auto-detects market) |
| `scan-all` | Scan all assets configured in `portfolio.json` |
| `watch <ASSETS...>` | Scan multiple assets, print a compact table |
| `backtest <ASSET>` | Replay history through the analyzer (no lookahead) |
| `report <ASSET>` | Full quant metrics report (regime, scores, vol forecast, indicators, ~50 features) |
| `record-stats` | Score every recorded signal against outcomes |
| `scan-chain <FILE>` | Analyze a normalized OptionsChain JSON fixture |
| `fetch-chain <ASSET>` | Fetch a live Indian F&O chain from broker and analyze it |
| `batch` | Scheduled batch scan with JSON report output (cron-friendly) |
| `dashboard` | Launch read-only web UI |

Common flags:

| Flag | Applies to | Description |
|---|---|---|
| `--market crypto\|us_equity\|in_equity\|in_fno\|forex` | scan, backtest, report, watch | Force market instead of auto-detecting |
| `--days N` | scan, backtest, report, watch | History window to fetch (default varies) |
| `--no-refresh` | scan, backtest, report, watch | Use cache even if stale |
| `--no-record` | scan, scan-chain, fetch-chain, scan-all, batch | Don't append to the signal log |
| `--llm` | scan, watch, scan-all, batch | Use optional LLM to rephrase thesis (needs `LLM_API_KEY`) |
| `--json` | report | Emit full report as JSON |
| `--per-analyzer` | backtest | Backtest each analyzer in isolation plus the blend |
| `--step N` | backtest | Bars between simulated signals (default 1) |
| `--sort confidence\|asset\|market` | watch | Sort the batch output |
| `--broker breeze\|angelone\|dhan` | fetch-chain | Broker to fetch from (default breeze) |
| `--expiry YYYY-MM-DD` | fetch-chain | Expiry date (required) |
| `--config <PATH>` | scan-all, batch | Path to portfolio.json |
| `--output <PATH>` | batch | Write JSON report to disk |
| `--host`, `--port` | dashboard | Bind address (default 127.0.0.1:8000) |

### Market auto-detection

| Pattern | Detected as |
|---|---|
| `BTC`, `ETH`, `SOL` (known crypto) | crypto |
| `NIFTY`, `BANKNIFTY`, `FINNIFTY`, `MIDCPNIFTY`, `SENSEX` | in_fno |
| `RELIANCE.NS`, `TCS.BO` (`.NS` / `.BO` suffix) | in_equity |
| `EURUSD`, `GBPUSD` (currency pairs) | forex |
| Everything else (`AAPL`, `MSFT`, `GOOGL`) | us_equity |

Override with `--market`.

---

## Ingestion adapters (10 sources)

| Adapter | Source | Market | Key needed? |
|---|---|---|---|
| `coingecko.py` | CoinGecko keyless API | Crypto | No |
| `coingecko_pro.py` | CoinGecko Pro | Crypto | Free tier key |
| `binance.py` | Binance public API | Crypto | No (fallback) |
| `yahoo.py` | Yahoo Finance chart endpoint | US equities, Indian equities | No |
| `fred.py` | FRED (St. Louis Fed) | US macro | Free key |
| `oanda.py` | OANDA | Forex | Free practice account |
| `breeze.py` | Breeze (Indian broker) | Indian F&O | Broker credentials |
| `angelone.py` | Angel One SmartAPI | Indian F&O | Broker credentials |
| `dhan.py` | Dhan | Indian F&O | Broker credentials |
| `indian_fno.py` | Normalized JSON fixture loader | Indian F&O | No |

Fallback chains: crypto tries CoinGecko Pro → keyless CoinGecko → Binance, so a
rate-limit error never kills a scan.

---

## Analyzers (17 pure-function specialists)

| Analyzer | What it reads | What it outputs |
|---|---|---|
| `crypto_trend` | Price series | Dual-MA trend + momentum for crypto |
| `equity_trend` | Price series | Dual-MA trend + momentum for US equities |
| `indian_equity` | Price series | Dual-MA trend for Indian equities |
| `forex_trend` | Price series | Dual-MA trend + z-score for forex |
| `rsi` | Price series | Relative Strength Index (0–100 overbought/oversold) |
| `macd` | Price series | MACD crossover momentum |
| `bollinger` | Price series | Bollinger Band position (±2σ) |
| `volume` | Price series | On-balance volume confirmation |
| `vwap` | Price series | Volume-weighted average price distance |
| `support_resistance` | Price series | Swing high/low cluster detection |
| `multi_timeframe` | Price series | Short/medium/long horizon agreement |
| `volatility` | Price series | ATR regime (always votes NEUTRAL; extreme tape scales other weights ×0.6) |
| `macro_context` | FRED macro observations | Tightening/easing posture (cap at weight 0.35) |
| `fno_oi` | Options chain | PCR, max pain, OI shifts, put/call walls |
| `correlation` | Multiple price series | Pairwise return correlation matrix (used by portfolio view) |
| `portfolio_signal` | Multiple signals + price series | Net bias, conviction weights, diversification score |

---

## Quant features & models

The `report` command produces a scored quant metrics summary from:

### ~53 deterministic features

| Category | Examples |
|---|---|
| Returns | 1/5/10/20-day return, log returns |
| Trend | SMA/EMA crossovers, trend strength, regression slope & R² |
| Volatility | Realized vol, ATR, vol ratios, Garman-Klass, Parkinson |
| Volume | OBV, volume z-score, VWAP distance, Amihud illiquidity |
| Momentum | RSI, MACD, rate of change, efficiency ratio |
| Range | Distance to N-bar high/low, position within range, candle-body ratios |
| Statistical | Rolling skew, kurtosis, Hurst exponent, autocorrelation, variance ratio |
| Candle shape | Body-to-range ratio, gap statistics, consecutive up/down bars |

### Statistical models (dependency-free pure Python)

| Model | What it does |
|---|---|
| **Kalman filter** | Local-level filter treating price = hidden fair value + noise. Reports fair value, distance (%), and slope. |
| **GARCH(1,1)** | Volatility forecast. Fit by exhaustive grid search (no stochastic optimizer, stays deterministic). Reports next-bar and annualized vol forecasts. |
| **2-state HMM** | Infers bull/bear regime from return pattern, with fixed deterministic initialization. Reports regime probability and state sequence. |
| **ADX** | Average Directional Index — measures whether a trend exists (above ~25 = trend, regardless of direction). |
| **Volume profile** | Point of control (busiest price level) and top-3 volume nodes — support/resistance from participation data. |

Indicators also include Keltner channels, envelope bands, and a blended trend/momentum/volume conviction score.

---

## Synthesis & confidence calibration

`synthesis/synthesize.py` folds all analyzer `SignalSource`s into one `Signal`:

1. **Net direction** — weighted vote: bullish sources add weight, bearish subtract.
   A deadband (±0.1) keeps tiny nets neutral.

2. **Confidence** — calibrated from three components:
   - **Agreement quality**: what fraction of total weight agrees with the final
     direction
   - **Source reliability**: each analyzer's historical accuracy (set from
     backtest data, currently ~0.50 for scaffold analyzers)
   - **Source diversity**: a source-count cap prevents few sources from reaching
     high confidence (1 source max 0.45, 5+ sources max 0.78, never 1.0)

3. **Invalidation level** — the price at which the view is wrong, derived from
   recent swing structure and keyed to the synthesized direction. This is the
   schema's most important honesty mechanism.

---

## Validation

### Signal recording

Every `scan` appends one line to `data/signals/signals.jsonl` with the signal
and the entry price at that moment. **Append-only**: the code has no path that
can rewrite an old line. Signals are recorded before anyone knows the outcome.

### Outcome scoring

`record-stats` scores recorded signals against what actually happened:

- A swing signal gets 10 trading days for its direction to play out
- If price touches the invalidation level first, it's an immediate miss
- Neutral signals are not scored (they make no claim)
- Produces a calibration curve showing whether confidence levels match hit rates

### Backtesting

`backtest <ASSET>` replays history through the same analyzers with a structural
**no-lookahead guarantee**:

- `signal_at(series, t)` is the only way to generate a historical signal
- It truncates the series to bars `[0..t]` before any analysis
- A unit test pins byte-identical output whether or not the future exists
- Macro observations dated after bar `t` are also invisible

The honest baseline finding: **roughly a coin flip on BTC** with the current
scaffold analyzers. That measured baseline is what every improvement gets judged
against.

---

## Orchestrator & portfolio

### Batch scanning

The orchestrator runs scans across multiple assets sequentially (to respect API
rate limits), with **fault isolation** — one asset failing never blocks another.

```bash
python -m alpha_engine.cli.main scan-all          # all in portfolio.json
python -m alpha_engine.cli.main batch --output r.json  # cron-friendly
```

Configure assets in `portfolio.json` at the project root.

### Portfolio view

The dashboard and `scan-all` output include a portfolio-level aggregation
(computed by `analyzers/portfolio_signal.py`):

- **Net bias**: confidence-weighted average of all directional signals (-1 to +1)
- **Conviction weights**: each asset's share of total directional confidence
- **Diversification score**: 1 minus average pairwise return correlation
- **Concentration flags**: warnings when views cluster (all one direction,
  same-direction assets highly correlated)
- **Correlation matrix**: pairwise return correlations for all directional assets

---

## Dashboard

The read-only dashboard serves from the stdlib HTTP server — no build step,
no auth, no writes, no trading actions.

```bash
python -m alpha_engine.cli.main dashboard       # via CLI
python -m web.server                            # directly
python -m web.server --host 0.0.0.0 --port 8000
```

| Route | Description |
|---|---|
| `/` | Dashboard HTML |
| `/api/dashboard` | Aggregate payload (latest signals, outcomes, portfolio view) |
| `/api/asset/<SYMBOL>` | Full recorded history for one asset |

The frontend is vanilla JS + inline SVG in `web/static/`. No build step, no npm.

---

## Project structure

```text
src/alpha_engine/
  schema/signal.py          Signal, SignalSource, Direction, Market enums. The contract.
  cache/models.py           Normalized data shapes: Candle, PriceSeries, OptionsChain, MacroObservation
  cache/interface.py        Cache (public read API) + LocalStore + TTL/staleness
  ingestion/                Source adapters: coingecko, yahoo, fred, oanda, breeze, angelone, dhan, binance, coingecko_pro, indian_fno, indian_broker
  quant/features.py         ~53 deterministic features (returns, trend, vol, volume, stats)
  quant/models.py           Kalman filter, GARCH(1,1), 2-state HMM — dependency-free pure Python
  quant/report.py           Scored quant report + ADX, Keltner, volume profile, verdict
  analyzers/                Pure-function specialists: trend, rsi, macd, bollinger, volume, vwap, support_resistance, multi_timeframe, volatility, macro_context, fno_oi, correlation, portfolio_signal, indian_equity, forex_trend, crypto_trend, equity_trend
  synthesis/synthesize.py   Weighted-vote synthesis with confidence calibration
  narrative/narrator.py     Templated thesis; optional LLM hook
  narrative/llm.py          LLM rephrasing with re-validation guard
  validation/recorder.py    Append-only JSONL signal log (data/signals/)
  validation/outcomes.py    Outcome scoring: hit/miss/miss_immediately, calibration curve
  validation/backtest.py    No-lookahead replay; signal_at is the truncation choke point
  dashboard/service.py      Dashboard data assembly with thread-safe snapshot
  orchestrator/             Multi-asset batch scanning with fault isolation
  cli/main.py               All CLI commands: scan, scan-all, watch, backtest, report, record-stats, scan-chain, fetch-chain, batch, dashboard
  config.py                 .env file loader (stdlib, no dependency)
web/
  server.py                 Read-only dashboard HTTP server (stdlib ThreadingHTTPServer)
  static/index.html         Dashboard HTML
  static/app.js             Dashboard JS
  static/style.css          Dashboard CSS
tests/                      23 test files, all network-free, covering every layer
docs/
  architecture.md           Pipeline deep dive
  analyzer-guide.md         How to write and add analyzers
  deployment.md             Docker and deployment guide
```

---

## Environment variables

All optional. The default path needs none — see `.env.example` for the full list.

| Variable | Purpose |
|---|---|
| `FRED_API_KEY` | US macro context (free, fred.stlouisfed.org) |
| `LLM_API_KEY` | Optional LLM narrator (works with any OpenAI-compatible API) |
| `LLM_MODEL` | Model name (default: `gpt-4o-mini`) |
| `LLM_API_BASE` | API base URL (default: `https://api.openai.com/v1`) |
| `COINGECKO_API_KEY` | Raise crypto rate limits (free tier) |
| `BREEZE_API_*` | Indian F&O chain via Breeze |
| `ANGEL_ONE_*` | Indian F&O chain via Angel One SmartAPI |
| `DHAN_*` | Indian F&O chain via Dhan |
| `OANDA_API_KEY` | Forex candles (free practice account) |

The app searches for `.env` / `.env.local` in the current directory, the project
root, and parent directories. Existing shell variables always take priority.

---

## Deployment

### Docker

```bash
docker compose up          # runs scan-all + dashboard
docker compose run engine python -m alpha_engine.cli.main scan BTC
```

See `Dockerfile` (slim Python 3.12 image) and `docker-compose.yml` (engine
service + dashboard service).

The Docker setup creates required `data/` directories at startup, and the engine
container runs a default scan-all on each start.

### Zero-setup script

```bash
./start.sh                   # creates venv + launches dashboard
./start.sh scan BTC          # creates venv + scans
./start.sh backtest BTC      # creates venv + backtests
./start.sh batch             # creates venv + batch scan
```

---

## Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q         # all tests must pass (network-free)
ruff check .      # lint must be clean

# Manual end-to-end check:
python -m alpha_engine.cli.main scan BTC
```

### Test suite (23 files, all network-free)

| Test file | What it covers |
|---|---|
| `test_core.py` | Determinism, schema validation, core pipeline |
| `test_quant.py` | Features, Kalman, GARCH, HMM, report determinism |
| `test_validation.py` | Recorder immutability, outcome scoring, no-lookahead pin |
| `test_analyzers.py` | Individual analyzer behavior on fixed inputs |
| `test_markets.py` | Yahoo/FRED parsing, equity + macro analyzers, blending |
| `test_fno.py` | Options chain PCR, max pain, OI shift |
| `test_cache.py` | Cache TTL, staleness, put/get round-trip |
| `test_cli.py` | Argument parsing, market detection, error paths |
| `test_backtest_extended.py` | Per-analyzer backtest, edge cases |
| `test_orchestrator.py` | Config loading, batch scanning, fault isolation |
| `test_portfolio.py` | Portfolio view, correlation, diversification |
| `test_dashboard.py` | Dashboard payload assembly |
| `test_dashboard_extended.py` | Dashboard edge cases, thread safety |
| `test_web.py` | HTTP server routing, static file serving |
| `test_ingestion_adapters.py` | Ingestion adapter parsing logic |
| `test_forex.py` | Forex trend analyzer |
| `test_config.py` | .env file loading |
| `test_narrator.py` | Thesis generation, LLM re-validation guard |
| `test_fixtures.py` | Fixture data loading |
| `test_indian_broker.py` | Broker error handling |
| `test_angelone.py` | Angel One adapter |
| `test_dhan.py` | Dhan adapter |

### CI

GitHub Actions runs tests on Python 3.11, 3.12, and 3.13, plus lint checks.

---

## Future work

The highest-leverage next phase is **Factor Ranking** (Phase 7 in
[FUTURE_WORK.md](FUTURE_WORK.md)): turn the ~53 features into a ranked table of
which factors actually predict forward returns, measured by Spearman IC and hit
rate. This establishes the scoring system every future addition gets judged
against.

Other planned work: wire up the existing but unimported correlation/portfolio
analyzers, add news/sentiment ingestion, crypto on-chain data, fundamentals,
macro breadth, an MCP server for AI assistant integration, and the full
validation feedback loop (offline, human-invoked calibration).

See [FUTURE_WORK.md](FUTURE_WORK.md) for the full phased roadmap. (The original
build plan it grew from — Phases 0–6 — is fulfilled and has been retired.)

---

## Status & honesty notes

- **Analyzers are scaffolds, not alpha.** They are transparent heuristics meant
  to exercise the pipeline. The backtester proves it: roughly coin-flip hit rate
  on BTC. That baseline is what improvement gets measured against.
- **Confidence calibration is improved** but still heuristic. The synthesis layer
  factors in source reliability and agreement quality. Fixing confidence against
  recorded outcomes is ongoing work.
- **Free data sources rate-limit.** The cache exists so you read local data
  instead of hammering APIs. If you see a 429, wait and retry. Tests are
  network-free.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The rule that matters: analyzers and
synthesis stay deterministic and tested. If your change makes a number depend on
an LLM or on randomness, it belongs somewhere else.

---

## Disclaimer

This software is provided for research and educational purposes only. It does not
constitute financial, investment, or trading advice, and its authors are not
registered investment advisers in any jurisdiction. Markets involve risk of loss.
Do your own research and consult a licensed professional before making any
financial decision. See [LICENSE](LICENSE) for warranty terms.
