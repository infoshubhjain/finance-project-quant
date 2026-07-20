# Running It Every Day — a no-experience-needed guide

[GETTING_STARTED.md](GETTING_STARTED.md) gets you a signal on your screen once.
This guide is about the harder thing: keeping it running by itself for months
without quietly rotting.

That distinction matters more than it sounds, and it is worth two paragraphs
before any commands.

---

## The problem this guide exists for

Scrapers do not usually crash. They **go quiet**.

The Fed changes a page layout. NSE renames a field. The SEC starts requiring a
different header. Your scraper keeps running, keeps exiting successfully, and
keeps returning nothing at all. Nothing errors. No alert fires. Every signal
after that day is computed with less information than you think — and it still
prints a confident-looking number.

You find out in August that it broke in March.

This engine is *especially* prone to that, because every data source is
deliberately fault-tolerant: a dead feed returns empty and the scan carries on.
That is the right behaviour for uptime and the worst possible behaviour for
noticing decay.

So the daily job is built around one idea: **make silence loud.**

---

## Part 1 — Set up the daily job (2 minutes)

```bash
./scripts/install-cron.sh
```

That is it. It installs a job that runs at 9am every day.

Want a different time?

```bash
./scripts/install-cron.sh --at 18:30    # 6:30pm, 24-hour clock
```

Check it worked:

```bash
crontab -l
```

You should see a line ending in `scripts/daily.sh`.

<details>
<summary><b>If it says "Operation not permitted" or just hangs (macOS)</b></summary>

macOS protects `crontab` behind a permission called **Full Disk Access**. Grant
it to your terminal:

1. Open **System Settings → Privacy & Security → Full Disk Access**
2. Turn it on for **Terminal** (or iTerm, or whichever you use)
3. Quit and reopen the terminal completely
4. Run `./scripts/install-cron.sh` again

Same restriction applies to `cron` itself when it runs your job, so this is
worth doing properly rather than working around.

To do it by hand instead:

```bash
./scripts/install-cron.sh --show    # prints the exact line to add
crontab -e                          # paste it in, save, quit
```

</details>

To remove it later: `./scripts/install-cron.sh --remove`

---

## Part 2 — What the daily job actually does

Three steps, in this order, and the order matters:

**1. `ingest` — refresh the context data.**
News headlines, crypto funding rates, company fundamentals, the Fed meeting
calendar. The scan step reads these *only from your local cache* and never
fetches them itself, so if this step never runs, those sources stay empty
forever and silently contribute nothing.

**2. `batch` — scan your portfolio.**
Every asset in `portfolio.json`, recorded to the signal log.

**3. `health` — check nothing has gone quiet.**
Reports any source that has stopped producing data, and exits non-zero if one
has.

---

## Part 3 — Checking on it

### The one command to remember

```bash
./start.sh doctor
```

It shows your Python setup, cache size, whether the cron job is installed, when
it last ran, how it went, and — most importantly — the health of every data
source.

### Just the source health

```bash
./start.sh health
```

You get something like this:

```text
source                   status detail
------------------------------------------------------------------------
events                   ok     last data 0.2d ago, 56 items total
news.fed_press           ok     last data 0.1d ago, 240 items total
news.nse_announcements   WARN   no data for 12.0d (expected within 3d)
news.rbi_press           FAIL   4 consecutive errors: HTTP 503
news.sec_edgar           off    SEC_USER_AGENT is not set
onchain                  ok     last data 0.1d ago, 1,204 items total
```

Four states, and the difference between them is the whole point:

| Status | Meaning | What to do |
|---|---|---|
| `ok` | Producing data recently | Nothing |
| `WARN` | No data for longer than expected, but no errors | Check whether the site changed |
| `FAIL` | Erroring repeatedly | Read `last_error`, fix the adapter |
| `off` | Deliberately disabled (missing key) | Nothing, unless you want it on |

`off` is not a failure. It means you never configured that source. The engine
distinguishes "switched off" from "broken" on purpose, because conflating them
is how a real breakage hides behind an expected absence.

### Reading the log

```bash
tail -50 data/reports/cron.log
```

Every run writes a start line, each step, and a result line:

```text
[2026-07-20T09:00:01Z] === daily run starting (pid 4821) ===
[2026-07-20T09:00:01Z] --- ingest ---
[2026-07-20T09:00:14Z] --- batch scan ---
[2026-07-20T09:00:22Z] --- source health ---
[2026-07-20T09:00:22Z] === finished in 21s — all healthy ===
```

The last line is the one to look at. It says one of:

- `all healthy` — everything worked
- `DEGRADED SOURCES` — something has gone quiet; run `./start.sh health`
- `batch exited N` — the scan itself failed

---

## Part 4 — What can break, and what happens

These are the failure modes the job is built to survive. You do not need to do
anything about them; this is here so the log makes sense when you read it.

| What happens | What the job does |
|---|---|
| Yesterday's run is still going | Skips today's with `SKIPPED: run N is still going`. Never two at once. |
| A run crashed and left the lock behind | Detects the dead process, recovers the lock, continues. One bad day cannot disable the job forever. |
| A network read hangs | Killed after 30 minutes with `TIMEOUT`. |
| The log gets large | Rotated at 5 MB, one previous generation kept. Cannot fill your disk. |
| One data source dies | Isolated. The other sources still refresh, the scan still runs, and health reports it. |
| The cache grows | Old rows are dropped on write. News keeps 30 days, on-chain 400, fundamentals 5 years. |
| A cache file gets corrupted | Treated as missing and refetched, with a warning. |
| The Mac is asleep at 9am | cron skips it. Run `./scripts/daily.sh` by hand to catch up. |

That last row is worth knowing: **cron does not run missed jobs.** If your
machine is off or asleep at the scheduled time, that day is simply skipped. If
you need catch-up behaviour, `launchd` on macOS can do it — but for a research
engine, a missed day costs you one day of recorded signals and nothing else.

---

## Part 5 — Turning on more data sources

Everything works with no keys. Each of these turns on one more source.

Put them in a file called `.env` in the project folder (copy `.env.example`):

```bash
cp .env.example .env
```

| Variable | What it turns on | Where to get it |
|---|---|---|
| `SEC_USER_AGENT` | SEC EDGAR filings | **Free, no signup.** See below. |
| `FRED_API_KEY` | US macro (rates, CPI, jobs) | https://fred.stlouisfed.org |
| `FINNHUB_API_KEY` | Company-specific news | https://finnhub.io |
| `FMP_API_KEY` | Company fundamentals | https://financialmodelingprep.com |
| `GLASSNODE_API_KEY` | Crypto on-chain flows | https://glassnode.com |

**`SEC_USER_AGENT` is the easiest win** — it needs no account at all. The SEC
just requires you to identify yourself in the request. Put your name and email:

```bash
SEC_USER_AGENT=Shubh Jain shubhj3@illinois.edu
```

Without it, SEC returns `403 Forbidden` on every request. That was a real bug in
this project until it was traced — the feed had been silently returning nothing.

After adding any key:

```bash
./start.sh ingest --force    # fetch immediately rather than waiting for 9am
./start.sh health            # confirm it went from 'off' to 'ok'
```

---

## Part 6 — Adding the RBI calendar (optional)

The engine automatically lowers its confidence in the days before a **US Federal
Reserve** rate decision, because it scrapes the Fed's meeting calendar.

It cannot do the same for the **Reserve Bank of India** — the RBI's meeting page
is built with JavaScript and serves no dates in a form any program can read. If
you care about Indian equities, add them by hand:

```bash
cp calendar.example.json calendar.json
```

Then edit it, replacing the EXAMPLE entries with real dates from rbi.org.in:

```json
[
  {
    "ts": "2026-08-06T05:30:00Z",
    "name": "RBI MPC decision",
    "region": "in",
    "importance": "high"
  }
]
```

Load it: `./start.sh ingest --kind events`

The engine ships **no dates of its own** on purpose. A wrong policy date would
lower confidence on the wrong day and leave it normal on the right one, which is
worse than having no calendar at all.

---

## Part 7 — Routine maintenance

Honestly: almost none. But here is the whole list.

**Weekly, 10 seconds:**

```bash
./start.sh doctor
```

Look at the health table. If everything is `ok` or `off`, you are done.

**When something says WARN or FAIL:**

1. `./start.sh health --json` shows the exact error
2. If it is a scraper, the source's website probably changed. The adapters print
   `CONTRACT BROKEN` when they detect this, and return nothing rather than a
   wrong number.
3. Fix the adapter in `src/alpha_engine/ingestion/`, or leave it — the engine
   runs fine without any single source.

**Monthly, if you like:**

```bash
./start.sh record-stats    # how your signals actually turned out
./start.sh calibrate       # re-derive analyzer reliability from real outcomes
```

`calibrate` is worth running once you have a few hundred recorded signals. It
replaces the engine's guessed reliability numbers with measured ones.

**Never needed:** clearing the cache. It prunes itself.

---

## Part 8 — The long game

The single most valuable thing the daily job does is **accumulate a track
record**. Every scan is written to `data/signals/signals.jsonl` before anyone
knows the outcome, and it is append-only — no code path can rewrite history.

That log is what makes this project honest. It is also the gate on the one
remaining unbuilt feature: the machine-learning layer needs **12+ months of
recorded outcomes** before it can be trained on anything real. Building it sooner
produces better-looking charts and a worse track record.

So the daily job is not really about today's signals. It is about being able to
answer, a year from now, the only question that matters:

> Did this ever actually work?

Check the current answer any time:

```bash
./start.sh record-stats
```

---

## Quick reference

```bash
./scripts/install-cron.sh          # set up the daily job
./scripts/install-cron.sh --remove # remove it
./scripts/daily.sh                 # run it right now, by hand

./start.sh doctor                  # full setup + health check
./start.sh health                  # just the data sources
./start.sh health --json           # machine-readable, with error details
./start.sh ingest --force          # refresh context data immediately
./start.sh record-stats            # how past signals turned out

tail -50 data/reports/cron.log     # what the last runs did
rm -rf data/.daily.lock            # clear a stuck lock (only if no run is active)
```
