# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## How to communicate with Shubh (project owner)

- Start every response with the exact phrase: "okay shubh, ill do that"
- Shubh is learning to code. While working, explain what is going on in simple,
  plain terms — what each file is for, what a concept means, and why a decision
  was made — like teaching a beginner, not briefing an expert. Short
  "what just happened and why" notes beat jargon. Define technical terms the
  first time they appear.

## Required reading

Read [context.md](context.md) fully before making changes — it holds the
non-negotiable rules, the layer table, current status, and the AI-assistant
checklist. [plan.md](plan.md) holds the phased roadmap; build phases in order.

## The cardinal rule

Decision-bearing numbers (`direction`, `confidence`, `invalidation_level`,
source weights) come from deterministic, tested pure Python. The LLM lives
only in `narrative/`, is optional and key-gated, and may only write the
`thesis` prose — never a number. No network calls or randomness in
`analyzers/` or `synthesis/`. The default path must stay keyless. Never
weaken the research-only disclaimer.

## Commands

```bash
# Setup (system Python is externally managed — always use the venv)
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# The loop — run before considering any change done
pytest -q                                  # all tests must pass (network-free)
ruff check .                               # must be clean
python -m alpha_engine.cli.main scan BTC   # manual end-to-end check

# Single test file / single test
pytest tests/test_core.py -q
pytest tests/test_core.py::test_name -q

# Other CLI entry points (also available as `alpha-engine <cmd>`)
python -m alpha_engine.cli.main scan-all           # every asset in portfolio.json
python -m alpha_engine.cli.main backtest BTC       # no-lookahead replay
python -m alpha_engine.cli.main report BTC         # quant metrics report (src/alpha_engine/quant/)
python -m alpha_engine.cli.main record-stats       # score the live signal log
python -m alpha_engine.cli.main batch --output r.json   # cron-friendly scan
python -m alpha_engine.cli.main dashboard          # read-only web UI (web/server.py)
python -m alpha_engine.cli.main watch BTC AAPL     # repeated scans

./start.sh <cmd>    # zero-setup wrapper: creates venv, installs, runs the command
```

## Architecture

One-way pipeline; each stage is a directory under `src/alpha_engine/`:

```text
ingestion/ -> cache/ -> analyzers/ -> synthesis/ -> narrative/ -> Signal -> validation/
(network)    (local)   (pure fns)    (weighted     (thesis       (immutable JSONL log,
                                      vote)         prose only)   outcomes, backtest)
```

- `schema/signal.py` is the contract everything compiles against. Changing a
  field means bumping `SCHEMA_VERSION` and updating every consumer.
- Only `ingestion/` touches the network. Analyzers read normalized models
  (`cache/models.py`) from the `Cache` and return a `SignalSource`; synthesis
  folds sources into one `Signal`.
- `validation/recorder.py` is append-only — signals are recorded before
  outcomes are known, so the log is honest. `validation/backtest.py` replays
  history with `signal_at` as the no-lookahead truncation choke point.
- `web/` (dashboard) is read-only and lives outside the installed package;
  the CLI reaches it via PYTHONPATH (see start.sh).
- `portfolio.json` configures which assets `scan-all`/`batch` cover.

## Extending it

- New data source → adapter in `ingestion/` outputting `cache/models.py`
  shapes; prefer keyless, gate keys behind config with graceful degradation
  (see `fred.py`).
- New analyzer → pure function in `analyzers/` following `crypto_trend.py`,
  with tests pinning behavior on fixed inputs. Analyzer or synthesis changes
  without tests are incomplete.
- Style: type hints, `from __future__ import annotations`, Pydantic for data
  shapes, docstrings that explain *why*.

## Before committing

- Tests pass, lint clean. No keys committed (`.env` is gitignored; only
  `.env.example` is tracked). Never commit `data/cache/` or cached market data.
