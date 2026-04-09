# Autoresearch: Reference Material

Load this file when stuck, plateauing (4+ discards in 5), entering a new optimization layer, or during dimension audits. Not needed during normal loop execution.

## Decision Tree Template

Use this table in autoresearch.md's Problem Profile section. Map each identified bottleneck to technique families.

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
| Memory-TLB                | Hugepages (MAP_HUGETLB/madvise)        | Reduce working set, NUMA-local    | Algorithm micro-opt      |
| System-mismatch           | Match target env, read target spec     | System-level changes (allocator)  | ALL code-level changes   |

## Dimension Checklist

Walk this list every ~10 experiments. Any layer with zero experiments is a blind spot — your next experiment MUST come from it.

1. **Language/runtime version** — newer compiler, newer VM, newer stdlib? What features does the latest version unlock?
2. **Runtime flags** — GC tuning, JIT options, memory settings, compilation thresholds?
3. **Build/compilation pipeline** — PGO (profile-guided optimization), LTO, AOT, CDS, native-image? Run benchmark, collect profile, recompile with profile data, measure again.
4. **OS/kernel** — huge pages, CPU affinity, scheduler policy, I/O scheduler, syscall avoidance?
5. **Hardware features** — run `lscpu` / `cat /proc/cpuinfo`. AVX2/AVX-512? AES-NI for hashing? NUMA topology? Specific cache line size? What does THIS machine offer that you're ignoring?
6. **Data representation** — layout, encoding, compression, SOA vs AOS, off-heap?
7. **Algorithm class** — are you micro-optimizing within one algorithm when a fundamentally different algorithm exists?
8. **Elimination** — what work can you skip entirely? Lazy evaluation, short-circuit, early exit, memoization. The fastest code is code that doesn't run.
9. **Problem reformulation** — solve something slightly different? Relax precision (float vs double)? Approximate instead of exact (t-digest vs full sort)? Change output format? Pre-sort input?
10. **I/O strategy** — mmap, io_uring, buffering, async, batching?
11. **Parallelism model** — threads, tasks, SIMD, pipeline vs data parallel?
12. **Pipeline reordering** — change the ORDER of operations. Filter before parse? Sort before group? Merge phases? Split phases?
13. **Cross-language hot path** — JNI/FFI to C for the bottleneck, WASM, bytecode manipulation, runtime code generation.
14. **Tool/library swap** — is there a faster parser, allocator, hash map, serializer available?
15. **Measurement methodology** — are you benchmarking correctly? Warmup, steady state, coordinated omission?

Write the audit to autoresearch.md under "Dimension Audit."

## Exploration Discipline

Name the active strategy at each decision point. These are cognitive activators — naming the algorithm forces the specific thinking pattern, not generic reasoning.

- **Three-phase structure.** The context hook tracks this automatically:
  - **Exploration** (experiments 1-20): Breadth-first. Cover every optimization layer at least once. Accept bold moves. Goal: map the search space.
  - **Guided** (experiments 20-60): Focus on the top 2-3 dimensions by measured impact. Use dimension importance analysis to prioritize.
  - **Refinement** (experiments 60+): Small, targeted variations. Strict improvement only. Use `runs 3-5` to confirm every gain.
- **Breadth-first layer sweep (first 20 experiments).** Before experiment 20, you MUST have tried at least one experiment in EACH of these layers: I/O strategy, runtime/compiler flags, data representation, algorithm class, parallelism model.
- **Sensitivity screening (dimension importance).** Every ~15 experiments, analyze the JSONL history: tag each experiment by which dimension it modified, compute keep-rate and average improvement per dimension. Rank dimensions by actual measured impact. Focus the next batch on the top 2-3 dimensions. Deprioritize dimensions with 0% keep rate after 3+ attempts.
- **Surrogate landscape.** Every ~10 experiments, build a mental model: "Based on N data points, the quality gradient points toward X. The most promising unexplored region is Y. The exhausted regions are Z." Write this model to autoresearch.md under "Landscape Model."
- **Simulated annealing.** Early in the session (T=high): accept bold architectural changes, even temporary regressions, if they open new possibility space. Later (T=low): strict improvement only. Name which mode you're in.
- **Tabu search.** Never revisit a failed approach. But explore the *boundary* of the tabu set — adjacent variations that avoid the specific failure mode.
- **Evolutionary crossover.** When multiple past experiments each had partial wins, combine their best traits into one experiment. The hybrid must beat both parents or get discarded.
- **Random restarts.** Every ~10 experiments, force a radically different *layer* of the stack — not a variation in the same layer. Break out of the current strategy basin by changing WHERE you optimize, not just WHAT.
- **Reverse-engineer the experts.** Ask: "What production system already solves this?" (Flink, ClickHouse, LMAX Disruptor, etc.) Search for how they solved it — actual implementations, not papers.
- **System observability.** Don't just read source code — observe the running system. Use OS-level tools (`perf stat`, `vmstat`, `iostat`, `strace`, GC logs, JFR/async-profiler) to understand what's actually happening.
- **Multi-fidelity.** Before committing to a full benchmark, do a cheap pre-check when possible (<5s). Kill obviously bad ideas early.
- **MCTS rollout.** Before running, simulate: "If this works, what does it unlock? If it fails, what do we learn?" Prioritize high-information experiments.
- **Bundle experiments.** Occasionally combine 2-3 small ideas to test interaction effects.

## Deep Optimization Patterns

Walk through these when stuck or when entering a new optimization layer.

- **Abstraction erasure.** "What abstraction is the runtime/language imposing that I could bypass entirely?" (String -> raw bytes, Double.parseDouble -> scaled integer arithmetic, HashMap -> flat open-addressing array, object -> struct-of-fields in a byte array)
- **Word-level thinking (SWAR).** "Can I process multiple bytes/elements in a single register operation?" Read 8 bytes as a long, use bitmasks to find delimiters or parse numbers in parallel.
- **Input domain specialization.** "The input isn't arbitrary — what structural constraints can I exploit?" Replace a parser loop with a dispatch tree for known formats.
- **Branch elimination.** "Which code path is most probable? Can I make it branchless?" Use arithmetic (mask, multiply, shift) instead of if/else. Lookup tables instead of computed conditionals.
- **Latency hiding.** "Can I interleave independent work to fill CPU pipeline stalls?" Process 2-3 independent data streams in the same thread.
- **Data structure bifurcation.** "Should I split into specialized fast/slow paths based on data distribution?" Measure the distribution first, then design for the common case.
- **Cache-line engineering.** "Is data I'll access together physically adjacent in memory?" Align entries to cache lines (64 bytes on x86) to avoid false sharing.
- **Shared-nothing parallelism.** "Can I eliminate ALL coordination during the hot path?" Per-thread private data structures, merge only at the end.
- **Measurement questioning.** "What am I ACTUALLY measuring? Is any measured work not part of the real problem?"
- **Precompute over compute.** "Can bounded-range arithmetic become a table lookup?" If input range is small, pre-build a lookup array.
- **Empirical distribution awareness.** "What does the actual data look like?" Optimize the most common case. Rare cases get a slow path.
- **Cross-signal correlation.** "What question requires joining two different profiler signals?" CPU x allocations, CPU x exceptions, allocations x thread-state, I/O x endpoints. The interesting performance stories live in the space between two signals.

## Performance Knowledge Base

At session start AND when plateauing, clone and read relevant handbook files for fresh ideas:

```bash
git clone https://github.com/sderosiaux/linux-perf-handbook.git /tmp/linux-perf-handbook 2>/dev/null || git -C /tmp/linux-perf-handbook pull -q
```

Then use the Read tool on `/tmp/linux-perf-handbook/<filename>`.

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
