---
description: Stop an active autoresearch experiment loop. Removes the auto-resume state file so the session can end cleanly.
---

# Autoresearch: Stop

Stop the active autoresearch loop and disable auto-resume.

## Steps

1. Find and remove the autoresearch state file for the current session:
   ```bash
   # List active state files
   ls -la ~/.claude/states/autoresearch/ 2>/dev/null

   # Remove all state files for current cwd
   for f in ~/.claude/states/autoresearch/*.md; do
     if grep -q "$(pwd)" "$f" 2>/dev/null; then
       echo "Removing: $f"
       rm "$f"
     fi
   done
   ```
2. Run `${CLAUDE_PLUGIN_ROOT}/scripts/status.sh` to show final summary
3. Report: total runs, best metric, improvement vs baseline
4. Suggest: "Run `/autoresearch:resume` to resume later, or `/autoresearch:create` to start a new session."
