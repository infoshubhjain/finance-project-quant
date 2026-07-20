#!/usr/bin/env bash
#
# The daily job. This is what cron should run — one entry, not a chain of them.
#
#   crontab -e
#   0 9 * * * /full/path/to/finance-project-quant/scripts/daily.sh
#
# It refreshes context data, runs the portfolio scan, then checks source health.
#
# Everything in here exists because unattended jobs fail in specific, boring
# ways that only show up weeks later:
#
#   - Two runs overlap because yesterday's hung, and they corrupt each other's
#     writes -> a lock, with stale-lock recovery so a crashed run does not
#     wedge the job forever.
#   - A hung network read keeps the job alive for hours -> a hard wall-clock
#     timeout on the whole run.
#   - The log grows until the disk complains -> rotation.
#   - A scraper breaks and nothing says so -> a health check that reports
#     degraded sources in the log and in the exit code.
#   - cron runs with a near-empty environment and a different working directory
#     -> everything below uses absolute paths and cds first.

set -uo pipefail   # deliberately NOT -e: a failed step must still reach the
                   # health check and the unlock, or the job wedges itself

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR" || exit 1

PYTHON="$PROJECT_DIR/.venv/bin/python"
LOG_DIR="$PROJECT_DIR/data/reports"
LOG="$LOG_DIR/cron.log"
LOCK_DIR="$PROJECT_DIR/data/.daily.lock"

# Assets to keep context data for. Override with ALPHA_ASSETS in the environment.
ASSETS="${ALPHA_ASSETS:-BTC ETH AAPL MSFT GOOGL NVDA}"

# Kill the run if it exceeds this. A daily job that runs for hours is hung.
MAX_RUNTIME_SECONDS="${ALPHA_MAX_RUNTIME:-1800}"

# Rotate the log once it passes this size.
MAX_LOG_BYTES="${ALPHA_MAX_LOG_BYTES:-5242880}"   # 5 MB

mkdir -p "$LOG_DIR"

log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*" >> "$LOG"; }

# --- log rotation ----------------------------------------------------------
# Keep one previous generation. Two files bounded at 5 MB each is all the
# history anyone actually reads, and it can never fill a disk.
rotate_log() {
    [ -f "$LOG" ] || return 0
    local size
    size=$(wc -c < "$LOG" 2>/dev/null | tr -d ' ')
    [ -z "$size" ] && return 0
    if [ "$size" -gt "$MAX_LOG_BYTES" ]; then
        mv "$LOG" "$LOG.1"
        log "log rotated (previous generation at cron.log.1)"
    fi
}

# --- locking ---------------------------------------------------------------
# `mkdir` is atomic on every POSIX filesystem, which `[ -f lockfile ]` is not.
# macOS has no flock(1), so this is the portable choice rather than the obvious
# one. The PID inside lets a later run tell a live job from a corpse.
acquire_lock() {
    if mkdir "$LOCK_DIR" 2>/dev/null; then
        echo $$ > "$LOCK_DIR/pid"
        return 0
    fi

    local old_pid
    old_pid=$(cat "$LOCK_DIR/pid" 2>/dev/null || echo "")

    if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
        log "SKIPPED: run $old_pid is still going (started $(date -r "$LOCK_DIR" -u '+%H:%M:%SZ' 2>/dev/null || echo '?'))"
        return 1
    fi

    # The lock holder is gone — a crash, a reboot, an OOM kill. Without this
    # branch one bad day would silently disable the job forever, which is the
    # single most common way a cron job dies quietly.
    log "recovering stale lock from dead pid ${old_pid:-unknown}"
    rm -rf "$LOCK_DIR"
    if mkdir "$LOCK_DIR" 2>/dev/null; then
        echo $$ > "$LOCK_DIR/pid"
        return 0
    fi
    log "SKIPPED: could not acquire lock after stale-lock recovery"
    return 1
}

release_lock() { rm -rf "$LOCK_DIR"; }

# --- portable timeout ------------------------------------------------------
# macOS ships no timeout(1) and no gtimeout unless coreutils is installed, so
# this runs the command in the background and reaps it if it overstays.
run_with_timeout() {
    local seconds="$1"; shift
    "$@" >> "$LOG" 2>&1 &
    local cmd_pid=$!

    ( sleep "$seconds"
      if kill -0 "$cmd_pid" 2>/dev/null; then
          log "TIMEOUT after ${seconds}s: killing pid $cmd_pid ($1 $2)"
          kill -TERM "$cmd_pid" 2>/dev/null
          sleep 5
          kill -KILL "$cmd_pid" 2>/dev/null
      fi ) &
    local watchdog=$!

    wait "$cmd_pid" 2>/dev/null
    local rc=$?
    kill "$watchdog" 2>/dev/null
    wait "$watchdog" 2>/dev/null
    return $rc
}

# --- preflight -------------------------------------------------------------
rotate_log

if [ ! -x "$PYTHON" ]; then
    log "FATAL: no interpreter at $PYTHON — run ./start.sh once to build the venv"
    exit 1
fi

acquire_lock || exit 0
trap 'release_lock' EXIT INT TERM

log "=== daily run starting (pid $$) ==="
START=$(date +%s)

# --- 1. refresh context data ----------------------------------------------
# News, on-chain, fundamentals and the FOMC calendar. The scan path reads these
# cache-only, so without this step they stay empty and every new analyzer
# silently contributes nothing.
log "--- ingest ---"
# shellcheck disable=SC2086
run_with_timeout "$MAX_RUNTIME_SECONDS" "$PYTHON" -m alpha_engine.cli.main ingest $ASSETS
INGEST_RC=$?
[ $INGEST_RC -ne 0 ] && log "ingest exited $INGEST_RC (continuing — context is optional)"

# --- 2. scan the portfolio ------------------------------------------------
log "--- batch scan ---"
run_with_timeout "$MAX_RUNTIME_SECONDS" \
    "$PYTHON" -m alpha_engine.cli.main batch --output "$LOG_DIR/daily.json"
BATCH_RC=$?

# --- 3. health check ------------------------------------------------------
# The step that makes slow decay visible. A source that broke three weeks ago
# is reported here rather than being noticed in six months.
log "--- source health ---"
"$PYTHON" -m alpha_engine.cli.main health --strict >> "$LOG" 2>&1
HEALTH_RC=$?

ELAPSED=$(( $(date +%s) - START ))

if [ $HEALTH_RC -ne 0 ]; then
    log "=== finished in ${ELAPSED}s — DEGRADED SOURCES (see above) ==="
elif [ $BATCH_RC -ne 0 ]; then
    log "=== finished in ${ELAPSED}s — batch exited $BATCH_RC ==="
else
    log "=== finished in ${ELAPSED}s — all healthy ==="
fi

# Non-zero if the scan failed or a source is degraded, so any cron wrapper that
# reports failures has something to report.
[ $BATCH_RC -ne 0 ] && exit "$BATCH_RC"
exit $HEALTH_RC
