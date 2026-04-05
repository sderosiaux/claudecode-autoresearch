#!/bin/bash
set -uo pipefail

# log-experiment.sh — Record experiment result to JSONL, handle git commit/revert.
#
# Usage: log-experiment.sh <status> <metric> <description> [metrics_json] [failure_reason]
#
#   status:       keep | discard | crash | guard_failed (metric improved but guard check failed)
#   metric:       primary metric value (number)
#   description:  short description of what was tried
#   metrics_json: optional JSON object of secondary metrics, e.g. '{"compile_us":4200}'
#                 If omitted, auto-extracts from last run-experiment METRIC lines.
#   failure_reason: optional root-cause analysis for discard/crash (why it failed, not just that it failed)
#
# Behavior:
#   keep           -> append JSONL entry, commit the log update
#   discard/crash/guard_failed -> git revert HEAD (preserves experiment in history),
#                                 fallback to git reset --hard if revert conflicts
#
# Reads config from the last "config" line in autoresearch.jsonl for metric metadata.

STATUS="${1:?Usage: log-experiment.sh <status> <metric> <description> [metrics_json] [failure_reason]}"
METRIC="${2:?Missing metric value}"
DESCRIPTION="${3:?Missing description}"
METRICS_JSON="${4:-}"
FAILURE_REASON="${5:-}"

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

# Parse dimension from description (e.g. "[data-layout] SoA for sensor structs")
DIMENSION=$(echo "$DESCRIPTION" | sed -n 's/^\[\([^]]*\)\].*/\1/p')
DIMENSION="${DIMENSION:-unknown}"

# Parse techniques from description (e.g. "| techniques: SoA, arena-allocator")
TECHNIQUES="[]"
if [[ "$DESCRIPTION" =~ \|\ *techniques:\ *(.*) ]]; then
    TECH_STR="${BASH_REMATCH[1]}"
    TECHNIQUES=$(echo "$TECH_STR" | tr ',' '\n' | sed 's/^ *//;s/ *$//' | jq -R . | jq -s .)
fi

# Compute diff size (net lines changed) for simplicity tracking
# With prepare-experiment.sh, code is already committed — diff against parent commit
CHECKPOINT=$(cat .autoresearch-checkpoint 2>/dev/null || echo "")
if [[ -n "$CHECKPOINT" ]]; then
  DIFF_STATS=$(git diff --shortstat "$CHECKPOINT" HEAD 2>/dev/null || echo "")
else
  # Fallback: diff staged+unstaged (pre-commit flow)
  DIFF_STATS=$(git diff --shortstat 2>/dev/null; git diff --cached --shortstat 2>/dev/null)
fi
LINES_ADDED=$(echo "$DIFF_STATS" | grep -oE '[0-9]+ insertion' | grep -oE '[0-9]+' | paste -sd+ - | bc 2>/dev/null || echo "0")
LINES_REMOVED=$(echo "$DIFF_STATS" | grep -oE '[0-9]+ deletion' | grep -oE '[0-9]+' | paste -sd+ - | bc 2>/dev/null || echo "0")
# Net added lines (positive = code grew, negative = code shrank)
DIFF_SIZE=$(( ${LINES_ADDED:-0} - ${LINES_REMOVED:-0} ))

# Flow: agent edits code -> commits experiment -> run-experiment -> log-experiment
# keep = append JSONL entry, commit the log update.
# discard/crash/guard_failed = git revert HEAD (preserves failed experiment in history).
#   Fallback to git reset --hard if revert conflicts.

COMMIT=$(git rev-parse --short=7 HEAD 2>/dev/null || echo "unknown")

# Experiment number = count of existing experiments + 1
EXP_NUM=$(grep -c '"status"' "$JSONL_FILE" 2>/dev/null) || EXP_NUM=0
EXP_NUM=$(( EXP_NUM + 1 ))

# Cost tracking: elapsed seconds since last experiment (proxy for token spend)
LAST_TS=$(grep '"timestamp"' "$JSONL_FILE" 2>/dev/null | tail -1 | jq -r '.timestamp // empty' 2>/dev/null)
if [[ -n "$LAST_TS" ]] && [[ "$LAST_TS" =~ ^[0-9]+$ ]]; then
  ELAPSED_S=$(( TIMESTAMP - LAST_TS ))
else
  ELAPSED_S=0
fi

ENTRY=$(jq -nc \
  --argjson n "$EXP_NUM" \
  --arg commit "$COMMIT" \
  --argjson metric "$METRIC" \
  --arg status "$STATUS" \
  --arg description "$DESCRIPTION" \
  --arg dimension "$DIMENSION" \
  --argjson techniques "$TECHNIQUES" \
  --argjson metrics "$METRICS_JSON" \
  --argjson diff_size "$DIFF_SIZE" \
  --arg failure_reason "$FAILURE_REASON" \
  --argjson elapsed_s "$ELAPSED_S" \
  --argjson timestamp "$TIMESTAMP" \
  '{n: $n, commit: $commit, metric: $metric, status: $status, description: $description, dimension: $dimension, techniques: $techniques, metrics: $metrics, diff_size: $diff_size, failure_reason: $failure_reason, elapsed_s: $elapsed_s, timestamp: $timestamp}')

case "$STATUS" in
  keep)
    # Experiment already committed by the agent before benchmark.
    # Just log the result and commit the JSONL update.
    echo "$ENTRY" >> "$JSONL_FILE"
    rm -f .autoresearch-checkpoint
    # Add each file separately — git add fails atomically if any path is missing
    git add autoresearch.jsonl 2>/dev/null || true
    git add autoresearch.md 2>/dev/null || true
    git add autoresearch.ideas.md 2>/dev/null || true
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

        # Capture added files BEFORE reset (after reset, HEAD=CHECKPOINT so diff is empty)
        ADDED_FILES=$(git diff --name-only --diff-filter=A "$CHECKPOINT" HEAD 2>/dev/null)
        git reset --hard "$CHECKPOINT" 2>/dev/null
        [[ -n "$ADDED_FILES" ]] && git clean -fd -- $ADDED_FILES 2>/dev/null || true

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
    # Commit the JSONL update so it's not left as uncommitted state
    git add autoresearch.jsonl 2>/dev/null || true
    git commit -q -m "log: $STATUS — $DESCRIPTION" 2>/dev/null || true
    echo "REVERTED ($STATUS): $DESCRIPTION (metric: $METRIC)"
    ;;
esac

# Print summary from JSONL
TOTAL=$(grep -c '"status"' "$JSONL_FILE" 2>/dev/null) || TOTAL=0
KEPT=$(grep -c '"keep"' "$JSONL_FILE" 2>/dev/null) || KEPT=0
echo "Total runs: $TOTAL | Kept: $KEPT"
