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
6. Install the pre-commit hook to enforce file structure:
   ```bash
   cp "${CLAUDE_PLUGIN_ROOT}/scripts/pre-commit-hook.sh" .git/hooks/pre-commit
   ```
   This validates autoresearch.jsonl entries, autoresearch.md sections, script executability, and ideas.md structure on every commit. If you break the structure, the commit is rejected.
7. Write the config line to `autoresearch.jsonl`:
   ```bash
   echo '{"type":"config","name":"<name>","metricName":"<metric>","metricUnit":"<unit>","bestDirection":"<lower|higher>","maxExperiments":100}' > autoresearch.jsonl
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
   **IMPORTANT:** The `session_id` value must match the current Claude Code session ID. Check for it in the environment or context.
9. **Profile & Classify** before any optimization.
   a. Profile the workload with appropriate tools (perf, profiler, flamegraph, time breakdown, GC logs, iostat, strace — whatever fits the stack).
   b. Classify the bottleneck into one or more categories:
      - **CPU-compute**: hot loop, algorithmic complexity, math-heavy
      - **CPU-branch**: mispredictions, polymorphic dispatch, megamorphic calls
      - **Memory-bandwidth**: large working set, cache misses, random access
      - **Memory-allocation**: GC pressure, fragmentation, object churn
      - **I/O-read**: disk, network input, deserialization
      - **I/O-write**: disk, network output, serialization
      - **Concurrency**: lock contention, false sharing, thread coordination
      - **Startup**: class loading, JIT warmup, initialization, cold paths
      - **External**: database, API calls, subprocess, network round-trips
   c. Write the **Problem Profile** section in autoresearch.md (see template below).
   d. Build a **Decision Tree** mapping each identified bottleneck to technique families to try first, second, and what to avoid.
   e. **Cross-signal correlation.** Don't stop at single-signal classification. Join two profiler signals and ask the question neither can answer alone:
      - CPU × allocations: "Which hot method drives the most GC pressure?"
      - CPU × exceptions: "How much compute is spent on traces that also threw exceptions?"
      - Allocations × thread state: "Are idle/parked threads still producing heap pressure?"
      - CPU × endpoints/phases: "Which business operation consumes the most CPU budget?"
      - I/O × allocations: "Do I/O-heavy paths also create allocation spikes?"
      Use whatever tool supports join queries (JFR + jfr-shell `decorateBy`, async-profiler wall-clock + alloc, perf + flamegraph diff, custom scripts joining two CSV exports on trace/thread ID). The bottleneck you identify from one signal is often a symptom — the root cause lives at the intersection of two signals.
   f. **Consult past experience**: run `mdvault search "autoresearch technique <bottleneck-type>" --top-k 10` for each identified bottleneck. Also run `mdvault search "autoresearch anti-pattern <bottleneck-type>" --top-k 5` to avoid known dead ends. Add relevant findings to `autoresearch.ideas.md` as high-priority candidates.
   g. **Characterize the search space.** Document every tunable dimension in the "Search Space" section of `autoresearch.md`. For each dimension, note: type (continuous, discrete, categorical), range/values, and dependencies with other dimensions. This description is injected into your reasoning before each experiment — the more precise the search space, the better the LLM generates variations ([arXiv:2510.17899](https://arxiv.org/abs/2510.17899): +30.7% with problem-specific info, +14.6% with search-space info).
10. Run baseline: `${CLAUDE_PLUGIN_ROOT}/scripts/run-experiment.sh "./autoresearch.sh"`
11. Log baseline: `${CLAUDE_PLUGIN_ROOT}/scripts/log-experiment.sh keep <metric_value> "baseline"`
12. Start the main loop immediately. Follow the Loop Rules below.

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

## Search Space
| Dimension | Type | Range/Values | Dependencies |
|-----------|------|--------------|--------------|
| <e.g. buffer_size> | continuous | 4KB–1MB | interacts with thread_count |
| <e.g. algorithm> | categorical | quicksort, mergesort, radixsort | — |
| <e.g. thread_count> | discrete | 1–16 | interacts with buffer_size |

**Active dimensions**: <N total> | **Explored**: <list> | **Unexplored**: <list>

Update this table as you discover new tunable dimensions. Before each experiment, consult the Unexplored list.

## Profiling Notes
<Where time/resources are actually spent. Update periodically.>

## Problem Profile

**Bottleneck classification**: <e.g. CPU-compute (72% in inner loop), Memory-bandwidth (L3 misses on hash lookups)>

### Decision Tree
| If bottleneck is...       | Try first                              | Try second                        | Avoid (won't help)       |
|---------------------------|----------------------------------------|-----------------------------------|--------------------------|
| CPU-compute (hot loop)    | Algorithm change, SIMD, loop unroll    | Precompute, lookup tables         | I/O tuning               |
| CPU-branch                | Branchless arithmetic, lookup tables   | Profile-guided optimization       | Data layout changes      |
| Memory-bandwidth          | Data layout (SoA), cache-line align    | Smaller types, compression        | More threads             |
| Memory-allocation         | Pool/arena, off-heap, reduce objects   | GC tuning, escape analysis        | I/O changes              |
| I/O-read                  | Buffering, mmap, async, io_uring       | Compression, fewer reads          | CPU micro-opt            |
| I/O-write                 | Batching, async flush, compression     | Buffer sizing, fewer writes       | Algorithm changes        |
| Concurrency               | Shared-nothing, per-thread buffers     | Lock-free, batching               | More locking granularity |
| Startup                   | AOT, CDS, lazy init, native-image      | Reduce classpath, class preload   | Runtime tuning           |
| External                  | Batching, caching, connection pool     | Async/parallel calls              | Code-level micro-opt     |

**Current focus**: <bottleneck> → trying <technique family> first.
**Pivot trigger**: If 3 discards in current focus, re-profile and check if bottleneck shifted.

## What's Been Tried
<Update as experiments accumulate. Note key wins, dead ends, architectural insights.>
```

### autoresearch.sh

Bash script (`set -euo pipefail`) that: pre-checks fast (<1s), runs the benchmark, outputs `METRIC name=number` lines. Keep it fast.

**The benchmark scripts are IN SCOPE — modify them freely.** You MUST extend them when a new optimization dimension requires it. Examples: create a `jvm.opts` file that the script reads for JVM flags, add `--add-modules` for Vector API, add environment variable support, change compiler flags, add profiling hooks. If a dimension expansion audit reveals "runtime flags" or "build pipeline" as a blind spot, your FIRST action is to extend the script to support it. Do NOT skip a promising dimension because "the script doesn't support it" — make the script support it, then run the experiment. During setup, consider creating a `jvm.opts` / `compiler.opts` file pattern upfront so flag experiments are frictionless.

### autoresearch.checks.sh (optional)

Bash script for backpressure checks: tests, types, lint. Only create when constraints require it. Keep output minimal (suppress success, show only errors).

## Loop Rules

**LOOP FOREVER.** Never ask "should I continue?" — the user expects autonomous work.

- **Primary metric is king.** Improved -> `keep`. Worse/equal -> `discard`.
- **Validate small gains.** When delta < 5%, re-run with `runs 5` to confirm. If stddev > delta, the gain is noise — `discard`. Don't pollute the kept history with phantom improvements.
- **Simpler is better.** Removing code for equal perf = keep.
- **Don't thrash.** Repeatedly reverting? Try something structurally different.
- **Crashes:** fix if trivial, otherwise log and move on.
- **Think longer when stuck.** Re-read source files, reason about what the system is doing at runtime.
- **Don't self-impose constraints.** Never lock the language version, runtime, JVM implementation, or OS configuration unless the user explicitly said to. The environment is part of the optimization surface.
- **Resuming:** if `autoresearch.md` exists, read it + git log, continue looping.

Each iteration:
1. Think about what to try next. Consult in order: **Decision Tree** (match bottleneck → technique family), **"What's Been Tried"** (avoid repeats), **`autoresearch.ideas.md`** (queued ideas), **top kept experiments** (the context hook injects the best 5 keeps — treat them as the "population" and mutate from the best, not just from the latest state).
2. Edit code (do NOT commit yet)
3. `${CLAUDE_PLUGIN_ROOT}/scripts/run-experiment.sh "./autoresearch.sh" [timeout] [checks_timeout] [runs] [warmup] [early_stop_pct]`
   Optional args: `runs` (default 1) — run benchmark N times, report median + stddev. `warmup` (default 0) — untimed warmup runs first (JVM/JIT). `early_stop_pct` (default 0) — if >0 and runs>1, abort remaining runs when the first run's metric is this % worse than the best known keep. Use `20` for aggressive early stopping, `50` for conservative.
   When metric noise is suspected, increase runs to 3-5. In the Refinement phase, use `runs 5 warmup 1 early_stop_pct 20` to save time on clearly worse experiments.
4. Parse the AUTORESEARCH_* output lines
5. **MANDATORY: call the script.** `${CLAUDE_PLUGIN_ROOT}/scripts/log-experiment.sh <status> <metric> "<description>"`
   This script handles EVERYTHING: JSONL logging, git commit on keep, git reset on discard/crash.
   **NEVER** manually append to autoresearch.jsonl. **NEVER** manually revert code. **NEVER** manually git commit experiments. The script does all of this.
6. **On keep with >5% improvement**, store the technique in mdvault for future sessions:
   ```bash
   mdvault remember "TECHNIQUE: <name> | BOTTLENECK: <type> | GAIN: <X>% | CONTEXT: <workload type> | WORKS-WHEN: <conditions> | FAILS-WHEN: <conditions>" --namespace autoresearch/techniques
   ```
7. **On confirmed anti-pattern** (technique tried 2+ times, always fails for this bottleneck type):
   ```bash
   mdvault remember "ANTI-PATTERN: <technique> does NOT work for <bottleneck-type> because <reason>" --namespace autoresearch/anti-patterns
   ```
8. Update "What's Been Tried" in autoresearch.md periodically
9. Write promising deferred ideas to `autoresearch.ideas.md` using checkbox format. The context hook counts `- [ ]` items and nudges you to try them when keep rate is low. Use this structure:
   ```markdown
   # Deferred Ideas
   ## High Priority
   - [ ] idea 1
   - [ ] idea 2
   ## Medium Priority
   - [ ] idea 3
   ## Tried and Kept (do not retry — already applied)
   - [x] description (+X%)
   ## Tried and Failed (do not retry)
   - [x] description (-X%): reason
   ```
   Move ideas between sections as you try them. This prevents re-trying failed approaches after resume.
10. **Every 3 discards in a row on the same bottleneck**: re-profile, update Problem Profile, and run `mdvault search "autoresearch technique <bottleneck>" --top-k 5` for ideas from past sessions.
11. **Cross-metric correlation (every ~10 experiments).** When secondary metrics exist, scan the JSONL history and ask: do metrics move together or trade off? A latency win that silently doubles memory is a trap. A throughput gain that also reduces allocations reveals a structural improvement worth doubling down on. Write observed correlations to "Landscape Model" in autoresearch.md — they inform which dimensions to explore next.
12. Repeat

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

- **Three-phase structure.** Name which phase you are in — the context hook tracks this automatically:
  - **Exploration** (experiments 1–20): Breadth-first. Cover every optimization layer at least once. Accept bold moves. Goal: map the search space, not optimize within it.
  - **Guided** (experiments 20–60): Decision-tree focused. Concentrate on the top 2-3 dimensions by measured impact. Use sensitivity screening to prioritize. Goal: extract maximum value from the best dimensions.
  - **Refinement** (experiments 60+): Small, targeted variations on the best known configuration. Strict improvement only. Use `runs 3-5` to confirm every gain. Goal: squeeze the last few percent.
  Inspired by [arXiv:2603.04027](https://arxiv.org/abs/2603.04027): Latin Hypercube Sampling → Simulated Annealing → Hill Climbing pipeline achieved +23% over defaults.
- **Breadth-first layer sweep (first 20 experiments).** Before experiment 20, you MUST have tried at least one experiment in EACH of these layers: I/O strategy, runtime/compiler flags, data representation, algorithm class, parallelism model. Big wins hide in layers you haven't touched — micro-optimizing parsing while ignoring mmap or JIT flags is a common trap. Check the dimension list and force yourself into unexplored territory early.
- **Think before running.** Before each experiment: "Could I know the answer without running this?" Syntactic changes the compiler treats identically are not experiments.
- **Sensitivity screening (dimension importance).** Every ~15 experiments, analyze the JSONL history: tag each experiment by which dimension it modified, compute keep-rate and average improvement per dimension. Rank dimensions by actual measured impact — not intuition. Focus the next batch on the top 2-3 dimensions. Explicitly deprioritize dimensions with 0% keep rate after 3+ attempts. Update "Search Space" explored/unexplored and "Profiling Notes" in autoresearch.md. ([arXiv:2512.19246](https://arxiv.org/abs/2512.19246): MetaSHAP shows dimension importance analysis outperforms blind BO in convergence.)
- **Surrogate landscape.** Every ~10 experiments, build a mental model: "Based on N data points, the quality gradient points toward X. The most promising unexplored region is Y. The exhausted regions are Z." Write this model to autoresearch.md under "Landscape Model."
- **Simulated annealing.** Early in the session (T=high): accept bold architectural changes, even temporary regressions, if they open new possibility space. Later (T=low): strict improvement only. Name which mode you're in.
- **Tabu search.** Never revisit a failed approach. But explore the *boundary* of the tabu set — adjacent variations that avoid the specific failure mode. Track tabu approaches in "What's Been Tried."
- **Evolutionary crossover.** When multiple past experiments each had partial wins, combine their best traits into one experiment. The hybrid must beat both parents or get discarded.
- **Random restarts.** Every ~10 experiments, force a radically different *layer* of the stack — not a variation in the same layer. If you've been changing code, try runtime flags. If you've been tuning the algorithm, try changing the data representation. Break out of the current strategy basin by changing WHERE you optimize, not just WHAT.
- **Dimension expansion.** Every ~10 experiments, STOP and enumerate all optimization layers you have NOT touched yet. Walk this checklist and ask "have I tried anything in this layer?":
  1. **Language/runtime version** — newer compiler, newer VM, newer stdlib? What features does the latest version unlock?
  2. **Runtime flags** — GC tuning, JIT options, memory settings, compilation thresholds?
  3. **Build/compilation pipeline** — PGO (profile-guided optimization), LTO, AOT, CDS, native-image? Run benchmark → collect profile → recompile with profile data → measure again.
  4. **OS/kernel** — huge pages, CPU affinity, scheduler policy, I/O scheduler, syscall avoidance?
  5. **Hardware features** — run `lscpu` / `cat /proc/cpuinfo`. AVX2/AVX-512? AES-NI for hashing? NUMA topology? Specific cache line size? What does THIS machine offer that you're ignoring?
  6. **Data representation** — layout, encoding, compression, SOA vs AOS, off-heap?
  7. **Algorithm class** — are you micro-optimizing within one algorithm when a fundamentally different algorithm exists?
  8. **Elimination** — what work can you skip entirely? Lazy evaluation, short-circuit, early exit, memoization. The fastest code is code that doesn't run. Which computations are actually needed?
  9. **Problem reformulation** — can you solve something slightly different? Relax precision (float vs double)? Approximate instead of exact (t-digest vs full sort)? Change output format? Pre-sort input?
  10. **I/O strategy** — mmap, io_uring, buffering, async, batching?
  11. **Parallelism model** — threads, tasks, SIMD, pipeline vs data parallel?
  12. **Pipeline reordering** — change the ORDER of operations. Filter before parse? Sort before group? Merge phases? Split phases? The sequence of stages matters as much as the stages themselves.
  13. **Cross-language hot path** — JNI/FFI to C for the bottleneck, WASM, bytecode manipulation, runtime code generation. Change the execution engine for the critical 5%.
  14. **Tool/library swap** — is there a faster parser, allocator, hash map, serializer available?
  15. **Measurement methodology** — are you benchmarking correctly? Warmup, steady state, coordinated omission?
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
- **Cross-signal correlation.** "What question requires joining two different profiler signals?" Single-signal analysis is always incomplete. CPU samples don't know which endpoint they serve. Allocation samples don't know if their trace threw exceptions. The interesting performance stories live in the space between two signals. When profiling, always ask at least one cross-signal question: CPU×allocations, CPU×exceptions, allocations×thread-state, I/O×endpoints. The data is already in the recording — connect it.

**NEVER STOP.** Keep going until interrupted.
