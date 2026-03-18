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

# --- Hard cap on total experiments ---
CONFIG_LINE=$(grep '"type":"config"' "$JSONL" 2>/dev/null | head -1)
MAX_EXPERIMENTS=$(echo "$CONFIG_LINE" | jq -r '.maxExperiments // empty' 2>/dev/null)
if [[ -n "$MAX_EXPERIMENTS" ]] && [[ "$MAX_EXPERIMENTS" =~ ^[0-9]+$ ]] && [[ $TOTAL -ge $MAX_EXPERIMENTS ]]; then
  jq -nc --arg ctx "AUTORESEARCH COMPLETE. Reached max experiments ($MAX_EXPERIMENTS). $TOTAL runs, $KEPT kept. STOP NOW — run /autoresearch:status for the final dashboard." \
    '{additionalContext: $ctx}'
  exit 0
fi

# --- Warnings (accumulated, shown together) ---
WARNINGS=""

# Wall: 10+ consecutive discards = hard wall, need radical change
if [[ $TOTAL -ge 10 ]]; then
  LAST10=$(grep '"status"' "$JSONL" | tail -10 | jq -r '.status' 2>/dev/null)
  WALL_DISCARDS=$(echo "$LAST10" | grep -c 'discard' || true)
  if [[ "$WALL_DISCARDS" -ge 10 ]]; then
    WARNINGS="${WARNINGS}
- WALL HIT: 10 consecutive discards. You MUST do one of: (1) profile the workload and show NEW data you haven't seen before, (2) change compiler/runtime/language, (3) reformulate the problem entirely (different algorithm, different data structure), (4) try ideas from autoresearch.ideas.md. Do NOT try another micro-variation of something that already failed."
  fi
fi

# Plateau: 4/5 recent discards (skip if wall already triggered)
if [[ $TOTAL -ge 5 ]] && [[ -z "$WARNINGS" ]]; then
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
- CONVERGENCE: ${TAIL_DISCARDS} consecutive non-keeps. You are likely at the local optimum. Options: (1) try a RADICALLY different architecture or layer, (2) run /autoresearch:stop if satisfied, (3) read the performance handbook for orthogonal ideas."
  fi
fi

# Tabu enforcer: extract themes from recent discards to prevent repeating failures
if [[ $DISCARDED -ge 5 ]]; then
  RECENT_DISCARDS=$(grep '"discard"' "$JSONL" | tail -20 | jq -r '.description // empty' 2>/dev/null | tr '[:upper:]' '[:lower:]')
  TABU_THEMES=""
  for theme in prefetch madvise map_populate mlock vectoriz "inline asm" cmov branchless \
               pgo lto funroll "cache.line" "cache.align" aligned noinline restrict \
               swar avx simd "insertion sort" "counting sort" "selection network" \
               "stack.alloc" "buffer.size" "huge.page" "cpu.pin" "branch.hint" \
               "builtin_expect" "code.layout" "-Os" "-O2" "clang"; do
    PATTERN=$(echo "$theme" | sed 's/\./[_ ]/g')
    COUNT=$(echo "$RECENT_DISCARDS" | grep -c "$PATTERN" 2>/dev/null || true)
    if [[ $COUNT -ge 2 ]]; then
      TABU_THEMES="${TABU_THEMES} ${theme}(${COUNT}x)"
    fi
  done
  if [[ -n "$TABU_THEMES" ]]; then
    WARNINGS="${WARNINGS}
- TABU (already failed recently):${TABU_THEMES}. Do NOT try variations of these unless you have a fundamentally new reason backed by fresh profiling data."
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

# Dimension audit nudge every 10 experiments
DIM_AUDIT=""
if [[ $TOTAL -gt 0 ]] && [[ $((TOTAL % 10)) -eq 0 ]]; then
  DIM_AUDIT="
- DIMENSION AUDIT DUE ($TOTAL experiments). List every optimization layer you have NOT tried: language version, runtime flags, build pipeline (PGO/AOT/CDS), OS/kernel, hardware features (lscpu), data layout, algorithm class, elimination (skip work entirely), problem reformulation (relax precision, approximate), I/O strategy, parallelism, pipeline reordering, cross-language hot path (JNI/FFI), library swaps, measurement methodology. Your next experiment MUST come from an unexplored layer."
fi

ENFORCE="AUTORESEARCH MODE ACTIVE ($TOTAL runs, $KEPT kept).${CHECKS}${WARNINGS}${DIM_AUDIT}

RULES:
- DO NOT STOP. DO NOT ASK \"should I continue?\". DO NOT PAUSE.
- Run the next experiment immediately after logging the previous one.
- ALWAYS use log-experiment.sh to record results. NEVER manually echo/append to autoresearch.jsonl. NEVER manually revert code. The script handles git commit/revert.
- Name the active cognitive strategy (tabu, annealing, crossover, etc.) before each experiment.
- Think before running — don't waste experiments on things you can reason about.
- Try structural changes before micro-optimizations.
- Every 10 experiments: audit which optimization LAYERS you haven't touched (runtime, build pipeline, OS, hardware, data layout, algorithm, elimination, problem reformulation, I/O, parallelism, pipeline order, cross-language, tooling)."

jq -nc --arg ctx "$ENFORCE" '{additionalContext: $ctx}'
