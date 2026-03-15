#!/bin/bash
set -uo pipefail

# stop-hook.sh — Auto-resume autoresearch loop on context reset.
#
# Registered as a Stop hook. When Claude stops and an autoresearch state file
# matches the current session, this blocks the stop (exit 2) and injects
# a resume prompt via stderr.
#
# Safety:
# - max_iterations limit (default 50)
# - Rate limit: min 30s between resumes
# - Crash detection: warns after 3 consecutive crashes
# - cwd mismatch check

LOG_FILE="$HOME/.claude/logs/autoresearch-stop.log"
mkdir -p "$(dirname "$LOG_FILE")"

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$LOG_FILE"
}

log "=== Autoresearch stop hook triggered ==="

HOOK_INPUT=$(cat)
log "Input: $HOOK_INPUT"

SESSION_ID=$(echo "$HOOK_INPUT" | jq -r '.session_id // empty')
if [[ -z "$SESSION_ID" ]]; then
  exit 0
fi

SHORT_ID="${SESSION_ID:0:8}"

# Find matching state file
STATE_DIR="$HOME/.claude/states/autoresearch"
STATE_FILE=""
shopt -s nullglob
for f in "$STATE_DIR"/*.md; do
  if [[ -f "$f" ]]; then
    FILE_SESSION=$(sed -n '/^---$/,/^---$/{ /^---$/d; p; }' "$f" | grep '^session_id:' | sed 's/session_id: *//' | sed 's/^"\(.*\)"$/\1/')
    if [[ "$FILE_SESSION" == "$SESSION_ID" ]]; then
      STATE_FILE="$f"
      break
    fi
  fi
done

if [[ -z "$STATE_FILE" ]]; then
  log "No matching state file, allowing exit"
  exit 0
fi

log "Found state file: $STATE_FILE"

# Parse frontmatter
FRONTMATTER=$(sed -n '/^---$/,/^---$/{ /^---$/d; p; }' "$STATE_FILE")
ITERATION=$(echo "$FRONTMATTER" | grep '^iteration:' | sed 's/iteration: *//')
MAX_ITERATIONS=$(echo "$FRONTMATTER" | grep '^max_iterations:' | sed 's/max_iterations: *//')
STATE_CWD=$(echo "$FRONTMATTER" | grep '^cwd:' | sed 's/cwd: *//' | sed 's/^"\(.*\)"$/\1/')
LAST_RESUME=$(echo "$FRONTMATTER" | grep '^last_resume:' | sed 's/last_resume: *//')

# Validate
if [[ ! "$ITERATION" =~ ^[0-9]+$ ]]; then
  log "Corrupted state (invalid iteration), cleaning up"
  rm "$STATE_FILE"
  exit 0
fi

MAX_ITERATIONS="${MAX_ITERATIONS:-10}"
if [[ ! "$MAX_ITERATIONS" =~ ^[0-9]+$ ]]; then
  MAX_ITERATIONS=10
fi

# cwd check — skip if we're in the wrong project
ACTUAL_CWD=$(echo "$HOOK_INPUT" | jq -r '.cwd // empty')
if [[ -n "$STATE_CWD" ]] && [[ -n "$ACTUAL_CWD" ]] && [[ "$STATE_CWD" != "$ACTUAL_CWD" ]]; then
  log "cwd mismatch (state=$STATE_CWD, actual=$ACTUAL_CWD), skipping"
  exit 0
fi

# Max iterations check
if [[ $ITERATION -ge $MAX_ITERATIONS ]]; then
  echo "Autoresearch [$SHORT_ID]: Reached max iterations ($MAX_ITERATIONS). Loop ended."
  rm "$STATE_FILE"
  exit 0
fi

# Rate limit: min 30s between resumes
NOW=$(date +%s)
LAST_RESUME="${LAST_RESUME:-0}"
if [[ $((NOW - LAST_RESUME)) -lt 30 ]]; then
  log "Rate limited (last resume ${LAST_RESUME}, now ${NOW}), allowing exit"
  rm "$STATE_FILE"
  exit 0
fi

# Crash detection: check last 3 results in JSONL
CRASH_WARNING=""
if [[ -n "$STATE_CWD" ]] && [[ -f "$STATE_CWD/autoresearch.jsonl" ]]; then
  LAST3=$(grep '"status"' "$STATE_CWD/autoresearch.jsonl" | tail -3 | jq -r '.status' 2>/dev/null)
  CRASH_COUNT=$(echo "$LAST3" | grep -c -E 'crash|checks_failed' || true)
  if [[ "$CRASH_COUNT" -ge 3 ]]; then
    CRASH_WARNING="WARNING: Last 3 experiments crashed or failed checks. Consider a structurally different approach. Read autoresearch.md carefully before continuing."
  fi
fi

# Update state file: increment iteration, update last_resume
NEXT_ITERATION=$((ITERATION + 1))
TEMP_FILE="${STATE_FILE}.tmp.$$"
sed -e "s/^iteration: .*/iteration: $NEXT_ITERATION/" \
    -e "s/^last_resume: .*/last_resume: $NOW/" "$STATE_FILE" > "$TEMP_FILE"
# Add last_resume if not present
if ! grep -q '^last_resume:' "$TEMP_FILE"; then
  sed -i '' "s/^iteration: $NEXT_ITERATION/iteration: $NEXT_ITERATION\nlast_resume: $NOW/" "$TEMP_FILE"
fi
mv "$TEMP_FILE" "$STATE_FILE"

# Build resume prompt
WORK_PROMPT=$(awk '/^---$/{i++; next} i>=2' "$STATE_FILE")

RESUME_MSG="## Autoresearch: Auto-Resume (iteration $NEXT_ITERATION/$MAX_ITERATIONS)

${CRASH_WARNING:+$CRASH_WARNING

}$WORK_PROMPT"

log "Blocking stop, injecting resume prompt for iteration $NEXT_ITERATION"

echo "$RESUME_MSG" >&2
exit 2
