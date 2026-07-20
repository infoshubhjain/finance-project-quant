# Changelog

All notable changes to Alpha Engine are recorded here. Dates are UTC.

## [0.4.0] — 2026-07-20 — running unattended without rotting

Everything in this release exists because of one failure mode: **scrapers do not
crash, they go quiet.** A source stops returning data, every adapter degrades to
empty exactly as designed, nothing errors, and every signal afterwards is
quietly weaker. You find out months later.

Two of those were already happening in this repo and had never been noticed.

### Fixed — sources that were silently dead

- **SEC EDGAR returned 403 on every request.** The SEC's fair-access policy
  requires a User-Agent identifying the requester with contact information;
  verified that no generic UA works, including a browser string and the project
  name. Now gated on `SEC_USER_AGENT` — set it and the feed works, leave it and
  the feed reports itself as deliberately off rather than 403ing in silence.
- **NSE announcements timed out on every request.** It needs a browser-shaped
  User-Agent; without one it hangs rather than refusing, which reads like a
  network problem instead of a policy one. With the fix it returns ~400 items —
  it was the largest feed all along, and had been contributing nothing.

### Added — making silence loud

- **`alpha_engine/health.py` and `alpha-engine health`.** Per-source tracking of
  last success, error streaks, and quiet periods, with per-source tolerances
  (news should arrive daily, fundamentals quarterly, a calendar almost never).
  Distinguishes three states that look identical from the outside: *producing*,
  *empty*, and *erroring* — plus *deliberately switched off*, so a missing key
  never masquerades as a breakage. Exits non-zero under `--strict` for cron.
- Health is recorded per feed, not just per kind. "news: 30 items" hid two of
  four feeds being dead; `news.sec_edgar` / `news.nse_announcements` do not.
- `RefreshReport` now carries `item_counts`, because "refreshed: news" hides a
  broken feed and "news: 0 items" reveals it.

### Added — the scheduled job

- **`scripts/daily.sh`**: ingest → batch scan → health check, with an atomic
  `mkdir` lock (no overlapping runs), stale-lock recovery keyed on whether the
  holding PID is alive (one crash must not disable the job forever), a portable
  wall-clock timeout (macOS ships no `timeout(1)`), and log rotation at 5 MB.
  Uses absolute paths throughout, because cron runs with a near-empty
  environment.
- **`scripts/install-cron.sh`**: one-command setup, `--at HH:MM`, `--remove`,
  `--show`. Replaces older entries matching the project directory, so upgrading
  cannot leave two jobs running — the previous entry called `batch` without
  `ingest`, leaving every context source permanently empty.
- `./start.sh doctor` now reports cache size, per-source health, cron
  installation state, last run and last result.

### Added — retention

- **Collections are pruned on write.** Measured before: a conservative 40
  headlines/day grew the news cache to 14,600 items and 2.9 MB after one year,
  of which ~5% were recent enough for any analyzer to read — every scan parsed
  all of it and every write re-serialized all of it. Windows are set from the
  consuming analyzer's lookback (news 30d vs. sentiment's 21d cutoff), and
  `tests/test_cache_retention.py` pins that relationship so the two cannot drift
  apart. Future calendar events are never pruned.

### Fixed — found by the deep-debug pass

- **Concurrent writes crashed.** The atomic-write temp filename used only the
  PID, so two threads in one process built the same path and the losing rename
  raised `FileNotFoundError`. `web/server.py` runs a `ThreadingHTTPServer`, so
  this was reachable. Temp names are now unique per process *and* thread.
- **`health.record()` lost updates** under concurrency (read-modify-write), so
  4 of 5 sources vanished. Now serialized by a lock. Cross-process races remain
  and are documented; the cron lock covers the scheduled path.
- **`save_health()` could raise**, which was the monitoring taking down the run
  it monitors — it is called from inside `refresh_context`'s except handler. It
  now never raises and reports whether the write landed.
- **A tz-naive timestamp in `health.json` crashed `health` and `doctor`** with a
  `TypeError`. That file is plain JSON people hand-edit. Naive values are now
  coerced to UTC and unparseable ones ignored.
- Clock skew (an NTP correction leaving `last_ok` in the future) no longer
  reports a healthy source as degraded.

- **The test suite was corrupting live operational state.** `health.record()`
  defaults to the real `data/health.json`, and several tests deliberately make a
  fetch raise to prove failures are isolated — so every test run wrote genuine-
  looking errors against the real health file. After running `pytest`,
  `alpha-engine health` reported `news.fed_press: 3 consecutive errors` for a
  failure that never happened, and the daily job exited non-zero over it. A
  monitor that cries wolf is worse than no monitor, so `tests/conftest.py` now
  redirects health writes to a temp file for the whole session.

- **The engine wrote its state relative to the working directory.** Eight
  modules hardcoded `data/...`, so running `alpha-engine` from anywhere but the
  project root created a stray `data/` folder there — splitting the signal log
  so `record-stats` reported on whichever fragment it found — and from `/` it
  crashed outright with `Read-only file system`. All writable state now resolves
  through `config.data_dir()`, overridable with `ALPHA_DATA_DIR`. The default is
  unchanged, so the repo flow behaves exactly as before, and `scripts/daily.sh`
  sets the override so the scheduled job cannot depend on cwd.

### Changed

- Version 0.4.0; package metadata filled in (classifiers, URLs, author) and the
  wheel verified to install and run in a clean venv.
- **New: [RUNNING_IT.md](RUNNING_IT.md)** — operating the engine long-term, in
  the same plain language as GETTING_STARTED.md.

## [0.3.0] — 2026-07-20 — the engine is feature-complete

This release closes every remaining engine phase in `FUTURE_WORK.md` except the
two that are deliberately deferred (the ML layer, which is gated on a year of
recorded outcomes, and QuantHQ, which is a separate codebase). The cardinal rule
is unchanged: every number below comes from deterministic tested Python.

### Added — Phase 10: the factor registry

- **`quant/factors.py`: 504 factors** across ten families, generated by
  systematic parameterization rather than written by hand. Adding a factor is
  one `_add(...)` line; it then appears in `factors` output, gets IC-scored, and
  is covered by the lookahead test with no other change anywhere.
- **Registry-wide lookahead pin.** `tests/test_factors.py` parameterizes over
  every entry and asserts each factor's value at bar `t` is identical whether
  computed on the full series or a series truncated at `t`.
- **Two families the library was missing entirely**: `oscillator` (RSI,
  stochastics, CCI, Williams %R, money-flow index — RSI existed in the signal
  path but was never *rankable*) and `risk_adjusted` (Sharpe, Sortino, Calmar,
  Ulcer index, current drawdown).
- **`noise_floor_ic()`** — the multiple-testing correction the ranking layer
  needed. Reports the |IC| the best of N *random* factors reaches by chance, and
  `factors` states plainly when the top result fails to clear it. Without this,
  ranking 500 factors on 60 observations reports |IC| ≈ 0.9 as if it meant
  something.
- **Cost tiers.** GARCH/HMM factors fit a model per bar and are ~100× everything
  else; they are tagged `slow` and excluded from the default panel
  (`--all-factors` opts in), keeping `factors BTC` at ~4s rather than minutes.
- `factors` gained `--top`, `--family`, `--clusters`, `--all-factors`.

### Added — Phase 11: data breadth

- **News (11a).** `NewsItem` model; keyless `ingestion/rss.py` (SEC EDGAR, Fed,
  RBI, NSE announcements) parsing RSS 2.0 and Atom with stdlib only;
  key-gated `ingestion/finnhub_news.py`; and `analyzers/sentiment.py` — a
  deterministic finance lexicon with negation handling and exponential
  freshness decay. **Not an LLM**, because sentiment produces a weight.
- **Crypto on-chain (11b).** `OnChainObservation` model; keyless
  `ingestion/binance_futures.py` (funding rate + open interest); BTC dominance
  via a second CoinGecko endpoint; key-gated `ingestion/glassnode.py`; and
  `analyzers/crypto_onchain.py`, whose funding vote is deliberately
  **contrarian** — crowded longs paying to stay long is a bearish read.
- **Fundamentals (11c).** `Fundamentals` model; `ingestion/fmp.py`;
  `ingestion/nse_disclosures.py` (NSE announcements + FII/DII flows); and
  `analyzers/fundamentals.py` reading accruals, leverage and growth. DCF and
  comparables remain deliberately unbuilt.
- **Macro breadth (11d).** `ingestion/rbi.py` (policy rates, bounds-checked),
  `ingestion/worldbank.py` (keyless global indicators), `EventItem` model, and
  `analyzers/macro_calendar.py` — purely defensive, returning a scalar in
  `(0, 1]` that dampens confidence before known events and can never raise it.
  `analyze_macro()` is now **region-aware**: an Indian equity tilts on RBI
  policy and FII flows first, while still seeing the Fed.
- **FOMC calendar scraper** (`ingestion/fomc_calendar.py`), keyless. The macro
  calendar was originally file-only; the Fed's schedule page is structured and
  parses reliably years ahead, so it is now fetched automatically and merged
  with any user-supplied `calendar.json`. Two traps the live page taught us, both
  fixture-tested: projection meetings carry an extra CSS class (splitting on the
  row div silently drops half of every year), and "notation vote" rows are policy
  statements, not rate decisions. The RBI's MPC page is deliberately *not*
  scraped — its served HTML contains no dates at all.
- **Forex depth (11e).** `analyzers/forex_carry.py`: rate differentials from
  cached policy rates, dollar-cycle transmission, and tighter thresholds for the
  RBI-managed rupee band.

### Added — Phase 12: a real orchestrator

- `orchestrator/engine.py` with **triggers** (scheduled / new-data / news-event /
  user-query), a **deterministic priority queue** (ties break by insertion
  counter, so runs are reproducible), **shared per-run context** (one fetch per
  series per run), and **freshness-driven ingestion** that asks the cache what
  has gone stale rather than refetching a fixed list.
- A news headline now triggers a targeted re-scan of **only the affected asset**
  instead of a full portfolio sweep.
- New commands: `ingest` and `orchestrate --news`.

### Added — Phase 14: MCP server

- `mcp_server.py` exposes `scan`, `report`, `backtest`, `factors` and
  `record_stats` over MCP to AI assistants. Stdlib only — no SDK dependency, in
  the same spirit as replacing `requests` with `urllib`.
- The four non-negotiables are each pinned by tests: the disclaimer travels with
  every payload (including error payloads), every tool is cache-first, nothing
  writes to the signal log unless explicitly asked, and no tool accepts an input
  that becomes a decision-bearing number.

### Added — beginner launch path

- `start.sh` rewritten as a real first-run experience: checks the Python version
  with actionable install instructions per OS, seeds a few signals so the
  dashboard is not empty on first launch, opens the browser automatically, and
  adds `menu` (numbered choices, no flags to remember) and `doctor` (setup
  diagnosis).
- **`HOW_IT_WORKS.md`** — the engine explained twice: once assuming no finance
  or programming knowledge, once as a technical reference.
- `README.md` rewritten around a three-line quickstart, with Windows and
  troubleshooting sections.

### Changed

- **Phase 11 context data is read-only in the scan path.** `_load_news`,
  `_load_onchain` and `_load_fundamentals` never fetch; `ingest`/`orchestrate`
  populate them. This restores `cache/interface.py`'s stated rule ("analyzers
  read from HERE, never from the network") and keeps the test suite offline.
- `cache/interface.py` gained a generic collection store (merge-by-key,
  traversal-safe bucket names) shared by all four new data kinds.
- `synthesis` sources now include news, on-chain, fundamentals and carry where
  data is available; the calendar scalar applies to every market.

### Removed

- `quant/features.py::compute_factor_panel()` — superseded by the registry's
  `compute_panel()`, which covers everything it did. Removed rather than left as
  dead code.

### Fixed

- **The volatility regime dampener never actually dampened anything** — a
  pre-existing bug, shipped since the volatility analyzer landed, found while
  verifying the new macro calendar end to end.

  Both layers reduced conviction by multiplying every source weight by a
  constant (0.6 in an extreme tape). But **every term in the confidence formula
  is a ratio** — agreement is `agreeing/total`, reliability is a weighted mean,
  `net` is `score/total` — so a constant factor cancels out of all of them. A
  scan in a violent tape produced a dampened-looking audit trail and a
  bit-identical confidence number. The same was true of the new calendar layer.

  `synthesize()` now takes a `conviction_scalar` applied to the final
  confidence, where it cannot cancel. Source weights are still scaled so the
  audit trail shows discounted inputs, but the weights are now the
  *explanation* and the scalar is the *effect*. Bounded to `(0, 1]`: these
  layers may only ever reduce conviction, and a scalar above 1.0 is clamped.

  Measured effect: an AAPL scan with a high-importance event one day out went
  from confidence 0.411 (unchanged, i.e. broken) to 0.247 (0.411 × 0.60).

  Existing tests only pinned `volatility_scalar()`'s return value, never its
  effect on a signal — which is exactly how this survived. There are now
  regression tests for both the cancellation property and the fix.

  The backtester applies the volatility scalar the same way, so backtests and
  live scans still measure the same engine. It deliberately does **not** apply
  the calendar scalar: replaying history with today's calendar would be
  lookahead, and point-in-time event data is not something the cache holds.

- **Sentiment freshness had no effect on a single headline.** Decay cancelled out
  of the weighted mean, so a three-week-old story counted exactly as hard as this
  morning's. Freshness now scales the source weight directly.
- **On-chain conviction was invisible when it mattered most.** The open-interest
  boost was clipped by the weight cap precisely when the positioning read was
  strongest. Weight now scales with corroboration breadth first.
- `mom_volnorm_1` was registered but could never compute (one return has no
  deviation); it is no longer registered rather than being a permanent `None`.

## [0.2.0b1] — 2026-07-18 — first beta

The beta turns the research engine into a small personal prop platform: it can
now backtest a stock **and its options together**, and take **paper-first**
trades (live execution behind an explicit gate). It stays research/education
software — the disclaimer and the deterministic cardinal rule are unchanged.

### Added
- **Joint options backtesting.** `backtest <ASSET> --options` replays the same
  no-lookahead signals as the price backtest, but simulates buying the matching
  at-the-money option (call when bullish, put when bearish) and reports the
  option P&L beside the underlying's. Option prices are Black-Scholes
  model-priced (`quant/black_scholes.py`) — pure-Python, deterministic, no new
  dependency and no paid data. Labelled model-priced, not tick-accurate.
- **Execution layer** (`execution/`), paper-first and owner-only:
  - `trade <ASSET>` places one order from a fresh signal. `--option` trades the
    ATM option instead of the underlying.
  - `webhook` runs an inbound trade receiver (e.g. for TradingView alerts). It
    refuses to start without `WEBHOOK_SECRET` and authenticates every request.
  - Live orders go out **only** when `LIVE_TRADING=1`; otherwise every order is
    simulated ("paper") and logged.
  - Hard size caps (`MAX_ORDER_QTY`, `MAX_ORDER_NOTIONAL`) reject oversized
    orders before any broker is contacted.
  - Append-only trade log at `data/trades/trades.jsonl`, same immutable pattern
    as the signal log.
  - Dhan live order adapter (`execution/dhan.py`); Angel One next behind the
    same interface. **The live path has not been round-tripped against a real
    account — place one tiny order and confirm the fill before trusting it.**
- **`GETTING_STARTED.md`** — a no-experience-needed setup guide, including how to
  obtain every API key.

### Fixed
- `PositionSize.daily_vol` description wrongly said "annualized"; it is the raw
  daily volatility (the annualized value is the separate field).

### Notes
- New env vars documented in `.env.example`: `LIVE_TRADING`, `MAX_ORDER_QTY`,
  `MAX_ORDER_NOTIONAL`, `WEBHOOK_SECRET`.
- Analyzers remain honest scaffolds (~coin-flip on BTC backtests). Nothing here
  claims proven edge; the options leg makes leverage and time-decay visible, it
  does not manufacture alpha.

## [0.1.0] — earlier

Deterministic multi-market research engine: ingest → cache → analyze →
synthesize → narrate → record → backtest, across crypto, US/Indian equities,
Indian F&O, forex, and macro. See README.md for the full capability matrix.
