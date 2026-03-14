#!/bin/bash
set -euo pipefail

# demo.sh — Simulate a full autoresearch session from scratch.
#
# Creates a temp project, runs 5 iterations (keep, keep, discard, crash, checks_failed),
# prints the dashboard. No Claude Code needed — just bash + git + node + jq.
#
# Usage: bash tests/demo.sh

PLUGIN="$(cd "$(dirname "$0")/.." && pwd)"
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT

cd "$T"
git init -q
git config user.email "demo@test.com"
git config user.name "Demo"

# --- Project setup ---
cat > app.js << 'EOF'
function processData(items) {
  const results = [];
  for (let i = 0; i < items.length; i++) {
    const item = items[i];
    const processed = item.toString();
    const trimmed = processed.trim();
    const upper = trimmed.toUpperCase();
    const lower = upper.toLowerCase();
    const final = lower.trim();
    results.push(final);
  }
  return results;
}

function validateInput(input) {
  if (input === null) { return false; }
  if (input === undefined) { return false; }
  if (typeof input !== 'object') { return false; }
  return true;
}

module.exports = { processData, validateInput };
EOF

cat > autoresearch.sh << 'BENCH'
#!/bin/bash
set -euo pipefail
node -c app.js >/dev/null 2>&1
echo "METRIC lines=$(wc -l < app.js | tr -d ' ')"
BENCH
chmod +x autoresearch.sh

cat > autoresearch.checks.sh << 'CHECKS'
#!/bin/bash
set -euo pipefail
node -e "const m=require('./app.js'); if(!m.processData||!m.validateInput) process.exit(1)"
CHECKS
chmod +x autoresearch.checks.sh

echo '{"type":"config","name":"reduce line count in app.js","metricName":"lines","metricUnit":"","bestDirection":"lower"}' > autoresearch.jsonl

git add -A && git commit -q -m "initial setup"

echo ""
echo "==========================================="
echo "  claudecode-autoresearch demo"
echo "  Goal: reduce lines in app.js (lower = better)"
echo "==========================================="
echo ""

# --- Iteration 0: Baseline ---
echo "--- Run 1: Baseline ---"
"$PLUGIN/scripts/run-experiment.sh" ./autoresearch.sh 2>&1 | grep "METRIC\|AUTORESEARCH_PASSED\|AUTORESEARCH_CHECKS"
"$PLUGIN/scripts/log-experiment.sh" keep 28 "baseline (28 lines)"
echo ""

# --- Iteration 1: Simplify with .map() ---
echo "--- Run 2: Simplify processData with .map() ---"
cat > app.js << 'EOF'
function processData(items) {
  return items.map(item => item.toString().trim().toLowerCase().trim());
}

function validateInput(input) {
  if (input === null) { return false; }
  if (input === undefined) { return false; }
  if (typeof input !== 'object') { return false; }
  return true;
}

module.exports = { processData, validateInput };
EOF
"$PLUGIN/scripts/run-experiment.sh" ./autoresearch.sh 2>&1 | grep "METRIC\|AUTORESEARCH_PASSED\|AUTORESEARCH_CHECKS"
"$PLUGIN/scripts/log-experiment.sh" keep 12 "simplify processData with .map() chain"
echo ""

# --- Iteration 2: Add comments (worse — should discard) ---
echo "--- Run 3: Add JSDoc comments (more lines = worse) ---"
cat > app.js << 'EOF'
/**
 * Processes items into lowercase trimmed strings.
 * @param {Array} items - Input items
 * @returns {string[]} Processed strings
 */
function processData(items) {
  return items.map(item => item.toString().trim().toLowerCase().trim());
}

/**
 * Validates that input is a non-null object.
 * @param {*} input
 * @returns {boolean}
 */
function validateInput(input) {
  if (input === null) { return false; }
  if (input === undefined) { return false; }
  if (typeof input !== 'object') { return false; }
  return true;
}

module.exports = { processData, validateInput };
EOF
"$PLUGIN/scripts/run-experiment.sh" ./autoresearch.sh 2>&1 | grep "METRIC\|AUTORESEARCH_PASSED\|AUTORESEARCH_CHECKS"
"$PLUGIN/scripts/log-experiment.sh" discard 22 "added JSDoc — more lines, no benefit"
echo ""

# --- Iteration 3: Break syntax (crash) ---
echo "--- Run 4: Introduce syntax error (crash) ---"
echo "function {{BROKEN" > app.js
"$PLUGIN/scripts/run-experiment.sh" ./autoresearch.sh 2>&1 | grep "METRIC\|AUTORESEARCH_PASSED\|AUTORESEARCH_CRASHED"
"$PLUGIN/scripts/log-experiment.sh" crash 0 "syntax error — reverted"
echo ""

# --- Iteration 4: Remove export (checks fail) ---
echo "--- Run 5: Remove validateInput export (checks fail) ---"
cat > app.js << 'EOF'
function processData(items) {
  return items.map(item => item.toString().trim().toLowerCase().trim());
}

module.exports = { processData };
EOF
"$PLUGIN/scripts/run-experiment.sh" ./autoresearch.sh 2>&1 | grep "METRIC\|AUTORESEARCH_PASSED\|AUTORESEARCH_CHECKS"
"$PLUGIN/scripts/log-experiment.sh" checks_failed 4 "removed validateInput — checks caught it"
echo ""

# --- Dashboard ---
echo "==========================================="
echo "  Final dashboard"
echo "==========================================="
echo ""
"$PLUGIN/scripts/status.sh"
echo ""
echo "JSONL log:"
cat autoresearch.jsonl
echo ""
echo "Current app.js (should be the 12-line version from Run 2):"
cat app.js
echo ""
echo "Git log:"
git log --oneline
