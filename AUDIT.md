# Production Audit — 2026-07-26

Every claim below was verified by running something. Where a finding is
unverified, it says so.

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

1. **`data/signals/signals.jsonl` has no backup path.** It is the compounding
   asset, explicitly not regenerable, and it lives in exactly one place.
2. **Yahoo Finance returned `429` to the runner during the probe.** Equity prices
   have no fallback source, unlike crypto's three-tier chain.
3. **The engine has no measured edge, and the docs are honest about it.** The
   risk is a reader who skips that. Every surface carries the disclaimer.

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

1. **`signals.jsonl` is read whole, parsed whole, on every dashboard load.** 73
   records / 148 KB today. At ~7 signals/day it is ~2.5 MB and 18k records in a
   year. The retention logic that protects the *caches* does not apply here,
   correctly — but the dashboard will need pagination or an index before then.
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
