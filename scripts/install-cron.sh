#!/usr/bin/env bash
#
# Install (or update) the alpha-engine daily cron entry.
#
#   ./scripts/install-cron.sh            # install at 9am local
#   ./scripts/install-cron.sh --at 18:30 # a different time
#   ./scripts/install-cron.sh --remove   # take it out again
#   ./scripts/install-cron.sh --show     # print what would be installed
#
# Why this is a script rather than a documented copy-paste: the entry has to
# carry an absolute path, and getting that wrong is the single most common
# reason a cron job silently never runs. This derives it.
#
# macOS has TWO separate TCC problems here and they need different fixes.
# Confusing them cost this project five days of a job that never ran once.
#
# 1. WRITING the crontab. If this script hangs or says "Operation not
#    permitted", grant Full Disk Access to your *terminal*.
#
# 2. cron READING your script at run time. This is the one that bites, because
#    step 1 succeeds and everything looks installed. `/usr/sbin/cron` is a
#    different process from your terminal and has its own TCC grant; if the repo
#    lives under ~/Desktop, ~/Documents or ~/Downloads it cannot read the script
#    at all. The job fails before it starts, writes no log, and mails the error
#    to /var/mail/$USER where nobody looks.
#
#    Measured on this machine 2026-07-27: five consecutive days of
#    "Operation not permitted", zero log lines, and the absence of
#    data/reports/cron.log was the only visible symptom.
#
#    Fix by granting Full Disk Access to /usr/sbin/cron, or by moving the repo
#    out of a protected folder. Granting it to your terminal does NOT fix this.
#
# Before installing anything, consider whether you need this at all: the
# GitHub Action (.github/workflows/daily-signals.yml) already runs the same job
# daily and commits the result back. Running both makes two schedulers append to
# one append-only signal log, which diverges and conflicts.

set -uo pipefail

warn_if_tcc_protected() {
    # cron cannot read these folders without its own Full Disk Access grant.
    case "$PROJECT_DIR" in
        "$HOME"/Desktop/*|"$HOME"/Documents/*|"$HOME"/Downloads/*)
            echo "" >&2
            echo "WARNING: this repo lives under a macOS-protected folder:" >&2
            echo "  $PROJECT_DIR" >&2
            echo "" >&2
            echo "The crontab entry will install fine and then NEVER RUN." >&2
            echo "/usr/sbin/cron cannot read scripts in ~/Desktop, ~/Documents" >&2
            echo "or ~/Downloads without its own Full Disk Access grant — and it" >&2
            echo "fails silently, writing no log. Check /var/mail/$USER for" >&2
            echo "'Operation not permitted' if you install anyway." >&2
            echo "" >&2
            echo "Fixes, best first:" >&2
            echo "  1. Use the GitHub Action instead — it already does this job." >&2
            echo "  2. Move the repo out of the protected folder." >&2
            echo "  3. System Settings -> Privacy & Security -> Full Disk Access," >&2
            echo "     add /usr/sbin/cron (Cmd-Shift-G to type the path)." >&2
            echo "" >&2
            ;;
    esac
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DAILY="$PROJECT_DIR/scripts/daily.sh"

AT="09:00"
ACTION="install"

while [ $# -gt 0 ]; do
    case "$1" in
        --at) AT="${2:-09:00}"; shift 2 ;;
        --remove) ACTION="remove"; shift ;;
        --show) ACTION="show"; shift ;;
        -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 1 ;;
    esac
done

if ! [[ "$AT" =~ ^([0-9]{1,2}):([0-9]{2})$ ]]; then
    echo "error: --at wants HH:MM (24h), got '$AT'" >&2
    exit 1
fi
HOUR="${BASH_REMATCH[1]}"; MINUTE="${BASH_REMATCH[2]}"
if [ "$HOUR" -gt 23 ] || [ "$MINUTE" -gt 59 ]; then
    echo "error: '$AT' is not a real time of day" >&2
    exit 1
fi

MARKER="# alpha-engine daily job (managed by scripts/install-cron.sh)"
ENTRY="$MINUTE $HOUR * * * $DAILY"

if [ ! -x "$DAILY" ]; then
    echo "error: $DAILY is missing or not executable" >&2
    echo "       run: chmod +x $DAILY" >&2
    exit 1
fi

if [ "$ACTION" = "show" ]; then
    printf '%s\n%s\n' "$MARKER" "$ENTRY"
    exit 0
fi

# Read the current crontab. An empty crontab exits non-zero, which is not an
# error here, so the failure is swallowed deliberately.
CURRENT="$(crontab -l 2>/dev/null || true)"

# Drop any previous version of our entry so repeated installs replace rather
# than accumulate.
#
# The filter matches the whole PROJECT directory, not just the daily script,
# because earlier setups invoked the CLI directly:
#
#   0 9 * * * cd /path/to/project && .venv/bin/python -m alpha_engine.cli.main batch ...
#
# Matching only the new script path would leave that line in place, and you
# would end up running two jobs — the old one skipping `ingest` entirely, so
# half the data sources stay empty while everything looks scheduled.
CLEANED="$(printf '%s\n' "$CURRENT" \
    | grep -v -F "$MARKER" \
    | grep -v -F "$PROJECT_DIR" \
    | grep -v '^# alpha-engine daily job' \
    | grep -v '^# The script handles locking' \
    | sed '/^[[:space:]]*$/d')"

REPLACED=$(printf '%s\n' "$CURRENT" | grep -c -F "$PROJECT_DIR" || true)

if [ "$ACTION" = "remove" ]; then
    printf '%s\n' "$CLEANED" | crontab - && echo "removed the alpha-engine cron entry."
    exit $?
fi

NEW="$(printf '%s\n%s\n%s\n' "$CLEANED" "$MARKER" "$ENTRY" | sed '/^[[:space:]]*$/d')"

warn_if_tcc_protected

if printf '%s\n' "$NEW" | crontab -; then
    echo "installed:"
    echo "  $ENTRY"
    if [ "${REPLACED:-0}" -gt 0 ]; then
        echo
        echo "  (replaced $REPLACED older alpha-engine entr(y/ies) — the previous"
        echo "   setup ran 'batch' without 'ingest', so context sources stayed empty)"
    fi
    echo
    echo "verify with:  crontab -l"
    echo "run it now:   $DAILY"
    echo "watch it:     tail -f $PROJECT_DIR/data/reports/cron.log"
else
    rc=$?
    echo "error: could not write the crontab (exit $rc)" >&2
    echo >&2
    echo "On macOS this is almost always Full Disk Access. Grant it to your" >&2
    echo "terminal in System Settings -> Privacy & Security -> Full Disk Access," >&2
    echo "then run this again. To do it by hand instead:" >&2
    echo >&2
    echo "  crontab -e" >&2
    echo "  # then add this line:" >&2
    echo "  $ENTRY" >&2
    exit $rc
fi
