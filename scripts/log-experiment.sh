#!/bin/bash
set -uo pipefail

# log-experiment.sh — Record experiment result to JSONL, handle git commit/revert.
#
# Usage: log-experiment.sh <status> <metric> <description> [metrics_json]
#
#   status:       keep | discard | crash | guard_failed (metric improved but guard check failed)
#   metric:       primary metric value (number)
#   description:  short description of what was tried
#   metrics_json: optional JSON object of secondary metrics, e.g. '{"compile_us":4200}'
#                 If omitted, auto-extracts from last run-experiment METRIC lines.
#
# Behavior:
#   keep           -> result appended to autoresearch.jsonl (commit already done by Claude)
#   discard/crash/guard_failed -> git checkout -- . (revert uncommitted changes)
#
# Reads config from the last "config" line in autoresearch.jsonl for metric metadata.

STATUS="${1:?Usage: log-experiment.sh <status> <metric> <description> [metrics_json]}"
METRIC="${2:?Missing metric value}"
DESCRIPTION="${3:?Missing description}"
METRICS_JSON="${4:-}"

JSONL_FILE="autoresearch.jsonl"

# Auto-extract secondary metrics from last run-experiment output if not provided
if [[ -z "$METRICS_JSON" ]]; then
  LAST_OUTPUT=$(find /tmp -maxdepth 1 -name 'autoresearch-*-output*' -newer "$JSONL_FILE" 2>/dev/null | head -1)
  if [[ -n "$LAST_OUTPUT" ]] && [[ -f "$LAST_OUTPUT" ]]; then
    METRICS_JSON=$(grep '^METRIC ' "$LAST_OUTPUT" 2>/dev/null | sed 's/^METRIC //' | awk -F= '{printf "\"%s\":%s,", $1, $2}' | sed 's/,$//' | sed 's/^/{/;s/$/}/')
    [[ "$METRICS_JSON" == "{}" ]] && METRICS_JSON="{}"
  else
    METRICS_JSON="{}"
  fi
fi

# Validate status
case "$STATUS" in
  keep|discard|crash|guard_failed) ;;
  *) echo "ERROR: Invalid status '$STATUS'. Must be keep|discard|crash|guard_failed" >&2; exit 1 ;;
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

# Flow: agent edits code -> commits experiment -> run-experiment -> log-experiment
# keep = append JSONL entry, commit the log update.
# discard/crash/guard_failed = git revert HEAD (preserves failed experiment in history).
#   Fallback to git reset --hard if revert conflicts.

COMMIT=$(git rev-parse --short=7 HEAD 2>/dev/null || echo "unknown")

# Experiment number = count of existing experiments + 1
EXP_NUM=$(grep -c '"status"' "$JSONL_FILE" 2>/dev/null) || EXP_NUM=0
EXP_NUM=$(( EXP_NUM + 1 ))

ENTRY=$(jq -nc \
  --argjson n "$EXP_NUM" \
  --arg commit "$COMMIT" \
  --argjson metric "$METRIC" \
  --arg status "$STATUS" \
  --arg description "$DESCRIPTION" \
  --argjson metrics "$METRICS_JSON" \
  --argjson timestamp "$TIMESTAMP" \
  '{n: $n, commit: $commit, metric: $metric, status: $status, description: $description, metrics: $metrics, timestamp: $timestamp}')

case "$STATUS" in
  keep)
    # Experiment already committed by the agent before benchmark.
    # Just log the result and commit the JSONL update.
    echo "$ENTRY" >> "$JSONL_FILE"
    rm -f .autoresearch-checkpoint
    git add autoresearch.jsonl autoresearch.md autoresearch.ideas.md 2>/dev/null || true
    git commit -q -m "log: keep — $DESCRIPTION" 2>/dev/null || true
    COMMIT=$(git rev-parse --short=7 HEAD 2>/dev/null || echo "unknown")
    echo "KEPT: $DESCRIPTION (metric: $METRIC, commit: $COMMIT)"
    ;;
  discard|crash|guard_failed)
    # Revert using git revert (preserves failed experiment in history as memory).
    # Fallback to git reset --hard only if revert produces merge conflicts.
    CHECKPOINT=$(cat .autoresearch-checkpoint 2>/dev/null || git rev-parse HEAD)
    CURRENT=$(git rev-parse HEAD 2>/dev/null)

    if [[ "$CURRENT" != "$CHECKPOINT" ]]; then
      # There are commits to revert (agent committed before verification)
      if git revert HEAD --no-edit 2>/dev/null; then
        echo "Reverted via git revert (experiment preserved in history for learning)"
      else
        # Revert conflicted — fallback to reset
        git revert --abort 2>/dev/null
        echo "Revert conflicted — falling back to git reset --hard"

        BAK_DIR=$(mktemp -d)
        cp "$JSONL_FILE" "$BAK_DIR/jsonl" 2>/dev/null || true
        cp autoresearch.md "$BAK_DIR/md" 2>/dev/null || true
        cp autoresearch.ideas.md "$BAK_DIR/ideas" 2>/dev/null || true

        git reset --hard "$CHECKPOINT" 2>/dev/null
        git clean -fd 2>/dev/null

        mv "$BAK_DIR/jsonl" "$JSONL_FILE" 2>/dev/null || true
        [[ -f "$BAK_DIR/md" ]] && mv "$BAK_DIR/md" autoresearch.md
        [[ -f "$BAK_DIR/ideas" ]] && mv "$BAK_DIR/ideas" autoresearch.ideas.md
        rm -rf "$BAK_DIR"
      fi
    else
      # No commits to revert — just discard uncommitted changes
      git checkout -- . 2>/dev/null
      git clean -fd 2>/dev/null
    fi

    rm -f .autoresearch-checkpoint

    echo "$ENTRY" >> "$JSONL_FILE"
    echo "REVERTED ($STATUS): $DESCRIPTION (metric: $METRIC)"
    ;;
esac

# Print summary from JSONL
TOTAL=$(grep -c '"status"' "$JSONL_FILE" 2>/dev/null) || TOTAL=0
KEPT=$(grep -c '"keep"' "$JSONL_FILE" 2>/dev/null) || KEPT=0
echo "Total runs: $TOTAL | Kept: $KEPT"
