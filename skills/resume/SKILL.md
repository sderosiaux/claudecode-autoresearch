---
description: Resume or continue an active autoresearch experiment loop. Use when autoresearch.md exists, or to resume after a pause/context reset.
effort: max
---

# Autoresearch: Resume Loop

You are resuming an active autoresearch experiment loop after a context reset or pause.

## Re-orient

1. Read `autoresearch.md` — understand objective, metrics, files in scope, **Problem Profile**, **Decision Tree**, what's been tried, landscape model, tabu list
2. Run `${CLAUDE_PLUGIN_ROOT}/scripts/status.sh` — see dashboard with keep rate trends and improvement curve
3. Read `autoresearch.jsonl` — last 20 lines. Note the keep rate of the last 20 experiments. If < 15% keeps, you're likely converging — focus on layer changes, not micro-opts.
4. If `autoresearch.ideas.md` exists, read it fully. Focus on untried ideas. Prune stale entries.
5. Run `git log --oneline -n 10` — see recent experiment commits
6. **Consult past experience**: for each bottleneck in the Problem Profile, run `mdvault search "autoresearch technique <bottleneck-type>" --top-k 5`. Also run `mdvault search "autoresearch anti-pattern <bottleneck-type>" --top-k 5` to avoid known dead ends. Add relevant findings to `autoresearch.ideas.md`.
7. Identify what layers have NOT been explored (check autoresearch.md "Dimension Audit" if it exists). Your first experiments after resume should target unexplored layers.
8. Determine status for next experiment:
   - AUTORESEARCH_CRASHED=true -> `crash`
   - AUTORESEARCH_GUARD=fail -> `guard_failed`
   - Metric improved (check direction in config) -> `keep`
   - Metric worse or equal -> `discard`

## Then Loop

Read the **Loop Rules** from `${CLAUDE_PLUGIN_ROOT}/skills/create/SKILL.md`. If keep rate < 15% or you feel stuck, also read `${CLAUDE_PLUGIN_ROOT}/skills/create/REFERENCE.md` for exploration strategies and patterns. Start the next experiment immediately.

**LOOP FOREVER. NEVER STOP. NEVER ASK "should I continue?"**

Be careful not to overfit to the benchmarks and do not cheat.
