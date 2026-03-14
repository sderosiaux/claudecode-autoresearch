#!/bin/bash
set -uo pipefail

# log-experiment.sh — Record experiment result to JSONL, handle git commit/revert.
#
# Usage: log-experiment.sh <status> <metric> <description> [metrics_json]
#
#   status:       keep | discard | crash | checks_failed
#   metric:       primary metric value (number)
#   description:  short description of what was tried
#   metrics_json: optional JSON object of secondary metrics, e.g. '{"compile_us":4200}'
#
# Behavior:
#   keep           -> result appended to autoresearch.jsonl (commit already done by Claude)
#   discard/crash/checks_failed -> git checkout -- . (revert uncommitted changes)
#
# Reads config from the last "config" line in autoresearch.jsonl for metric metadata.

STATUS="${1:?Usage: log-experiment.sh <status> <metric> <description> [metrics_json]}"
METRIC="${2:?Missing metric value}"
DESCRIPTION="${3:?Missing description}"
METRICS_JSON="${4:-{}}"

JSONL_FILE="autoresearch.jsonl"

# Validate status
case "$STATUS" in
  keep|discard|crash|checks_failed) ;;
  *) echo "ERROR: Invalid status '$STATUS'. Must be keep|discard|crash|checks_failed" >&2; exit 1 ;;
esac

# Validate metric is a number (integer or float), default to 0
if ! echo "$METRIC" | grep -qE '^-?[0-9]+\.?[0-9]*$'; then
  echo "WARNING: metric '$METRIC' is not a number, defaulting to 0" >&2
  METRIC=0
fi

# Validate metrics_json is valid JSON, default to {}
if ! echo "$METRICS_JSON" | jq empty 2>/dev/null; then
  echo "WARNING: invalid metrics JSON, defaulting to {}" >&2
  METRICS_JSON="{}"
fi

TIMESTAMP=$(date +%s)

# Flow: agent edits code (uncommitted) -> run-experiment -> log-experiment
# keep = write JSONL, then commit everything (including the new JSONL entry).
# discard/crash = save JSONL aside, revert working tree, restore JSONL, append entry.

COMMIT=$(git rev-parse --short=7 HEAD 2>/dev/null || echo "unknown")

ENTRY=$(jq -nc \
  --arg commit "$COMMIT" \
  --argjson metric "$METRIC" \
  --arg status "$STATUS" \
  --arg description "$DESCRIPTION" \
  --argjson metrics "$METRICS_JSON" \
  --argjson timestamp "$TIMESTAMP" \
  '{commit: $commit, metric: $metric, status: $status, description: $description, metrics: $metrics, timestamp: $timestamp}')

case "$STATUS" in
  keep)
    # Append entry first, then commit everything
    echo "$ENTRY" >> "$JSONL_FILE"
    git add -A 2>/dev/null
    git commit -q -m "experiment: $DESCRIPTION" 2>/dev/null || true
    COMMIT=$(git rev-parse --short=7 HEAD 2>/dev/null || echo "unknown")
    echo "KEPT: $DESCRIPTION (metric: $METRIC, commit: $COMMIT)"
    ;;
  discard|crash|checks_failed)
    # Save JSONL outside the repo, revert everything, restore JSONL, append entry
    JSONL_BAK=$(mktemp)
    cp "$JSONL_FILE" "$JSONL_BAK" 2>/dev/null || true
    git checkout -- . 2>/dev/null
    git clean -fd 2>/dev/null
    mv "$JSONL_BAK" "$JSONL_FILE"
    echo "$ENTRY" >> "$JSONL_FILE"
    echo "REVERTED ($STATUS): $DESCRIPTION (metric: $METRIC)"
    ;;
esac

# Print summary from JSONL
TOTAL=$(grep -c '"status"' "$JSONL_FILE" 2>/dev/null || echo 0)
KEPT=$(grep -c '"keep"' "$JSONL_FILE" 2>/dev/null || echo 0)
echo "Total runs: $TOTAL | Kept: $KEPT"
