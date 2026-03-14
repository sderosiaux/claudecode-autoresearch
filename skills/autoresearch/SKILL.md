---
description: Resume or continue an active autoresearch experiment loop. Use when autoresearch.md exists, or to resume after a pause/context reset.
---

# Autoresearch: Resume Loop

You are resuming an active autoresearch experiment loop.

## First Steps

1. Read `autoresearch.md` — understand objective, metrics, files in scope, what's been tried
2. Read `autoresearch.jsonl` — check recent results (last 10 lines)
3. Run `git log --oneline -n 10` — see recent experiment commits
4. If `autoresearch.ideas.md` exists, check it for promising paths. Prune stale entries.
5. Run `${CLAUDE_PLUGIN_ROOT}/scripts/status.sh` to see dashboard

## Loop

Each iteration:
1. Think about what to try next (avoid repeating failed approaches from "What's Been Tried")
2. Edit code (do NOT commit — log-experiment handles git)
3. `${CLAUDE_PLUGIN_ROOT}/scripts/run-experiment.sh "./autoresearch.sh"`
4. Parse AUTORESEARCH_* output lines to get metric and status
5. Determine status:
   - AUTORESEARCH_CRASHED=true -> `crash`
   - AUTORESEARCH_CHECKS=fail -> `checks_failed`
   - Metric improved (check direction) -> `keep`
   - Metric worse or equal -> `discard`
6. `${CLAUDE_PLUGIN_ROOT}/scripts/log-experiment.sh <status> <metric> "<description>" '[optional metrics json]'`
   (commits on keep, reverts working tree on discard/crash)
7. Update "What's Been Tried" in autoresearch.md every ~5 iterations
8. Write promising deferred ideas to `autoresearch.ideas.md`
9. Repeat

**LOOP FOREVER. NEVER STOP. NEVER ASK "should I continue?"**

Be careful not to overfit to the benchmarks and do not cheat.
