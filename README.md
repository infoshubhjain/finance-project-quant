# Alpha Engine

An open, deterministic research engine that turns market data into structured,
confidence-scored signals. It is built to be read, cloned, and run by anyone, with
**zero setup required** for the default path.

> **This is a research and education project, not investment advice.** It produces
> directional *research views*, not buy/sell recommendations. Nothing here is a
> solicitation to trade. See [Disclaimer](#disclaimer).

## What it does

It runs a clean pipeline, one stage at a time:

```
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

# Generate a signal. No API key needed.
python -m alpha_engine.cli.main scan BTC
```

You should see a JSON `Signal` printed, with a direction, a calibrated confidence,
the contributing inputs, an invalidation level, and a plain-language thesis.

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
| US equities       | planned                 | Finnhub / FMP (free)     | —                     |
| US macro context  | planned                 | FRED (free key)          | —                     |
| Indian equities   | planned                 | —                        | Angel One / Breeze    |
| Indian F&O / OI   | planned                 | —                        | Breeze / Dhan         |

The LLM narrator is also optional. With no model key, a deterministic template
writes the thesis. A configured model only upgrades the phrasing.

## How it's organized

```
src/alpha_engine/
  schema/        the Signal contract. The spine. Read this first.
  cache/         the read interface analyzers call instead of the network
  ingestion/     source adapters that normalize data into the cache
  analyzers/     deterministic, pure-function specialists (one per concern)
  synthesis/     folds analyzer outputs into one Signal
  narrative/     writes the thesis string (templated, optional LLM)
  validation/    (next) immutable signal recording + backtesting
  cli/           the command you actually run
tests/           proof the deterministic core is deterministic
```

## Status and honesty notes

This is an early scaffold. It proves the architecture end to end with one market and
one simple analyzer. A few things are deliberately honest about their limits:

- **The trend analyzer is a scaffold, not alpha.** It's a transparent
  moving-average heuristic meant to exercise the pipeline. It is not a profitable
  strategy and is not claimed to be.
- **Confidence is not yet calibrated.** The current heuristic can pin confidence at
  extreme values. Calibrating it honestly is the job of the validation layer, which
  measures signals against realized outcomes before anyone trusts the number.
- **Free data sources rate-limit.** The cache layer exists precisely so you read
  local data instead of hammering APIs. If you see a `429`, wait and retry.

## Roadmap

1. Validation harness: immutable, timestamped recording of every signal plus
   inputs, joined later against outcomes. This is the trust engine.
2. More markets: US equities and macro context next (free keys), then Indian
   equities and F&O depth (broker accounts).
3. Synthesis across multiple analyzers per asset.
4. Optional LLM narrator, gated behind a user-supplied key.
5. Multi-agent orchestration, once one market is validated end to end.

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
