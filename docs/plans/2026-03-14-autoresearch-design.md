# claudecode-autoresearch — Design

## Problem

Optimizing a metric (test speed, bundle size, training loss) requires repetitive edit-benchmark-evaluate cycles. An AI agent can do this autonomously, but Claude Code has no built-in experiment loop infrastructure.

## Solution

A Claude Code plugin that provides:
- Shell scripts for timed experiment execution, result logging, and git commit/revert
- Skills that teach Claude how to set up and run the loop
- Hooks for auto-resume (Stop) and context injection (UserPromptSubmit)
- Append-only JSONL persistence that survives context resets

Inspired by [pi-autoresearch](https://github.com/davebcn87/pi-autoresearch) (for the pi AI agent).

## Architecture

```
User: /autoresearch:create
      |
      v
Skill asks goal/command/metric
      |
      v
Creates: branch, autoresearch.md, .sh, state file
      |
      v
  LOOP FOREVER
  |  Claude edits code
  |       |
  |       v
  |  Bash: run-experiment.sh <cmd>     <- timer + capture + checks
  |       |
  |       v
  |  Bash: log-experiment.sh           <- JSONL + git commit/revert
  |       |
  |       v
  |  Better? keep : discard
  |       |
  +-------+
  Context reset?
      |
      v
  Stop hook --- exit 2 ---> resume prompt (auto-resume)

  UserPromptSubmit hook --- injects autoresearch context
```

## Components

### Scripts

| Script | Purpose |
|--------|---------|
| `run-experiment.sh` | Runs command with timer, captures output, runs optional checks |
| `log-experiment.sh` | Appends to JSONL, git commit (keep) or git checkout (discard/crash) |
| `status.sh` | Parses JSONL, prints dashboard summary |
| `stop-hook.sh` | Auto-resume: reads state file, exit 2 + stderr prompt |
| `context-hook.sh` | Injects autoresearch mode reminder when JSONL exists |

### Skills

| Skill | Purpose |
|-------|---------|
| `autoresearch:create` | Setup: goal, command, metric, files in scope. Creates branch + files. |
| `autoresearch` | Resume/main loop skill. Reads context, loops forever. |
| `autoresearch:stop` | Removes state file, stops auto-resume. |
| `autoresearch:status` | Runs status.sh, shows dashboard. |

### Hooks

| Event | Script | Behavior |
|-------|--------|----------|
| `Stop` | `stop-hook.sh` | If state file exists for session: increment iteration, exit 2 with resume prompt |
| `UserPromptSubmit` | `context-hook.sh` | If autoresearch.jsonl in cwd: inject context reminder |

### Persistence

| File | Format | Purpose |
|------|--------|---------|
| `autoresearch.jsonl` | JSON lines | Append-only experiment log (config headers + results) |
| `autoresearch.md` | Markdown | Session doc: objective, metrics, files, what's been tried |
| `autoresearch.sh` | Bash | Benchmark script |
| `autoresearch.checks.sh` | Bash (optional) | Backpressure checks (tests, types, lint) |
| `autoresearch.ideas.md` | Markdown (optional) | Deferred optimization ideas |
| `~/.claude/states/autoresearch/<id>.md` | YAML frontmatter + body | Auto-resume state per session |

### State file format

```yaml
---
session_id: "abc-123"
iteration: 5
max_iterations: 50
cwd: "/Users/me/project"
started_at: "2026-03-14T10:00:00"
---
Resume the autoresearch experiment loop. Read autoresearch.md and git log for context.
```

### Safety

- max_iterations: 50 default (configurable in create skill)
- Rate limit: min 30s between resumes
- Crash detection: 3 consecutive crashes = warning in resume prompt
- `/autoresearch:stop` removes state file = next Stop exits cleanly
- cwd mismatch = skip (don't resume in wrong project)
