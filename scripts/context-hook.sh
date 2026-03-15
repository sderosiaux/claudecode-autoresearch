#!/bin/bash
set -uo pipefail

# context-hook.sh — Inject autoresearch context on UserPromptSubmit.
#
# When autoresearch.jsonl exists in the current working directory:
# 1. Enforces loop continuation (DO NOT STOP)
# 2. Injects exploration behaviors (language/domain agnostic)

HOOK_INPUT=$(cat)

CWD=$(echo "$HOOK_INPUT" | jq -r '.cwd // empty')
if [[ -z "$CWD" ]]; then
  exit 0
fi

JSONL="$CWD/autoresearch.jsonl"
if [[ ! -f "$JSONL" ]]; then
  exit 0
fi

# Don't inject on stop commands
PROMPT=$(echo "$HOOK_INPUT" | jq -r '.prompt // empty')
if [[ "$PROMPT" =~ autoresearch:stop ]] || [[ "$PROMPT" =~ autoresearch-stop ]]; then
  exit 0
fi

# --- Session stats ---
TOTAL=$(grep -c '"status"' "$JSONL" 2>/dev/null) || TOTAL=0
KEPT=$(grep -c '"keep"' "$JSONL" 2>/dev/null) || KEPT=0
DISCARDED=$(grep -c '"discard"' "$JSONL" 2>/dev/null) || DISCARDED=0

# --- Hard cap on total experiments ---
CONFIG_LINE=$(grep '"type":"config"' "$JSONL" 2>/dev/null | head -1)
MAX_EXPERIMENTS=$(echo "$CONFIG_LINE" | jq -r '.maxExperiments // empty' 2>/dev/null)
if [[ -n "$MAX_EXPERIMENTS" ]] && [[ "$MAX_EXPERIMENTS" =~ ^[0-9]+$ ]] && [[ $TOTAL -ge $MAX_EXPERIMENTS ]]; then
  jq -nc --arg ctx "AUTORESEARCH COMPLETE. Reached max experiments ($MAX_EXPERIMENTS). $TOTAL runs, $KEPT kept. STOP NOW — run /claudecode-autoresearch:autoresearch-status for the final dashboard." \
    '{additionalContext: $ctx}'
  exit 0
fi

# --- Detect plateau (4/5 recent discards) ---
PLATEAU=""
if [[ $DISCARDED -ge 5 ]]; then
  LAST5=$(grep '"status"' "$JSONL" | tail -5 | jq -r '.status' 2>/dev/null)
  DISCARD_COUNT=$(echo "$LAST5" | grep -c 'discard' || true)
  if [[ "$DISCARD_COUNT" -ge 4 ]]; then
    PLATEAU=" PLATEAU WARNING (${DISCARD_COUNT}/5 recent discards): Stop tweaking. Re-read the source files from scratch. Try a structurally different approach."
  fi
fi

# --- Compose context ---
CHECKS=""
if [[ -x "$CWD/autoresearch.checks.sh" ]]; then
  CHECKS=" Backpressure checks active."
fi

IDEAS=""
if [[ -f "$CWD/autoresearch.ideas.md" ]]; then
  IDEAS=" Check autoresearch.ideas.md for queued ideas."
fi

ENFORCE="AUTORESEARCH MODE ACTIVE ($TOTAL runs, $KEPT kept).${CHECKS}${IDEAS}${PLATEAU}

RULES:
- DO NOT STOP. DO NOT ASK \"should I continue?\". DO NOT PAUSE.
- Run the next experiment immediately after logging the previous one.
- Read autoresearch.md for session context. Use the plugin scripts.

EXPLORATION:
- Measure before changing. Use the language's profiling/diagnostic tools to find where time or resources are actually spent.
- Look at actual output and behavior, not just source code.
- Try algorithmic and structural changes before micro-optimizations.
- When stuck: re-read the source files completely, question your assumptions, try the opposite of what you've been doing.
- Combine previous wins. Two small improvements may unlock a third.
- Keep a mental model of what the system is doing at runtime. Think about what the CPU, memory, compiler, or runtime is actually doing with your code."

jq -nc --arg ctx "$ENFORCE" '{additionalContext: $ctx}'
