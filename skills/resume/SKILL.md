---
description: Resume or continue an active autoresearch experiment loop. Use when autoresearch.md exists, or to resume after a pause/context reset.
---

# Autoresearch: Resume Loop

You are resuming an active autoresearch experiment loop after a context reset or pause.

## Re-orient

1. Read `autoresearch.md` — understand objective, metrics, files in scope, what's been tried
2. Read `autoresearch.jsonl` — check recent results (last 10 lines)
3. Run `git log --oneline -n 10` — see recent experiment commits
4. If `autoresearch.ideas.md` exists, check it for promising untried ideas. Prune stale entries.
5. Run `${CLAUDE_PLUGIN_ROOT}/scripts/status.sh` to see dashboard
6. Determine status for next experiment:
   - AUTORESEARCH_CRASHED=true -> `crash`
   - AUTORESEARCH_CHECKS=fail -> `checks_failed`
   - Metric improved (check direction in config) -> `keep`
   - Metric worse or equal -> `discard`

## Then Loop

Read the **Loop Rules** and **Exploration Discipline** sections from `${CLAUDE_PLUGIN_ROOT}/skills/create/SKILL.md`. Follow them exactly. Start the next experiment immediately.

**LOOP FOREVER. NEVER STOP. NEVER ASK "should I continue?"**

Be careful not to overfit to the benchmarks and do not cheat.
