---
description: Set up and run an autonomous experiment loop for any optimization target. Use when asked to "run autoresearch", "optimize X in a loop", "set up autoresearch", or "start experiments".
effort: max
---

# Autoresearch: Setup

Autonomous experiment loop: try ideas, keep what works, discard what doesn't, never stop.

## Scripts

All scripts are in the plugin. Reference them as:
- `${CLAUDE_PLUGIN_ROOT}/scripts/prepare-experiment.sh`
- `${CLAUDE_PLUGIN_ROOT}/scripts/run-experiment.sh`
- `${CLAUDE_PLUGIN_ROOT}/scripts/log-experiment.sh`
- `${CLAUDE_PLUGIN_ROOT}/scripts/status.sh`

## Setup Steps

1. Use `AskUserQuestion` to confirm: **Goal**, **Metric** (+ direction), **Files in scope**, **Constraints**, **Guard** (optional safety-net command that must always pass — e.g. `pnpm test`, `tsc --noEmit`). Do NOT infer constraints silently — ask the user what's off-limits. If they say "none", write "none". The environment (language version, runtime, OS) is always part of the optimization surface unless the user explicitly restricts it.
2. Create branch: `git checkout -b autoresearch/<goal>-<date>`
3. Read the source files in scope. Understand the workload deeply before writing anything.
4. Write `autoresearch.md` (session doc, template below) and `autoresearch.sh` (benchmark script). Commit both.
5. If a guard was specified (or constraints require correctness checks), write `autoresearch.checks.sh` — the guard script. This must exit 0 when everything is OK; non-zero blocks the keep. Commit it.
6. Install the pre-commit hook:
   ```bash
   cp "${CLAUDE_PLUGIN_ROOT}/scripts/pre-commit-hook.sh" .git/hooks/pre-commit
   ```
7. Write the config line to `autoresearch.jsonl`:
   ```bash
   echo '{"type":"config","name":"<name>","metricName":"<metric>","metricUnit":"<unit>","bestDirection":"<lower|higher>"}' > autoresearch.jsonl
   ```
8. Create the auto-resume state file:
   ```bash
   mkdir -p ~/.claude/states/autoresearch
   cat > ~/.claude/states/autoresearch/$(openssl rand -hex 4).md << 'STATEOF'
   ---
   session_id: "$SESSION_ID"
   cwd: "$(pwd)"
   last_resume: 0
   ---
   Resume the autoresearch experiment loop. Read autoresearch.md and git log for context. Check autoresearch.ideas.md if it exists. Be careful not to overfit to the benchmarks and do not cheat.
   STATEOF
   ```
   **IMPORTANT:** The `session_id` value must match the current Claude Code session ID.
9. **Profile & Classify** before any optimization.
   a. Profile the workload (perf, profiler, flamegraph, GC logs, iostat, strace — whatever fits).
   b. Classify bottleneck: CPU-compute, CPU-branch, Memory-bandwidth, Memory-allocation, I/O-read, I/O-write, Concurrency, Startup, External.
   c. Write the **Problem Profile** in autoresearch.md. Build a **Decision Tree** (see `${CLAUDE_PLUGIN_ROOT}/skills/create/REFERENCE.md` for the template table).
   d. **Cross-signal correlation**: join two profiler signals to find root causes at intersections (CPU x allocations, CPU x exceptions, alloc x thread-state, I/O x endpoints).
   e. **Characterize the search space**: document every tunable dimension in the "Search Space" section of autoresearch.md — type (continuous, discrete, categorical), range/values, dependencies.
   f. **Consult past experience**: `mdvault search "autoresearch technique <bottleneck-type>" --top-k 10` if available.
   g. **Algorithmic complexity audit**: For each hot function, state current Big-O and theoretical best Big-O. If current is worse by ≥1 level (e.g., O(N²) vs O(N log N)), the FIRST experiment MUST target the worst Big-O gap.
   h. **Headroom table**: For each bottleneck, estimate: (1) % of total runtime, (2) theoretical max speedup if fully optimized, (3) headroom = (1) × (2). Write this table in autoresearch.md. Attack the highest-headroom row first.
10. Run baseline: `${CLAUDE_PLUGIN_ROOT}/scripts/run-experiment.sh "./autoresearch.sh"`
11. Log baseline: `${CLAUDE_PLUGIN_ROOT}/scripts/log-experiment.sh keep <metric_value> "baseline"`
12. Start the main loop immediately. Follow the Loop Rules below.

### autoresearch.md template

```markdown
# Autoresearch: <goal>

## Objective
<What we're optimizing and the workload.>

## Metrics
- **Primary**: <name> (<unit>, lower/higher is better)
- **Secondary**: <name>, <name>, ...

## How to Run
`./autoresearch.sh` outputs `METRIC name=number` lines.
Log results with `${CLAUDE_PLUGIN_ROOT}/scripts/log-experiment.sh`.

## Files in Scope
<Every file the agent may modify, with a brief note on what it does.>

## Off Limits
<What must NOT be touched.>

## Guard
<Guard command if any, e.g. `pnpm test`. "none" if no guard.>

## Constraints
<ONLY constraints the user explicitly stated.>

## Search Space
| Dimension | Type | Range/Values | Dependencies |
|-----------|------|--------------|--------------|

**Active dimensions**: <N> | **Explored**: <list> | **Unexplored**: <list>

## Headroom Table
| Bottleneck | % Runtime | Max Speedup | Headroom | Current Big-O | Best Big-O |
|------------|-----------|-------------|----------|---------------|------------|

## Profiling Notes
<Where time/resources are actually spent. Update periodically.>

## Problem Profile
**Bottleneck classification**: <e.g. CPU-compute (72%), Memory-bandwidth>
**Current focus**: <bottleneck> -> trying <technique family> first.
**Pivot trigger**: If 3 discards in current focus, re-profile.

## What's Been Tried
<Update as experiments accumulate. Note key wins, dead ends.>
```

### autoresearch.sh

Bash script (`set -euo pipefail`): pre-checks fast (<1s), runs the benchmark, outputs `METRIC name=number` lines. **The benchmark scripts are IN SCOPE — modify them freely.** Extend them when a new optimization dimension requires it (JVM flags file, compiler flags, env vars). Do NOT skip a dimension because "the script doesn't support it" — make the script support it.

### autoresearch.checks.sh — the Guard (optional)

The guard is a safety net that must always pass. If the benchmark improves but the guard fails, the change is reverted (`guard_failed`). Common guards: `pnpm test`, `tsc --noEmit`, `pnpm build`. Create only when the user specifies a guard or constraints require it. Never modify the guard script during the loop.

## Loop Rules

**LOOP FOREVER.** Never ask "should I continue?" — the user expects autonomous work.

- **Primary metric is king.** Improved -> `keep`. Worse/equal -> `discard`.
- **Validate small gains.** Delta < 5% -> re-run with `runs 5`. If stddev > delta, discard.
- **Simpler is better.** Removing code for equal perf = keep.
- **Don't thrash.** Repeatedly reverting? Try something structurally different.
- **Guard failed?** Metric improved but guard failed → `guard_failed`, revert. Don't touch the guard script — adapt the implementation.
- **Crashes:** fix if trivial, otherwise log and move on.
- **Don't self-impose constraints.** The environment is part of the optimization surface unless the user said otherwise.
- **Resuming:** if `autoresearch.md` exists, read it + git log, continue looping.

Each iteration:
1. Choose what to try next. Consult: **Headroom table** -> **Decision Tree** -> **"What's Been Tried"** -> **`autoresearch.ideas.md`** -> **top kept experiments** (the context hook injects top 5 keeps as "population" — mutate from the best, not just the latest). **Always attack the highest-headroom bottleneck first.**
2. Edit code
3. **Prepare the experiment** (stages, validates atomicity, commits). This enables `git revert` on failure (preserving the attempt in history as memory):
   ```bash
   ${CLAUDE_PLUGIN_ROOT}/scripts/prepare-experiment.sh "<description>" [files...]
   ```
   If `AUTORESEARCH_PREPARED=false`, skip to next iteration.
4. `${CLAUDE_PLUGIN_ROOT}/scripts/run-experiment.sh "./autoresearch.sh" [timeout] [checks_timeout] [runs] [warmup] [early_stop_pct]`
   `runs` (default 1): N times, report median. `warmup` (default 0): untimed. `early_stop_pct` (default 0): abort remaining runs when first run is >N% worse than best. In Refinement phase, use `runs 5 warmup 1 early_stop_pct 20`.
5. Parse AUTORESEARCH_* output lines
6. **MANDATORY:** `${CLAUDE_PLUGIN_ROOT}/scripts/log-experiment.sh <status> <metric> "<description>"`
   **NEVER** manually append to JSONL or revert code. The script handles rollback.
6. On keep with >5% improvement, store in mdvault if available:
   ```bash
   mdvault remember "TECHNIQUE: <name> | BOTTLENECK: <type> | GAIN: <X>% | CONTEXT: <workload> | WORKS-WHEN: <conditions>" --namespace autoresearch/techniques
   ```
7. Update "What's Been Tried" in autoresearch.md periodically
8. Write deferred ideas to `autoresearch.ideas.md` using checkbox format (`- [ ]` / `- [x]`)
9. Every 3 discards in a row: re-profile, update Problem Profile. At 5 consecutive discards: trigger the escalation protocol (see below).
10. Cross-metric correlation every ~10 experiments if secondary metrics exist
11. Repeat

**When stuck (5+ consecutive discards), escalate in order:**
1. **Re-read ALL in-scope files** from scratch — you may have missed something
2. **Re-read the original goal/direction** — recalibrate
3. **Review full experiment history** (`git log --oneline -50`) — look for patterns in what worked vs failed
4. **Combine near-misses** — two changes that individually didn't help might work together
5. **Try the OPPOSITE** of what hasn't been working — if optimizing in one direction, reverse
6. **Radical architecture change** — rethink the approach entirely, different algorithm/structure

Also read `${CLAUDE_PLUGIN_ROOT}/skills/create/REFERENCE.md` for exploration strategies, dimension checklists, decision tree templates, and optimization patterns.

**NEVER STOP.** Keep going until interrupted.
