#!/bin/bash
set -euo pipefail

# test-scripts.sh — Integration tests for autoresearch scripts.
#
# Creates a temporary git project, runs experiments, verifies JSONL output,
# tests stop-hook behavior. No infinite loop — deterministic 3-iteration test.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SCRIPTS="$PLUGIN_ROOT/scripts"

PASS=0
FAIL=0
TESTS=0

assert_eq() {
  TESTS=$((TESTS + 1))
  local label="$1" expected="$2" actual="$3"
  if [[ "$expected" == "$actual" ]]; then
    PASS=$((PASS + 1))
    echo "  PASS: $label"
  else
    FAIL=$((FAIL + 1))
    echo "  FAIL: $label"
    echo "    expected: '$expected'"
    echo "    actual:   '$actual'"
  fi
}

assert_contains() {
  TESTS=$((TESTS + 1))
  local label="$1" needle="$2" haystack="$3"
  if echo "$haystack" | grep -q "$needle"; then
    PASS=$((PASS + 1))
    echo "  PASS: $label"
  else
    FAIL=$((FAIL + 1))
    echo "  FAIL: $label (not found: '$needle')"
  fi
}

assert_file_exists() {
  TESTS=$((TESTS + 1))
  local label="$1" file="$2"
  if [[ -f "$file" ]]; then
    PASS=$((PASS + 1))
    echo "  PASS: $label"
  else
    FAIL=$((FAIL + 1))
    echo "  FAIL: $label (file not found: $file)"
  fi
}

# --- Setup temp project ---
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

cd "$TMPDIR"
git init -q
git config user.email "test@test.com"
git config user.name "Test"

# Create a simple benchmark: count lines in a file
cat > benchmark.txt << 'EOF'
line1
line2
line3
line4
line5
line6
line7
line8
line9
line10
EOF

cat > autoresearch.sh << 'BENCH'
#!/bin/bash
set -euo pipefail
COUNT=$(wc -l < benchmark.txt | tr -d ' ')
echo "METRIC lines=$COUNT"
BENCH
chmod +x autoresearch.sh

git add -A
git commit -q -m "initial: 10 lines"

echo ""
echo "=== Test 1: run-experiment.sh ==="

OUTPUT=$("$SCRIPTS/run-experiment.sh" "./autoresearch.sh")
assert_contains "outputs METRIC line" "METRIC lines=10" "$OUTPUT"
assert_contains "outputs AUTORESEARCH_EXIT" "AUTORESEARCH_EXIT=0" "$OUTPUT"
assert_contains "outputs AUTORESEARCH_PASSED" "AUTORESEARCH_PASSED=true" "$OUTPUT"
assert_contains "outputs AUTORESEARCH_CRASHED" "AUTORESEARCH_CRASHED=false" "$OUTPUT"
assert_contains "outputs AUTORESEARCH_CHECKS" "AUTORESEARCH_CHECKS=skip" "$OUTPUT"

# Extract duration
DURATION=$(echo "$OUTPUT" | grep AUTORESEARCH_DURATION | cut -d= -f2)
assert_contains "duration is a number" "." "$DURATION"

echo ""
echo "=== Test 2: run-experiment.sh with checks ==="

cat > autoresearch.checks.sh << 'CHECKS'
#!/bin/bash
set -euo pipefail
# Simple check: file must have fewer than 20 lines
COUNT=$(wc -l < benchmark.txt | tr -d ' ')
if [[ $COUNT -ge 20 ]]; then
  echo "ERROR: too many lines ($COUNT >= 20)"
  exit 1
fi
echo "checks pass"
CHECKS
chmod +x autoresearch.checks.sh

OUTPUT=$("$SCRIPTS/run-experiment.sh" "./autoresearch.sh")
assert_contains "checks pass" "AUTORESEARCH_CHECKS=pass" "$OUTPUT"

echo ""
echo "=== Test 3: run-experiment.sh with crashing command ==="

OUTPUT=$("$SCRIPTS/run-experiment.sh" "exit 1" || true)
assert_contains "crashed" "AUTORESEARCH_CRASHED=true" "$OUTPUT"
assert_contains "not passed" "AUTORESEARCH_PASSED=false" "$OUTPUT"
assert_contains "checks skipped on crash" "AUTORESEARCH_CHECKS=skip" "$OUTPUT"

echo ""
echo "=== Test 4: log-experiment.sh (init + keep) ==="

# Write config
echo '{"type":"config","name":"line count","metricName":"lines","metricUnit":"","bestDirection":"lower"}' > autoresearch.jsonl

"$SCRIPTS/log-experiment.sh" keep 10 "baseline"
assert_file_exists "jsonl exists" "autoresearch.jsonl"

LINES=$(wc -l < autoresearch.jsonl | tr -d ' ')
assert_eq "jsonl has 2 lines (config + result)" "2" "$LINES"

LAST=$(tail -1 autoresearch.jsonl)
assert_contains "result has keep status" '"keep"' "$LAST"
assert_contains "result has metric 10" '"metric":10' "$LAST"

echo ""
echo "=== Test 5: log-experiment.sh (discard reverts uncommitted) ==="

# Make a change but do NOT commit (log-experiment handles git)
echo "extra line" >> benchmark.txt

"$SCRIPTS/log-experiment.sh" discard 11 "added line - worse"

# Check that the change was reverted
CURRENT_LINES=$(wc -l < benchmark.txt | tr -d ' ')
assert_eq "discard reverted changes" "10" "$CURRENT_LINES"

JSONL_LINES=$(wc -l < autoresearch.jsonl | tr -d ' ')
assert_eq "jsonl has 3 lines" "3" "$JSONL_LINES"

echo ""
echo "=== Test 6: status.sh ==="

OUTPUT=$("$SCRIPTS/status.sh" "autoresearch.jsonl")
assert_contains "shows session name" "line count" "$OUTPUT"
assert_contains "shows run count" "Runs:" "$OUTPUT"
assert_contains "shows kept count" "kept" "$OUTPUT"

echo ""
echo "=== Test 7: stop-hook.sh (no state file = allow exit) ==="

# Clean state dir
rm -rf ~/.claude/states/autoresearch
mkdir -p ~/.claude/states/autoresearch

HOOK_OUTPUT=$(echo '{"session_id":"test-session-123"}' | "$SCRIPTS/stop-hook.sh" 2>&1 || true)
EXIT_CODE=$?
# Should exit 0 (no state file)
assert_eq "no state file = exit 0" "0" "$EXIT_CODE"

echo ""
echo "=== Test 8: stop-hook.sh (with state file = blocks stop) ==="

STATE_FILE="$HOME/.claude/states/autoresearch/test123.md"
cat > "$STATE_FILE" << STATEEOF
---
session_id: test-session-456
cwd: "$TMPDIR"
last_resume: 0
---
Resume the autoresearch experiment loop. Read autoresearch.md for context.
STATEEOF

# Run stop hook — should exit 2
set +e
HOOK_STDERR=$(echo '{"session_id":"test-session-456","cwd":"'"$TMPDIR"'"}' | "$SCRIPTS/stop-hook.sh" 2>&1 1>/dev/null)
EXIT_CODE=$?
set -e

assert_eq "state file present = exit 2 (block)" "2" "$EXIT_CODE"
assert_contains "resume prompt injected" "Auto-Resume" "$HOOK_STDERR"

# Check last_resume was updated in state file
NEW_RESUME=$(grep 'last_resume:' "$STATE_FILE" | head -1 | sed 's/last_resume: *//')
TESTS=$((TESTS + 1))
if [[ "$NEW_RESUME" -gt 0 ]]; then
  PASS=$((PASS + 1))
  echo "  PASS: last_resume updated"
else
  FAIL=$((FAIL + 1))
  echo "  FAIL: last_resume not updated"
fi

echo ""
echo "=== Test 9: stop-hook.sh (maxExperiments reached = allow exit) ==="

# Add enough experiments to hit maxExperiments=3
echo '{"type":"config","name":"test","metricName":"x","metricUnit":"","bestDirection":"lower","maxExperiments":3}' > autoresearch.jsonl
echo '{"status":"keep","metric":10}' >> autoresearch.jsonl
echo '{"status":"keep","metric":9}' >> autoresearch.jsonl
echo '{"status":"discard","metric":11}' >> autoresearch.jsonl

# Recreate state file (previous test's hook may have updated last_resume)
cat > "$STATE_FILE" << STATEEOF
---
session_id: test-session-456
cwd: "$TMPDIR"
last_resume: 0
---
Resume loop.
STATEEOF

set +e
echo '{"session_id":"test-session-456","cwd":"'"$TMPDIR"'"}' | "$SCRIPTS/stop-hook.sh" 2>/dev/null
EXIT_CODE=$?
set -e

assert_eq "maxExperiments reached = exit 0" "0" "$EXIT_CODE"
# State file should be cleaned up
TESTS=$((TESTS + 1))
if [[ ! -f "$STATE_FILE" ]]; then
  PASS=$((PASS + 1))
  echo "  PASS: state file cleaned up after maxExperiments"
else
  FAIL=$((FAIL + 1))
  echo "  FAIL: state file still exists after maxExperiments"
fi

echo ""
echo "=== Test 10: context-hook.sh ==="

# Reset JSONL (test 9 may have filled it to maxExperiments)
echo '{"type":"config","name":"test","metricName":"x","metricUnit":"","bestDirection":"lower","maxExperiments":100}' > autoresearch.jsonl
echo '{"status":"keep","metric":10}' >> autoresearch.jsonl

# Test with JSONL present
CTX_OUTPUT=$(echo '{"cwd":"'"$TMPDIR"'","prompt":"optimize something"}' | "$SCRIPTS/context-hook.sh")
assert_contains "injects context" "AUTORESEARCH MODE ACTIVE" "$CTX_OUTPUT"

# Test without JSONL
EMPTY_DIR=$(mktemp -d)
CTX_OUTPUT=$(echo '{"cwd":"'"$EMPTY_DIR"'","prompt":"hello"}' | "$SCRIPTS/context-hook.sh" || true)
assert_eq "no jsonl = no context" "" "$CTX_OUTPUT"
rm -rf "$EMPTY_DIR"

# Test with stop command — should NOT inject
CTX_OUTPUT=$(echo '{"cwd":"'"$TMPDIR"'","prompt":"/autoresearch:stop"}' | "$SCRIPTS/context-hook.sh" || true)
assert_eq "stop command = no context" "" "$CTX_OUTPUT"

# --- Cleanup ---
rm -rf ~/.claude/states/autoresearch/test123.md 2>/dev/null

echo ""
echo "================================"
echo "Results: $PASS/$TESTS passed, $FAIL failed"
echo "================================"

if [[ $FAIL -gt 0 ]]; then
  exit 1
fi
