# PLAN.md — Build Roadmap

The detailed plan for building this from the current scaffold into a full
multi-market research engine with a usable interface. Phases are ordered so each
one produces something testable on its own and nothing depends on a later phase
existing. Build top to bottom. Do not skip ahead to the fun parts (the
orchestrator, the ML loop); they are explicitly last for good reasons stated
below.

Legend: each milestone lists **Goal**, **Build**, **Done when**, and **Notes**.

---

## Phase 0 — Foundation `DONE`

The scaffold already shipped: signal schema, cache layer + local store, keyless
CoinGecko ingestion, one deterministic trend analyzer, synthesis seam, templated
narrator, CLI `scan`, tests, and open-source scaffolding (README, license,
contributing, env example).

**Done when:** `pytest` is green and `scan BTC` prints a valid Signal. (Met.)

---

## Phase 1 — Validation Harness `DONE`

**Goal.** Turn the engine from "prints opinions" into "has a verifiable track
record." This is the single most important phase. It is the trust engine, the
calibration mechanism, and the only thing that builds a compounding asset (a
proprietary dataset of signals vs. realized outcomes).

**Build.**

- `validation/recorder.py` — appends every emitted Signal, with full inputs
  and a UTC timestamp, to an immutable store (start with append-only JSONL
  under `data/signals/`, one line per signal; never mutate past records).
- `validation/outcomes.py` — given a recorded signal and later price data from
  the cache, compute the realized outcome over the signal's timeframe (did
  price move in the predicted direction before hitting the invalidation level?).
- `validation/backtest.py` — replay historical cached price series through an
  analyzer to generate signals at past timestamps, then score them. Produces
  hit rate, average move captured, and a calibration curve (predicted
  confidence vs. realized accuracy).
- Wire `scan` to record every signal it emits via the recorder.
- New CLI command: `backtest <ASSET>` and `record-stats`.

**Done when.** You can run `backtest BTC` and get an honest hit-rate and
calibration report, and every live `scan` is being recorded immutably. (Met:
recorder + outcomes + backtest shipped with 16 tests, including an explicit
no-lookahead pin via the `signal_at` choke point. First honest read on 90 days
of BTC: ~50% hit rate, and high-confidence signals underperform low-confidence
ones — the calibration problem is now measured, not suspected.)

**Notes.** Expect the honest finding that the scaffold analyzer has little or
no edge. That is a feature, not a failure: now you can improve against measured
truth. Calibration output here is what later fixes the "confidence pins at 1.0"
problem. Backtests must use only data available at the simulated time (no
lookahead). Guard against this explicitly; it is the most common backtesting
bug.

---

## Phase 2 — Second Market: US Equities + Macro Context `NEXT`

**Goal.** Prove the architecture generalizes beyond crypto, and add the macro
layer your original design called for.

**Build.**

- `ingestion/finnhub.py` — US equity daily candles (free key, gated behind
  config).
- `ingestion/fred.py` — macro series (CPI, fed funds, unemployment) via free
  FRED key, normalized into `MacroObservation`.
- `analyzers/equity_trend.py` — reuse trend logic where sensible; keep it its
  own pure function so it can diverge.
- `analyzers/macro_context.py` — produces a `SignalSource` reflecting macro
  posture (e.g. tightening vs. easing) as a contextual tilt.
- Synthesis already accepts multiple sources; now exercise it with trend +
  macro.

**Done when.** `scan AAPL` produces a signal blending a price-structure source
and a macro-context source, runs with a free FRED+Finnhub key, and the crypto
default path still runs with no key at all.

**Notes.** This is where the multi-source synthesis seam earns its keep. Keep
each source independently testable. Document the new free keys in README's
capability matrix and `.env.example`.

---

## Phase 3 — Indian Markets: Equities and the F&O Depth Feature

**Goal.** The home-market depth that makes this distinctive: Indian equities
and, crucially, F&O analytics (OI, PCR, max-pain) that generic tools ignore.

**Build.**

- `ingestion/angelone.py` and/or `ingestion/breeze.py` — Indian equity +
  options chain data, gated behind a broker account (config-driven). Breeze's
  single-call full options chain is ideal for the derivatives analyzer.
- `cache/models.py` — extend with an `OptionsChain` normalized model (strikes,
  OI, volume, expiry, right).
- `analyzers/fno_oi.py` — deterministic computation of PCR, max-pain, and
  unusual OI shifts into a `SignalSource`. Pure functions, heavily unit-tested
  with fixture chains.
- New `Market.IN_FNO` flows wired through synthesis.

**Done when.** With broker credentials configured, `scan NIFTY` produces an
F&O-aware signal computing real OI/PCR/max-pain, and the system degrades
gracefully (clear message, no crash) when credentials are absent.

**Notes.** This is the "depth showcase" for the portfolio and the most
defensible analytics in the project. Keep the research-only framing especially
firm here, since F&O is exactly the regulated retail space. No "buy/sell"; only
structural analysis.

---

## Phase 4 — Optional LLM Narrator

**Goal.** Upgrade thesis prose quality without ever letting the model touch a
number.

**Build.**

- `narrative/llm.py` — takes a fully-formed Signal + the templated thesis,
  calls a model (key from env) to rephrase more fluently, then **re-validates**
  that no numeric field changed before accepting. If validation fails or no key
  is present, fall back to the template silently.
- Config flag to enable; off by default.

**Done when.** With a key set, theses read naturally; with no key, behavior is
identical to today; in both cases the numbers are provably untouched (a test
asserts the post-LLM Signal's numeric fields equal the pre-LLM ones).

**Notes.** Never put a paywall mid-pipeline. The engine must remain fully
functional with zero keys. The re-validation step is mandatory, not optional.

---

## Phase 5 — The Interface (CLI polish + Dashboard)

**Goal.** Make outputs visible to non-terminal users and produce the portfolio
screenshot.

**Build.**

- CLI polish: `scan`, `backtest`, `record-stats`, plus a `watch` command that
  scans a configured list of assets and prints a table.
- A thin read-only web dashboard (separate `web/` dir, e.g. Next.js + Tailwind,
  or a minimal FastAPI + HTML if you want to stay single-language). It reads
  recorded signals and the latest scan results and renders them with their
  theses, confidence, invalidation levels, and the validation track record.
- Keep it read-only. No accounts, no execution, no order placement. Ever.

**Done when.** A visitor can see current signals and the honest historical track
record in a browser, and the CLI covers the daily workflow.

**Notes.** The track record view is the point: showing failed signals alongside
winners is what separates this from a tip-seller. Build the CLI first; the
dashboard is presentation on top of data that already exists.

---

## Phase 6 — Multi-Agent Orchestration `LAST, ON PURPOSE`

**Goal.** The architecture showcase from the original design: an orchestrator
that fires the right analyzers on the right triggers, with always-on-style
ingestion.

**Build.**

- `orchestrator/` — decides which analyzers run given a trigger (scheduled
  scan, new data, user query), manages priority, and maintains shared context
  across analyzers.
- Scheduled batch ingestion jobs (cron-style) keeping the cache fresh, rather
  than always-on services (same architecture, a fraction of the ops cost).

**Done when.** A single command or schedule refreshes data and produces signals
across all configured markets without manual per-asset invocation.

**Notes.** This is genuinely impressive and genuinely last. A single scheduled
pipeline does everything needed until you have many markets validated. Building
the orchestrator earlier is building a traffic system for a town with one car.

---

## Permanently deferred / handle with extreme care

- **ML feedback loop that tunes signals from backtest results.** Valuable but
  dangerous before you have a large, clean signal-vs-outcome dataset (Phase 1
  must run for months first). Premature tuning overfits to noise and quietly
  makes the track record dishonest. Revisit only with substantial recorded
  history.
- **Real-time / always-on infrastructure.** Scheduled batch demonstrates the
  same architecture far more cheaply and is friendlier to anyone cloning the
  repo.
- **Anything resembling execution, order placement, or managed money.** Out of
  scope by design. It changes the regulatory profile entirely and breaks the
  research-only framing.

---

## Cross-cutting principles (apply in every phase)

1. **Determinism in the decision path.** (See CONTEXT.md Section 2.)
   Non-negotiable.
2. **Keyless default path stays keyless.** New markets are additive and gated.
3. **Every deterministic change ships with tests.**
4. **No lookahead in backtests.** Only use data available at the simulated
   instant.
5. **Honesty over hype.** Heuristics are scaffolds until validation proves
   edge. Show losing signals. Keep the research-only disclaimer prominent.
6. **Smallest testable increment.** Prefer a working slice of one market over
   a half-built abstraction across five.

---

## Suggested immediate next step

Build **Phase 2, US equities + macro context**, starting with
`ingestion/finnhub.py` (daily candles behind a free key) and
`analyzers/equity_trend.py`. The validation harness is live, so every new
analyzer lands with a backtest report from day one — and run `scan` regularly
in the meantime; the recorded-signal dataset only compounds while scans happen.
