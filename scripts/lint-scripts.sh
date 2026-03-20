#!/bin/bash
set -uo pipefail

# lint-scripts.sh — Static checks on autoresearch bash scripts.
#
# Run before releases to catch common bugs.
# Usage: lint-scripts.sh [directory]

SCRIPT_DIR="${1:-$(cd "$(dirname "$0")" && pwd)}"
ERRORS=0

fail() {
  echo "FAIL: $1" >&2
  ERRORS=$((ERRORS + 1))
}

for script in "$SCRIPT_DIR"/*.sh; do
  [[ "$(basename "$script")" == "lint-scripts.sh" ]] && continue
  name=$(basename "$script")

  # 1. Syntax check
  if ! bash -n "$script" 2>/dev/null; then
    fail "$name — syntax error:"
    bash -n "$script" 2>&1 | head -5 >&2
    continue
  fi

  # 2. Dangerous $(grep -c ... || echo ...) — double-output bug
  #    grep -c outputs "0" AND exits 1 on no matches, || echo 0 adds another "0"
  HITS=$(grep -nE '\$\(.*grep -c .+\|\|.*echo' "$script" 2>/dev/null || true)
  if [[ -n "$HITS" ]]; then
    fail "$name — dangerous \$(grep -c ... || echo ...) pattern (double-output bug):"
    echo "$HITS" | sed 's/^/  /' >&2
    echo "  Fix: VAR=\$(grep -c ...) || VAR=0" >&2
  fi

  # 3. $(( $(cmd || echo X) )) — arithmetic with potential multi-line substitution
  HITS=$(grep -nE '\$\(\(.*\$\(.*\|\|.*echo' "$script" 2>/dev/null || true)
  if [[ -n "$HITS" ]]; then
    fail "$name — dangerous \$(( \$(cmd || echo X) )) pattern:"
    echo "$HITS" | sed 's/^/  /' >&2
    echo "  Fix: split into VAR=\$(cmd) || VAR=0; then \$(( VAR + 1 ))" >&2
  fi

  # 4. Unquoted $SORT_FLAG or similar that may be empty with set -u
  HITS=$(grep -nE 'sort .* \$[A-Z_]+[^"]' "$script" 2>/dev/null | grep -v '"\$' || true)
  if [[ -n "$HITS" ]]; then
    fail "$name — potentially unquoted variable in sort command (may break with set -u):"
    echo "$HITS" | sed 's/^/  /' >&2
  fi

  # 5. shellcheck (errors only) if available
  if command -v shellcheck &>/dev/null; then
    SC_OUT=$(shellcheck -S error -f gcc "$script" 2>/dev/null || true)
    if [[ -n "$SC_OUT" ]]; then
      fail "$name — shellcheck errors:"
      echo "$SC_OUT" | head -10 | sed 's/^/  /' >&2
    fi
  fi
done

echo ""
if [[ $ERRORS -gt 0 ]]; then
  echo "$ERRORS issue(s) found." >&2
  exit 1
fi

echo "All scripts passed lint checks."
exit 0
