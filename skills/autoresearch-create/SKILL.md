---
description: Set up and run an autonomous experiment loop for any optimization target. Use when asked to "run autoresearch", "optimize X in a loop", "set up autoresearch", or "start experiments".
---

# Autoresearch: Setup

Autonomous experiment loop: try ideas, keep what works, discard what doesn't, never stop.

## Scripts

All scripts are in the plugin. Reference them as:
- `${CLAUDE_PLUGIN_ROOT}/scripts/run-experiment.sh`
- `${CLAUDE_PLUGIN_ROOT}/scripts/log-experiment.sh`
- `${CLAUDE_PLUGIN_ROOT}/scripts/status.sh`

## Setup Steps

1. Ask (or infer from context): **Goal**, **Command**, **Metric** (+ direction), **Files in scope**, **Constraints**.
2. Create branch: `git checkout -b autoresearch/<goal>-<date>`
3. Read the source files in scope. Understand the workload deeply before writing anything.
4. Write `autoresearch.md` (session doc) and `autoresearch.sh` (benchmark script). Commit both.
5. If constraints require correctness checks (tests must pass, types must check), write `autoresearch.checks.sh`. Commit it.
6. Write the config line to `autoresearch.jsonl`:
   ```bash
   echo '{"type":"config","name":"<name>","metricName":"<metric>","metricUnit":"<unit>","bestDirection":"<lower|higher>","maxExperiments":100}' > autoresearch.jsonl
   ```
7. Create the auto-resume state file:
   ```bash
   mkdir -p ~/.claude/states/autoresearch
   cat > ~/.claude/states/autoresearch/$(openssl rand -hex 4).md << 'STATEOF'
   ---
   session_id: "$SESSION_ID"
   iteration: 0
   max_iterations: 10
   cwd: "$(pwd)"
   started_at: "$(date -u +%Y-%m-%dT%H:%M:%S)"
   last_resume: 0
   ---
   Resume the autoresearch experiment loop. Read autoresearch.md and git log for context. Check autoresearch.ideas.md if it exists. Be careful not to overfit to the benchmarks and do not cheat.
   STATEOF
   ```
   **IMPORTANT:** The `session_id` value must match the current Claude Code session ID. Check for it in the environment or context.
8. Run baseline: `${CLAUDE_PLUGIN_ROOT}/scripts/run-experiment.sh "./autoresearch.sh"`
9. Log baseline: `${CLAUDE_PLUGIN_ROOT}/scripts/log-experiment.sh keep <metric_value> "baseline"`
10. Start the main loop immediately. Follow the Loop Rules below.

### autoresearch.md

This is the heart of the session. A fresh agent with zero context should be able to read this file and run the loop effectively.

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

## Constraints
<Hard rules: tests must pass, no new deps, etc.>

## What's Been Tried
<Update as experiments accumulate. Note key wins, dead ends, architectural insights.>
```

### autoresearch.sh

Bash script (`set -euo pipefail`) that: pre-checks fast (<1s), runs the benchmark, outputs `METRIC name=number` lines. Keep it fast.

### autoresearch.checks.sh (optional)

Bash script for backpressure checks: tests, types, lint. Only create when constraints require it. Keep output minimal (suppress success, show only errors).

## Loop Rules

**LOOP FOREVER.** Never ask "should I continue?" — the user expects autonomous work.

- **Primary metric is king.** Improved -> `keep`. Worse/equal -> `discard`.
- **Simpler is better.** Removing code for equal perf = keep.
- **Don't thrash.** Repeatedly reverting? Try something structurally different.
- **Crashes:** fix if trivial, otherwise log and move on.
- **Think longer when stuck.** Re-read source files, reason about what the CPU/system is doing.
- **Resuming:** if `autoresearch.md` exists, read it + git log, continue looping.

Each iteration:
1. Think about what to try next (read autoresearch.md "What's Been Tried")
2. Edit code (do NOT commit yet)
3. `${CLAUDE_PLUGIN_ROOT}/scripts/run-experiment.sh "./autoresearch.sh"`
4. Parse the AUTORESEARCH_* output lines
5. `${CLAUDE_PLUGIN_ROOT}/scripts/log-experiment.sh <status> <metric> "<description>"`
   (log-experiment handles git: commits on keep, reverts on discard/crash)
6. Update "What's Been Tried" in autoresearch.md periodically
7. Write promising deferred ideas to `autoresearch.ideas.md`
8. Repeat

**NEVER STOP.** Keep going until interrupted.
