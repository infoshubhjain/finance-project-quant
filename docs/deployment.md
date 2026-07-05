# Deployment Guide

Three ways to run the engine, from lightest to most automated. Every path
works keyless; `.env` keys only add layers (macro context, F&O chains, forex,
LLM phrasing).

## 1. Local (the default)

```bash
./start.sh scan BTC          # one-command setup + first scan
# or manually:
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m alpha_engine.cli.main scan BTC
python -m alpha_engine.cli.main dashboard      # http://127.0.0.1:8000
```

Copy `.env.example` to `.env` for optional keys. The engine loads it
automatically; never commit it.

## 2. Docker

```bash
docker build -t alpha-engine .
docker run --rm alpha-engine scan BTC
docker run --rm -p 8000:8000 alpha-engine dashboard --host 0.0.0.0
```

Or with compose, which mounts `./data` so cache and the signal log survive
container restarts, and passes `.env` keys through:

```bash
docker compose run --rm engine scan-all
docker compose up dashboard
```

Note `--host 0.0.0.0` inside containers — the default `127.0.0.1` binding is
unreachable from outside the container. The dashboard has **no auth**; expose
it beyond localhost only behind something that adds it (reverse proxy, VPN,
tailnet).

## 3. Scheduled batch scans (cron)

The `batch` command is built for cron: it scans the portfolio, appends to the
signal log, writes a JSON report, and exits nonzero if any asset errored.

```cron
# m  h    dom mon dow  command
  15 22   *   *   1-5  cd /path/to/repo && .venv/bin/python -m alpha_engine.cli.main \
      batch --config portfolio.json --output data/reports/$(date +\%Y\%m\%d).json \
      >> data/logs/batch.log 2>&1
```

Guidelines:

- **Pick a time after your markets close** (22:15 UTC covers US close;
  Indian F&O reads age fast, so scan those during IST market hours).
- **Respect rate limits**: one batch per day per market is plenty; the cache
  makes intra-day re-runs cheap but the fetches are the scarce resource.
- **Keep the venv path absolute** in cron — no shell profile is loaded.

With Docker instead: `docker compose run --rm engine batch --config
portfolio.json --output data/reports/report.json`.

## Monitoring and alerting (honest minimum)

There is no built-in alerting; these three checks cover the failure modes:

1. **Batch exit code** — `batch` returns nonzero when any asset failed; let
   cron mail you or wrap it: `... || curl -fsS https://hc-ping.com/<uuid>/fail`
   (a dead-man switch like healthchecks.io also catches cron *not running*).
2. **Signal log freshness** — the newest file under `data/signals/` should be
   younger than your batch interval:
   `find data/signals -name '*.jsonl' -mtime -1 | grep -q . || echo STALE`.
3. **Calibration drift** — `record-stats` weekly; if the high-confidence
   bucket's hit rate sags below its stated confidence, the engine is
   overclaiming and the reliability factors need re-fitting against the new
   outcomes.

## CI

`.github/workflows/ci.yml` runs ruff (lint + format check) and the full test
suite on Python 3.11/3.12/3.13 with pip caching, on every push and PR. A red
`ruff format --check` means run `ruff format .` locally and re-push.
