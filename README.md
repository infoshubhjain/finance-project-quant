# Alpha Engine

An open, deterministic research engine that turns market data into structured,
confidence-scored signals. It is built to be read, cloned, and run by anyone, with
**zero setup required** for the default path.

> **This is a research and education project, not investment advice.** It produces
> directional *research views*, not buy/sell recommendations. Nothing here is a
> solicitation to trade. See [Disclaimer](#disclaimer).

## What it does

It runs a clean pipeline, one stage at a time:

```text
  ingest          cache            analyze            synthesize        narrate
 (sources)  ->  (local store)  ->  (deterministic) ->  (weighted vote) -> (prose)
                                                                              |
                                                                              v
                                                                          Signal
```

Every numeric, decision-bearing field is computed by **deterministic, tested
Python**. The only free-text field, `thesis`, is the only thing a language model is
ever allowed to write, and even then it may not change a single number. This
separation is the core design principle of the project.

## Quickstart

```bash
git clone <your-repo-url> alpha-engine
cd alpha-engine
pip install -e ".[dev]"

# Optional: copy `.env.example` to `.env` and fill in your own values.
# The app auto-loads `.env` if present, so you can keep your shell clean.

# Generate a signal. No API key needed. Crypto and US equities both work keyless.
python -m alpha_engine.cli.main scan BTC
python -m alpha_engine.cli.main scan AAPL

# Analyze a normalized Indian F&O chain fixture or a raw broker-export JSON,
# without any broker credentials.
python -m alpha_engine.cli.main scan-chain data/cache/chain/NIFTY.json
python -m alpha_engine.cli.main scan-chain raw_chain.json --underlying NIFTY

# Fetch a live Breeze chain from a test account and analyze it.
python -m alpha_engine.cli.main fetch-chain NIFTY --expiry 2026-07-30

# Print a compact table for a batch of assets.
python -m alpha_engine.cli.main watch BTC AAPL NIFTY --sort confidence

# Open the read-only dashboard.
python -m web.server

# Replay history through the analyzer (no lookahead) and see the honest hit rate.
python -m alpha_engine.cli.main backtest BTC --days 365

# Score every signal you've recorded against what the market actually did.
python -m alpha_engine.cli.main record-stats
```

You should see a JSON `Signal` printed, with a direction, a calibrated confidence,
the contributing inputs, an invalidation level, and a plain-language thesis.

All optional integrations read from environment variables. If you prefer, put
your values in `.env` instead of exporting them one by one; the app will load
it automatically.

Run the tests to confirm the deterministic core behaves:

```bash
pytest -q
```

## Capability matrix

The engine runs in a real, useful mode with no credentials, and unlocks more as you
add free keys or broker accounts. Nothing here requires payment.

| Market            | Default (zero-key)      | With free key            | With broker account   |
| ----------------- | ----------------------- | ------------------------ | --------------------- |
| Crypto            | CoinGecko (works now)   | —                        | —                     |
| US equities       | Yahoo (works now)       | —                        | —                     |
| US macro context  | —                       | FRED (works now)         | —                     |
| Indian equities   | Yahoo via `.NS` / `.BO` | —                        | Angel One / Breeze    |
| Indian F&O / OI   | analytics ready*        | `scan-chain` raw/fixture flow | Breeze / Angel One / Dhan |

\* The F&O analytics (PCR, max pain, OI walls) are built and tested;
`scan NIFTY` runs them on any normalized chain in the cache, `scan-chain`
can normalize a raw broker-export JSON first, and `fetch-chain` can pull a live
chain from a Breeze test account before analyzing it. `watch` gives you a
compact batch view across multiple assets, and `python -m web.server` exposes a
read-only dashboard over the same recorded signal log.

The LLM narrator is also optional. With no model key, a deterministic template
writes the thesis. A configured model only upgrades the phrasing.

## How it's organized

```text
src/alpha_engine/
  schema/        the Signal contract. The spine. Read this first.
  cache/         the read interface analyzers call instead of the network
  ingestion/     source adapters that normalize data into the cache
  analyzers/     deterministic, pure-function specialists (one per concern)
  synthesis/     folds analyzer outputs into one Signal
  narrative/     writes the thesis string (templated, optional LLM)
  validation/    immutable signal recording, outcome scoring, backtesting
  cli/           the commands you actually run: scan, backtest, record-stats
tests/           proof the deterministic core is deterministic
```

## Status and honesty notes

This is an early scaffold. It proves the architecture end to end with one market and
one simple analyzer. A few things are deliberately honest about their limits:

- **The trend analyzer is a scaffold, not alpha.** It's a transparent
  moving-average heuristic meant to exercise the pipeline. It is not a profitable
  strategy and is not claimed to be — and the backtester now proves it, showing a
  roughly coin-flip hit rate. That measured baseline is what improvement gets
  judged against.
- **Confidence is not yet calibrated.** The current heuristic can pin confidence at
  extreme values, and the backtest's calibration curve makes the miscalibration
  visible (high-confidence buckets do not hit more often). Fixing the number against
  recorded outcomes is the next analyzer-side job.
- **Free data sources rate-limit.** The cache layer exists precisely so you read
  local data instead of hammering APIs. If you see a `429`, wait and retry.

## Roadmap

1. ~~Validation harness~~ **done**: every `scan` is recorded to an append-only
   log, `backtest` replays history with no lookahead, and `record-stats` scores
   recorded signals against realized outcomes. This is the trust engine.
2. ~~US equities + macro context~~ **done**: `scan AAPL` works keyless via
   Yahoo, and a free FRED key adds a macro-posture tilt (tightening vs. easing)
   blended into the signal. Indian cash equities also route keylessly via
   Yahoo when you use `.NS` / `.BO` tickers.
3. ~~Indian markets (F&O depth)~~ **done**: the distinctive analytics layer.
   `OptionsChain` cache model, deterministic PCR / max-pain / OI-shift
   analyzers, and live broker adapters for Breeze, Angel One, and Dhan.
   `scan NIFTY` runs on any normalized chain in the cache, `scan-chain`
   normalizes raw broker-export JSONs, and `fetch-chain` pulls live chains.
4. ~~Optional LLM narrator~~ **done**: gated behind a user-supplied key, with
   mandatory re-validation that no numeric field changed. Falls back to a
   deterministic template when no key is set.
5. ~~CLI / dashboard polish~~ **done**: `watch` batches assets, `scan-chain`
   supports raw broker exports, `fetch-chain` supports Breeze/Angel One/Dhan,
   `scan-all` and `batch` run multi-asset scans, and the read-only dashboard
   renders the log.
6. ~~Multi-agent orchestration~~ **done**: the orchestrator decides which
   analyzers fire and when, with `scan-all` and `batch` CLI commands for
   scheduled batch scans.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The one rule that matters: analyzers and
synthesis stay deterministic and tested. If your change makes a number depend on an
LLM or on randomness, it belongs somewhere else.

## Disclaimer

This software is provided for research and educational purposes only. It does not
constitute financial, investment, or trading advice, and its authors are not
registered investment advisers or research analysts in any jurisdiction. Markets
involve risk of loss. Do your own research and consult a licensed professional
before making any financial decision. See [LICENSE](LICENSE) for warranty terms.
