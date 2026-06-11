# CONTEXT.md

This file orients anyone (human or AI assistant like Claude Code) working on this
project. Read it fully before making changes. It explains what the project is, the
non-negotiable design rules, how the pieces fit, and how to work on it safely.

---

## 1. What this project is

An open, deterministic **research engine** that turns market data into structured,
confidence-scored signals across multiple markets (crypto first, then US equities
and macro, then Indian equities and F&O). It is three things at once, by design:

1. A **personal research/trading engine** the author controls end to end.
2. A **portfolio piece** demonstrating clean multi-agent/data-pipeline architecture.
3. A **free, clonable community tool** anyone can run and extend.

It is **not** a product that sells signals, not a managed-money service, and not an
advisory. It produces directional *research views*, not buy/sell recommendations.

### What it explicitly is NOT, and why

- **Not investment advice.** This framing is legal and deliberate. In India,
  charging retail users for buy/sell recommendations triggers SEBI Research Analyst
  registration. The project stays free and non-advisory to stay clear of that.
  Every signal carries a research-only disclaimer. Do not remove it.
- **Not a profit claim.** The current analyzers are transparent scaffolds, not
  proven alpha. Whether any signal has edge is a question the validation layer
  answers with data, never something the marketing or docs assert.

---

## 2. The cardinal design rule (read this twice)

**Decision-bearing numbers are computed by deterministic, tested Python. A language
model may write ONLY the `thesis` prose string, and may never set or change a
number.**

Concretely:

- `direction`, `confidence`, `invalidation_level`, and every `SignalSource.weight`
  are produced by pure functions. Same input, same output, always.
- No analyzer makes a network call. Analyzers read from the `Cache`. Fetching lives
  in `ingestion/`.
- No randomness in `analyzers/` or `synthesis/`.
- The LLM lives only in `narrative/`, is optional, and is gated behind a
  user-supplied key. With no key, a deterministic template writes the thesis.

If a proposed change makes a number depend on an LLM or on randomness, it is wrong
and belongs in a different layer or not at all. This rule is what makes the system
backtestable and trustworthy. It is the heart of the project.

---

## 3. Architecture and data flow

```
  ingest          cache             analyze            synthesize        narrate
 (sources)  ->  (local store)  ->  (deterministic) ->  (weighted vote) -> (prose)
                                                                              |
                                                                              v
                                                                          Signal
                                                                              |
                                                                              v
                                                                       validation
                                                                  (record + backtest)
```

Each stage is one directory under `src/alpha_engine/`:

| Layer        | Directory      | Responsibility                                              | May call network? | May use LLM? |
| ------------ | -------------- | ----------------------------------------------------------- | ----------------- | ------------ |
| Schema       | `schema/`      | The `Signal` contract. The spine everything depends on.     | no                | no           |
| Cache        | `cache/`       | Read interface + local store. Analyzers read from here.     | no                | no           |
| Ingestion    | `ingestion/`   | Source adapters that normalize external data into cache.    | YES               | no           |
| Analyzers    | `analyzers/`   | Deterministic, pure-function specialists.                   | no                | no           |
| Synthesis    | `synthesis/`   | Folds analyzer `SignalSource`s into one `Signal`.           | no                | no           |
| Narrative    | `narrative/`   | Writes the `thesis` string. Templated; optional LLM.        | only LLM call     | YES (only)   |
| Validation   | `validation/`  | Immutable signal recording + backtesting (next to build).   | no                | no           |
| CLI          | `cli/`         | The `scan` command wiring it all together.                  | via ingestion     | via narrative|

### The Signal schema is the contract

Everything compiles against `schema/signal.py`. Fields: `asset`, `market`,
`direction`, `confidence` (0-1), `timeframe`, `signal_sources` (list), 
`invalidation_level`, `thesis`, `timestamp` (UTC), `schema_version`. Changing a
field changes every downstream layer, so bump `SCHEMA_VERSION` deliberately and
update consumers. The `invalidation_level` field ("the price at which this view is
wrong") is the most important honesty mechanism in the schema; keep it meaningful.

---

## 4. Repository layout

```
src/alpha_engine/
  schema/signal.py          Signal, SignalSource, enums. Read first.
  cache/models.py           Normalized data shapes (Candle, PriceSeries, MacroObservation).
  cache/interface.py        Cache (public read API) + LocalStore + TTL/staleness.
  ingestion/coingecko.py    Keyless crypto source. The zero-setup default path.
  analyzers/crypto_trend.py First deterministic analyzer (dual-MA trend + momentum).
  synthesis/synthesize.py   Weighted-vote synthesis into a Signal.
  narrative/narrator.py     Templated thesis; optional-LLM hook.
  validation/               (empty; next major build)
  cli/main.py               `scan <ASSET>` entry point.
tests/test_core.py          Determinism + schema validation tests.
pyproject.toml              Packaging, deps, pytest/ruff config.
README.md                   User-facing overview + capability matrix.
CONTRIBUTING.md             Contributor rules (mirrors the cardinal rule).
PLAN.md                     The full build roadmap.
.env.example                Optional keys (all free tiers). Default path needs none.
```

---

## 5. Current status (as of initial scaffold)

**Working:** end-to-end pipeline on the keyless crypto path. `scan BTC` fetches from
CoinGecko, caches locally, runs the trend analyzer, synthesizes a signal, writes a
templated thesis, prints JSON. 10 unit tests pass.

**Known honest limitations (documented, not hidden):**

- The trend analyzer is a scaffold heuristic, not proven alpha.
- Confidence is not yet calibrated and can pin at extreme values. Calibration is the
  validation layer's job.
- Free data sources (CoinGecko keyless) rate-limit with HTTP 429. The cache exists
  precisely to minimize hits; wait and retry on 429. Tests are network-free.

---

## 6. How to work on this project

### Environment (macOS, Homebrew Python)

The system Python is externally managed, so always use the project venv:

```bash
python3 -m venv .venv
source .venv/bin/activate        # re-run this in every new terminal
pip install -e ".[dev]"
```

### The loop

```bash
pytest -q                                  # must pass
ruff check .                               # must be clean
python -m alpha_engine.cli.main scan BTC   # manual end-to-end check
```

### Before committing

- Tests pass and lint is clean.
- No secret/key committed. `.env` is gitignored; only `.env.example` is tracked.
- `data/cache/` is gitignored (regenerated on run); never commit cached market data.
- If you touched the schema, bump `SCHEMA_VERSION` and update all consumers.

---

## 7. Instructions specifically for Claude Code / AI assistants

When asked to extend this project:

1. **Respect the cardinal rule (Section 2) above all else.** If a request would put
   an LLM or randomness in the decision path, flag it and propose the correct layer
   instead, rather than silently complying.
2. **New data source?** Write an adapter in `ingestion/` that outputs the normalized
   models in `cache/models.py`. Prefer keyless/free sources. Gate anything needing
   credentials behind config so the default clone still runs with zero setup.
3. **New analyzer?** Pure function from a cache model to a `SignalSource`, placed in
   `analyzers/`, with unit tests pinning behavior on fixed inputs. Follow the
   `crypto_trend.py` + `tests/test_core.py` pattern.
4. **Always add tests** for deterministic logic. A change to `analyzers/` or
   `synthesis/` without a corresponding test is incomplete.
5. **Never weaken the disclaimer or imply profit.** Describe heuristics plainly.
6. **Keep the default path keyless.** Do not introduce a hard dependency on a paid
   API or a required key into the crypto default flow.
7. **Match existing style:** type hints, `from __future__ import annotations`,
   docstrings that explain *why* not just *what*, Pydantic for all data shapes.
8. When in doubt about scope or ordering, consult `PLAN.md` and prefer the smallest
   change that is testable on its own.

---

## 8. The one-paragraph summary (if you read nothing else)

This is a free, open, deterministic engine that turns market data into
confidence-scored research signals. Numbers come from tested pure-Python; an LLM may
only write the prose rationale and never a number. Data flows ingest -> cache ->
analyze -> synthesize -> narrate -> (soon) validate. The crypto path runs with zero
setup. It is research/education only, never advice. Keep it deterministic, keep it
honest, keep the default path keyless, and prove value with the validation layer
rather than asserting it.