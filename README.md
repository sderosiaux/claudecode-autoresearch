# claudecode-autoresearch

Autonomous experiment loop for Claude Code. Edit, benchmark, keep or discard, repeat forever.

*Inspired by [pi-autoresearch](https://github.com/davebcn87/pi-autoresearch) and [karpathy/autoresearch](https://github.com/karpathy/autoresearch).*

---

## What it does

Claude runs an optimization loop autonomously: edit code, run benchmark, keep if better, revert if worse, repeat. It never stops unless you tell it to. Survives context resets via auto-resume.

Works for anything with a number you can measure: test speed, bundle size, build times, training loss, Lighthouse scores, error count, security findings.

## Install

```bash
# Add the marketplace (one-time)
claude plugins marketplace add sderosiaux/claude-plugins

# Install
claude plugins install autoresearch@sderosiaux-claude-plugins
```

Restart Claude Code after installing.

## Quick start

```
/autoresearch:create optimize vitest execution time
```

Claude asks about your goal, metric, files in scope, and constraints (or infers from context). Then it creates a branch, writes config files, profiles the workload, runs the baseline, and starts looping.

### The loop

```
LOOP (forever or until maxExperiments):
  1. Pick the next idea (profiling, past results, headroom table)
  2. Edit code
  3. Run benchmark (autoresearch.sh) → METRIC name=value
  4. Run guard (autoresearch.checks.sh) → pass/fail
  5. Keep if metric improved AND guard passed
  6. Revert if metric worse OR guard failed
  7. Log to autoresearch.jsonl, git commit or revert
  8. Repeat
```

![Experiment loop](loop-diagram.png)

### Example session

```
#   Status          Metric    Description
1   keep            42.3s     baseline
2   keep            38.1s     parallelize test files across 4 workers
3   discard         39.0s     switch to happy-dom (slower than jsdom here)
4   keep            31.7s     lazy-import heavy test fixtures
5   crash           —         syntax error in config — auto-fixed
6   keep            28.4s     replace glob imports with explicit paths
7   guard_failed    25.1s     faster but broke 3 integration tests — reverted
8   keep            26.9s     selective mocking — tests pass, still faster
```

```
Session: optimize vitest execution time
Runs: 8 | 5 kept | 1 discarded | 1 crashed | 1 guard_failed
Baseline: 42.3s → Best: 26.9s (-36.4%)
```

When Claude hits a context limit, the Stop hook auto-resumes a fresh session that picks up where it left off.

## Benchmark vs Guard

Two scripts, two jobs:

| Script | Role | Question it answers |
|--------|------|---------------------|
| `autoresearch.sh` | **Benchmark** | "Did the metric improve?" |
| `autoresearch.checks.sh` | **Guard** | "Did anything else break?" |

The benchmark produces numbers (`METRIC time=26.9`). The guard is a pass/fail safety net — tests, types, lint, whatever must never regress. If the metric improves but the guard fails, the change is reverted.

The guard is optional. Skip it when your benchmark already covers correctness (e.g., training loss — if it runs, it works). Add it when you're optimizing one thing but need another thing to stay green.

**Examples:**

| Goal | Benchmark (`autoresearch.sh`) | Guard (`autoresearch.checks.sh`) |
|------|-------------------------------|----------------------------------|
| Faster tests | `pnpm test` (measures time) | — (tests ARE the metric) |
| Smaller bundle | `pnpm build && du -sb dist` | `pnpm test` (don't break features) |
| Faster API | `wrk -t4 http://...` | `pnpm test` (don't break endpoints) |
| Fix all errors | `pnpm test 2>&1 \| grep -c FAIL` | `pnpm build` (must still compile) |

### Commands

| Command | Purpose |
|---------|---------|
| `/autoresearch:create` | Setup: goal, metric, guard, branch, config files, baseline |
| `/autoresearch:resume` | Resume after pause or context reset |
| `/autoresearch:stop` | End auto-resume, show final summary |
| `/autoresearch:status` | Print experiment dashboard |

## Presets: debug, fix, security — same loop, different metric

There's no separate "debug mode" or "fix mode". Autoresearch is one loop. What changes is the metric:

| Use case | Metric | Direction | Guard | How to start |
|----------|--------|-----------|-------|--------------|
| **Performance** | seconds, ev/s, MB | lower/higher | `tests pass` | `/autoresearch:create optimize API response time` |
| **Fix errors** | failing test count | lower | `build passes` | `/autoresearch:create reduce failing tests to zero` |
| **Fix lint** | eslint error count | lower | `tests pass` | `/autoresearch:create fix all eslint errors` |
| **Test coverage** | coverage % | higher | `tests pass` | `/autoresearch:create increase test coverage` |
| **Bundle size** | KB | lower | `tests pass` | `/autoresearch:create reduce bundle size` |
| **Training loss** | val_bpb | lower | — | `/autoresearch:create minimize validation loss` |

Just describe what you want. Claude figures out the metric, benchmark script, and guard. The loop mechanics — profiling, statistical validation, auto-revert, auto-resume — are always the same.

## How it works

Three layers:

**Scripts** — real shell/python code, not prompts. `run-experiment.sh` handles timing, multi-run median, warmup, early stopping, timeout. `log-experiment.sh` writes JSONL and handles git atomically (commit on keep, hard-reset on discard). `status.sh` prints the dashboard.

**Skills** — teach Claude the loop discipline. Profiling before optimizing, headroom analysis, exploration strategy (breadth-first → guided → refinement), plateau detection.

**Hooks** — keep the loop alive across context resets. The Stop hook blocks exit and re-injects the resume prompt. The context hook injects the top-5 best experiments as "population" for Claude to mutate from.

### Files created in your project

| File | Purpose |
|------|---------|
| `autoresearch.jsonl` | Append-only experiment log (one JSON per run) |
| `autoresearch.md` | Session context: objective, scope, profiling notes, what's been tried |
| `autoresearch.sh` | Benchmark script — produces `METRIC name=value` |
| `autoresearch.checks.sh` | Guard script (optional) — exit 0 = pass, non-zero = fail |

A fresh Claude session can resume from `autoresearch.md` + `autoresearch.jsonl` alone.

### Statistical rigor

Single runs can lie. The loop supports:
- **Multi-run median**: `runs 5` → run 5 times, report median per metric
- **Warmup**: `warmup 1` → discard first run (JIT, cache cold)
- **Early stopping**: `early_stop_pct 20` → abort remaining runs if first run is >20% worse than best known
- **Stddev check**: gains < 5% with high stddev → discard (noise, not signal)

### Auto-resume

When Claude stops (context limit, crash), the Stop hook blocks exit and resumes a new session. Safety: 30s cooldown, crash detection after 3 consecutive failures. The loop ends at `maxExperiments` (default 100). `/autoresearch:stop` disables it.

## Real-world result

**[Streaming Aggregation: 57K to 48M ev/s (831x)](https://github.com/sderosiaux/autoresearch-java-streaming-aggregation-bench)** — 395 experiments across two sessions (Java + C). Every commit is an experiment. Commit messages document technique, throughput, and delta.

## Try it

Clone and run the demo — no Claude Code needed, just `bash`, `git`, `node`, and `jq`:

```bash
git clone https://github.com/sderosiaux/claudecode-autoresearch
cd claudecode-autoresearch
bash tests/demo.sh
```

Simulates 5 iterations (2 keeps, 1 discard, 1 crash, 1 guard_failed) and prints the dashboard.

Full test suite (29 tests):

```bash
bash tests/test-scripts.sh
```

## License

MIT
