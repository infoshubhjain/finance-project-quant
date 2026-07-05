# PROMPT.md — Next Build Phase

Use this prompt with an AI assistant to continue building the Alpha Engine.
Read CLAUDE.md and context.md first for project rules.

---

## Project Context

You are working on the Alpha Engine — an open, deterministic research engine
that turns market data into confidence-scored signals. All 6 phases of the
original plan are complete. The engine has:

- 229 passing tests, lint clean
- Keyless crypto (CoinGecko) and US equities (Yahoo)
- Indian F&O analytics with Breeze/Angel One/Dhan adapters
- Confidence calibration using source reliability + agreement quality
- LLM narrator (optional, gated behind key)
- Multi-asset orchestrator with `scan-all` and `batch`
- Read-only web dashboard
- Append-only signal log with outcome scoring

**Non-negotiable rules:**
1. Decision-bearing numbers are computed by deterministic, tested Python
2. An LLM may only write the `thesis` string, never set or change a number
3. No analyzer makes a network calls — ingestion handles data fetching
4. The default path stays keyless
5. Every deterministic change ships with tests
6. No lookahead in backtests
7. Honesty over hype — show losing signals, don't imply profit

---

## Tasks — Build in Order

### Task 1: Add More Analyzers (5 new analyzers)

Build these analyzers as pure functions in `src/alpha_engine/analyzers/`.
Each returns a `SignalSource` with direction, weight, and detail.

**1a. Support/Resistance Analyzer (`support_resistance.py`)**
- Identify key support/resistance levels from recent swing highs/lows
- Vote bullish if price near support with bounce pattern
- Vote bearish if price near resistance with rejection pattern
- Weight scales with number of touches and recency

**1b. MACD Crossover Analyzer (`macd.py`)**
- Standard MACD (12, 26, 9) with signal line
- Bullish when MACD crosses above signal line
- Bearish when MACD crosses below signal line
- Weight based on histogram magnitude and crossover strength

**1c. VWAP Analyzer (`vwap.py`)**
- Volume-Weighted Average Price relative to current price
- Bullish if price above VWAP (institutional buying pressure)
- Bearish if price below VWAP (institutional selling pressure)
- Weight based on distance from VWAP and volume profile

**1d. Multi-Timeframe Trend Analyzer (`multi_timeframe.py`)**
- Analyze trend on multiple timeframes (daily, 4h, 1h using available data)
- Strong signal when all timeframes align
- Weak/conflicting signal when timeframes diverge
- Weight based on alignment strength

**1e. Volatility Regime Analyzer (`volatility.py`)**
- Measure current volatility vs historical average (ATR-based)
- Low volatility = potential breakout setup (neutral, but boost other signals)
- High volatility = trending environment (boost trend signals, reduce mean-reversion)
- Extreme volatility = caution flag (reduce all signal weights)

**Each analyzer needs:**
- Unit tests in `tests/test_analyzers.py` with deterministic inputs
- Fixtures pinned to specific price series, not random data
- Integration with CLI's `_build_price_signal()` function

### Task 2: Add More Data Sources (3 new ingestion adapters)

Build these adapters in `src/alpha_engine/ingestion/`.
Each normalizes data into cache models.

**2a. CoinCap Adapter (`coincap.py`)**
- Keyless alternative to CoinGecko for crypto
- Historical OHLCV data from CoinCap API
- Fallback if CoinGecko rate-limits

**2b. OANDA Adapter (`oanda.py`)**
- Forex data from OANDA (free demo account)
- Credential-gated via `OANDA_API_KEY` and `OANDA_ACCOUNT_ID`
- Support major pairs: EUR/USD, GBP/USD, USD/JPY, etc.

**2c. CoinGecko Pro Adapter (`coingecko_pro.py`)**
- Upgrade path from keyless CoinGecko
- Higher rate limits, more data points
- Gate behind `COINGECKO_API_KEY`

**Each adapter needs:**
- Credential handling with graceful degradation
- Rate-limit retry logic (follow Angel One pattern)
- Tests with mocked HTTP responses
- Integration with CLI auto-detection

### Task 3: Backtest All Analyzers

Extend `validation/backtest.py` to replay all analyzers, not just trend.

**3a. Macro Backtest Alignment**
- Need point-in-time macro data (FRED series change over time)
- Store historical macro observations in cache
- Score macro analyzer against historical macro state

**3b. Volume Backtest**
- Volume analyzer uses OBV which needs full history
- Verify no lookahead in OBV computation
- Test against known volume spike patterns

**3c. RSI/Bollinger Backtest**
- These are already partially covered but not explicitly tested
- Add to backtest replay alongside trend
- Measure calibration per-analyzer

**3d. Multi-Analyzer Backtest**
- Backtest the full synthesis pipeline (trend + RSI + Bollinger + volume)
- Compare single-analyzer vs multi-analyzer hit rates
- Generate calibration curve per analyzer type

### Task 4: CI/CD with GitHub Actions

Create `.github/workflows/ci.yml`:

```yaml
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e ".[dev]"
      - run: pytest -q
      - run: ruff check .
```

Also add:
- Python 3.11 and 3.13 matrix testing
- Coverage reporting (optional)
- Dependency caching

### Task 5: Forex Market Support

Complete the `Market.FOREX` path that exists in schema.

**5a. Forex Analyzer (`forex_trend.py`)**
- Carry trade signals (interest rate differential)
- Mean reversion on major pairs
- Correlation with risk sentiment (VIX, equities)

**5b. Forex Ingestion**
- Wire OANDA adapter from Task 2b
- Auto-detect forex pairs (EUR/USD, GBP/USD patterns)

**5c. CLI Integration**
- `scan EURUSD` auto-detects as forex
- `backtest EURUSD` works
- Portfolio.json supports forex assets

### Task 6: Portfolio-Level Signals

Cross-asset analysis for portfolio construction.

**6a. Correlation Analyzer (`correlation.py`)**
- Compute rolling correlation between assets
- Identify diversification opportunities
- Flag concentrated risk (e.g., all crypto signals bullish)

**6b. Portfolio Signal (`portfolio_signal.py`)**
- Aggregate signals across portfolio into overall positioning
- Risk budget: how much capital each signal deserves
- Diversification score

**6c. Dashboard Integration**
- Portfolio view on dashboard showing aggregate positioning
- Correlation matrix visualization
- Risk concentration alerts

### Task 7: Documentation Improvements

**7a. Analyzer Development Guide**
- How to write a new analyzer (step-by-step)
- How to test it against fixtures
- How to integrate with synthesis

**7b. Architecture Deep Dive**
- Data flow diagrams
- Decision path explanation
- How confidence calibration works

**7c. Deployment Guide**
- Docker setup (already have Dockerfile)
- Cron scheduling for batch scans
- Monitoring and alerting

---

## Verification Checklist

After completing each task, verify:

```bash
# Must pass
source .venv/bin/activate
pytest -q                    # all tests pass
ruff check .                 # lint clean
python -m alpha_engine.cli.main scan BTC      # crypto works
python -m alpha_engine.cli.main scan AAPL     # equity works
python -m alpha_engine.cli.main scan NIFTY    # F&O works
python -m alpha_engine.cli.main backtest BTC  # backtest works
python -m alpha_engine.cli.main watch BTC AAPL NIFTY  # batch works
```

## Success Criteria

- [ ] All new analyzers have unit tests with deterministic fixtures
- [ ] All new adapters have mocked HTTP tests
- [ ] Backtest covers all analyzer types
- [ ] CI runs on every push
- [ ] Forex market is functional end-to-end
- [ ] Portfolio signals provide actionable insights
- [ ] Documentation enables new contributors
- [ ] No regressions in existing functionality
- [ ] All changes follow the cardinal rule (no LLM in decision path)
- [ ] Default path remains keyless

---

## Notes for the AI Assistant

1. **Build incrementally** — complete one analyzer/adapter at a time, test, commit
2. **Follow existing patterns** — match the style of crypto_trend.py, breeze.py, etc.
3. **No shortcuts** — every deterministic change needs tests
4. **Honest limitations** — document what each new analyzer can and cannot do
5. **Keyless first** — new adapters should have keyless fallbacks where possible

Start with Task 1 (analyzers) since they add the most value with the least risk.
