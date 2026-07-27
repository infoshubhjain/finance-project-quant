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
python -m alpha_engine.cli.main scan BTC --no-record   # manual end-to-end check
#   --no-record matters: the signal log is a track record, and a developer
#   verifying a build should not append test scans to it.

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
| `dashboard` | web app: dashboard + AI terminal + HTTP/MCP API |
| `strategies` | list user strategies available to `strategy-backtest` |
| `strategy-backtest <ASSET> --strategy <Name>` | trade-level backtest: equity curve, trades, Sharpe |
| `terminal [question]` | AI research chat, bring your own LLM key |

Plus `python mcp_server.py` (or `./start.sh mcp`) — the MCP server for AI assistants.

### The three outside surfaces

`scan` etc. are also reachable without the CLI. All three dispatch into the SAME
table — `src/alpha_engine/toolkit.py` — so they cannot drift:

| Surface | Entry point | Use |
|---|---|---|
| MCP over stdio | `mcp_server.py` | Claude Desktop, Cursor, Windsurf |
| MCP over HTTP | `POST /api/v1/mcp` | remote MCP clients |
| REST | `GET/POST /api/v1/tools/<name>` | anything else; `GET /api/v1/tools` self-describes |

**A new tool goes in `toolkit.py` and appears on all three at once.** Adding one
to `mcp_server.py` is a bug — that file is transport only.

CI (`.github/workflows/ci.yml`) tests on Python 3.11–3.13; coverage reported, not gated.
49 test files, ~2540 tests, all network-free, ~40s. `tests/test_browser.py` is
opt-in (`pip install -e ".[browser]"`) and skipped otherwise, so CI stays fast.

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
- `web/` (server) and `mcp.py` are read-only transports. They now live **inside** the
  package at `src/alpha_engine/web/` and `src/alpha_engine/mcp.py`, so `pip install
  alpha-engine` ships the dashboard, terminal, HTTP API and MCP server. Root
  `mcp_server.py` is a three-line shim kept only because MCP client configs point at a
  file path. The frontend is declared as package data in `pyproject.toml`; forget that
  and the wheel ships a web server with no pages.
- `portfolio.json` at the repo root configures `scan-all` / `batch` / `orchestrate`.

Two directories sit *beside* the pipeline rather than in it:

- `quant/` — the 504-factor registry, IC ranking, Black-Scholes.
- `strategy/` — user-authored trading rules and the trade-level backtester. Ported
  from the standalone `nifty_backtester` Streamlit app, rewritten pure-Python over
  `Candle` so the repo keeps its single runtime dependency.

### `validation/` vs `strategy/` — two different questions

They look similar and answer opposite things. Putting a change in the wrong one is
the most likely mistake here.

| | `validation/backtest.py` | `strategy/engine.py` |
|---|---|---|
| Question | were the ENGINE's signals directionally right? | what would MY rule's account have done? |
| Signals from | the analyzer pipeline (fixed) | a user's `BaseStrategy` subclass |
| Output | hit rate, captured move, calibration curve | trades, equity curve, Sharpe, max drawdown |
| No-lookahead | structural — `signal_at()` truncates before any analysis | detected, not enforced — see below |

The asymmetry is unavoidable: `signal_at` owns signal generation so it can truncate,
but a user strategy is handed the whole series and could read `candles[i+1]`. So
`run_strategy_backtest(check_lookahead=True)` re-runs the strategy on truncated
history and reports bars whose signal changed. **A non-empty `lookahead_violations`
voids every metric in that report** — the CLI and the tools both lead with it for
that reason. Never present those numbers without it.

The position is also lagged one bar on purpose: a signal from bar `t`'s close fills
at `t+1`. `tests/test_strategy.py` pins both properties; keep them.

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
- **New tool (MCP + HTTP + AI terminal)** → add to `TOOLS` and `HANDLERS` in
  `src/alpha_engine/toolkit.py`, never in `mcp_server.py`. Five non-negotiables:
  disclaimer on every payload, cache-first (`no_refresh=True`), read-only by default,
  never accept an input that becomes a decision-bearing number, and **never accept
  code**. If the tool can change state on disk, name its write arguments in
  `WRITE_ARGS` so `read_only_tools()` can hide them from the AI terminal and the HTTP
  write gate can refuse them.
- **New strategy** → a `BaseStrategy` subclass in `strategies/` (or
  `$ALPHA_STRATEGY_DIR`). Implement `generate_signals(candles) -> list[int]` returning
  one 1/-1/0 per bar; optionally override `verify_on_option`. Use the aligned helpers
  in `strategy/indicators.py` rather than hand-rolling offsets — that is where
  lookahead comes from. It is discovered automatically; no registration.
- **New LLM provider** → one entry in `PROVIDERS` in `narrative/providers.py`. If it is
  OpenAI-compatible (most gateways are) that is the whole change; a genuinely new
  dialect needs a branch in `chat()`, `_parse()` and `tools_for()`.
- **Style**: type hints, `from __future__ import annotations`, Pydantic for data shapes,
  ruff line length 100, docstrings that explain *why*.

## Security boundaries

The HTTP API is the first surface a stranger can reach. Four rules, all enforced in
code and pinned by `tests/test_api.py`:

- **No route accepts strategy source.** Loading a strategy executes Python; over a
  network that is remote code execution. A caller may select and parameterise a
  strategy already on disk, nothing more. Doing it properly needs the sandbox in
  FUTURE_WORK Phase A2, which belongs in the platform repo.
- **Binding off-loopback without `ALPHA_API_KEY` is refused, not warned about.** A
  warning printed to a terminal nobody is watching is not a security control.
- **Writes are off by default** (`--allow-writes` opts in), and the gate applies to
  the MCP-over-HTTP transport too — it must not be bypassable by changing protocol.
- **BYO keys are never stored.** The terminal's API key arrives in the request body,
  goes to the provider, and is dropped. There is no key store; `alpha_engine/web/server.py`'s
  access log is silenced for this reason and must stay that way.

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
  and the losing rename raises; `alpha_engine/web/server.py` is a ThreadingHTTPServer.
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
- **Every source failing at once with `CERTIFICATE_VERIFY_FAILED` is an empty CA trust
  store, not a dead source.** `net.py` uses the stdlib default SSL context, so it reads
  the system store and honours `SSL_CERT_FILE`. One cause produces two messages —
  `self-signed certificate in certificate chain` when the server sends its (self-signed)
  root, `unable to get local issuer certificate` when it sends only the intermediate — so
  differing errors across sources are not evidence of differing problems. `doctor` checks
  the store and prints the fix. Never "solve" this by disabling verification.
- `mcp_server.py` must print **nothing** to stdout except JSON-RPC. Diagnostics go to
  stderr or the protocol stream is corrupted.
- **`ruff>=0.4` is not a pin — the rule set is.** CI installs the newest ruff, so when
  0.16 widened its defaults, 62 findings appeared across untouched files and CI went red
  for three runs with no commit responsible. `[tool.ruff.lint] select` in `pyproject.toml`
  now names the rules explicitly. Widening it is deliberate, separate work: turn on one
  family, fix what it finds, commit. Never widen it inside a feature branch.
- **`ruff format` also formats python inside markdown.** `HOW_IT_WORKS.md` and
  `docs/analyzer-guide.md` align their comment columns by hand because they are teaching
  material; `[tool.ruff.format] exclude = ["*.md"]` keeps that. Don't remove it and then
  "fix" the docs.
- **A `strategy_backtest` with `lookahead_violations` is not a weak result, it is a void
  one.** The CLI prints it to stderr before the metrics and the tool payload attaches a
  `warning` — because a reader who sees Sharpe 3.1 first has already been misled. Keep
  the ordering.
- **`Interval` drives annualisation** (`strategy/metrics.py::bars_per_year_for`). Sharpe
  on minute bars annualised with 252 is wrong by roughly 9x, and flatteringly so. If you
  add an interval, add its constant.
- **`ema_series` returns a trimmed list; `strategy/indicators.py` returns an aligned one.**
  Both are correct for their callers. Strategy code wants `fast[i]` to mean bar `i`, so it
  uses the aligned wrappers — mixing them up is an off-by-warmup lookahead bug that looks
  like alpha.
- **No single futures exchange is reachable from everywhere, and the blocks point in
  opposite directions.** Measured 2026-07-26: Binance answers 451 and Bybit 403 from a
  GitHub runner but 200 from a home connection; OKX and Kraken answer 200 from the runner
  and time out from a US residential IP. `orchestrator/engine.py::FUTURES_CHAIN` therefore
  walks adapters until one answers and latches the winner. Re-run
  `.github/workflows/probe-exchanges.yml` before reordering it — reachability is a property
  of the network, not of the code, and it changes without announcement.
- **Record health per FEED, never per kind.** This is restated because it already cost a
  month: `onchain` recorded one aggregate, so when Binance began refusing every request
  CoinGecko's single dominance reading kept it at "1 item, ok" and six dead fetches were
  invisible across a month of green builds. `run()` in the orchestrator takes a
  `{feed: count}` map for exactly this reason, and only records feeds that were *attempted*
  (an untried fallback would age into a false alarm).
- **Cache freshness must come from the FETCH time, not from the data's own dates.**
  `get_macro` compared the newest observation's date against a 1-day TTL. FRED's monthly
  series are always weeks old by construction, so every series was permanently stale and a
  four-equity batch made twelve FRED calls instead of three — forever, silently.
  `LocalStore.macro_fetched_at` reads the file mtime instead.
- **Two adapters for one metric will not share a unit.** Binance reports open interest in
  base coin, Gate in contracts (1 contract = 0.0001 BTC), and OKX in USD. The analyzer
  reads OI as `last / first`, so a series holding two units has a ~100,000x step that
  scores as a colossal build-up. `crypto_onchain._by_metric` reads only the freshest
  source per metric; that guard holds for adapters nobody has written yet, whereas a
  cross-adapter unit convention does not survive the third venue.
- **Validate numeric tool arguments at `toolkit.call_tool`, not per handler.** `step=-5`
  used to return HTTP 200 with a backtest reporting zero signals, because
  `range(warmup, n, -5)` is empty rather than an error — a confidently wrong number, which
  is worse here than a failure. `_BOUNDS` is checked once so all three transports inherit
  it.
- **The daily data commit carries `[skip ci]`, which suppresses every workflow on that
  commit.** That is intended on `main` (a data commit should not re-run the suite), but it
  means a branch whose HEAD is a data commit shows *no* CI checks at all — including on a
  pull request. Push a real commit to get a run.
- **Frontend files are package data, not code.** `src/alpha_engine/web/static/` only ends
  up in a wheel because `[tool.setuptools.package-data]` names it. A new asset directory
  needs a new entry there, and the failure mode is a server that starts fine and 404s
  every page — which no test catches unless it installs the wheel.
- **When a scheduled job "isn't running", read `/var/mail/$USER` first.** cron
  reports failures by mail and nowhere else. A local 9am entry here failed for five
  consecutive days with `Operation not permitted` — macOS will not let
  `/usr/sbin/cron` read `~/Desktop`, and `/usr/sbin/cron` is a different process
  from your terminal with its own Full Disk Access grant. Granting it to the
  terminal fixes writing the crontab and does nothing for the job. The tell was
  that `data/reports/cron.log` did not exist at all: an absent log is a louder
  signal than a stale one, and the same reasoning as the silent-decay rule above.
  The local entry is gone; `.github/workflows/daily-signals.yml` owns the daily
  scan. Do not re-add a second scheduler — two writers on one append-only signal
  log diverge.
