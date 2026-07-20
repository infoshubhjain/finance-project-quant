# Getting Started — a no-experience-needed guide

This guide gets Alpha Engine running on your computer **even if you have never
written a line of code**. Follow it top to bottom. Every step is a copy-paste.

> **Read this first.** This tool produces *research views*, not advice, and it can
> optionally place **real trades in your own broker account**. Live trading is
> **OFF by default** and stays off until you deliberately turn it on (last
> section). Until then, everything is "paper" — pretend money, fully safe.

---

## Part 1 — Install the two things you need (10 minutes)

You need **Python** (the language this runs on) and **Git** (downloads the code).

### On a Mac

1. Open the **Terminal** app (press `Cmd+Space`, type "Terminal", hit Enter).
2. Paste this and press Enter — it installs Apple's developer tools, which include Git:
   ```bash
   xcode-select --install
   ```
   Click "Install" in the popup and wait for it to finish.
3. Install Python from [python.org/downloads](https://www.python.org/downloads/) —
   download the latest, open the file, click through the installer.

### On Windows

1. Install Python from [python.org/downloads](https://www.python.org/downloads/).
   **Important:** on the first installer screen, tick the box that says
   **"Add Python to PATH"**, then click Install.
2. Install Git from [git-scm.com/download/win](https://git-scm.com/download/win) —
   accept all the defaults.
3. Open the **"Command Prompt"** app (press the Windows key, type "cmd", Enter).

### Check it worked

Paste this. You should see version numbers, not errors:
```bash
python --version
git --version
```
> If `python` says "not found", try `python3` instead — some systems name it that.
> Wherever this guide says `python`, use `python3`.

---

## Part 2 — Download and set it up (5 minutes)

First, download the code. Paste these **one line at a time** — the first
downloads the project, the second moves into its folder:

```bash
git clone https://github.com/infoshubhjain/finance-project-quant.git
cd finance-project-quant
```

### The fastest possible start

On Mac or Linux, one command sets everything up, generates a few real signals,
and opens a dashboard in your browser:

```bash
./start.sh
```

That is genuinely all of it. If anything goes wrong, run `./start.sh doctor` —
it checks your Python version, whether the install worked, and tries a real scan
to tell you exactly what is broken.

And if you would rather not remember any commands at all:

```bash
./start.sh menu
```

gives you a numbered list to pick from.

The rest of this guide uses individual commands, so you can see what each piece
does.

### Your first scan

Now run your first scan. **Use the block for your computer** — both do the same
thing (set everything up, then read the market for Bitcoin), no API keys needed.

**Mac / Linux** — one command does everything:
```bash
./start.sh scan BTC
```

> If you see "permission denied", run `chmod +x start.sh` once, then try again.

**Windows** — install once, then run:

```bash
python -m pip install -e .
python -m alpha_engine.cli.main scan BTC
```

> You run the `pip install` line **only once, ever**. After that, every command
> is just the `python -m alpha_engine.cli.main ...` part.

The first run takes a minute (it's installing things). When it finishes you'll
see a block of JSON — a structured read on Bitcoin: a direction, a confidence
score, and a plain-English `thesis`. **That's the whole engine working.**

> **Windows users — one rule for the rest of this guide:** wherever you see
> `./start.sh`, type `python -m alpha_engine.cli.main` instead; everything after
> it stays the same. So `./start.sh scan AAPL` becomes
> `python -m alpha_engine.cli.main scan AAPL`.

Try a few more (all free, no keys):
```bash
./start.sh scan AAPL           # Apple stock
./start.sh scan RELIANCE.NS    # Reliance (Indian stock)
```

---

## Part 3 — Backtest a stock AND its options together

"Backtesting" means replaying history to see how the engine's signals would have
done — with **no cheating** (it never peeks at the future).

```bash
./start.sh backtest AAPL --days 365            # the stock alone
./start.sh backtest AAPL --days 365 --options  # the stock + its matching option
```

The `--options` version simulates buying the matching option (a call when
bullish, a put when bearish) for every signal, and shows you both results side
by side. You'll see something like:
```
"option_win_rate": 0.46,        <- fewer than half the option trades won
"avg_option_return": 0.21,      <- but winners were big (options are leveraged)
"avg_underlying_return": 0.003  <- the stock itself barely moved
```
That gap is the whole lesson of options: big leverage, but time works against
you. (The option prices are *calculated* with a standard finance formula, not
real historical quotes — free minute-by-minute option history doesn't exist.)

---

## Part 4 — Paper trading (safe, no real money)

This simulates placing a trade and writes it to a log — **nothing reaches a
broker.** This is the default and needs no keys.

```bash
./start.sh trade AAPL              # paper-trade the stock from a fresh signal
./start.sh trade NIFTY --option --expiry 2026-07-31   # paper-trade the option
```

If the signal isn't strong enough, it tells you "not actionable" and places
nothing — that's intended. Every paper (and later, live) order is saved to
`data/trades/trades.jsonl` so you have a full record.

### Trading from a webhook (alerts)

You can also have an outside alert (like a TradingView alert) trigger a trade.
Start the receiver — it **refuses to run without a password**, so set one:

```bash
# Mac/Linux:
export WEBHOOK_SECRET="pick-a-long-random-password"
./start.sh webhook

# Windows:
set WEBHOOK_SECRET=pick-a-long-random-password
python -m alpha_engine.cli.main webhook
```

Then any alert that sends a POST to `http://your-computer:8787/webhook` with that
password and a JSON body like `{"asset":"NIFTY","direction":"bullish",
"as_option":true,"spot":24500,"expiry":"2026-07-31"}` will place a **paper**
order. It stays paper until you turn on live mode (Part 6).

---

## Part 5 — Getting the API keys (all optional)

**You do not need any of these to use the engine.** The default scans, backtests,
and paper trades all work with zero keys. Add a key only for the extra feature it
unlocks. To use any key, put it in a file named `.env` in the project folder (copy
the provided `.env.example` to `.env` and fill in the lines you want).

| What you want | Which key | Cost |
|---|---|---|
| US economic context on stock signals | FRED | Free |
| Higher crypto data limits | CoinGecko | Free tier |
| Fetch Indian option chains + **live trading** | Dhan or Angel One | Free (brokerage account) |
| Forex (currency) data | OANDA | Free practice account |
| Nicer AI-written explanations | any OpenAI-style key | Paid, optional |

### FRED (free — US macro data)
1. Go to [fred.stlouisfed.org](https://fred.stlouisfed.org/) → "My Account" → create one.
2. Open [My Account → API Keys](https://fredaccount.stlouisfed.org/apikeys) → "Request API Key".
3. Copy the key into `.env`: `FRED_API_KEY=your_key_here`

### Dhan (free — Indian options data + live trading)
1. Open a free account at [dhan.co](https://dhan.co/) (needs Indian KYC, like any broker).
2. Go to **DhanHQ → My Profile → DhanHQ Trading APIs** (or [api.dhan.co](https://api.dhan.co/)).
3. Generate an **Access Token**. Note your **Client ID** (a number shown in your profile).
4. Put both in `.env`:
   ```
   DHAN_CLIENT_ID=1000000123
   DHAN_ACCESS_TOKEN=your_long_access_token
   ```
   > The access token **expires** (usually every 24 hours). When live orders
   > start failing with an auth error, generate a fresh one and update `.env`.

### Angel One (free — alternative Indian broker)
1. Open an account at [angelone.in](https://www.angelone.in/).
2. Go to [smartapi.angelbroking.com](https://smartapi.angelbroking.com/) → create an app → get your **API Key**.
3. Angel One's login also uses your client ID, a PIN, and a TOTP (the 6-digit
   authenticator code). Put what you have in `.env`:
   ```
   ANGEL_ONE_API_KEY=your_api_key
   ANGEL_ONE_CLIENT_ID=your_client_id
   ANGEL_ONE_ACCESS_TOKEN=your_session_token
   ```

### OANDA (free — forex data)
1. Sign up for a free **practice** account at [oanda.com](https://www.oanda.com/).
2. In the account management page, generate an API token.
3. `.env`: `OANDA_API_KEY=your_token`

### CoinGecko (optional — higher crypto limits)
1. Free "Demo" key at [coingecko.com/en/api](https://www.coingecko.com/en/api/pricing).
2. `.env`: `COINGECKO_API_KEY=your_key`

### An AI key (optional — prettier explanations)
Works with any OpenAI-compatible service. `.env`:
```
LLM_API_KEY=your_key
LLM_MODEL=gpt-4o-mini
```
Without it, the engine writes explanations from a template — no key required.

---

## Part 6 — Going LIVE (real money — read every line)

> **Stop.** Live mode places **real orders with real money in your own account.**
> Do the whole checklist below before you enable it. Test on paper for days first.

Live trading is protected by **three separate locks**:

1. **The master switch is off.** Live orders only happen if `LIVE_TRADING=1` is set.
   Absent that, *everything* is paper — guaranteed.
2. **Size caps.** Even in live mode, an order bigger than the caps is **rejected
   before it reaches the broker**. Defaults are deliberately tiny. Set your own:
   ```
   MAX_ORDER_QTY=5          # never trade more than 5 units/lots at once
   MAX_ORDER_NOTIONAL=50000 # never place an order worth more than ₹50,000
   ```
3. **Instrument safety.** For Dhan, you must supply a file
   `data/dhan_instruments.json` mapping each symbol to Dhan's internal ID. If a
   symbol isn't in it, the order is **refused** rather than guessed — so a wrong
   contract can't be traded by accident.

### The go-live checklist

- [ ] You've paper-traded for several days and read every line in `data/trades/trades.jsonl`.
- [ ] Your broker keys work (try `./start.sh fetch-chain NIFTY --broker dhan --expiry <date>` first).
- [ ] You've set `MAX_ORDER_QTY` and `MAX_ORDER_NOTIONAL` to amounts you can afford to lose.
- [ ] You've created `data/dhan_instruments.json` for the symbols you'll trade.
- [ ] You understand this software has **no warranty** and its signals are
      unproven scaffolds (see the honesty notes in the README).

Only then:
```bash
# Mac/Linux:
export LIVE_TRADING=1
export WEBHOOK_SECRET="your-password"
./start.sh trade NIFTY --option --expiry 2026-07-31 --qty 1
```
**Place ONE tiny order first and confirm it in your broker's app.** Treat that
first real fill as the true test — nothing else proves the live path works.

To go back to safety, just close the terminal or run `unset LIVE_TRADING`
(Windows: `set LIVE_TRADING=`). Paper mode returns instantly.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `python: command not found` | Use `python3` instead, everywhere. |
| `./start.sh: permission denied` | Run `chmod +x start.sh` once, then retry. |
| A scan fails with "429" | You hit a free data limit. Wait a minute and retry; the engine caches to avoid this. |
| `.env` changes seem ignored | Make sure the file is named exactly `.env` (not `.env.txt`) and is in the project folder. |
| Live order rejected: "no securityId" | You need `data/dhan_instruments.json` (Part 6, lock 3). |
| The dashboard is blank | No signals recorded yet. Run `./start.sh scan BTC`, then reload. |
| Anything else | Run `./start.sh doctor` — it diagnoses the whole setup and tries a real scan. |

---

## Part 7 — The rest of what it can do

Once the basics work, these are worth knowing about:

```bash
./start.sh factors BTC       # rank 505 factors by measured predictive power
./start.sh report BTC        # full quant report: trend, volatility, models
./start.sh risk              # portfolio risk: sizing, VaR, concentration
./start.sh record-stats      # how your past signals actually turned out
./start.sh ingest            # refresh news / on-chain / fundamentals caches
./start.sh orchestrate --news # let headlines trigger targeted re-scans
```

**On `factors`:** it ranks 505 different calculations by how well each one
predicted what happened next. Read the "noise floor" line at the bottom of the
output — it tells you what the luckiest of 500 *completely random* factors would
have scored on your amount of data. If the best factor doesn't beat that line,
you have learned nothing, and the engine says so plainly. Testing hundreds of
things and reporting only the winner is the single most common way people fool
themselves with backtests.

**On `ingest`:** news, on-chain and fundamentals data are only read from your
local cache during a scan — they are never fetched mid-scan. That keeps scans
fast and stops the engine hammering free APIs. Run `ingest` (or `orchestrate`)
to fill those caches, ideally on a schedule.

---

## Part 8 — Keeping it running

Everything above is manual. To have it run by itself every day:

```bash
./scripts/install-cron.sh
```

Then check on it occasionally with `./start.sh doctor`.

**[RUNNING_IT.md](RUNNING_IT.md) covers this properly** — the scheduled job, how
to tell when a data source has quietly died, and what to do about it. Worth
reading once if you plan to leave this running.

---

## The one-paragraph version

Install Python + Git, `git clone` the repo, run `./start.sh`. Everything
works with zero keys: scans, options backtests (`backtest AAPL --options`), and
paper trades (`trade AAPL`). Add keys only for extras. Live trading needs a
broker key **and** the `LIVE_TRADING=1` switch **and** passing the size caps —
it is off until you do all three on purpose. Start on paper. Stay on paper until
you trust it.
