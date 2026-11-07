#!/bin/bash
# Scheduled entry point. Usage: run.sh [mode] [extra args]   (mode defaults to tournament)
# Secrets come from $SECRETS_FILE (mode 600, never committed). One run at a time.
set -uo pipefail
cd "$(dirname "$0")"
STATE_DIR="${STATE_DIR:-$HOME/.metaculus-bot}"
SECRETS_FILE="${SECRETS_FILE:-$STATE_DIR/secrets.env}"
mkdir -p "$STATE_DIR/logs" "$STATE_DIR/reports"
LOCK="$STATE_DIR/run.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  # stale lock: older than 3 h means the holder died
  if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +180 2>/dev/null)" ]; then rm -rf "$LOCK"; mkdir "$LOCK" || exit 0; else exit 0; fi
fi
trap 'rm -rf "$LOCK"' EXIT
set -a; . "$SECRETS_FILE"; set +a
export STATE_DIR REPORTS_DIR="$STATE_DIR/reports" COST_LOG="$STATE_DIR/costs.tsv" USAGE_LOG="$STATE_DIR/usage.tsv"
MODE="${1:-tournament}"; shift || true
# Live tournament runs stay off until LIVE=1 is set in the secrets file, which we do only after
# a dry run of the configured backend has passed. Test modes always run.
if [ "$MODE" = tournament ] && [ "${LIVE:-0}" != 1 ]; then
  echo "$(date -u +%FT%TZ) skip: LIVE is not 1" >> "$STATE_DIR/logs/skipped.log"; exit 0
fi
LOG="$STATE_DIR/logs/$(date -u +%Y-%m-%d).log"
echo "=== $(date -u +%FT%TZ) mode=$MODE $*" >> "$LOG"
PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin" uv run --frozen python main.py --mode "$MODE" "$@" >> "$LOG" 2>&1
echo "=== exit $? $(date -u +%FT%TZ)" >> "$LOG"
