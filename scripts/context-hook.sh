#!/bin/bash
set -uo pipefail

# context-hook.sh — Inject autoresearch context on UserPromptSubmit.
#
# When autoresearch.jsonl exists in the current working directory:
# 1. Enforces loop continuation (DO NOT STOP)
# 2. Detects anti-patterns (wall, plateau, micro-opt streaks, stale ideas)
# 3. Builds tabu list from recent discard themes
# 4. Reminds to profile periodically
# 5. Tracks unchecked ideas in autoresearch.ideas.md
# 6. Injects exploration behaviors (language/domain agnostic)

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

# --- Config ---
CONFIG_LINE=$(grep '"type":"config"' "$JSONL" 2>/dev/null | head -1)

# --- Phase label ---
if [[ $TOTAL -lt 20 ]]; then
  PHASE="EXPLORATION"
elif [[ $TOTAL -lt 60 ]]; then
  PHASE="GUIDED"
else
  PHASE="REFINEMENT"
fi

# --- Top kept experiments (population for LLM context) ---
POPULATION=""
if [[ $KEPT -ge 2 ]]; then
  DIRECTION=$(echo "$CONFIG_LINE" | jq -r '.bestDirection // "lower"' 2>/dev/null)
  if [[ "$DIRECTION" == "lower" ]]; then
    SORT_FLAG=""  # ascending = best first for lower-is-better
  else
    SORT_FLAG="-r"  # descending = best first for higher-is-better
  fi
  TOP_KEEPS=$(grep '"keep"' "$JSONL" | jq -r '[.metric, .description] | @tsv' 2>/dev/null | sort -t$'\t' -k1 -n $SORT_FLAG | head -5)
  if [[ -n "$TOP_KEEPS" ]]; then
    POPULATION="
- POPULATION (top 5 keeps — mutate from these, not just the latest):
"
    while IFS=$'\t' read -r metric desc; do
      POPULATION="${POPULATION}  ${metric}: ${desc}"$'\n'
    done <<< "$TOP_KEEPS"
  fi
fi

# --- Warnings (accumulated, shown together) ---
WARNINGS=""

# Wall: 10+ consecutive discards = hard wall, need radical change
if [[ $TOTAL -ge 10 ]]; then
  LAST10=$(grep '"status"' "$JSONL" | tail -10 | jq -r '.status' 2>/dev/null)
  WALL_DISCARDS=$(echo "$LAST10" | grep -c 'discard' || true)
  if [[ "$WALL_DISCARDS" -ge 10 ]]; then
    WARNINGS="${WARNINGS}
- WALL HIT: 10 consecutive discards. You MUST: (1) Read \${CLAUDE_PLUGIN_ROOT}/skills/create/REFERENCE.md, (2) re-profile and show NEW data, (3) change compiler/runtime/language OR reformulate the problem entirely, (4) try ideas from autoresearch.ideas.md. Do NOT try another micro-variation."
  fi
fi

# Plateau: 4/5 recent discards (skip if wall already triggered)
if [[ $TOTAL -ge 5 ]] && [[ -z "$WARNINGS" ]]; then
  LAST5=$(grep '"status"' "$JSONL" | tail -5 | jq -r '.status' 2>/dev/null)
  DISCARD_COUNT=$(echo "$LAST5" | grep -c 'discard' || true)
  if [[ "$DISCARD_COUNT" -ge 4 ]]; then
    WARNINGS="${WARNINGS}
- PLATEAU: ${DISCARD_COUNT}/5 recent discards. MANDATORY ACTIONS:
  1. Re-profile the workload NOW — the bottleneck may have shifted.
  2. Update the Problem Profile and Decision Tree in autoresearch.md.
  3. Read \${CLAUDE_PLUGIN_ROOT}/skills/create/REFERENCE.md for exploration strategies and patterns.
  4. Pick a technique from a DIFFERENT bottleneck category than your last attempts."
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

# Convergence: 15+ consecutive non-keeps = likely converged
if [[ $TOTAL -ge 15 ]]; then
  TAIL_DISCARDS=0
  while IFS= read -r s; do
    case "$s" in
      keep) break ;;
      *) TAIL_DISCARDS=$((TAIL_DISCARDS + 1)) ;;
    esac
  done < <(grep '"status"' "$JSONL" | jq -r '.status' 2>/dev/null | tac)
  if [[ $TAIL_DISCARDS -ge 15 ]]; then
    WARNINGS="${WARNINGS}
- CONVERGENCE: ${TAIL_DISCARDS} consecutive non-keeps. You are likely at the local optimum. Options: (1) Read \${CLAUDE_PLUGIN_ROOT}/skills/create/REFERENCE.md for unexplored layers, (2) run /autoresearch:stop if satisfied."
  fi
fi

# Tabu: list last 10 discards so the LLM can spot patterns itself
if [[ $DISCARDED -ge 5 ]]; then
  RECENT_DISCARDS=$(grep '"discard"' "$JSONL" | tail -10 | jq -r '.description // empty' 2>/dev/null)
  if [[ -n "$RECENT_DISCARDS" ]]; then
    TABU_LIST=""
    while IFS= read -r desc; do
      [[ -n "$desc" ]] && TABU_LIST="${TABU_LIST}  - ${desc}"$'\n'
    done <<< "$RECENT_DISCARDS"
    WARNINGS="${WARNINGS}
- RECENT DISCARDS (do NOT repeat these patterns):
${TABU_LIST}  Check autoresearch.md 'What's Been Tried' for the full history before choosing your next experiment."
  fi
fi

# Profiling reminder: every ~15 experiments since last profiling mention
if [[ $TOTAL -ge 10 ]]; then
  LAST_PROFILE_IDX=$(grep -n '"status"' "$JSONL" | tail -"$TOTAL" | grep -in 'profil' | tail -1 | cut -d: -f1)
  if [[ -z "$LAST_PROFILE_IDX" ]]; then
    LAST_PROFILE_IDX=0
  fi
  SINCE_PROFILE=$((TOTAL - LAST_PROFILE_IDX))
  if [[ $SINCE_PROFILE -ge 15 ]]; then
    WARNINGS="${WARNINGS}
- PROFILING DUE: Last profiling was ${SINCE_PROFILE} experiments ago. Profile the workload NOW (perf stat, flamegraph, phase timing, etc.) and update Profiling Notes in autoresearch.md before the next experiment. Data-driven beats guessing."
  fi
fi

# Stale ideas: ideas file exists with untried ideas
UNCHECKED_IDEAS=0
if [[ -f "$CWD/autoresearch.ideas.md" ]]; then
  # Count unchecked checkboxes: - [ ] (preferred format)
  UNCHECKED_IDEAS=$(grep -c '^\s*- \[ \]' "$CWD/autoresearch.ideas.md" 2>/dev/null || true)

  # Fallback: count lines under "High potential" or "High Priority" starting with -
  if [[ $UNCHECKED_IDEAS -eq 0 ]]; then
    UNCHECKED_IDEAS=$(sed -n '/^## High [Pp]/,/^##/p' "$CWD/autoresearch.ideas.md" 2>/dev/null | grep -c '^-' || true)
  fi

  if [[ $UNCHECKED_IDEAS -gt 0 ]] && [[ $SMALL_COUNT -ge 3 ]] 2>/dev/null; then
    # Micro-opt streak + untried ideas = hard directive
    WARNINGS="${WARNINGS}
- MANDATORY: You have ${UNCHECKED_IDEAS} untried ideas AND you are in a micro-opt streak. STOP inventing small tweaks. Your next experiment MUST come from autoresearch.ideas.md. Mark tried ideas with [x]."
  elif [[ $UNCHECKED_IDEAS -gt 3 ]] && [[ $KEPT -gt 0 ]]; then
    KEEP_RATE=$((100 * KEPT / TOTAL))
    if [[ $KEEP_RATE -lt 15 ]]; then
      WARNINGS="${WARNINGS}
- LOW KEEP RATE (${KEEP_RATE}%) + ${UNCHECKED_IDEAS} UNTRIED IDEAS: Your hit rate is low. Try ideas from autoresearch.ideas.md before inventing new ones. Mark tried ideas with [x]."
    elif [[ $UNCHECKED_IDEAS -gt 0 ]]; then
      WARNINGS="${WARNINGS}
- UNTRIED IDEAS: ${UNCHECKED_IDEAS} unchecked ideas in autoresearch.ideas.md. Consider trying one. Mark tried ideas with [x]."
    fi
  fi
fi

# --- Compose context ---
CHECKS=""
if [[ -x "$CWD/autoresearch.checks.sh" ]]; then
  CHECKS=" Backpressure checks active."
fi

# Dimension importance analysis nudge every 15 experiments
DIM_IMPORTANCE=""
if [[ $TOTAL -ge 15 ]] && [[ $((TOTAL % 15)) -eq 0 ]]; then
  DIM_IMPORTANCE="
- DIMENSION IMPORTANCE ANALYSIS DUE ($TOTAL experiments). Parse autoresearch.jsonl: tag each experiment by dimension modified, compute keep-rate and avg improvement per dimension. Rank by actual impact. Update 'Search Space' explored/unexplored in autoresearch.md. Focus the next experiments on the top 2-3 dimensions by measured impact."
fi

# Dimension audit nudge every 10 experiments
DIM_AUDIT=""
if [[ $TOTAL -gt 0 ]] && [[ $((TOTAL % 10)) -eq 0 ]]; then
  DIM_AUDIT="
- DIMENSION AUDIT DUE ($TOTAL experiments). List every optimization layer you have NOT tried: language version, runtime flags, build pipeline (PGO/AOT/CDS), OS/kernel, hardware features (lscpu), data layout, algorithm class, elimination (skip work entirely), problem reformulation (relax precision, approximate), I/O strategy, parallelism, pipeline reordering, cross-language hot path (JNI/FFI), library swaps, measurement methodology. Your next experiment MUST come from an unexplored layer."
fi

ENFORCE="AUTORESEARCH MODE ACTIVE ($TOTAL runs, $KEPT kept) | Phase: $PHASE.${CHECKS}${POPULATION}${WARNINGS}${DIM_IMPORTANCE}${DIM_AUDIT}

RULES:
- DO NOT STOP. DO NOT ASK \"should I continue?\". DO NOT PAUSE.
- Run the next experiment immediately after logging the previous one.
- ALWAYS use log-experiment.sh to record results. NEVER manually echo/append to autoresearch.jsonl. NEVER manually revert code. The script handles git commit/revert.
- Name the active cognitive strategy (tabu, annealing, crossover, etc.) before each experiment.
- Think before running — don't waste experiments on things you can reason about.
- Try structural changes before micro-optimizations.
- Every 10 experiments: audit which optimization LAYERS you haven't touched (runtime, build pipeline, OS, hardware, data layout, algorithm, elimination, problem reformulation, I/O, parallelism, pipeline order, cross-language, tooling)."

jq -nc --arg ctx "$ENFORCE" '{additionalContext: $ctx}'
