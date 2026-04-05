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

   # Remove only the state file for the CURRENT session (not other concurrent sessions)
   SESSION_ID="${CLAUDE_SESSION_ID:-}"
   for f in ~/.claude/states/autoresearch/*.md; do
     FILE_SESSION=$(sed -n '/^---$/,/^---$/{ /^---$/d; p; }' "$f" 2>/dev/null | grep '^session_id:' | sed 's/session_id: *//' | sed 's/^"\(.*\)"$/\1/')
     if [[ -n "$SESSION_ID" ]] && [[ "$FILE_SESSION" == "$SESSION_ID" ]]; then
       echo "Removing: $f (session match)"
       rm "$f"
     elif [[ -z "$SESSION_ID" ]] && grep -q "$(pwd)" "$f" 2>/dev/null; then
       # Fallback: match by cwd if session ID unavailable
       echo "Removing: $f (cwd match)"
       rm "$f"
     fi
   done
   ```
2. Run `${CLAUDE_PLUGIN_ROOT}/scripts/status.sh` to show final summary
3. Report: total runs, best metric, improvement vs baseline
4. Suggest: "Run `/autoresearch:resume` to resume later, or `/autoresearch:create` to start a new session."
