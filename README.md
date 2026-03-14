# claudecode-autoresearch

Autonomous experiment loop for Claude Code. Edit, benchmark, keep or discard, repeat forever.

*Inspired by [pi-autoresearch](https://github.com/davebcn87/pi-autoresearch) and [karpathy/autoresearch](https://github.com/karpathy/autoresearch).*

---

## What it does

Claude runs an optimization loop autonomously: edit code, run benchmark, keep if better, revert if worse, repeat. It never stops unless you tell it to. Survives context resets via auto-resume.

Works for any optimization target: test speed, bundle size, build times, training loss, Lighthouse scores.

## Install

Via the marketplace:
```bash
claude plugins install claudecode-autoresearch@sderosiaux-claude-plugins
```

Manual:
```bash
git clone https://github.com/sderosiaux/claudecode-autoresearch ~/.claude/plugins/manual/claudecode-autoresearch
```

## Usage

### Start a session

```
/autoresearch:create optimize unit test runtime
```

Claude asks about your goal, command, metric, and files in scope (or infers from context). Then it creates a branch, writes config files, runs the baseline, and starts looping.

### The loop

```mermaid
flowchart TD
    A["/autoresearch:create"] --> B["Setup: branch, autoresearch.md, .sh, .jsonl"]
    B --> C["Run baseline"]
    C --> LOOP

    subgraph LOOP["Experiment Loop (runs forever)"]
        direction TB
        E["Claude edits code"] --> F["run-experiment.sh ./autoresearch.sh"]
        F --> G{"Benchmark\npassed?"}
        G -- "exit != 0" --> CRASH["log: crash"]
        G -- "exit 0" --> H{"checks.sh\nexists?"}
        H -- no --> J{"Metric\nimproved?"}
        H -- yes --> I{"Checks\npassed?"}
        I -- no --> CFAIL["log: checks_failed"]
        I -- yes --> J
        J -- yes --> KEEP["log: keep (git commit)"]
        J -- no --> DISCARD["log: discard (git revert)"]
        KEEP --> E
        DISCARD --> E
        CRASH --> E
        CFAIL --> E
    end

    LOOP -- "context reset" --> STOP{"Stop hook"}
    STOP -- "iteration < max" --> RESUME["exit 2: inject resume prompt"]
    RESUME --> LOOP
    STOP -- "iteration >= max" --> END["Session ends"]

    style KEEP fill:#2ea043,color:#fff
    style DISCARD fill:#d29922,color:#fff
    style CRASH fill:#cf222e,color:#fff
    style CFAIL fill:#cf222e,color:#fff
    style RESUME fill:#1f6feb,color:#fff
```

### Commands

| Command | Purpose |
|---------|---------|
| `/autoresearch:create` | Setup: goal, command, metric, branch, config files |
| `/autoresearch` | Resume/continue the loop |
| `/autoresearch:stop` | Remove state file, end auto-resume |
| `/autoresearch:status` | Print dashboard |

## How it works

```mermaid
graph LR
    subgraph Plugin
        S1["skills/"] --> S2["autoresearch-create"]
        S1 --> S3["autoresearch"]
        S1 --> S4["autoresearch-stop"]
        S1 --> S5["autoresearch-status"]
        SC["scripts/"] --> SC1["run-experiment.sh"]
        SC --> SC2["log-experiment.sh"]
        SC --> SC3["status.sh"]
        H["hooks/"] --> H1["Stop → stop-hook.sh"]
        H --> H2["UserPromptSubmit → context-hook.sh"]
    end

    subgraph Project["Your project (generated)"]
        P1["autoresearch.jsonl"]
        P2["autoresearch.md"]
        P3["autoresearch.sh"]
        P4["autoresearch.checks.sh"]
    end

    subgraph State["~/.claude/states/autoresearch/"]
        ST1["session-id.md"]
    end

    SC1 -- runs --> P3
    SC1 -- runs --> P4
    SC2 -- appends --> P1
    H1 -- reads --> ST1
    H2 -- detects --> P1

    style Plugin fill:#f6f8fa,stroke:#d0d7de
    style Project fill:#dafbe1,stroke:#2ea043
    style State fill:#ddf4ff,stroke:#1f6feb
```

### Persistence

| File | Purpose |
|------|---------|
| `autoresearch.jsonl` | Append-only experiment log |
| `autoresearch.md` | Session doc (objective, files, what's been tried) |
| `autoresearch.sh` | Benchmark script |
| `autoresearch.checks.sh` | Optional correctness checks (tests, types, lint) |

A fresh Claude session can resume from `autoresearch.md` + `autoresearch.jsonl` alone.

### Auto-resume

```mermaid
sequenceDiagram
    participant C as Claude
    participant S as Stop Hook
    participant F as State File

    C->>C: Loop until context limit
    C--xS: Agent stops
    S->>F: Find state file for session
    F-->>S: iteration=3, max=50
    S->>S: Check: iteration < max? rate limit ok?
    S->>C: exit 2 + resume prompt (stderr)
    C->>C: Reads autoresearch.md, continues loop
    Note over C,F: Repeats up to max_iterations
```

Safety limits:
- Max 50 iterations (configurable)
- Min 30s between resumes
- Crash detection: warns after 3 consecutive failures
- `/autoresearch:stop` disables it

## Example domains

| Domain | Metric | Command |
|--------|--------|---------|
| Test speed | seconds (lower) | `pnpm test` |
| Bundle size | KB (lower) | `pnpm build && du -sb dist` |
| Build speed | seconds (lower) | `pnpm build` |
| Training loss | val_bpb (lower) | `uv run train.py` |
| Lighthouse | perf score (higher) | `lighthouse http://localhost:3000 --output=json` |

## Testing

```bash
bash tests/test-scripts.sh
```

## License

MIT
