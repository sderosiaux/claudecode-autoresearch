#!/bin/bash
set -uo pipefail

# status.sh — Print dashboard summary from autoresearch.jsonl
#
# Usage: status.sh [path/to/autoresearch.jsonl]

JSONL_FILE="${1:-autoresearch.jsonl}"

if [[ ! -f "$JSONL_FILE" ]]; then
  echo "No autoresearch.jsonl found."
  exit 0
fi

# Read config (last config line)
CONFIG=$(grep '"type":"config"' "$JSONL_FILE" | tail -1)
if [[ -z "$CONFIG" ]]; then
  CONFIG=$(grep '"type": "config"' "$JSONL_FILE" | tail -1)
fi

METRIC_NAME="metric"
METRIC_UNIT=""
DIRECTION="lower"
SESSION_NAME=""

if [[ -n "$CONFIG" ]]; then
  METRIC_NAME=$(echo "$CONFIG" | jq -r '.metricName // "metric"')
  METRIC_UNIT=$(echo "$CONFIG" | jq -r '.metricUnit // ""')
  DIRECTION=$(echo "$CONFIG" | jq -r '.bestDirection // "lower"')
  SESSION_NAME=$(echo "$CONFIG" | jq -r '.name // ""')
fi

# Count results (non-config lines)
RESULTS=$(grep -v '"type"' "$JSONL_FILE" || true)
TOTAL=$(echo "$RESULTS" | grep -c '"status"' 2>/dev/null || echo 0)
KEPT=$(echo "$RESULTS" | grep -c '"keep"' 2>/dev/null || echo 0)
DISCARDED=$(echo "$RESULTS" | grep -c '"discard"' 2>/dev/null || echo 0)
CRASHED=$(echo "$RESULTS" | grep -c '"crash"' 2>/dev/null || echo 0)
CHECKS_FAILED=$(echo "$RESULTS" | grep -c '"checks_failed"' 2>/dev/null || echo 0)

if [[ $TOTAL -eq 0 ]]; then
  echo "No experiments recorded yet."
  exit 0
fi

# Find baseline (first result)
BASELINE=$(echo "$RESULTS" | grep '"status"' | head -1 | jq -r '.metric')

# Find best kept metric
if [[ "$DIRECTION" == "lower" ]]; then
  BEST=$(echo "$RESULTS" | grep '"keep"' | jq -r '.metric' | sort -n | head -1)
else
  BEST=$(echo "$RESULTS" | grep '"keep"' | jq -r '.metric' | sort -rn | head -1)
fi

# Calculate delta
DELTA=""
if [[ -n "$BEST" ]] && [[ -n "$BASELINE" ]] && [[ "$BASELINE" != "0" ]]; then
  DELTA=$(echo "scale=1; (($BEST - $BASELINE) / $BASELINE) * 100" | bc 2>/dev/null)
  if [[ -n "$DELTA" ]]; then
    # Add + sign for positive
    case "$DELTA" in
      -*) ;;
      *) DELTA="+$DELTA" ;;
    esac
    DELTA=" (${DELTA}%)"
  fi
fi

# Print dashboard
[[ -n "$SESSION_NAME" ]] && echo "Session: $SESSION_NAME"
echo "Runs: $TOTAL | $KEPT kept | $DISCARDED discarded | $CRASHED crashed | $CHECKS_FAILED checks_failed"
echo "Baseline: ${BASELINE}${METRIC_UNIT} ($METRIC_NAME)"
[[ -n "$BEST" ]] && echo "Best:     ${BEST}${METRIC_UNIT}${DELTA}"

# Last 5 results
echo ""
echo "Recent:"
echo "$RESULTS" | grep '"status"' | tail -5 | while IFS= read -r line; do
  S=$(echo "$line" | jq -r '.status')
  M=$(echo "$line" | jq -r '.metric')
  D=$(echo "$line" | jq -r '.description')
  C=$(echo "$line" | jq -r '.commit')
  printf "  %-15s %s%-4s %s %s\n" "$S" "$M" "$METRIC_UNIT" "$C" "$D"
done
