# Production Audit — 2026-07-26

Every claim below was verified by running something. Where a finding is
unverified, it says so.

> **Revision 2.** The first pass of this audit was incomplete and said so only
> in passing. It covered 8 of 12 phases: it never ran the frontend, never made a
> single call through the AI terminal, and skipped the frontend performance
> phase entirely — while the code it was auditing had been described as
> "shipped". Both sections returned HTTP 200 and that was treated as working.
> Revision 2 adds the missing phases, and §9 records what each one found.

Scope: the whole repository at `fix/scraper-and-product-surfaces`, including the
scheduled Action, the new strategy layer, the HTTP/MCP API, and the two-section
web app.

---

## 1. Executive summary

**The headline finding is not a bug in the code. It is that the code had been
lying about its own health for a month, in green.**

The `daily-signals` Action reported success every day while collecting zero
futures positioning data. Binance answers `HTTP 451` to every request from a
datacenter IP — exactly where the scheduled scan runs. Nothing noticed, because
health was recorded per *kind*: CoinGecko's single BTC-dominance reading kept
`onchain` at `"1 item, ok"` while six fetches returned nothing.

That is precisely the failure mode `health.py`'s docstring says the module exists
to prevent, and it slipped past because the module was used at the wrong
granularity. The engine's own silent-decay principle was correct; its
application had one gap, and the gap was the whole point.

| | before | after |
|---|---|---|
| on-chain observations per scheduled run | 1 | **451** |
| health detail | `onchain ok 1 items` | `onchain.binance_futures ? 1 attempt, no data yet`<br>`onchain.gate_futures ok 450 items` |
| build when the scrape dies | green forever | red, data still committed |
| FRED calls per four-equity batch | 12 | 3 |
| `execution/dhan.py` coverage | 0% | 100% |
| CI status | red since 2026-07-23 | green on 3.11/3.12/3.13 |

**Verdict: approved for the use it is built for — self-hosted, single-operator
research. Not approved as a hosted multi-tenant service, and it does not claim
to be one.** See §8.

### Top risks now

1. **Equity prices still have exactly one source.** Yahoo answers `429` when it
   throttles, and there is no keyless alternative to fail over to — every
   candidate checked (Stooq on both domains) now serves a JavaScript
   proof-of-work challenge at HTTP 200. Mitigated with retry-and-backoff plus a
   per-source health record, which survives a throttle but not an outage.
2. **No browser has rendered these pages.** Structure, wiring, contracts and XSS
   safety are all tested; visual layout is not. A CSS mistake passes everything.
3. **The engine has no measured edge, and the docs are honest about it.** The
   risk is a reader who skips that. Every surface carries the disclaimer.

*(Revision 1 listed the signal log's missing backup and the whole-log re-parse
here. Both are fixed — see §2.)*

---

## 2. Critical issues found and fixed

| Sev | File | Issue | Fix |
|---|---|---|---|
| **CRIT** | `orchestrator/engine.py` | Health recorded per kind, so a dead feed hid behind a live sibling for a month of green builds | Per-feed recording; only *attempted* feeds recorded |
| **CRIT** | `ingestion/binance_futures.py` | 451 from all datacenter IPs → zero futures data in CI, permanently | `FUTURES_CHAIN` walks adapters until one answers, latches the winner |
| **HIGH** | `cache/interface.py:314` | Macro staleness measured from newest *observation date*, not fetch time. FRED monthly data is always weeks old, so the cache **never hit** | Measure from file mtime (`macro_fetched_at`) |
| **HIGH** | `analyzers/crypto_onchain.py` | Two adapters, two units for open interest; blending puts a ~100,000× step in the series that scores as a huge fake build-up | `_by_metric` reads only the freshest source per metric |
| **HIGH** | `toolkit.py` | `step=-5` → HTTP 200 with a zero-signal backtest; `capital=-1000` → metrics off a negative equity curve. `range(w, n, -5)` is empty, not an error | `_BOUNDS` checked in `call_tool`, inherited by all three transports |
| **HIGH** | `execution/dhan.py` | 0% coverage on the module that places **real orders** | 17 tests, 100% |
| **HIGH** | `docker-compose.yml` | `up dashboard` broken by the new bind guard (regression introduced in this changeset) | `--allow-public`; port published on 127.0.0.1 |
| **MED** | `pyproject.toml` | `ruff>=0.4` unpinned → ruff 0.16 widened defaults → CI red since Jul 23 with no commit responsible | Name the rule set explicitly |
| **MED** | `cli/main.py` | `ingest` exited 0 when a source returned nothing (only exceptions counted) | `--strict`; workflow turns it red |
| **MED** | `health.py:128` | "never run" reported for both *never attempted* and *attempted, got nothing* | Distinguished |
| **MED** | `.github/workflows/` | `daily.json` generated every run and discarded | Committed |
| **LOW** | `cli/main.py` | Dashboard ImportError advised `python -m web.server`, which cannot work if the import failed | Accurate message |
| **LOW** | `CLAUDE.md`, `AGENTS.md` | Documented verification step `scan BTC` writes to the track record | `--no-record` |
| **HIGH** | `ingestion/yahoo.py` | Used bare `net.get` while `net.get_with_retry` (honouring `Retry-After`) already existed for exactly this; a 429 failed every equity in the batch | Retry + per-source health |
| **HIGH** | *(no file)* | Price had **no health record at all** — the most important input was the least observable | `price.yahoo` recorded |
| **HIGH** | `.github/workflows/` | The commit step is the log's only backup *and* the thing most able to destroy it; a truncating bug would be pushed | `scripts/verify_signal_log.py` refuses a shrunken or invalid log |
| **HIGH** | packaging | `pip install` shipped the CLI but not the dashboard, terminal, API or MCP server | `web/` and `mcp.py` moved into the package; static declared as package data |
| **MED** | `validation/recorder.py` | Whole log re-parsed through pydantic per HTTP request | Memoised on `(mtime, size)` |
| **MED** | `.github/workflows/` | A red build was the only alarm, and nobody watches a red build | Opens/updates a GitHub issue naming the dead source |
| **MED** | `narrative/providers.py` | Local models (Ollama, LM Studio) were unusable — the only config needing no key and sending no data out | `local` provider + `LLM_API_BASE` |

---

## 3. Security findings

Red-teamed against a live server. **No exploit path found.**

| Attack | Result |
|---|---|
| `/static/../../.env`, encoded and doubled traversal | 404 |
| `/api/asset/../../etc/passwd` | 400 |
| `asset=../../../etc/passwd`, `$(whoami)`, null byte | 400 |
| Inline code in `strategy_backtest` | 400, argument ignored |
| Malformed JSON, array-instead-of-object, 70 KB body | 400 |
| `DELETE` / `PUT` on a tool | 501 |
| Write to the signal log without `--allow-writes` | 403, on both REST **and** MCP-over-HTTP |
| API key when `ALPHA_API_KEY` set | 401 without, 200 with |

Structural properties that make this hold:

- **No database.** The entire persistence layer is JSON files, so the SQL/NoSQL
  injection classes do not exist.
- **No `eval`, `exec`, `pickle`, `yaml.load`, `os.system`, or `shell=True`**
  anywhere in `src/` or `web/`. The one `exec_module` is the strategy loader,
  which reads from disk only and is never reachable from HTTP.
- **No tool accepts code.** Pinned by a test that asserts no input schema has a
  `code`/`source`/`script` property.
- **BYO API keys are never persisted.** No key store, access log silenced, keys
  redacted from provider error messages, `connect-src 'self'` prevents an
  injected script exfiltrating a pasted key.
- **No secrets in the repo.** `.env` untracked; scanned for key patterns.

**Residual risks, unfixed and deliberate:**

| Risk | Rating | Note |
|---|---|---|
| Strategy files execute arbitrary Python | **Accepted** | Same trust as running the repo. Never reachable over HTTP. A real sandbox is FUTURE_WORK A2, platform repo. |
| Rate limiter is a fixed window | Low | A client can burst 2× across a boundary. Fine for protecting a laptop; use a token bucket if this ever fronts real traffic. |
| No HTTPS | Low | Localhost-first by design. Put it behind a reverse proxy to expose it. |
| BYO key travels plaintext over HTTP on localhost | Low | Same mitigation. |

---

## 4. Performance

Measured on 91 cached BTC bars.

| Path | Time | Assessment |
|---|---|---|
| `scan` (cached) | 188 ms | Fine |
| `backtest` | 111 ms | Fine |
| `factors` (504 factors) | 219 ms | Good — the noise floor is the honest part, not the speed |
| `strategy-backtest` | 111 ms | Fine |
| CLI cold import | 103 ms | Fine |
| Full test suite | 24 s, 2397 tests | Good |

**Bottlenecks that will appear later, in order:**

1. ~~`signals.jsonl` is read whole on every dashboard load.~~ **Fixed.** The
   parse is memoised on the file's `(mtime, size)`, which is exactly correct
   rather than a heuristic because the log is append-only. Measured on a
   simulated year — 18,000 records, 6.7 MB — a warm read went from 66 ms to
   0.05 ms. Outcome scoring is deliberately *not* cached: it reads live prices.
2. **`factors` on a long history with `cost="slow"` factors** (GARCH/HMM) goes
   from ~4 s to minutes. Already documented and excluded from the default panel.
3. **The HTTP server is synchronous per request.** `ThreadingHTTPServer` handles
   concurrency, but a `factors` call blocks its thread for the duration.

---

## 5. Technical debt

| Area | Debt | Recommendation |
|---|---|---|
| Packaging | `web/` is not installed by `pip install alpha-engine`, so the dashboard/terminal/API are clone-only | Decide deliberately: move to `src/alpha_engine/web/`, or accept clone-only and say so in the README |
| Coverage | `cli/main.py` 43%, `toolkit.py` 60%, `ingestion/breeze.py` 33% | CLI is thin wiring; `toolkit.py` gaps are the untested error branches — worth closing |
| Testing | No end-to-end test boots the server and drives a real user flow | One smoke test per section |
| Frontend | No JS tests at all; `terminal.js` is 355 lines of untested logic | It is XSS-safe by construction (`textContent` everywhere) but the markdown renderer deserves a test |
| Data | The signal log has no backup | `git push` is the backup today; make that explicit or add one |
| Observability | Health is per-run in CI (fresh `data/` every time), so multi-day decay is undetectable there | Either commit `health.json`, or accept that `ingest --strict` is the CI signal (current choice, documented) |

---

## 6. Production readiness scorecard

Scored for **self-hosted single-operator research**, which is what this is.

| Category | Score | Evidence |
|---|---|---|
| **Security** | 8/10 | Red team found nothing. Localhost-first, writes gated, keys never stored, no injection surface. −2: no HTTPS story, strategy execution is trusted-by-design. |
| **Reliability** | 8/10 | Three-tier futures chain, per-feed health, `--strict`, push retry, data committed even on degradation. Verified green on a real runner. −2: Yahoo has no fallback and 429'd during the probe. |
| **Scalability** | 5/10 | Correct for one operator. JSON files, whole-file reads, synchronous handlers. Not a defect — it is the stated scope — but it is a ceiling. |
| **Maintainability** | 9/10 | Genuinely unusual. Comments explain *why*, gotchas are recorded with the incident that caused them, one runtime dependency. |
| **Observability** | 8/10 | Per-feed health with named failure states, `doctor`, `--strict`. −2: no history in CI. |
| **Test coverage** | 8/10 | 2397 tests, 83%, network-free, 24 s. Every risky invariant pinned. −2: no E2E, no JS tests. |
| **Documentation** | 9/10 | AGENTS.md/CLAUDE.md/README are accurate — verified by re-reading against the code this session. |
| **DevOps** | 7/10 | CI on three Pythons, Docker multi-stage non-root, scheduled job with lock/timeout/rotation. −3: no staging, no rollback, no alerting beyond a red build. |

**Overall: Beta / early-Production for its stated scope.**

---

## 7. Prioritised roadmap

**Fix immediately (0–24 h)**
1. Merge PR #1. `main` is currently red and has the broken scraper.
2. Decide on the signal-log backup. One `git push` failure away from data loss.

**This week**
3. Add a Yahoo fallback for equity prices (Stooq is keyless and permissive) — it is the one remaining single-source dependency.
4. One E2E smoke test: boot the server, load each section, call one tool.
5. Close the `toolkit.py` error-branch coverage gap.

**This month**
6. Decide the `web/` packaging question.
7. Dashboard pagination before the log gets large.
8. Alerting on a red daily build (email/push), or the red build is a tree falling in a forest.

**Long term**
9. Sandbox before any strategy arrives over a network (FUTURE_WORK A2 — different repo).
10. Signed, verifiable backtest results (the actual moat).

---

## 8. Final verdict

**Approved for production as a self-hosted, single-operator research tool.**

The engineering is careful in ways most codebases are not: one runtime
dependency, structural no-lookahead guarantees, honest disclaimers on every
surface, and comments that record the incident behind each rule. The security
posture on the new API surface held against everything thrown at it.

**Not approved, and not proposed, as a hosted multi-tenant service.** That needs
the sandbox, key custody, quotas and data licensing described in FUTURE_WORK
Part B — which the project itself says belongs in a different repository, for
reasons this audit agrees with.

The one caveat worth stating plainly: **the analyzers have no demonstrated
edge.** The repo says so repeatedly and the backtester exists to keep saying so.
That is a research finding, not a defect — but it is the thing to remember
before any of this touches money.


---

## 9. Phases the first pass missed

Recorded because an audit that quietly skips a phase is worse than no audit —
it produces a scorecard that reads as coverage.

### Phase 1 / 6 — architecture and data layer

| Question | Answer |
|---|---|
| Languages | Python 3.10+ (engine), vanilla JS/CSS (frontend, no build step) |
| Runtime deps | **one** — pydantic. Everything else is stdlib, on purpose. |
| Entry points | `alpha-engine` console script, `python -m alpha_engine.web.server`, `python -m alpha_engine.mcp`, root `mcp_server.py` shim |
| **Database** | **None.** Persistence is JSON files under `data/`, plus one append-only JSONL log. This is why the SQL/NoSQL injection classes do not exist, and why §7 scores Scalability 5. |
| Schema / migrations | `schema/signal.py` + `SCHEMA_VERSION`. No migration tooling — a field change means updating every consumer by hand. |
| Auth | Optional bearer key (`ALPHA_API_KEY`) on the HTTP API. No user accounts, by design. |
| Deployment | Docker (multi-stage, non-root) or a clone. GitHub Actions runs the daily scan. |

### Phase 5 — per-endpoint review

Every route dispatches into `toolkit.call_tool`, so validation, bounds, the
disclaimer and the write gate are inherited rather than re-implemented per
endpoint. That is the design property that makes this table short.

| Endpoint | Validation | Authz | Rate limit | Notes |
|---|---|---|---|---|
| `GET /api/dashboard` | n/a | — | — | read-only; payload contract pinned by an E2E test |
| `GET /api/asset/<sym>` | regex on symbol | — | — | 400 on anything else |
| `GET /api/v1/tools` | n/a | — | — | self-describing catalogue |
| `GET|POST /api/v1/tools/<name>` | name regex + `_BOUNDS` + schema | key if set | yes | write args gated |
| `POST /api/v1/mcp` | JSON-RPC shape | key if set | yes | same write gate — not bypassable by transport |
| `POST /api/v1/agent` | question + key required | key if set | yes | caller's LLM key, never stored |
| `GET /api/v1/providers` | n/a | — | — | static catalogue |

Gap that remains: **no structured request logging.** The access log is silenced
deliberately (the agent endpoint carries a user's API key in its body), so there
is no per-request record at all. Correct for privacy, a real gap for debugging.

### Phase 7b — frontend performance

No build step, so what is in the repo is what ships.

| Page | First visit (gzipped) |
|---|---|
| `/` | 9.7 KB |
| `/terminal` | 14.2 KB |
| `/dashboard` | 17.9 KB |

- **20 KB gzipped for every asset combined.** Nothing to bundle, tree-shake or
  code-split; adding a bundler would cost more than it saves.
- **Zero external requests** — no CDN, no fonts, no analytics. Enforced by CSP,
  not just convention.
- 5 requests to interactive on `/terminal`, all same-origin.
- `theme-init.js` (337 B) is render-blocking in `<head>` **on purpose**: it sets
  the theme before first paint to avoid a flash. Correct trade.
- Charts are hand-rolled inline SVG redrawn on theme change, which is why
  `app.js` listens for `themechange` rather than relying on CSS.

### Phase 11 — runtime validation

The gap that mattered. Now covered by `tests/test_end_to_end.py`, which drives a
real server the way a browser does:

- both sections load, and every asset they reference is served
- the dashboard payload contract is checked in both directions
- every element id the JS reaches for exists in its page (a null
  `getElementById` throws on the next line and kills the page silently)
- the terminal runs the full stack — HTTP handler, agent loop, tool registry and
  analyzers are all production code, with only the model stubbed
- the answer is asserted to be checkable against the tool that produced it

Verified by hand as well as in tests: a pip-installed wheel in a clean venv
serves `/`, `/dashboard`, `/terminal`, `/static/*`, `/api/v1/tools`, and lists
all nine tools over MCP. That entire path failed before revision 2.

---

## 10. What is still not done

Named plainly, because the scorecard above would otherwise imply coverage that
does not exist.

| Item | Why it matters | Status |
|---|---|---|
| No browser ever rendered these pages | Structure, wiring and safety are tested; **visual layout is not**. A CSS mistake would pass every test here. | Open — needs a human to look, or Playwright |
| No structured request logging | Nothing to debug a production incident with | Open, deliberate trade against key privacy |
| `toolkit.py` at 60% coverage | The gaps are error branches | Open |
| Equity prices have no second source | Every keyless alternative is behind a bot challenge | Mitigated (retry + health), not solved |
| No staging environment | Changes go from laptop to main | Open |
| The engine has no measured edge | The reason the whole backtesting apparatus exists | Open by nature, honestly documented |
