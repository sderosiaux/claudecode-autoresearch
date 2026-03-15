#!/bin/bash
set -uo pipefail

# context-hook.sh — Inject autoresearch context on UserPromptSubmit.
#
# When autoresearch.jsonl exists in the current working directory:
# 1. Enforces loop continuation (DO NOT STOP)
# 2. Detects anti-patterns (plateau, micro-opt streaks, stale ideas)
# 3. Injects exploration behaviors (language/domain agnostic)

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

# --- Warnings (accumulated, shown together) ---
WARNINGS=""

# Plateau: 4/5 recent discards
if [[ $TOTAL -ge 5 ]]; then
  LAST5=$(grep '"status"' "$JSONL" | tail -5 | jq -r '.status' 2>/dev/null)
  DISCARD_COUNT=$(echo "$LAST5" | grep -c 'discard' || true)
  if [[ "$DISCARD_COUNT" -ge 4 ]]; then
    WARNINGS="${WARNINGS}
- PLATEAU: ${DISCARD_COUNT}/5 recent discards. Stop tweaking. Re-read source files from scratch. Try a structurally different approach."
  fi
fi

# Micro-opt streak: last 3 keeps all <5% improvement
SMALL_COUNT=0
if [[ $KEPT -ge 4 ]]; then
  # Get last 4 keep metrics to compute 3 deltas
  LAST4_KEEPS=$(grep '"keep"' "$JSONL" | tail -4 | jq -r '.metric' 2>/dev/null)
  if [[ $(echo "$LAST4_KEEPS" | wc -l) -ge 4 ]]; then
    DIRECTION=$(echo "$CONFIG_LINE" | jq -r '.bestDirection // "lower"' 2>/dev/null)
    SMALL_COUNT=0
    PREV=""
    while IFS= read -r m; do
      if [[ -n "$PREV" ]]; then
        if [[ "$DIRECTION" == "lower" ]]; then
          PCT=$(echo "scale=1; ($PREV - $m) * 100 / $PREV" | bc 2>/dev/null) || PCT="0"
        else
          PCT=$(echo "scale=1; ($m - $PREV) * 100 / $PREV" | bc 2>/dev/null) || PCT="0"
        fi
        # Check if < 5%
        IS_SMALL=$(echo "$PCT < 5" | bc 2>/dev/null) || IS_SMALL=0
        if [[ "$IS_SMALL" -eq 1 ]]; then
          SMALL_COUNT=$((SMALL_COUNT + 1))
        fi
      fi
      PREV="$m"
    done <<< "$LAST4_KEEPS"
    if [[ $SMALL_COUNT -ge 3 ]]; then
      WARNINGS="${WARNINGS}
- MICRO-OPT STREAK: Last 3 improvements were all <5%. You are in diminishing returns. Profile the workload to find where time is actually spent, or try a fundamentally different architecture."
    fi
  fi
fi

# Stale ideas: ideas file exists with untried ideas
HIGH_IDEAS=0
if [[ -f "$CWD/autoresearch.ideas.md" ]]; then
  # Count lines under "High potential" or "High Priority" that start with -
  HIGH_IDEAS=$(sed -n '/^## High [Pp]/,/^##/p' "$CWD/autoresearch.ideas.md" 2>/dev/null | grep -c '^-' || true)
  if [[ $HIGH_IDEAS -gt 0 ]] && [[ $SMALL_COUNT -ge 3 ]] 2>/dev/null; then
    # Micro-opt streak + untried ideas = hard directive
    WARNINGS="${WARNINGS}
- MANDATORY: You have ${HIGH_IDEAS} untried high-priority ideas AND you are in a micro-opt streak. STOP inventing small tweaks. Your next experiment MUST come from autoresearch.ideas.md."
  elif [[ $HIGH_IDEAS -gt 0 ]]; then
    WARNINGS="${WARNINGS}
- UNTRIED IDEAS: You have ${HIGH_IDEAS} high-priority ideas in autoresearch.ideas.md. Try one before generating new micro-optimizations."
  fi
fi

# --- Compose context ---
CHECKS=""
if [[ -x "$CWD/autoresearch.checks.sh" ]]; then
  CHECKS=" Backpressure checks active."
fi

ENFORCE="AUTORESEARCH MODE ACTIVE ($TOTAL runs, $KEPT kept).${CHECKS}${WARNINGS}

RULES:
- DO NOT STOP. DO NOT ASK \"should I continue?\". DO NOT PAUSE.
- Run the next experiment immediately after logging the previous one.
- Read autoresearch.md for session context. Use the plugin scripts.

EXPLORATION:
- Think before running. Could you know the answer without an experiment? Purely syntactic changes rarely matter — don't waste runs on them.
- Measure before changing. Use the language's profiling/diagnostic tools to find where time or resources are actually spent.
- Look at actual output and behavior, not just source code.
- Try algorithmic and structural changes before micro-optimizations.
- When stuck: re-read the source files completely, question your assumptions, try the opposite of what you've been doing.
- Combine previous wins — occasionally bundle 2-3 small ideas into one experiment to test synergies.
- If your metric variance is larger than your improvements, increase iterations or add statistical analysis (multiple runs, median).
- Keep a mental model of what the system is doing at runtime — what the CPU, memory, compiler, or runtime is actually doing with your code."

jq -nc --arg ctx "$ENFORCE" '{additionalContext: $ctx}'
