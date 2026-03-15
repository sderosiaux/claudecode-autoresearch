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

1. Use `AskUserQuestion` to confirm: **Goal**, **Metric** (+ direction), **Files in scope**, **Constraints**. Do NOT infer constraints silently — ask the user what's off-limits. If they say "none" or don't specify constraints, write "none" in the constraints section. The environment (language version, runtime, OS) is always part of the optimization surface unless the user explicitly restricts it.
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
   cwd: "$(pwd)"
   last_resume: 0
   ---
   Resume the autoresearch experiment loop. Read autoresearch.md and git log for context. Check autoresearch.ideas.md if it exists. Be careful not to overfit to the benchmarks and do not cheat.
   STATEOF
   ```
   **IMPORTANT:** The `session_id` value must match the current Claude Code session ID. Check for it in the environment or context.
8. **Profile the workload** before any optimization. Use the language's profiling tools to understand where time/resources are actually spent. Record findings in autoresearch.md.
9. Run baseline: `${CLAUDE_PLUGIN_ROOT}/scripts/run-experiment.sh "./autoresearch.sh"`
10. Log baseline: `${CLAUDE_PLUGIN_ROOT}/scripts/log-experiment.sh keep <metric_value> "baseline"`
11. Start the main loop immediately. Follow the Loop Rules below.

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
<ONLY constraints the user explicitly stated. Do NOT invent constraints from the environment (e.g. do not lock to the currently installed language version, runtime, or OS). The optimization loop should be free to explore any version, runtime, or toolchain unless the user said otherwise.>

## Profiling Notes
<Where time/resources are actually spent. Update periodically.>

## What's Been Tried
<Update as experiments accumulate. Note key wins, dead ends, architectural insights.>
```

### autoresearch.sh

Bash script (`set -euo pipefail`) that: pre-checks fast (<1s), runs the benchmark, outputs `METRIC name=number` lines. Keep it fast.

**The benchmark scripts are NOT immutable.** You can and should modify them to support new optimization dimensions. Examples: add a `jvm.opts` file that the script reads for JVM flags, add environment variable support, change compiler flags, add profiling hooks. If a dimension expansion audit reveals "runtime flags" as a blind spot, extend the script to support it — don't assume the scripts are off-limits.

### autoresearch.checks.sh (optional)

Bash script for backpressure checks: tests, types, lint. Only create when constraints require it. Keep output minimal (suppress success, show only errors).

## Loop Rules

**LOOP FOREVER.** Never ask "should I continue?" — the user expects autonomous work.

- **Primary metric is king.** Improved -> `keep`. Worse/equal -> `discard`.
- **Simpler is better.** Removing code for equal perf = keep.
- **Don't thrash.** Repeatedly reverting? Try something structurally different.
- **Crashes:** fix if trivial, otherwise log and move on.
- **Think longer when stuck.** Re-read source files, reason about what the system is doing at runtime.
- **Don't self-impose constraints.** Never lock the language version, runtime, JVM implementation, or OS configuration unless the user explicitly said to. The environment is part of the optimization surface.
- **Resuming:** if `autoresearch.md` exists, read it + git log, continue looping.

Each iteration:
1. Think about what to try next (read autoresearch.md "What's Been Tried")
2. Edit code (do NOT commit yet)
3. `${CLAUDE_PLUGIN_ROOT}/scripts/run-experiment.sh "./autoresearch.sh" [timeout] [checks_timeout] [runs] [warmup]`
   Optional args: `runs` (default 1) — run benchmark N times, report median + stddev. `warmup` (default 0) — untimed warmup runs first (JVM/JIT).
   When metric noise is suspected, increase runs to 3-5.
4. Parse the AUTORESEARCH_* output lines
5. **MANDATORY: call the script.** `${CLAUDE_PLUGIN_ROOT}/scripts/log-experiment.sh <status> <metric> "<description>"`
   This script handles EVERYTHING: JSONL logging, git commit on keep, git reset on discard/crash.
   **NEVER** manually append to autoresearch.jsonl. **NEVER** manually revert code. **NEVER** manually git commit experiments. The script does all of this.
6. Update "What's Been Tried" in autoresearch.md periodically
7. Write promising deferred ideas to `autoresearch.ideas.md`
8. Repeat

## Performance Knowledge Base

**Trigger:** At session start (before first experiment) AND when plateauing (4+ discards in last 5 experiments), you MUST read relevant handbook files for fresh ideas.

**How:** Clone once, then read files directly:
```bash
git clone https://github.com/sderosiaux/linux-perf-handbook.git /tmp/linux-perf-handbook 2>/dev/null || git -C /tmp/linux-perf-handbook pull -q
```
Then use the Read tool on `/tmp/linux-perf-handbook/<filename>`. Extract applicable techniques and add promising ones to `autoresearch.ideas.md`.

**Which files to read** (pick 2-3 most relevant to the optimization domain):

| Domain | Files |
|--------|-------|
| JVM / Java | `10-java-jvm.md` |
| Latency / tail latency | `13-latency-analysis.md`, `coordinated-omission-guide.md` |
| I/O / disk / storage | `04-disk-storage.md`, `19-storage-engine-patterns.md` |
| CPU / profiling | `05-performance-profiling.md`, `16-scheduler-interrupts.md` |
| Memory / allocation | `15-memory-subsystem.md` |
| Network / throughput | `03-network-analysis.md`, `09-network-tuning.md` |
| Kernel tuning | `08-kernel-tuning.md` |
| Containers | `07-containers-k8s.md`, `container-debugging-patterns.md` |
| Database | `14-database-profiling.md`, `database-production-debugging.md` |
| General debugging | `00-troubleshooting-framework.md`, `julia-evans-systems-debugging.md` |
| Lock-free / concurrency | `crdt-lock-free-distributed-state.md` |
| eBPF / tracing | `06-ebpf-tracing.md`, `17-ftrace-production.md` |
| Off-CPU / blocking | `18-off-cpu-analysis.md` |

**Example:** For a Java throughput benchmark, at session start read `10-java-jvm.md` and `05-performance-profiling.md`. When plateauing, read `13-latency-analysis.md` or `15-memory-subsystem.md` for orthogonal ideas.

## Exploration Discipline

Name the active strategy at each decision point. These are cognitive activators — naming the algorithm forces the specific thinking pattern, not generic reasoning.

- **Think before running.** Before each experiment: "Could I know the answer without running this?" Syntactic changes the compiler treats identically are not experiments.
- **Sensitivity screening.** Profile every ~10 experiments. Rank factors by impact. Focus experiments on the top 2-3 dimensions. Explicitly ignore factors that don't move the needle. Update "Profiling Notes" in autoresearch.md.
- **Surrogate landscape.** Every ~10 experiments, build a mental model: "Based on N data points, the quality gradient points toward X. The most promising unexplored region is Y. The exhausted regions are Z." Write this model to autoresearch.md under "Landscape Model."
- **Simulated annealing.** Early in the session (T=high): accept bold architectural changes, even temporary regressions, if they open new possibility space. Later (T=low): strict improvement only. Name which mode you're in.
- **Tabu search.** Never revisit a failed approach. But explore the *boundary* of the tabu set — adjacent variations that avoid the specific failure mode. Track tabu approaches in "What's Been Tried."
- **Evolutionary crossover.** When multiple past experiments each had partial wins, combine their best traits into one experiment. The hybrid must beat both parents or get discarded.
- **Random restarts.** Every ~10 experiments, force a radically different *layer* of the stack — not a variation in the same layer. If you've been changing code, try runtime flags. If you've been tuning the algorithm, try changing the data representation. Break out of the current strategy basin by changing WHERE you optimize, not just WHAT.
- **Dimension expansion.** Every ~10 experiments, STOP and enumerate all optimization layers you have NOT touched yet. Walk this checklist and ask "have I tried anything in this layer?":
  1. **Language/runtime version** — newer compiler, newer VM, newer stdlib? What features does the latest version unlock?
  2. **Runtime flags** — GC tuning, JIT options, memory settings, compilation thresholds?
  3. **OS/kernel** — huge pages, CPU affinity, scheduler policy, I/O scheduler, syscall avoidance?
  4. **Data representation** — layout, encoding, compression, SOA vs AOS, off-heap?
  5. **Algorithm class** — are you micro-optimizing within one algorithm when a fundamentally different algorithm exists?
  6. **I/O strategy** — mmap, io_uring, buffering, async, batching?
  7. **Parallelism model** — threads, tasks, SIMD, pipeline vs data parallel?
  8. **Tool/library swap** — is there a faster parser, allocator, hash map, serializer available?
  9. **Measurement methodology** — are you benchmarking correctly? Warmup, steady state, coordinated omission?
  Any layer with zero experiments is a blind spot. Your next experiment MUST come from an unexplored layer.
  Write the layer audit to autoresearch.md under "Dimension Audit."
- **Reverse-engineer the experts.** When optimizing a well-studied problem, ask: "What production system already solves this?" (Flink, ClickHouse, LMAX Disruptor, etc.) Search for how they solved it — their architecture, data structures, and tricks. Not papers — actual implementations and design docs.
- **System observability.** Don't just read source code — observe the running system. Use OS-level tools (`perf stat`, `vmstat`, `iostat`, `strace`, GC logs, JFR/async-profiler) to understand what the CPU, memory, and I/O subsystem are actually doing. The bottleneck you assume from reading code is often not the real one.
- **Multi-fidelity.** Before committing to a full benchmark, do a cheap pre-check when possible (<5s). Kill obviously bad ideas early.
- **MCTS rollout.** Before running, simulate downstream: "If this works, what does it unlock? If it fails, what do we learn?" Prioritize high-information experiments over safe incremental ones.
- **Drain ideas backlog.** Before inventing new micro-opts, try high-potential ideas from `autoresearch.ideas.md`.
- **Bundle experiments.** Occasionally combine 2-3 small ideas to test interaction effects.
- **Check metric noise.** If recent improvements are small, use `runs 3` (or more) on the run-experiment script to get median + stddev. If stddev > improvement, the gain is noise.

## Deep Optimization Patterns

When generating experiment ideas, ask yourself these questions. Each one is a cognitive pattern that unlocks non-obvious improvements. Walk through them when stuck or when entering a new optimization layer.

- **Abstraction erasure.** "What abstraction is the runtime/language imposing that I could bypass entirely?" (String → raw bytes, Double.parseDouble → scaled integer arithmetic, HashMap → flat open-addressing array, object → struct-of-fields in a byte array)
- **Word-level thinking (SWAR).** "Can I process multiple bytes/elements in a single register operation?" Read 8 bytes as a long, use bitmasks to find delimiters or parse numbers in parallel. The CPU processes 64 bits at once — use all of them.
- **Input domain specialization.** "The input isn't arbitrary — what structural constraints can I exploit?" If temperatures have exactly 1 decimal digit and max 2 integer digits, there are only 4 formats: `n.n`, `nn.n`, `-n.n`, `-nn.n`. Replace a parser loop with a dispatch tree.
- **Branch elimination.** "Which code path is most probable? Can I make it branchless?" Use arithmetic (mask, multiply, shift) instead of if/else. Lookup tables instead of computed conditionals. The branch predictor is a finite resource — don't waste it on predictable paths.
- **Latency hiding.** "Can I interleave independent work to fill CPU pipeline stalls?" Process 2-3 independent data streams in the same thread so the CPU has useful work while waiting on memory loads. This isn't parallelism — it's keeping the pipeline full.
- **Data structure bifurcation.** "Should I split into specialized fast/slow paths based on data distribution?" If 75% of keys are < 8 bytes, use an inline hash map for short keys and a pointer-based one for long keys. Measure the distribution first, then design for the common case.
- **Cache-line engineering.** "Is data I'll access together physically adjacent in memory?" Place key + stats contiguously so the cache fetch for lookup amortizes the stats access. Align entries to cache lines (64 bytes on x86) to avoid false sharing.
- **Shared-nothing parallelism.** "Can I eliminate ALL coordination during the hot path?" Per-thread private data structures, merge only at the end. No locks, no atomics, no CAS during processing. File-segment partitioning rather than record-level parallelism.
- **Measurement questioning.** "What am I ACTUALLY measuring? Is any measured work not part of the real problem?" JVM startup/teardown, GC finalization, file munmap — these are overhead, not computation. Question every phase in the measured window.
- **Precompute over compute.** "Can bounded-range arithmetic become a table lookup?" If an expression's input has a small range (0-8, 0-64), pre-build a lookup array. A table load from L1 cache (~1ns) beats complex arithmetic in a pipeline.
- **Empirical distribution awareness.** "What does the actual data look like?" Don't design for worst-case uniformly. Profile the real distribution (key lengths, value ranges, access patterns), then optimize the most common case. Rare cases get a slow path — that's fine.

**NEVER STOP.** Keep going until interrupted.
