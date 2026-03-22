#!/bin/bash
set -uo pipefail

# prepare-experiment.sh — Stage and commit experiment changes before running the benchmark.
#
# Usage: prepare-experiment.sh <description> [files...]
#
#   description:  one-sentence description of the experiment
#   files:        specific files to stage (default: all tracked modified files via git add -u)
#
# This script:
#   1. Saves a checkpoint (HEAD before commit) for safe rollback
#   2. Stages the specified files (or all modified tracked files)
#   3. Validates there's something to commit
#   4. Commits with an experiment: prefix
#
# Output (machine-readable):
#   AUTORESEARCH_PREPARED=<true|false>
#   AUTORESEARCH_CHECKPOINT=<commit-hash>
#   AUTORESEARCH_COMMIT=<commit-hash>
#   AUTORESEARCH_FILES_CHANGED=<N>

DESCRIPTION="${1:?Usage: prepare-experiment.sh <description> [files...]}"
shift
FILES=("$@")

PROJECT_DIR="$(pwd)"

# --- Save checkpoint for safe revert ---
CHECKPOINT=$(git rev-parse HEAD 2>/dev/null)
echo "$CHECKPOINT" > "$PROJECT_DIR/.autoresearch-checkpoint"

# --- Stage files ---
if [[ ${#FILES[@]} -gt 0 ]]; then
  git add "${FILES[@]}" 2>/dev/null
else
  git add -u 2>/dev/null
fi

# --- Check there's something to commit ---
if git diff --cached --quiet 2>/dev/null; then
  echo "No changes to commit — nothing was modified." >&2
  echo "AUTORESEARCH_PREPARED=false"
  rm -f "$PROJECT_DIR/.autoresearch-checkpoint"
  exit 0
fi

FILES_CHANGED=$(git diff --cached --name-only | wc -l | tr -d ' ')

# --- Atomicity warning ---
if [[ $FILES_CHANGED -gt 5 ]]; then
  echo "WARNING: $FILES_CHANGED files changed — verify this is a single logical change." >&2
fi

# --- Commit ---
COMMIT_MSG="$DESCRIPTION"
[[ ! "$COMMIT_MSG" =~ ^experiment: ]] && COMMIT_MSG="experiment: $COMMIT_MSG"

COMMIT_ERR=$(mktemp)
if ! git commit -q -m "$COMMIT_MSG" 2>"$COMMIT_ERR"; then
  # Pre-commit hooks (formatters) may have modified staged files — re-stage and retry
  if [[ ${#FILES[@]} -gt 0 ]]; then
    git add "${FILES[@]}" 2>/dev/null
  else
    git add -u 2>/dev/null
  fi
  if ! git commit -q -m "$COMMIT_MSG" 2>>"$COMMIT_ERR"; then
    echo "ERROR: commit failed (hook or other issue):" >&2
    cat "$COMMIT_ERR" >&2
    rm -f "$COMMIT_ERR"
    # Unstage and clean up
    git reset HEAD 2>/dev/null
    rm -f "$PROJECT_DIR/.autoresearch-checkpoint"
    echo "AUTORESEARCH_PREPARED=false"
    exit 1
  fi
fi
rm -f "$COMMIT_ERR"

COMMIT=$(git rev-parse --short=7 HEAD 2>/dev/null)
echo "Committed: $COMMIT_MSG ($COMMIT, $FILES_CHANGED files)" >&2

echo "AUTORESEARCH_PREPARED=true"
echo "AUTORESEARCH_CHECKPOINT=$CHECKPOINT"
echo "AUTORESEARCH_COMMIT=$COMMIT"
echo "AUTORESEARCH_FILES_CHANGED=$FILES_CHANGED"
