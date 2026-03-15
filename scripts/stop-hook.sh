#!/bin/bash
set -uo pipefail

# stop-hook.sh — Auto-resume autoresearch loop on context reset.
#
# Registered as a Stop hook. When Claude stops and an autoresearch state file
# matches the current session, this blocks the stop (exit 2) and injects
# a resume prompt via stderr.
#
# Safety:
# - maxExperiments checked from autoresearch.jsonl (the real cap)
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
STATE_CWD=$(echo "$FRONTMATTER" | grep '^cwd:' | sed 's/cwd: *//' | sed 's/^"\(.*\)"$/\1/')
LAST_RESUME=$(echo "$FRONTMATTER" | grep '^last_resume:' | sed 's/last_resume: *//')

# cwd check — skip if we're in the wrong project
ACTUAL_CWD=$(echo "$HOOK_INPUT" | jq -r '.cwd // empty')
if [[ -n "$STATE_CWD" ]] && [[ -n "$ACTUAL_CWD" ]] && [[ "$STATE_CWD" != "$ACTUAL_CWD" ]]; then
  log "cwd mismatch (state=$STATE_CWD, actual=$ACTUAL_CWD), skipping"
  exit 0
fi

# Check maxExperiments from JSONL (the real cap)
if [[ -n "$STATE_CWD" ]] && [[ -f "$STATE_CWD/autoresearch.jsonl" ]]; then
  JSONL="$STATE_CWD/autoresearch.jsonl"
  MAX_EXP=$(grep '"type":"config"' "$JSONL" 2>/dev/null | head -1 | jq -r '.maxExperiments // empty' 2>/dev/null)
  TOTAL_EXP=$(grep -c '"status"' "$JSONL" 2>/dev/null || echo 0)
  if [[ -n "$MAX_EXP" ]] && [[ "$MAX_EXP" =~ ^[0-9]+$ ]] && [[ $TOTAL_EXP -ge $MAX_EXP ]]; then
    log "maxExperiments reached ($TOTAL_EXP/$MAX_EXP), allowing exit"
    echo "Autoresearch [$SHORT_ID]: Reached $MAX_EXP experiments. Done."
    rm "$STATE_FILE"
    exit 0
  fi
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

# Update last_resume in state file
TEMP_FILE="${STATE_FILE}.tmp.$$"
if grep -q '^last_resume:' "$STATE_FILE"; then
  sed "s/^last_resume: .*/last_resume: $NOW/" "$STATE_FILE" > "$TEMP_FILE"
else
  sed "/^cwd:/a\\
last_resume: $NOW" "$STATE_FILE" > "$TEMP_FILE"
fi
mv "$TEMP_FILE" "$STATE_FILE"

# Build resume prompt
WORK_PROMPT=$(awk '/^---$/{i++; next} i>=2' "$STATE_FILE")

RESUME_MSG="## Autoresearch: Auto-Resume

${CRASH_WARNING:+$CRASH_WARNING

}$WORK_PROMPT"

log "Blocking stop, injecting resume prompt"

echo "$RESUME_MSG" >&2
exit 2
