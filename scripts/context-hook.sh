#!/bin/bash
set -uo pipefail

# context-hook.sh — Inject autoresearch context on UserPromptSubmit.
#
# When autoresearch.jsonl exists in the current working directory,
# adds a reminder to the prompt context so Claude stays in autoresearch mode.

HOOK_INPUT=$(cat)

CWD=$(echo "$HOOK_INPUT" | jq -r '.cwd // empty')
if [[ -z "$CWD" ]]; then
  exit 0
fi

JSONL="$CWD/autoresearch.jsonl"
if [[ ! -f "$JSONL" ]]; then
  exit 0
fi

# Check if this is a /autoresearch:stop command — don't inject context
PROMPT=$(echo "$HOOK_INPUT" | jq -r '.prompt // empty')
if [[ "$PROMPT" =~ autoresearch:stop ]] || [[ "$PROMPT" =~ autoresearch-stop ]]; then
  exit 0
fi

# Count experiments
TOTAL=$(grep -c '"status"' "$JSONL" 2>/dev/null || echo 0)
KEPT=$(grep -c '"keep"' "$JSONL" 2>/dev/null || echo 0)

# Check for ideas file
IDEAS=""
if [[ -f "$CWD/autoresearch.ideas.md" ]]; then
  IDEAS=" Ideas backlog exists at autoresearch.ideas.md."
fi

# Check for checks file
CHECKS=""
if [[ -x "$CWD/autoresearch.checks.sh" ]]; then
  CHECKS=" Backpressure checks active (autoresearch.checks.sh)."
fi

jq -nc --arg ctx "AUTORESEARCH MODE ACTIVE ($TOTAL runs, $KEPT kept). Use run-experiment.sh and log-experiment.sh from the plugin scripts. Read autoresearch.md for session context.${CHECKS}${IDEAS}" \
  '{additionalContext: $ctx}'
