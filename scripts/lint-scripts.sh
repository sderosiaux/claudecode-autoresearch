#!/bin/bash
set -uo pipefail

# lint-scripts.sh — Shellcheck all autoresearch bash scripts.
#
# Usage: lint-scripts.sh [directory]

SCRIPT_DIR="${1:-$(cd "$(dirname "$0")" && pwd)}"

if ! command -v shellcheck &>/dev/null; then
  echo "ERROR: shellcheck is required. Install: brew install shellcheck / apt install shellcheck" >&2
  exit 1
fi

ERRORS=0
for script in "$SCRIPT_DIR"/*.sh; do
  [[ "$(basename "$script")" == "lint-scripts.sh" ]] && continue
  if ! shellcheck -S warning "$script"; then
    ERRORS=$((ERRORS + 1))
  fi
done

if [[ $ERRORS -gt 0 ]]; then
  echo "$ERRORS script(s) failed." >&2
  exit 1
fi

echo "All scripts passed shellcheck."
