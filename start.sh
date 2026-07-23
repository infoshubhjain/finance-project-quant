#!/usr/bin/env bash
#
# Alpha Engine — the one command you need.
#
#   ./start.sh              Set everything up, generate some signals, open the dashboard
#   ./start.sh scan BTC     Generate one signal and print it
#   ./start.sh menu         Pick what to do from a list (no flags to remember)
#   ./start.sh help         Show every command
#
# You do not need to know Python to use this. The script creates its own
# isolated Python environment inside this folder, installs what it needs there,
# and never touches the rest of your computer.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

# Run from the project root no matter where the user invoked this from. Every
# relative path below (`pip install -e .`, `data/`, `ruff .`, `pytest`,
# `portfolio.json`) resolves against the working directory, so without this,
# `~/somewhere $ /path/to/start.sh scan BTC` installs the wrong directory and
# scatters a stray `data/` folder into whatever folder you happened to be in.
# scripts/daily.sh already does this; start.sh needs it for the same reason.
cd "$SCRIPT_DIR"

# web/ is not an installed package, but the dashboard command imports it.
export PYTHONPATH="${SCRIPT_DIR}:${PYTHONPATH:-}"

# Colors, but only when writing to a real terminal (so piping to a file or CI
# log stays readable instead of full of escape codes).
if [ -t 1 ]; then
    RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
    BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; BLUE=''; BOLD=''; NC=''
fi

log()  { echo -e "${BLUE}==>${NC} $*"; }
ok()   { echo -e "${GREEN} ok${NC} $*"; }
warn() { echo -e "${YELLOW}  ! ${NC}$*"; }
err()  { echo -e "${RED}error:${NC} $*" >&2; }

die() {
    err "$1"
    [ $# -gt 1 ] && echo -e "\n${BOLD}How to fix it:${NC}\n$2\n" >&2
    exit 1
}

# ---------------------------------------------------------------------------
# Step 1 — check Python exists, in language a beginner can act on
# ---------------------------------------------------------------------------

if ! command -v python3 >/dev/null 2>&1; then
    die "Python 3 is not installed (or not on your PATH)." \
"  macOS:   brew install python3
           ...or download it from https://www.python.org/downloads/

  Windows: download from https://www.python.org/downloads/
           IMPORTANT: tick \"Add Python to PATH\" in the installer.
           Then run this script from Git Bash or WSL, not cmd.exe.

  Linux:   sudo apt install python3 python3-venv    (Debian/Ubuntu)
           sudo dnf install python3                 (Fedora)"
fi

PY_VERSION="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
PY_OK="$(python3 -c 'import sys; print(1 if sys.version_info >= (3, 10) else 0)')"
if [ "$PY_OK" != "1" ]; then
    die "Python $PY_VERSION is too old — this project needs 3.10 or newer." \
"  Install a newer Python from https://www.python.org/downloads/
  then run ./start.sh again."
fi

# ---------------------------------------------------------------------------
# Step 2 — create the isolated environment (once)
# ---------------------------------------------------------------------------

if [ ! -d "$VENV_DIR" ]; then
    log "First run — creating an isolated Python environment in .venv/"
    echo "    (this folder holds this project's libraries; nothing else on your"
    echo "     computer is changed, and deleting it undoes everything)"
    if ! python3 -m venv "$VENV_DIR" 2>/dev/null; then
        die "Could not create the virtual environment." \
"  On Debian/Ubuntu this usually means the venv module is missing:
      sudo apt install python3-venv
  Then run ./start.sh again."
    fi
    ok "environment created"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# ---------------------------------------------------------------------------
# Step 3 — install the project (only when something is actually missing)
# ---------------------------------------------------------------------------

if ! python -c "import alpha_engine" 2>/dev/null; then
    log "Installing the engine and its libraries (takes a minute the first time)"
    python -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
    if ! python -m pip install -e ".[dev]" --quiet; then
        die "Installation failed." \
"  The most common cause is no internet connection.
  If you are online and it still fails, run this to see the full error:
      source .venv/bin/activate && pip install -e \".[dev]\""
    fi
    ok "engine installed"
fi

# Where the engine writes. Mirrors config.data_dir(): ALPHA_DATA_DIR wins,
# otherwise `data/` under the project root. Resolving it the same way here is
# what keeps the checks below (is the signal log empty? how big is the cache?)
# pointed at the files the engine actually wrote.
DATA_DIR="${ALPHA_DATA_DIR:-$SCRIPT_DIR/data}"
DATA_DIR="${DATA_DIR/#\~/$HOME}"

mkdir -p "$DATA_DIR"/cache/{price,macro,chain,news,onchain,fundamentals,events} \
         "$DATA_DIR"/signals "$DATA_DIR"/reports

run_cli() { python -m alpha_engine.cli.main "$@"; }

# ---------------------------------------------------------------------------
# Helpers used by the default (dashboard) path
# ---------------------------------------------------------------------------

SIGNAL_LOG="$DATA_DIR/signals/signals.jsonl"

seed_if_empty() {
    # The dashboard displays recorded signals. On a brand-new clone there are
    # none, so a first-time user would meet an empty page and reasonably assume
    # the thing is broken. Generate a few so the first screen has content.
    if [ -s "$SIGNAL_LOG" ]; then
        return 0
    fi
    log "No signals recorded yet — generating a few so the dashboard has data"
    echo "    (scanning BTC, ETH and AAPL; this needs internet and takes ~20s)"
    local failed=0
    for asset in BTC ETH AAPL; do
        if run_cli scan "$asset" >/dev/null 2>&1; then
            ok "scanned $asset"
        else
            warn "could not scan $asset (offline, or the data source is rate-limiting)"
            failed=$((failed + 1))
        fi
    done
    if [ "$failed" -eq 3 ]; then
        warn "No signals could be generated. The dashboard will open but be empty."
        warn "Check your internet connection, then run:  ./start.sh scan BTC"
    fi
}

open_browser() {
    local url="$1"
    # Give the server a moment to bind before pointing a browser at it.
    ( sleep 2
      if command -v open >/dev/null 2>&1; then open "$url"            # macOS
      elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$url"  # Linux
      elif command -v start >/dev/null 2>&1; then start "$url"        # Git Bash
      fi ) >/dev/null 2>&1 &
}

show_help() {
    cat <<'EOF'

  Alpha Engine — an open research engine for market signals
  ═════════════════════════════════════════════════════════

  RESEARCH ONLY. Not financial advice. See the README before you rely on
  anything this prints.

  THE EASY WAY
    ./start.sh                 Set up, generate signals, open the dashboard
    ./start.sh menu            Choose from a list — no commands to memorize

  LOOKING AT ONE ASSET
    ./start.sh scan BTC        A signal: direction, confidence, why
    ./start.sh scan AAPL       Works for US stocks too (no API key needed)
    ./start.sh report BTC      Full quant report: trend, volatility, models
    ./start.sh factors BTC     Rank 500+ factors by measured predictive power

  CHECKING WHETHER IT ACTUALLY WORKS
    ./start.sh backtest BTC    Replay history with no lookahead
    ./start.sh record-stats    How past signals really turned out
    ./start.sh calibrate       Re-derive analyzer reliability from outcomes

  RUNNING IT REGULARLY
    ./start.sh scan-all        Scan everything in portfolio.json
    ./start.sh batch           Same, cron-friendly, writes a JSON report
    ./start.sh ingest          Refresh news / on-chain / fundamentals caches
    ./start.sh orchestrate --news   Let headlines trigger targeted re-scans

  DEVELOPMENT
    ./start.sh test            Run the test suite
    ./start.sh lint            Check code style
    ./start.sh doctor          Diagnose a broken setup

  OPTIONAL API KEYS  (everything above works without any of them)
    FRED_API_KEY        US macro data      https://fred.stlouisfed.org
    FINNHUB_API_KEY     company news       https://finnhub.io
    FMP_API_KEY         fundamentals       https://financialmodelingprep.com
    GLASSNODE_API_KEY   crypto on-chain    https://glassnode.com
    LLM_API_KEY         AI-written summary (prose only — never a number)

    Put them in a file called .env — copy .env.example to start.

EOF
}

show_menu() {
    echo ""
    echo -e "  ${BOLD}What would you like to do?${NC}"
    echo ""
    echo "    1) Open the dashboard in my browser"
    echo "    2) Analyze one asset (e.g. BTC, AAPL)"
    echo "    3) Scan everything in my portfolio"
    echo "    4) Backtest an asset — did this ever work?"
    echo "    5) See how past signals actually turned out"
    echo "    6) Show all commands"
    echo "    q) Quit"
    echo ""
    printf "  Choose [1-6 or q]: "
    read -r choice
    echo ""
    case "$choice" in
        1) exec "$SCRIPT_DIR/start.sh" ;;
        2) printf "  Which asset? (e.g. BTC, ETH, AAPL, RELIANCE.NS): "
           read -r asset
           [ -z "$asset" ] && { err "No asset given."; exit 1; }
           run_cli scan "$asset" ;;
        3) run_cli scan-all ;;
        4) printf "  Which asset? "; read -r asset
           [ -z "$asset" ] && { err "No asset given."; exit 1; }
           run_cli backtest "$asset" ;;
        5) run_cli record-stats ;;
        6) show_help ;;
        q|Q) echo "  Bye." ;;
        *) err "'$choice' isn't one of the options."; exit 1 ;;
    esac
}

run_doctor() {
    echo ""
    echo -e "  ${BOLD}Setup check${NC}"
    echo ""
    echo "    Python:      $(python --version 2>&1)"
    echo "    Environment: $VENV_DIR"
    python -c "import alpha_engine; print('    Engine:      installed')" 2>/dev/null \
        || echo "    Engine:      NOT INSTALLED — delete .venv/ and rerun ./start.sh"
    if [ -s "$SIGNAL_LOG" ]; then
        echo "    Signals:     $(wc -l < "$SIGNAL_LOG" | tr -d ' ') recorded"
    else
        echo "    Signals:     none yet — run ./start.sh scan BTC"
    fi
    echo "    Cached data: $(find "$DATA_DIR/cache" -name '*.json' 2>/dev/null | wc -l | tr -d ' ') files"
    echo "    Cache size:  $(du -sh "$DATA_DIR/cache" 2>/dev/null | cut -f1 | tr -d ' ')"
    echo ""

    echo -e "  ${BOLD}Optional API keys${NC}"
    for key in FRED_API_KEY FINNHUB_API_KEY FMP_API_KEY GLASSNODE_API_KEY LLM_API_KEY SEC_USER_AGENT; do
        if [ -n "${!key:-}" ]; then echo "    $key: set"
        else echo "    $key: not set (that's fine — the engine works without it)"; fi
    done
    echo ""

    # The part that matters for a job left running for months. A source that
    # died three weeks ago produces no error anywhere — only this shows it.
    echo -e "  ${BOLD}Data source health${NC}"
    # `health` exits non-zero when a source is degraded, and `set -o pipefail`
    # would make that abort doctor right here — hiding the cron and end-to-end
    # sections in exactly the situation you ran doctor to investigate.
    run_cli health 2>&1 | sed 's/^/    /' || true

    echo -e "  ${BOLD}Scheduled job${NC}"
    if crontab -l 2>/dev/null | grep -q "scripts/daily.sh"; then
        echo "    cron:        installed ($(crontab -l 2>/dev/null | grep 'scripts/daily.sh' | head -1))"
    else
        echo "    cron:        NOT installed — run ./scripts/install-cron.sh"
    fi
    if [ -f "$DATA_DIR/reports/cron.log" ]; then
        local last_run
        last_run=$(grep 'daily run starting' "$DATA_DIR/reports/cron.log" 2>/dev/null | tail -1)
        echo "    last run:    ${last_run:-never}"
        local last_result
        last_result=$(grep '=== finished' "$DATA_DIR/reports/cron.log" 2>/dev/null | tail -1)
        [ -n "$last_result" ] && echo "    last result: $last_result"
    else
        echo "    last run:    never (no cron.log yet)"
    fi
    if [ -d "$DATA_DIR/.daily.lock" ]; then
        warn "a daily run holds the lock (pid $(cat "$DATA_DIR/.daily.lock/pid" 2>/dev/null || echo '?'))"
        warn "if no run is active, remove it: rm -rf \"$DATA_DIR/.daily.lock\""
    fi
    echo ""

    log "Checking the engine can actually produce a signal..."
    if run_cli scan BTC --no-record --no-refresh >/dev/null 2>&1; then
        ok "the pipeline works end to end"
    else
        warn "the test scan failed — usually no internet, or a rate-limited data source"
    fi
    echo ""
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

case "${1:-}" in
    ""|dashboard)
        seed_if_empty
        echo ""
        log "Starting the dashboard at ${BOLD}http://localhost:8000${NC}"
        echo "    Your browser should open by itself. If it doesn't, copy that"
        echo "    address into it manually."
        echo "    Press Ctrl+C here when you want to stop."
        echo ""
        open_browser "http://localhost:8000"
        run_cli dashboard
        ;;
    help|--help|-h)
        show_help
        ;;
    menu)
        show_menu
        ;;
    doctor)
        run_doctor
        ;;
    test)
        shift
        log "Running the test suite"
        pytest -q "$@"
        ;;
    lint)
        log "Checking code style"
        ruff check . && ruff format --check .
        ;;
    mcp)
        # Used by AI assistants, which speak JSON-RPC on stdin/stdout. Nothing
        # may be printed to stdout here or the protocol stream is corrupted.
        exec python "$SCRIPT_DIR/mcp_server.py"
        ;;
    *)
        run_cli "$@"
        ;;
esac
