# Graph Report - .  (2026-07-18)

## Corpus Check
- 111 files · ~74,381 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1384 nodes · 3064 edges · 79 communities (73 shown, 6 thin omitted)
- Extraction: 72% EXTRACTED · 28% INFERRED · 0% AMBIGUOUS · INFERRED: 869 edges (avg confidence: 0.7)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Dashboard Web Layer
- Indian F&O Open Interest
- Calibration System
- Factor Ranking
- Project Docs & Rules
- Ingestion Adapters
- Quantitative Features
- Macro Context Analysis
- Batch Orchestrator
- CLI Entry Points
- Narrative Thesis Layer
- Quant Report
- Dhan Broker Adapter
- Signal Synthesis
- Bollinger RSI Analyzers
- Signal Schema
- Angel One Tests
- Dhan Tests
- Options Pricing Models
- Portfolio View
- Volatility Regime
- Dashboard Frontend JS
- Correlation Analysis
- Broker Client Sessions
- Ingestion Adapter Tests
- Options Chain Data
- Forex Trend Analyzer
- Cache Local Store
- GARCH Volatility Models
- Multi-Timeframe Trend
- Portfolio Risk Reports
- Support Resistance Levels
- Backtest Extended Tests
- Crypto Trend Analyzer
- RSI Momentum Analyzer
- Web Server Tests
- Tail Risk Metrics
- Angel One Adapter
- HTTP Dashboard Server
- Risk Position Sizing
- Cache Read Interface
- Quant Layer Tests
- Breeze Broker Adapter
- Indian Equity Analyzer
- Env Config Loader
- MACD Analyzer
- Cache TTL Staleness
- LLM Narrator
- CLI Tests
- Angel One Live Client
- VWAP Analyzer
- HMM Regime Gate
- Cache Macro Read/Write
- CoinGecko Pro Adapter
- Vol Position Sizing
- Options Chain Models
- Start Script
- OpenCode Config
- Drawdown Metrics
- Risk CLI Command
- Chain Fixture Loader
- Max Drawdown Calc
- HMM Fit Model
- ADX Indicator
- Dashboard Data Init
- Quant Layer Init
- Validation Layer Init
- Web App Init
- Package Metadata
- Dashboard HTML

## God Nodes (most connected - your core abstractions)
1. `PriceSeries` - 123 edges
2. `Cache` - 73 edges
3. `Signal` - 63 edges
4. `Candle` - 56 edges
5. `SignalSource` - 51 edges
6. `Direction` - 46 edges
7. `LocalStore` - 38 edges
8. `Market` - 38 edges
9. `_series()` - 37 edges
10. `OptionsChain` - 34 edges

## Surprising Connections (you probably didn't know these)
- `test_fresh_data_is_not_stale()` --calls--> `is_stale()`  [INFERRED]
  tests/test_cache.py → src/alpha_engine/cache/interface.py
- `test_detect_market_does_not_steal_other_symbols()` --calls--> `detect_market()`  [INFERRED]
  tests/test_forex.py → src/alpha_engine/cli/main.py
- `test_detect_market_forex_pairs()` --calls--> `detect_market()`  [INFERRED]
  tests/test_forex.py → src/alpha_engine/cli/main.py
- `test_detect_market_override_wins()` --calls--> `detect_market()`  [INFERRED]
  tests/test_forex.py → src/alpha_engine/cli/main.py
- `test_market_autodetection()` --calls--> `detect_market()`  [INFERRED]
  tests/test_markets.py → src/alpha_engine/cli/main.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Main Pipeline Stages** — pipeline, ingestion_adapters, cache_models, analyzer_pattern, synthesis, signal_schema, validation [EXTRACTED 1.00]
- **Backtesting Mechanism** — no_lookahead_guarantee, signal_at, outcome_scoring, outcome_10day_window [EXTRACTED 1.00]
- **Confidence Calibration System** — confidence_calibration, confidence_source_cap, source_reliability, deadband, volatility_regime_gate [EXTRACTED 1.00]

## Communities (79 total, 6 thin omitted)

### Community 0 - "Dashboard Web Layer"
Cohesion: 0.05
Nodes (69): The price at which the trend read is wrong: the recent swing low for a     bulli, trend_invalidation(), build_dashboard_payload(), latest_records(), Any, Path, Read-only dashboard data assembly.  The web layer should stay paper-thin. It ask, Return the newest record per asset, newest first. (+61 more)

### Community 1 - "Indian F&O Open Interest"
Cohesion: 0.06
Nodes (45): analyze_fno(), _format_summary(), max_pain(), oi_support_resistance(), pcr(), F&O open-interest analyzer: PCR, max pain, and OI-shift structure for Indian ind, The strike minimizing total intrinsic payout to option holders at expiry.      F, An honest invalidation level from OI structure: the biggest put wall is     the (+37 more)

### Community 2 - "Calibration System"
Cohesion: 0.07
Nodes (45): cmd_calibrate(), Offline calibration: compute per-analyzer reliability from recorded signals., AnalyzerCalibration, calibrate(), CalibrationResult, load_calibration(), BaseModel, Path (+37 more)

### Community 3 - "Factor Ranking"
Cohesion: 0.06
Nodes (53): coverage(), FactorScore, forward_returns(), hit_rate(), ic_decay(), rank_factors(), rank_ic(), Factor ranking: which features actually predict forward returns?  This module an (+45 more)

### Community 4 - "Project Docs & Rules"
Cohesion: 0.05
Nodes (43): Analyzer Pure Function Pattern, Append-Only Signal Log, Cache Models, Calibration Curve, Cardinal Rule: Deterministic Numbers Only, Confidence Calibration, Confidence Source Count Cap, Dashboard UI (+35 more)

### Community 5 - "Ingestion Adapters"
Cohesion: 0.04
Nodes (32): Alpha Engine, fetch_daily(), Binance public-data adapter: the keyless crypto *fallback*.  PLAN.md's Task 2a a, True if this adapter can serve the symbol., Fetch daily OHLCV for a crypto asset, normalize, cache, and return it.      Bina, supports(), fetch_daily(), CoinGecko ingestion adapter. Chosen as the default source because it needs no AP (+24 more)

### Community 6 - "Quantitative Features"
Cohesion: 0.08
Nodes (45): Candle, One OHLCV bar, normalized. Volume optional because some macro/forex     sources, compute_factor_panel(), compute_features(), _corr(), ema_series(), _ewma_vol(), _garman_klass() (+37 more)

### Community 7 - "Macro Context Analysis"
Cohesion: 0.08
Nodes (37): analyze_macro(), _latest_delta(), Macro context analyzer. Reads the US policy/inflation/labor posture and emits a, Change from `back` observations ago to the latest. None if too short., Year-over-year fractional change of an index series (12 monthly obs)., Fold available macro series into one small contextual SignalSource., _yoy(), fetch_series() (+29 more)

### Community 8 - "Batch Orchestrator"
Cohesion: 0.09
Nodes (35): AssetTarget, BatchReport, load_config(), _load_targets_from_defaults(), _load_targets_from_file(), _parse_asset_string(), Any, Path (+27 more)

### Community 9 - "CLI Entry Points"
Cohesion: 0.12
Nodes (34): Namespace, _add_market_args(), build_parser(), cmd_batch(), cmd_dashboard(), cmd_factors(), cmd_fetch_chain(), cmd_record_stats() (+26 more)

### Community 10 - "Narrative Thesis Layer"
Cohesion: 0.12
Nodes (33): Attempt to rewrite the thesis via an LLM. Returns the rewritten thesis     if th, Return True if and only if every numeric, decision-bearing field on the     post, rewrite_thesis(), _validate_numeric_fields_unchanged(), Narrative layer. Writes the `thesis` string and NOTHING else. It receives a full, Deterministic, no-dependency thesis. Always available., Return a copy of the signal with `thesis` populated. Always works offline., _template_thesis() (+25 more)

### Community 11 - "Quant Report"
Cohesion: 0.11
Nodes (32): build_report(), _clamp(), classify_regime(), _extension_penalty(), _fmt(), momentum_score(), _pct(), BaseModel (+24 more)

### Community 12 - "Dhan Broker Adapter"
Cohesion: 0.11
Nodes (21): _get_with_retry(), _headers(), _parse_option_chain(), _parse_option_chain_list(), _parse_strike_list(), Any, Live Dhan adapter for Indian options-chain data.  Dhan (https://dhan.co) provide, Parse Dhan's optionChain format where each entry has strike + CE + PE. (+13 more)

### Community 13 - "Signal Synthesis"
Cohesion: 0.12
Nodes (27): A single contributing input to a signal, with the partial view it gave.     The, SignalSource, _agreement_quality(), _calibrate_confidence(), _net_direction(), Synthesis. Takes the SignalSources produced by one or more analyzers and folds t, What fraction of total weight agrees with the final direction.     Returns a val, Weighted average reliability of the sources that agree with the final     direct (+19 more)

### Community 14 - "Bollinger RSI Analyzers"
Cohesion: 0.11
Nodes (24): analyze_bollinger(), _bollinger_bands(), _pct_b(), Bollinger Bands analyzer. Measures volatility and price position relative to a m, Returns (lower, middle, upper) or None if insufficient data., %B: where price sits within the bands. 0 = at lower, 1 = at upper., Produce one SignalSource from Bollinger Band position.      Direction:     - %B, Compute RSI using the standard Wilder smoothing method.      Returns None if ins (+16 more)

### Community 15 - "Signal Schema"
Cohesion: 0.15
Nodes (25): Direction, Market, Enum, str, The signal schema. This is the contract between every layer of the system.  Noth, The market a signal applies to. Drives which analyzer produced it and     which, Directional bias. Deliberately not 'buy'/'sell' so the system reads as     resea, Timeframe (+17 more)

### Community 16 - "Angel One Tests"
Cohesion: 0.12
Nodes (16): _normalize_expiry(), Normalize expiry date to DDMMMYYYY format (e.g. '30JUL2026').      Accepts YYYY-, _patch_sleep(), Tests for the Angel One ingestion adapter.  These tests mock the HTTP layer so n, Replace time.sleep with a recorder so retry tests run instantly., _StubResponse, test_fetch_chain_calls_correct_url(), test_normalize_expiry_dd_mm_yyyy() (+8 more)

### Community 17 - "Dhan Tests"
Cohesion: 0.11
Nodes (18): _patch_sleep(), Tests for the Dhan ingestion adapter.  These tests mock the HTTP layer so no rea, Replace time.sleep with a recorder so retry tests run instantly., Dhan's typical response with optionChain nested under data., Fallback to records format., Handle lowercase ce/pe keys., _StubResponse, test_fetch_chain_calls_correct_url() (+10 more)

### Community 18 - "Options Pricing Models"
Cohesion: 0.12
Nodes (22): bs_price(), _demo(), norm_cdf(), Black-Scholes-Merton European option pricing — pure-Python, deterministic.  Indi, Standard normal cumulative distribution, exact via the error function., Black-Scholes price of one European option.      Args:         spot: underlying, Self-check: put-call parity and monotonicity must hold exactly., _annualized_vol() (+14 more)

### Community 19 - "Portfolio View"
Cohesion: 0.15
Nodes (14): build_portfolio_view(), _concentration_flags(), PortfolioView, BaseModel, Portfolio-level aggregation: fold many per-asset Signals into one view of overal, One aggregate read over the latest signal per asset., Aggregate the latest signals (one per asset) into a portfolio view.      `series, _signed() (+6 more)

### Community 20 - "Volatility Regime"
Cohesion: 0.15
Nodes (20): analyze_volatility(), _atr_ratio(), classify_regime(), Volatility regime analyzer (ATR-based).  ATR (Average True Range) measures how f, Current ATR(period) divided by the average true range over `baseline`     bars., Deterministic weight multiplier for the other analyzers' sources.     Unknown re, Produce one contextual SignalSource naming the volatility regime.      Always NE, volatility_scalar() (+12 more)

### Community 21 - "Dashboard Frontend JS"
Cohesion: 0.23
Nodes (21): corrColor(), dirClass(), esc(), fmtDate(), fmtNum(), fmtPct(), hideTooltip(), loadAssetHistory() (+13 more)

### Community 22 - "Correlation Analysis"
Cohesion: 0.13
Nodes (16): correlation_matrix(), CorrelationMatrix, diversification_pairs(), pearson(), BaseModel, Cross-asset correlation analytics.  Unlike the other analyzers (one asset in, on, Pairs whose |correlation| is low enough to actually diversify., Pairwise return correlations over a shared window. `matrix[i][j]` pairs     `ass (+8 more)

### Community 23 - "Broker Client Sessions"
Cohesion: 0.16
Nodes (15): BreezeSession, _customerdetails(), DhanLiveClient, Thin live Dhan client using the documented REST contract.      The client authen, BrokerCredentials, IndianBroker, load_broker_credentials(), Enum (+7 more)

### Community 24 - "Ingestion Adapter Tests"
Cohesion: 0.15
Nodes (13): _FakeResponse, Any, Tests for the Task-2 ingestion adapters: Binance (keyless crypto fallback), Coin, test_binance_normalizes_klines(), test_binance_unmapped_symbol_raises(), test_coingecko_pro_requires_key(), test_coingecko_pro_sends_key_header(), test_crypto_fallback_prefers_pro_with_key() (+5 more)

### Community 25 - "Options Chain Data"
Cohesion: 0.13
Nodes (8): Exception, Path, Returns (series, stale). series is None if nothing cached yet.         stale=Tru, Returns (chain, stale). Same contract as get_price: None means         nothing c, Writer-private temp name. The PID suffix keeps two processes writing the     sam, A cache file that fails to parse is treated as absent (the caller will     refet, _tmp_path(), _warn_corrupt()

### Community 26 - "Forex Trend Analyzer"
Cohesion: 0.17
Nodes (17): analyze_forex_trend(), Forex analyzer: trend read blended with mean-reversion, tuned for majors.  Major, Produce one SignalSource for a currency pair.      Direction resolution:     - t, _zscore(), Tests for the forex market path: the forex_trend analyzer, market auto-detection, _series(), test_detect_market_does_not_steal_other_symbols(), test_detect_market_forex_pairs() (+9 more)

### Community 27 - "Cache Local Store"
Cohesion: 0.20
Nodes (17): LocalStore, Zero-dependency file-backed store. JSON for simplicity at this stage;     swap t, _chain(), _macro_obs(), _price_series(), datetime, Tests for cache staleness and the Cache read/write interface.  Key properties: -, test_chain_round_trip_preserves_data() (+9 more)

### Community 28 - "GARCH Volatility Models"
Cohesion: 0.14
Nodes (18): log_returns(), Log returns ln(p_t / p_{t-1}); log so multi-day returns add up., fit_garch(), _garch_ll(), GarchResult, kalman_fair_value(), KalmanResult, BaseModel (+10 more)

### Community 29 - "Multi-Timeframe Trend"
Cohesion: 0.12
Nodes (16): Shared by every analyzer that needs a simple moving average., _sma(), _horizon_direction(), Multi-timeframe trend alignment analyzer.  The idea: a move is more trustworthy, Trend of one horizon: sign of the SMA's change across the last `window`     bars, analyze_volume(), _obv(), Volume Profile analyzer. Uses On-Balance Volume (OBV) to confirm price trends wi (+8 more)

### Community 30 - "Portfolio Risk Reports"
Cohesion: 0.22
Nodes (10): build_risk_report(), Aggregate risk reads across the portfolio's signals.      This is the main entry, Aggregate risk reads for a portfolio of signals., RiskReport, _candle(), Tests for the risk agent (analyzers/risk.py).  All tests use crafted inputs — no, Create two assets with different volatility profiles., _series() (+2 more)

### Community 31 - "Support Resistance Levels"
Cohesion: 0.16
Nodes (17): analyze_support_resistance(), _cluster_levels(), Support/Resistance analyzer. Finds price levels the market has repeatedly respec, Indexes of swing highs and swing lows. A swing high at i means its high     stri, Group nearby swing prices into levels.      Returns one dict per level: its mean, Produce one SignalSource from support/resistance structure.      Direction:, _swing_points(), _flat_bar() (+9 more)

### Community 32 - "Backtest Extended Tests"
Cohesion: 0.19
Nodes (17): _macro_as_of(), Point-in-time view of macro data: only observations dated on or before     the s, _macro_obs(), Tests for the Task-3 backtest extensions: full-pipeline replay, point-in-time ma, OBV at bar t depends only on bars [0..t]: analyzing a truncated series     equal, The signal at bar t must be identical whether the macro series stops at     t or, The original guarantee, re-pinned for the full pipeline (now including     MACD,, _series() (+9 more)

### Community 33 - "Crypto Trend Analyzer"
Cohesion: 0.18
Nodes (15): analyze_trend(), _momentum(), Crypto trend analyzer. The first specialist. Demonstrates the cardinal rule: thi, Simple rate of change over `lookback` bars, as a fraction., Produce one SignalSource from price structure.      Direction: fast SMA above sl, Tests for the deterministic core. These prove the cardinal rule: given fixed inp, _series(), test_confidence_bounds_enforced() (+7 more)

### Community 34 - "RSI Momentum Analyzer"
Cohesion: 0.18
Nodes (16): analyze_multi_timeframe(), Produce one SignalSource from cross-horizon trend alignment.      Direction: maj, analyze_rsi(), RSI (Relative Strength Index) analyzer. A pure-function momentum oscillator that, Produce one SignalSource from RSI momentum.      Direction:     - RSI < oversold, _series(), test_analyze_rsi_insufficient_data(), test_analyze_rsi_is_deterministic() (+8 more)

### Community 35 - "Web Server Tests"
Cohesion: 0.21
Nodes (16): build_asset_history(), Full recorded signal history for one asset, newest first.      Each record is sc, _get(), Tests for the web dashboard server and the per-asset history service.  The HTTP, _series(), _signal(), test_api_asset_rejects_bad_symbol(), test_api_asset_returns_json() (+8 more)

### Community 36 - "Tail Risk Metrics"
Cohesion: 0.17
Nodes (9): historical_cvar(), historical_var(), Historical Value at Risk at `confidence` level over trailing `window`.      VaR, Conditional VaR (expected shortfall): average loss beyond the VaR threshold., Full tail-risk read for one asset. None when history is too short., tail_risk_flag(), TestHistoricalCVaR, TestHistoricalVar (+1 more)

### Community 37 - "Angel One Adapter"
Cohesion: 0.19
Nodes (14): _get_with_retry(), _headers(), _parse_aggregated(), _parse_ce_pe_split(), _parse_option_chain(), _parse_strike_list(), Any, Live Angel One adapter for Indian options-chain data.  Angel One's SmartAPI prov (+6 more)

### Community 38 - "HTTP Dashboard Server"
Cohesion: 0.19
Nodes (9): BaseHTTPRequestHandler, server_url(), build_parser(), DashboardHandler, main(), ArgumentParser, Read-only web dashboard for the recorded signal log.  Run:     python -m web.ser, Baseline hardening: no MIME sniffing, no framing, and scripts/styles         onl (+1 more)

### Community 39 - "Risk Position Sizing"
Cohesion: 0.22
Nodes (11): _compute_risk_score(), normalize_positions(), PositionSize, BaseModel, Risk agent: portfolio-level risk reads layered on top of per-asset signals.  Thi, Normalize un-normalized position weights so they sum to 1.0.      Returns a new, Deterministic risk score 0-100 (100 = minimal risk).      Components (each 0-100, Inverse-volatility weight for one asset. (+3 more)

### Community 40 - "Cache Read Interface"
Cohesion: 0.20
Nodes (15): Cache, The public read interface. Analyzers get one of these and ask it for data.     T, cmd_backtest(), _fetch_crypto_daily(), _load_macro(), _load_series(), Crypto fetch chain: CoinGecko Pro when a key exists, then keyless     CoinGecko,, Best-effort macro data: serve from cache, refresh stale series only when a     F (+7 more)

### Community 41 - "Quant Layer Tests"
Cohesion: 0.26
Nodes (13): down_series(), flat_series(), _range_closes(), Tests for the quant layer: feature table, models, and the scored report.  Everyt, Wrap a close path in plausible OHLCV candles., _series(), test_corr_refuses_misaligned_returns(), test_dist_median_uses_true_median_on_even_window() (+5 more)

### Community 42 - "Breeze Broker Adapter"
Cohesion: 0.23
Nodes (10): BreezeLiveClient, _checksum(), _json_body(), _option_chain_body(), _post_or_get(), Any, Live Breeze adapter for Indian options-chain data.  This is the credential-gated, Thin live Breeze client built on the documented REST contract. (+2 more)

### Community 43 - "Indian Equity Analyzer"
Cohesion: 0.20
Nodes (11): analyze_indian_equity(), _gap_analysis(), _intraday_range(), Indian equity analyzer. A dedicated analyzer for Indian cash equities that exten, Compute the average gap size as a fraction of price.      Indian equities freque, Average intraday range as a fraction of price.      Indian equities tend to have, Produce one SignalSource for an Indian cash equity.      Combines:     1. Price, test_analyze_indian_equity_downtrend() (+3 more)

### Community 44 - "Env Config Loader"
Cohesion: 0.20
Nodes (7): _load_env_file(), load_project_env(), Path, Project-wide environment loading helpers.  The app already uses environment vari, Parse a .env file: one KEY=VALUE per line, `export` prefix allowed.     Every ke, Load the nearest local `.env` files once, if present.      Existing environment, Tests for the project-wide environment loader.

### Community 45 - "MACD Analyzer"
Cohesion: 0.20
Nodes (10): analyze_macd(), _macd_lines(), MACD (Moving Average Convergence Divergence) crossover analyzer.  MACD is the ga, Return (macd_line, signal_line), tail-aligned. None if too little data., Produce one SignalSource from MACD momentum.      Direction:     - MACD crossed, test_macd_accelerating_uptrend_is_bullish(), test_macd_deterministic(), test_macd_fresh_bearish_crossover() (+2 more)

### Community 46 - "Cache TTL Staleness"
Cohesion: 0.31
Nodes (10): is_stale(), datetime, The cache interface. This is the seam the plan insists on: analyzers read from H, _ttl_for(), test_chain_has_fifteen_minute_ttl(), test_old_data_is_stale(), test_price_1h_has_one_hour_ttl(), test_price_1m_has_two_minute_ttl() (+2 more)

### Community 47 - "LLM Narrator"
Cohesion: 0.18
Nodes (10): _build_prompt(), _call_llm(), _extract_numeric_fields(), Any, Optional LLM narrator. Upgrades thesis prose quality without ever letting the mo, Extract the numeric, decision-bearing fields for comparison.      These are the, Build the LLM prompt. We give the model all the context it needs to     write a, Call the OpenAI-compatible chat API and return the assistant's message.      Ret (+2 more)

### Community 48 - "CLI Tests"
Cohesion: 0.25
Nodes (6): CLI smoke tests: the argument parser wires every subcommand, market auto-detecti, _series(), test_cmd_report_end_to_end_json(), test_cmd_report_short_history_fails_cleanly(), test_cmd_scan_end_to_end(), test_cmd_scan_records_to_the_log()

### Community 49 - "Angel One Live Client"
Cohesion: 0.27
Nodes (8): AngelOneLiveClient, Thin live Angel One client using the documented SmartAPI REST contract.      The, BrokerNotConfiguredError, RuntimeError, Raised when a requested provider is missing its required environment., test_fetch_chain_normalizes_response(), test_missing_access_token_raises(), test_missing_api_key_raises()

### Community 50 - "VWAP Analyzer"
Cohesion: 0.25
Nodes (8): analyze_vwap(), VWAP (Volume-Weighted Average Price) analyzer.  VWAP is the average price paid p, Rolling VWAP over the last `window` bars. None when volume is missing     or zer, Produce one SignalSource from price's position relative to rolling VWAP.      Di, _vwap(), test_vwap_deterministic(), test_vwap_price_above_is_bullish(), test_vwap_price_below_is_bearish()

### Community 51 - "HMM Regime Gate"
Cohesion: 0.46
Nodes (4): HMM bull probability as a risk overlay. Returns (label, confidence).      A bull, regime_gate(), HmmResult, TestRegimeGate

### Community 52 - "Cache Macro Read/Write"
Cohesion: 0.36
Nodes (3): Write macro observations, merging with any existing cached data.          Observ, MacroObservation, A single value of a macro series at a date, e.g. US CPI for a month.     Normali

### Community 53 - "CoinGecko Pro Adapter"
Cohesion: 0.29
Nodes (6): fetch_daily(), MissingAPIKeyError, RuntimeError, CoinGecko Pro adapter: the *keyed upgrade path* over the keyless default.  Same, Raised when the Pro adapter is asked to fetch without a key., Fetch daily prices from CoinGecko Pro, normalize, cache, and return.

### Community 54 - "Vol Position Sizing"
Cohesion: 0.43
Nodes (3): Inverse-volatility weight for one asset. None when history is too short.      Th, vol_position_size(), TestVolPositionSize

### Community 55 - "Options Chain Models"
Cohesion: 0.43
Nodes (6): Interval, OptionRight, Enum, str, Normalized data shapes. Every external source, however messy, is mapped into the, Whether an option is a call (right to buy) or a put (right to sell).

### Community 56 - "Start Script"
Cohesion: 0.38
Nodes (4): log(), ok(), PYTHONPATH, start.sh script

### Community 57 - "OpenCode Config"
Cohesion: 0.33
Nodes (5): permission, bash, edit, webfetch, $schema

### Community 58 - "Drawdown Metrics"
Cohesion: 0.47
Nodes (3): drawdown_metrics(), Trailing max drawdown and current drawdown over the last `window` bars.      Ret, TestDrawdownMetrics

### Community 59 - "Risk CLI Command"
Cohesion: 0.40
Nodes (5): cmd_risk(), Any, Portfolio risk report: position sizing, VaR/CVaR, concentration, regime gate., Human-readable risk report block., _render_risk_text()

### Community 60 - "Chain Fixture Loader"
Cohesion: 0.67
Nodes (3): _load_chain_file(), Path, Load a normalized options-chain fixture from disk.

### Community 61 - "Max Drawdown Calc"
Cohesion: 0.67
Nodes (3): max_drawdown(), Worst peak-to-trough drop, as a negative fraction (-0.25 = -25%)., test_max_drawdown_matches_hand_calc()

### Community 62 - "HMM Fit Model"
Cohesion: 0.67
Nodes (3): fit_hmm(), 2-state Gaussian HMM on returns, fit with Baum-Welch (EM).      Initialization i, test_hmm_flags_the_current_block()

### Community 63 - "ADX Indicator"
Cohesion: 0.67
Nodes (3): adx(), Average Directional Index (Wilder). 0-100; above ~25 usually reads as     'a tre, test_adx_higher_in_trend_than_range()

## Knowledge Gaps
- **25 isolated node(s):** `$schema`, `bash`, `edit`, `webfetch`, `alpha-engine` (+20 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **6 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `PriceSeries` connect `Volatility Regime` to `Dashboard Web Layer`, `Indian F&O Open Interest`, `Calibration System`, `Factor Ranking`, `Ingestion Adapters`, `Quantitative Features`, `Macro Context Analysis`, `Quant Report`, `Bollinger RSI Analyzers`, `Signal Schema`, `Options Pricing Models`, `Portfolio View`, `Correlation Analysis`, `Ingestion Adapter Tests`, `Options Chain Data`, `Forex Trend Analyzer`, `Cache Local Store`, `GARCH Volatility Models`, `Multi-Timeframe Trend`, `Portfolio Risk Reports`, `Support Resistance Levels`, `Backtest Extended Tests`, `Crypto Trend Analyzer`, `RSI Momentum Analyzer`, `Web Server Tests`, `Tail Risk Metrics`, `Risk Position Sizing`, `Cache Read Interface`, `Quant Layer Tests`, `Indian Equity Analyzer`, `MACD Analyzer`, `CLI Tests`, `VWAP Analyzer`, `HMM Regime Gate`, `CoinGecko Pro Adapter`, `Vol Position Sizing`, `Options Chain Models`, `Drawdown Metrics`, `ADX Indicator`?**
  _High betweenness centrality (0.359) - this node is a cross-community bridge._
- **Why does `Cache` connect `Cache Read Interface` to `Dashboard Web Layer`, `Indian F&O Open Interest`, `Calibration System`, `Web Server Tests`, `Ingestion Adapters`, `Macro Context Analysis`, `Batch Orchestrator`, `CLI Entry Points`, `Risk CLI Command`, `Cache TTL Staleness`, `Cache Macro Read/Write`, `Volatility Regime`, `CoinGecko Pro Adapter`, `Ingestion Adapter Tests`, `Options Chain Data`, `Cache Local Store`?**
  _High betweenness centrality (0.166) - this node is a cross-community bridge._
- **Why does `OptionsChain` connect `Indian F&O Open Interest` to `Angel One Adapter`, `Cache Read Interface`, `CLI Entry Points`, `Breeze Broker Adapter`, `Dhan Broker Adapter`, `Angel One Live Client`, `Broker Client Sessions`, `Options Chain Models`, `Options Chain Data`, `Cache Local Store`, `Chain Fixture Loader`?**
  _High betweenness centrality (0.141) - this node is a cross-community bridge._
- **Are the 32 inferred relationships involving `PriceSeries` (e.g. with `CorrelationMatrix` and `PortfolioView`) actually correct?**
  _`PriceSeries` has 32 INFERRED edges - model-reasoned connections that need verification._
- **Are the 35 inferred relationships involving `Cache` (e.g. with `MacroObservation` and `OptionsChain`) actually correct?**
  _`Cache` has 35 INFERRED edges - model-reasoned connections that need verification._
- **Are the 41 inferred relationships involving `Signal` (e.g. with `PortfolioView` and `PositionSize`) actually correct?**
  _`Signal` has 41 INFERRED edges - model-reasoned connections that need verification._
- **Are the 44 inferred relationships involving `Candle` (e.g. with `fetch_daily()` and `fetch_daily()`) actually correct?**
  _`Candle` has 44 INFERRED edges - model-reasoned connections that need verification._