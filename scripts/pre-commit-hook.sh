#!/bin/bash
set -uo pipefail

# pre-commit-hook.sh — Validate autoresearch file structure before commit.
#
# Install during autoresearch setup:
#   cp "${CLAUDE_PLUGIN_ROOT}/scripts/pre-commit-hook.sh" .git/hooks/pre-commit
#
# Only runs checks when autoresearch.jsonl exists (autoresearch session active).
# Fast (<200ms) — runs on every commit during the experiment loop.

JSONL="autoresearch.jsonl"
[[ ! -f "$JSONL" ]] && exit 0

ERRORS=""
err() { ERRORS="${ERRORS}\n  - $1"; }

# --- autoresearch.jsonl: validate structure ---
# Config line must exist
if ! grep -q '"type":"config"' "$JSONL" 2>/dev/null; then
  err "autoresearch.jsonl: missing config line (must contain '\"type\":\"config\"')"
fi

# Each experiment entry must have required fields
LINENO_=0
while IFS= read -r line; do
  LINENO_=$((LINENO_ + 1))
  # Skip config lines
  echo "$line" | grep -q '"type"' && continue
  # Skip empty lines
  [[ -z "$line" ]] && continue

  for field in status metric description; do
    if ! echo "$line" | grep -q "\"$field\"" 2>/dev/null; then
      err "autoresearch.jsonl line $LINENO_: missing required field '$field'"
      break 2  # stop after first bad line to avoid noise
    fi
  done

  # Validate status value
  STATUS=$(echo "$line" | jq -r '.status // empty' 2>/dev/null)
  case "$STATUS" in
    keep|discard|crash|guard_failed) ;;
    "") ;;  # might be a non-experiment line
    *) err "autoresearch.jsonl line $LINENO_: invalid status '$STATUS' (must be keep|discard|crash|guard_failed)" ;;
  esac
done < "$JSONL"

# --- autoresearch.md: required sections ---
if [[ -f "autoresearch.md" ]]; then
  for section in "## Objective" "## Metrics" "## Files in Scope" "## Constraints" "## How to Run"; do
    if ! grep -q "^$section" "autoresearch.md" 2>/dev/null; then
      err "autoresearch.md: missing required section '$section'"
    fi
  done
else
  err "autoresearch.md: file not found (required for session state)"
fi

# --- autoresearch.sh: exists, executable, emits METRIC ---
if [[ -f "autoresearch.sh" ]]; then
  if [[ ! -x "autoresearch.sh" ]]; then
    err "autoresearch.sh: not executable (run: chmod +x autoresearch.sh)"
  fi
  if ! grep -qE 'METRIC|\.\/|exec ' "autoresearch.sh" 2>/dev/null; then
    err "autoresearch.sh: should output METRIC lines or delegate to a binary that does"
  fi
else
  err "autoresearch.sh: file not found (required benchmark script)"
fi

# --- autoresearch.checks.sh: if exists, must be executable ---
if [[ -f "autoresearch.checks.sh" ]] && [[ ! -x "autoresearch.checks.sh" ]]; then
  err "autoresearch.checks.sh: not executable (run: chmod +x autoresearch.checks.sh)"
fi

# --- autoresearch.ideas.md: if exists, validate structure ---
if [[ -f "autoresearch.ideas.md" ]]; then
  HAS_KEPT=$(grep -c '^## Tried and Kept' "autoresearch.ideas.md" 2>/dev/null || true)
  HAS_FAILED=$(grep -c '^## Tried and Failed' "autoresearch.ideas.md" 2>/dev/null || true)
  if [[ "$HAS_KEPT" -eq 0 ]] && [[ "$HAS_FAILED" -eq 0 ]]; then
    # Only warn if the file has more than 5 items — early in the session it's fine to skip
    ITEM_COUNT=$(grep -c '^- ' "autoresearch.ideas.md" 2>/dev/null || true)
    if [[ "$ITEM_COUNT" -gt 5 ]]; then
      err "autoresearch.ideas.md: missing tracking sections. Add '## Tried and Kept' and '## Tried and Failed' to prevent re-trying old experiments."
    fi
  fi
fi

# --- Report ---
if [[ -n "$ERRORS" ]]; then
  echo "AUTORESEARCH PRE-COMMIT: structure validation failed" >&2
  echo -e "$ERRORS" >&2
  echo "" >&2
  echo "Fix the issues above or bypass with: git commit --no-verify" >&2
  exit 1
fi

exit 0
