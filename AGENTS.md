# AGENTS.md

Alpha Engine: a deterministic market-research signal engine (Python 3.10+, src layout,
package `alpha_engine`). Read `context.md` before non-trivial changes — it holds the
non-negotiable design rules and the layer table. `FUTURE_WORK.md` holds the roadmap.

## Communicating with the owner

Shubh is learning to code. Explain changes in plain, beginner-friendly terms — what each
file is for and why a decision was made. Define technical terms on first use.

## The cardinal rule (never violate)

Decision-bearing numbers (`direction`, `confidence`, `invalidation_level`, source weights)
come only from deterministic, tested pure Python. The LLM lives only in `narrative/`,
is optional and key-gated, and may write only the `thesis` prose — never a number.
No network calls or randomness in `analyzers/` or `synthesis/`. The default path must stay
keyless. Never weaken the research-only disclaimer. If a request would break this, flag it
and propose the correct layer instead of complying.

## Commands

```bash
# Setup — system Python is externally managed (Homebrew); always use the venv
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Before any change is "done" (all three):
pytest -q                                  # all tests pass; suite is network-free
ruff check . && ruff format --check .      # CI gates BOTH lint and format
python -m alpha_engine.cli.main scan BTC   # manual end-to-end check

# Single test
pytest tests/test_core.py::test_name -q

./start.sh <cmd>   # zero-setup wrapper: creates venv, installs, runs any CLI command
```

Other CLI entry points (also as `alpha-engine <cmd>`): `scan-all`, `backtest <ASSET>`,
`report <ASSET>`, `record-stats`, `batch --output r.json`, `dashboard`, `watch`,
`scan-chain`, `fetch-chain`. CI (`.github/workflows/ci.yml`) tests on Python 3.11–3.13;
coverage is reported but not gated.

## Architecture

One-way pipeline; each stage is a directory under `src/alpha_engine/`, and each stage may
only look left:

```text
ingestion/ -> cache/ -> analyzers/ -> synthesis/ -> narrative/ -> Signal -> validation/
(network)    (local)   (pure fns)    (weighted vote) (prose only)          (append-only log, backtest)
```

- `schema/signal.py` is the contract everything compiles against. Changing a field means
  bumping `SCHEMA_VERSION` and updating every consumer.
- Only `ingestion/` touches the network. Analyzers read normalized models
  (`cache/models.py`) from the `Cache` and return a `SignalSource`.
- `validation/recorder.py` is append-only (`data/signals/signals.jsonl`) — no code path
  may rewrite old lines. `validation/backtest.py` uses `signal_at` as the sole
  no-lookahead truncation choke point; a test pins byte-identical output.
- `web/` (dashboard) is read-only and lives **outside** the installed package; the CLI
  reaches it via PYTHONPATH (see `start.sh`). Don't move it into `src/` casually.
- `portfolio.json` at the repo root configures `scan-all` / `batch` assets.

## Extending

- New data source → adapter in `ingestion/` outputting `cache/models.py` shapes; prefer
  keyless, gate keys behind config with graceful degradation (see `fred.py`).
- New analyzer → pure function in `analyzers/` following `crypto_trend.py`, with tests
  pinning behavior on fixed inputs. Analyzer/synthesis changes without tests are incomplete.
- Style: type hints, `from __future__ import annotations`, Pydantic for data shapes,
  ruff line length 100, docstrings that explain *why*.

## Gotchas

- Tests must stay network-free; free APIs (CoinGecko keyless) 429 easily — the cache
  exists to absorb that. Wait and retry on 429; never add retries that hammer.
- Never commit `.env` (only `.env.example` is tracked) or `data/cache/` contents.
- `.env` / `.env.local` are loaded by `src/alpha_engine/config.py` (stdlib loader, not
  python-dotenv); shell variables take priority.
- Analyzers are honest scaffolds (~coin-flip on BTC backtests). Never write docs or
  code comments implying proven alpha.
