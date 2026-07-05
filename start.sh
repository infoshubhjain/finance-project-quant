#!/usr/bin/env bash
# Alpha Engine — One-command setup and run
# Usage:
#   ./start.sh              # setup + show help
#   ./start.sh scan BTC     # setup + scan BTC
#   ./start.sh scan-all     # setup + scan all assets
#   ./start.sh backtest BTC # setup + backtest
#   ./start.sh dashboard    # setup + launch web dashboard
#   ./start.sh test         # setup + run tests
#   ./start.sh batch        # setup + run batch scan
#
# This script:
# 1. Creates a Python venv if missing
# 2. Installs the package in editable mode
# 3. Runs the requested command

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

# Project root must be on PYTHONPATH so `web.server` is importable (web/ is
# not installed as a package, but the CLI dashboard command needs to reach it).
export PYTHONPATH="${SCRIPT_DIR}:${PYTHONPATH:-}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log() { echo -e "${BLUE}[alpha-engine]${NC} $*"; }
ok() { echo -e "${GREEN}[ok]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC} $*"; }
err() { echo -e "${RED}[error]${NC} $*" >&2; }

# --- Step 1: Ensure venv exists ---
if [ ! -d "$VENV_DIR" ]; then
    log "Creating Python virtual environment..."
    python3 -m venv "$VENV_DIR"
    ok "Virtual environment created at $VENV_DIR"
fi

# --- Step 2: Activate venv ---
log "Activating virtual environment..."
source "$VENV_DIR/bin/activate"

# --- Step 3: Install package if not installed ---
if ! python -c "import alpha_engine" 2>/dev/null; then
    log "Installing alpha-engine in editable mode..."
    pip install -e ".[dev]" --quiet
    ok "Package installed"
else
    ok "Package already installed"
fi

# --- Step 4: Create data directories ---
mkdir -p data/cache/price data/cache/macro data/cache/chain data/signals data/reports

# --- Step 5: Run the command ---
if [ $# -eq 0 ]; then
    log "No command given — launching dashboard (use --help for CLI usage)"
    log "Dashboard: http://localhost:8000"
    python -m alpha_engine.cli.main dashboard
    exit $?
fi

if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
    echo ""
    echo "Alpha Engine — Open research engine for market signals"
    echo "===================================================="
    echo ""
    echo "Usage: ./start.sh <command> [args...]"
    echo ""
    echo "Commands:"
    echo "  (no args)            Launch web dashboard on http://localhost:8000"
    echo "  scan <ASSET>         Generate a signal (e.g. scan BTC, scan AAPL)"
    echo "  scan-all             Scan all configured assets"
    echo "  batch                Run scheduled batch scan"
    echo "  watch <ASSETS...>    Scan multiple assets, compact table"
    echo "  backtest <ASSET>     Replay history, no lookahead"
    echo "  record-stats         Score recorded signals against outcomes"
    echo "  dashboard            Launch web dashboard on http://localhost:8000"
    echo "  test                 Run test suite"
    echo "  lint                 Run linter"
    echo ""
    echo "Examples:"
    echo "  ./start.sh                     # launch dashboard"
    echo "  ./start.sh scan BTC            # crypto, no key needed"
    echo "  ./start.sh scan AAPL           # US equity, no key needed"
    echo "  ./start.sh scan-all            # scan everything"
    echo ""
    echo "Optional env vars (all free tiers):"
    echo "  FRED_API_KEY        US macro data (https://fred.stlouisfed.org)"
    echo "  LLM_API_KEY         Optional LLM narrator"
    echo "  BREEZE_API_KEY      Indian F&O chain (Breeze)"
    echo "  ANGEL_ONE_API_KEY   Indian F&O chain (Angel One)"
    echo ""
    echo "Config: portfolio.json — define your asset list for scan-all/batch"
    echo ""
    exit 0
fi

# Forward all arguments to the CLI
log "Running: python -m alpha_engine.cli.main $*"
python -m alpha_engine.cli.main "$@"
