#!/bin/bash
set -uo pipefail

# run-experiment.sh — Run a command with timing, capture output, run optional checks.
#
# Usage: run-experiment.sh <command> [timeout_seconds] [checks_timeout_seconds]
#
# Output (last lines, machine-readable):
#   AUTORESEARCH_EXIT=<0|1>
#   AUTORESEARCH_DURATION=<seconds>
#   AUTORESEARCH_PASSED=<true|false>
#   AUTORESEARCH_CRASHED=<true|false>
#   AUTORESEARCH_TIMED_OUT=<true|false>
#   AUTORESEARCH_CHECKS=<pass|fail|skip>
#   AUTORESEARCH_CHECKS_DURATION=<seconds>
#   AUTORESEARCH_CHECKS_OUTPUT=<base64-encoded>
#
# METRIC lines from the command stdout are passed through as-is.

COMMAND="${1:?Usage: run-experiment.sh <command> [timeout] [checks_timeout]}"
TIMEOUT="${2:-600}"
CHECKS_TIMEOUT="${3:-300}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(pwd)"
CHECKS_FILE="$PROJECT_DIR/autoresearch.checks.sh"

# --- Save checkpoint for safe revert ---
git rev-parse HEAD > "$PROJECT_DIR/.autoresearch-checkpoint" 2>/dev/null

# --- Run benchmark ---
TMPOUT=$(mktemp)
trap 'rm -f "$TMPOUT"' EXIT

START=$(date +%s%N)
timeout "${TIMEOUT}s" bash -c "$COMMAND" > "$TMPOUT" 2>&1
EXIT_CODE=$?
END=$(date +%s%N)

DURATION=$(echo "scale=3; ($END - $START) / 1000000000" | bc)
TIMED_OUT=false
CRASHED=false
PASSED=false

if [[ $EXIT_CODE -eq 124 ]]; then
  TIMED_OUT=true
  CRASHED=true
elif [[ $EXIT_CODE -ne 0 ]]; then
  CRASHED=true
else
  PASSED=true
fi

# Output the command's stdout (includes METRIC lines)
cat "$TMPOUT"

# --- Run checks if benchmark passed and checks file exists ---
CHECKS_STATUS="skip"
CHECKS_DURATION="0"
CHECKS_OUTPUT_B64=""

if [[ "$PASSED" == "true" ]] && [[ -x "$CHECKS_FILE" ]]; then
  CHECKS_TMPOUT=$(mktemp)
  CHECKS_START=$(date +%s%N)
  timeout "${CHECKS_TIMEOUT}s" bash "$CHECKS_FILE" > "$CHECKS_TMPOUT" 2>&1
  CHECKS_EXIT=$?
  CHECKS_END=$(date +%s%N)

  CHECKS_DURATION=$(echo "scale=3; ($CHECKS_END - $CHECKS_START) / 1000000000" | bc)

  if [[ $CHECKS_EXIT -eq 0 ]]; then
    CHECKS_STATUS="pass"
  else
    CHECKS_STATUS="fail"
    PASSED=false
  fi

  # Encode last 80 lines of checks output for structured passing
  CHECKS_OUTPUT_B64=$(tail -80 "$CHECKS_TMPOUT" | base64)
  rm -f "$CHECKS_TMPOUT"
fi

# --- Structured output ---
echo ""
echo "AUTORESEARCH_EXIT=$EXIT_CODE"
echo "AUTORESEARCH_DURATION=$DURATION"
echo "AUTORESEARCH_PASSED=$PASSED"
echo "AUTORESEARCH_CRASHED=$CRASHED"
echo "AUTORESEARCH_TIMED_OUT=$TIMED_OUT"
echo "AUTORESEARCH_CHECKS=$CHECKS_STATUS"
echo "AUTORESEARCH_CHECKS_DURATION=$CHECKS_DURATION"
echo "AUTORESEARCH_CHECKS_OUTPUT=$CHECKS_OUTPUT_B64"
