# Autoresearch — Claude Code Plugin

## Plugin Release Workflow

After changing plugin files (skills, hooks, scripts, plugin.json), you MUST:

1. Bump version in `.claude-plugin/plugin.json`
2. Commit + push
3. Create a GitHub release (tag matching the version)

The marketplace caches plugins by version. Without a version bump + release, machines using `claude plugins update` will get stale cached files.

## Architecture

- Exploration discipline single source of truth: `skills/create/SKILL.md`
- `skills/resume/SKILL.md` — thin re-orientation wrapper, references create
- `scripts/context-hook.sh` — terse exploration nudge (not the full discipline)
