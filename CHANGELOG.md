# Changelog

All notable changes to Alpha Engine are recorded here. Dates are UTC.

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
