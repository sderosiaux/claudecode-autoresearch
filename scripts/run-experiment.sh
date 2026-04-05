#!/bin/bash
set -uo pipefail

# run-experiment.sh — Run benchmark with timing, then run optional guard (safety-net checks).
#
# Usage: run-experiment.sh <command> [timeout] [guard_timeout] [runs] [warmup] [early_stop_pct]
#
# Args:
#   command:        benchmark command to run
#   timeout:        max seconds per run (default: 600)
#   guard_timeout:  max seconds for guard/checks (default: 300)
#   runs:           number of measured runs, report median (default: 1)
#   warmup:         number of untimed warmup runs (default: 0)
#   early_stop_pct: if >0 and runs>1, abort remaining runs when first run
#                   metric is this % worse than the best known keep (default: 0)
#
# Output (last lines, machine-readable):
#   AUTORESEARCH_EXIT=<0|1>
#   AUTORESEARCH_DURATION=<seconds>
#   AUTORESEARCH_PASSED=<true|false>
#   AUTORESEARCH_CRASHED=<true|false>
#   AUTORESEARCH_TIMED_OUT=<true|false>
#   AUTORESEARCH_GUARD=<pass|fail|skip>
#   AUTORESEARCH_GUARD_DURATION=<seconds>
#   AUTORESEARCH_GUARD_OUTPUT=<base64-encoded>
#   AUTORESEARCH_RUNS=<N>
#   AUTORESEARCH_WARMUP=<N>
#
# When runs > 1, METRIC lines report the median across runs.
# Per-metric stddev is emitted as METRIC <name>_stddev=<value>.

COMMAND="${1:?Usage: run-experiment.sh <command> [timeout] [guard_timeout] [runs] [warmup] [early_stop_pct]}"
TIMEOUT="${2:-600}"
GUARD_TIMEOUT="${3:-300}"
RUNS="${4:-1}"
WARMUP="${5:-0}"
EARLY_STOP_PCT="${6:-0}"  # If >0 and runs>1, abort remaining runs if first run metric is this % worse than best known

PROJECT_DIR="$(pwd)"
GUARD_FILE="$PROJECT_DIR/autoresearch.checks.sh"

# --- Check max experiments cap ---
if [[ -f "$PROJECT_DIR/autoresearch.jsonl" ]]; then
  MAX_EXP=$(grep '"type":"config"' "$PROJECT_DIR/autoresearch.jsonl" 2>/dev/null | tail -1 | jq -r '.maxExperiments // empty' 2>/dev/null)
  TOTAL_EXP=$(grep -c '"status"' "$PROJECT_DIR/autoresearch.jsonl" 2>/dev/null) || TOTAL_EXP=0
  if [[ -n "$MAX_EXP" ]] && [[ "$MAX_EXP" =~ ^[0-9]+$ ]] && [[ $TOTAL_EXP -ge $MAX_EXP ]]; then
    echo "AUTORESEARCH_LIMIT_REACHED=true"
    echo "Max experiments reached ($MAX_EXP). Stop the loop." >&2
    exit 1
  fi
fi

# --- Early stopping: get best known metric for comparison ---
BEST_METRIC=""
BEST_DIRECTION=""
PRIMARY_METRIC_NAME=""
if [[ $EARLY_STOP_PCT -gt 0 ]] && [[ $RUNS -gt 1 ]] && [[ -f "$PROJECT_DIR/autoresearch.jsonl" ]]; then
  BEST_DIRECTION=$(grep '"type":"config"' "$PROJECT_DIR/autoresearch.jsonl" 2>/dev/null | tail -1 | jq -r '.bestDirection // "lower"' 2>/dev/null)
  PRIMARY_METRIC_NAME=$(grep '"type":"config"' "$PROJECT_DIR/autoresearch.jsonl" 2>/dev/null | tail -1 | jq -r '.metricName // empty' 2>/dev/null)
  SORT_FLAG=""
  [[ "$BEST_DIRECTION" = "higher" ]] && SORT_FLAG="-r"
  BEST_METRIC=$(grep '"keep"' "$PROJECT_DIR/autoresearch.jsonl" 2>/dev/null | jq -r '.metric' 2>/dev/null | sort -n $SORT_FLAG | head -1)
fi

TMPOUT=$(mktemp /tmp/autoresearch-$$-output.XXXXXX)
METRICS_DIR=$(mktemp -d)
# Note: TMPOUT is NOT cleaned up here — log-experiment.sh reads it for secondary metrics auto-extraction.
# It lives in /tmp and will be cleaned by the OS. Only clean METRICS_DIR.
trap 'rm -rf "$METRICS_DIR"' EXIT

# --- Warmup runs (untimed, output discarded) ---
if [[ $WARMUP -gt 0 ]]; then
  echo "Warmup: $WARMUP runs..." >&2
  for ((w=1; w<=WARMUP; w++)); do
    timeout "${TIMEOUT}s" bash -c "$COMMAND" > /dev/null 2>&1 || true
    echo "  warmup $w/$WARMUP done" >&2
  done
fi

# --- Measured runs ---
TOTAL_START=$(date +%s%N)
EXIT_CODE=0
TIMED_OUT=false
CRASHED=false
PASSED=true
COMPLETED_RUNS=0

for ((r=1; r<=RUNS; r++)); do
  [[ $RUNS -gt 1 ]] && echo "Run $r/$RUNS..." >&2

  timeout "${TIMEOUT}s" bash -c "$COMMAND" > "$TMPOUT" 2>&1
  RUN_EXIT=$?

  if [[ $RUN_EXIT -eq 124 ]]; then
    TIMED_OUT=true
    CRASHED=true
    PASSED=false
    EXIT_CODE=$RUN_EXIT
    break
  elif [[ $RUN_EXIT -ne 0 ]]; then
    CRASHED=true
    PASSED=false
    EXIT_CODE=$RUN_EXIT
    break
  fi

  COMPLETED_RUNS=$((COMPLETED_RUNS + 1))

  # Save METRIC lines from this run
  grep '^METRIC ' "$TMPOUT" > "$METRICS_DIR/run_$r" 2>/dev/null || true
  # Save non-METRIC output from last successful run
  grep -v '^METRIC ' "$TMPOUT" > "$METRICS_DIR/last_output" 2>/dev/null || true

  # Early stopping: after first run, compare to best known metric
  if [[ $r -eq 1 ]] && [[ $EARLY_STOP_PCT -gt 0 ]] && [[ -n "$BEST_METRIC" ]] && [[ -n "$PRIMARY_METRIC_NAME" ]]; then
    FIRST_METRIC=$(grep "^METRIC ${PRIMARY_METRIC_NAME}=" "$METRICS_DIR/run_1" 2>/dev/null | sed "s/^METRIC ${PRIMARY_METRIC_NAME}=//" | head -1)
    if [[ -n "$FIRST_METRIC" ]]; then
      SHOULD_STOP=$(echo "$FIRST_METRIC $BEST_METRIC $EARLY_STOP_PCT $BEST_DIRECTION" | awk '{
        cur=$1; best=$2; pct=$3; dir=$4
        if (dir == "lower") { delta = (cur - best) * 100 / best }
        else { delta = (best - cur) * 100 / best }
        if (delta > pct) print "yes"; else print "no"
      }')
      if [[ "$SHOULD_STOP" == "yes" ]]; then
        echo "Early stop: first run metric ($FIRST_METRIC) is >${EARLY_STOP_PCT}% worse than best ($BEST_METRIC). Skipping remaining runs." >&2
        break
      fi
    fi
  fi
done

TOTAL_END=$(date +%s%N)
DURATION=$(echo "scale=3; ($TOTAL_END - $TOTAL_START) / 1000000000" | bc)

# --- Output results ---
if [[ "$PASSED" == "true" ]] && [[ $RUNS -gt 1 ]]; then
  # Non-METRIC output from last run (compilation messages, progress, etc.)
  cat "$METRICS_DIR/last_output" 2>/dev/null

  # Compute median + stddev per metric across runs
  METRIC_NAMES=$(cat "$METRICS_DIR"/run_* 2>/dev/null | sed 's/^METRIC //' | cut -d= -f1 | sort -u)

  for name in $METRIC_NAMES; do
    VALUES=$(for ((i=1; i<=COMPLETED_RUNS; i++)); do
      grep "^METRIC ${name}=" "$METRICS_DIR/run_$i" 2>/dev/null | sed "s/^METRIC ${name}=//"
    done | sort -n)
    COUNT=$(echo "$VALUES" | wc -l | tr -d ' ')

    if [[ $COUNT -eq 0 ]]; then continue; fi

    # Median: for odd N, middle element; for even N, average of two middle elements
    if (( COUNT % 2 == 1 )); then
      MID=$(( (COUNT + 1) / 2 ))
      MEDIAN=$(echo "$VALUES" | sed -n "${MID}p")
    else
      MID1=$(( COUNT / 2 ))
      MID2=$(( MID1 + 1 ))
      V1=$(echo "$VALUES" | sed -n "${MID1}p")
      V2=$(echo "$VALUES" | sed -n "${MID2}p")
      MEDIAN=$(echo "scale=6; ($V1 + $V2) / 2" | bc)
    fi

    # Stddev
    STDDEV=$(echo "$VALUES" | awk '{s+=$1; ss+=$1*$1; n++} END {if(n>1) printf "%.2f", sqrt((ss - s*s/n)/(n-1)); else print "0"}')

    echo "METRIC ${name}=${MEDIAN}"
    [[ $COUNT -gt 1 ]] && echo "METRIC ${name}_stddev=${STDDEV}"
  done

  # Show individual runs to stderr for transparency
  echo "" >&2
  echo "Individual runs:" >&2
  for ((i=1; i<=COMPLETED_RUNS; i++)); do
    echo "  run $i: $(cat "$METRICS_DIR/run_$i" 2>/dev/null | tr '\n' ' ')" >&2
  done
else
  # Single run or failed — output as-is (backward compatible)
  cat "$TMPOUT"
fi

# --- Run guard if benchmark passed and guard file exists ---
GUARD_STATUS="skip"
GUARD_DURATION="0"
GUARD_OUTPUT_B64=""

if [[ "$PASSED" == "true" ]] && [[ -x "$GUARD_FILE" ]]; then
  GUARD_TMPOUT=$(mktemp)
  GUARD_START=$(date +%s%N)
  timeout "${GUARD_TIMEOUT}s" bash "$GUARD_FILE" > "$GUARD_TMPOUT" 2>&1
  GUARD_EXIT=$?
  GUARD_END=$(date +%s%N)

  GUARD_DURATION=$(echo "scale=3; ($GUARD_END - $GUARD_START) / 1000000000" | bc)

  if [[ $GUARD_EXIT -eq 0 ]]; then
    GUARD_STATUS="pass"
  else
    GUARD_STATUS="fail"
    PASSED=false
  fi

  # Encode last 80 lines of guard output for structured passing
  GUARD_OUTPUT_B64=$(tail -80 "$GUARD_TMPOUT" | base64)
  rm -f "$GUARD_TMPOUT"
fi

# --- Structured output ---
echo ""
echo "AUTORESEARCH_EXIT=$EXIT_CODE"
echo "AUTORESEARCH_DURATION=$DURATION"
echo "AUTORESEARCH_PASSED=$PASSED"
echo "AUTORESEARCH_CRASHED=$CRASHED"
echo "AUTORESEARCH_TIMED_OUT=$TIMED_OUT"
echo "AUTORESEARCH_GUARD=$GUARD_STATUS"
echo "AUTORESEARCH_GUARD_DURATION=$GUARD_DURATION"
echo "AUTORESEARCH_GUARD_OUTPUT=$GUARD_OUTPUT_B64"
echo "AUTORESEARCH_RUNS=$RUNS"
echo "AUTORESEARCH_WARMUP=$WARMUP"
