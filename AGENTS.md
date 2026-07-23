# AGENTS.md

Alpha Engine: a deterministic market-research signal engine (Python 3.10+, src layout,
package `alpha_engine`). Read `context.md` before non-trivial changes — it holds the
non-negotiable design rules and the layer table. `FUTURE_WORK.md` holds the roadmap;
`HOW_IT_WORKS.md` explains the architecture in plain language then in depth.

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
./start.sh doctor  # diagnose a broken setup
```

CLI commands (also available as `alpha-engine <cmd>`):

| Command | Purpose |
|---|---|
| `scan <ASSET>` | one signal |
| `scan-all` / `batch --output r.json` | portfolio sweep, cron-friendly |
| `watch <ASSETS...>` | compact multi-asset table |
| `report <ASSET>` | full quant report |
| `factors <ASSET>` | rank the 504-factor registry by IC (`--family`, `--clusters`, `--all-factors`, `--top`) |
| `backtest <ASSET>` | no-lookahead replay (`--options` adds a model-priced ATM leg) |
| `record-stats` / `calibrate` | score recorded signals; re-derive reliability |
| `risk` | portfolio risk report |
| `ingest [ASSETS...]` | refresh news / on-chain / fundamentals caches |
| `orchestrate --news` | event-driven run: headlines trigger targeted re-scans |
| `trade <ASSET>` / `webhook` | paper-first execution, `LIVE_TRADING`-gated |
| `scan-chain` / `fetch-chain` | Indian F&O options chains |
| `health` | per-source status; `--strict` exits non-zero when degraded |
| `dashboard` | read-only web UI |

Plus `python mcp_server.py` (or `./start.sh mcp`) — the MCP server for AI assistants.

CI (`.github/workflows/ci.yml`) tests on Python 3.11–3.13; coverage reported, not gated.
39 test files, ~2200 tests, all network-free, ~20s.

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
- `web/` (dashboard) and `mcp_server.py` are read-only and live **outside** the installed
  package; the CLI reaches `web/` via PYTHONPATH (see `start.sh`). Don't move them into
  `src/` casually.
- `portfolio.json` at the repo root configures `scan-all` / `batch` / `orchestrate`.

### Two layers that only ever reduce confidence

`volatility_scalar()` and `macro_calendar.calendar_scalar()` return floats in `(0, 1]`.
They are defensive by construction — a "caution" mechanism that could *raise* confidence
would be a bug wearing a costume. Tests pin the upper bound; keep them.

**Do not implement dampening by scaling source weights.** That was the original design and
it silently did nothing: every term in `_calibrate_confidence` is a ratio (agreement,
reliability, `net`), so a constant factor cancels out of all of them. Dampening must be
passed to `synthesize(conviction_scalar=...)`, which applies it to the final confidence.
Weights are still scaled *as well*, but only so the audit trail shows discounted inputs —
they are the explanation, not the mechanism. `tests/test_core.py` pins both the
cancellation property and the fix; if you touch this, keep both tests.

### The read-only rule for Phase 11 context data

Price and macro refresh inline during a scan (a scan without prices is meaningless).
News, on-chain and fundamentals are **cache-only in the scan path** — `_load_news`,
`_load_onchain`, `_load_fundamentals` in `cli/main.py` never fetch. They are populated by
`ingest` or `orchestrate`'s freshness pass.

This is `cache/interface.py`'s own stated rule. Fetching four RSS feeds and three APIs per
scan would rate-limit free sources, slow a sub-second command to multiple seconds, and put
the network back into the test suite. If you make these fetch inline, `pytest` time jumps
from ~23s to ~70s — that is the symptom.

## Extending

- **New data source** → adapter in `ingestion/` outputting `cache/models.py` shapes; prefer
  keyless, gate keys behind config with graceful degradation (see `fred.py`, `glassnode.py`).
  Scraped sources must fail *loudly*: validate the response shape and print `CONTRACT BROKEN`
  with an empty return rather than a plausible wrong number (see `nse_disclosures.py`, `rbi.py`).
- **New analyzer** → pure function in `analyzers/` following `crypto_trend.py`, with tests
  pinning behavior on fixed inputs. Wire it into `_build_price_signal`. An analyzer with no
  consumer is dead weight.
- **New factor** → one `_add(...)` line in `quant/factors.py`. It then appears in `factors`
  output, gets IC-scored, and is covered by the registry-wide lookahead test automatically.
  Factors take `(Bars, t)` and may read only indices `[0..t]`.
- **New MCP tool** → add to `TOOLS` and `HANDLERS` in `mcp_server.py`. Four non-negotiables:
  disclaimer on every payload, cache-first (`no_refresh=True`), read-only by default, and
  never accept an input that becomes a decision-bearing number.
- **Style**: type hints, `from __future__ import annotations`, Pydantic for data shapes,
  ruff line length 100, docstrings that explain *why*.

## Gotchas

- Tests must stay network-free; free APIs (CoinGecko keyless) 429 easily — the cache
  exists to absorb that. Wait and retry on 429; never add retries that hammer.
- Never commit `.env` (only `.env.example` is tracked) or `data/cache/` contents.
- `.env` / `.env.local` are loaded by `src/alpha_engine/config.py` (stdlib loader, not
  python-dotenv); shell variables take priority.
- Analyzers are honest scaffolds (~coin-flip on BTC backtests). Never write docs or
  code comments implying proven alpha.
- **Factor rankings need the noise floor.** `noise_floor_ic()` reports what the best of N
  random factors scores by chance. On short history it is large (|IC| ~0.45 on 60 bars).
  Never present a top-ranked factor without it — that is how backtests lie.
- GARCH/HMM factors are `cost="slow"` and excluded from the default panel. Including them
  turns `factors` from ~4s into minutes. Measure before assuming that changed.
- The macro calendar has two sources that MERGE into one event cache: FOMC dates are
  scraped (`ingestion/fomc_calendar.py`), everything else is user-supplied
  (`ingestion/calendar_file.py`). The Fed page hides half its rows behind an extra CSS
  class and includes non-decision "notation vote" rows — the parser handles both, and
  the fixtures in `tests/test_macro_breadth.py` encode those two traps. Don't simplify
  them away.
- **Every new ingestion adapter must record health** (`alpha_engine.health.record`) with
  an item count. Adapters degrade to empty by design, so without a health record a dead
  source is indistinguishable from a quiet one and decays silently for months. Record per
  *feed*, not just per kind — an aggregate count hides individual feeds dying.
- **Diagnostics must never be load-bearing.** `save_health` never raises: it is called
  from inside `refresh_context`'s except handler, so a raise there would turn a handled
  source failure into an unhandled crash.
- **Collections prune on write** (`cache/interface.py::RETENTION`). Retention windows are
  set from the consuming analyzer's lookback; `tests/test_cache_retention.py` pins the
  pairing. Raising an analyzer's lookback past its window silently starves it.
- Atomic-write temp names key on PID **and** thread id. PID alone collides between threads
  and the losing rename raises; `web/server.py` is a ThreadingHTTPServer.
- The scheduled job is `scripts/daily.sh` (lock, stale-lock recovery, timeout, rotation,
  health gate). Do not add cron entries that call the CLI directly — an entry without
  `ingest` leaves every context source permanently empty.
- All writable state resolves through `config.data_dir()`, which honours `ALPHA_DATA_DIR`.
  Never hardcode `Path("data/...")` in a new module: the default is cwd-relative, so a
  hardcoded path makes that module write to a different place than the rest of the engine
  when run from anywhere but the project root.
- The same trap applies to the shell scripts, and `start.sh` fell into it. Every relative
  path in it (`pip install -e .`, `data/`, `ruff .`, `pytest`, `portfolio.json`) resolves
  against the *caller's* cwd, so it now `cd`s to `$SCRIPT_DIR` first — as `scripts/daily.sh`
  already did. Its own state checks go through `$DATA_DIR`, which mirrors `data_dir()`.
  `tests/test_launcher.py` pins both; keep them if you touch the launcher.
- `start.sh` runs under `set -euo pipefail`, so any CLI command that exits non-zero by
  design needs `|| true`. `health` exits non-zero when a source is degraded — unguarded,
  that truncated `doctor` precisely when a source had gone quiet.
- `mcp_server.py` must print **nothing** to stdout except JSON-RPC. Diagnostics go to
  stderr or the protocol stream is corrupted.
