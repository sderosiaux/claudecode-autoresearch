#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUT_DIR="$SCRIPT_DIR/out"
CHECK_DATA="$SCRIPT_DIR/data/check-1k.txt"

export JAVA_HOME="${JAVA_HOME:-$(dirname $(dirname $(readlink -f $(which javac) 2>/dev/null || echo /opt/homebrew/opt/openjdk/libexec/openjdk.jdk/Contents/Home/bin/javac)))}"
export PATH="$JAVA_HOME/bin:$PATH"

if [[ ! -f "$CHECK_DATA" ]]; then
    java -cp "$OUT_DIR" DataGenerator 1000 "$CHECK_DATA"
fi

java -cp "$OUT_DIR" StreamingAggregator "$CHECK_DATA" | sed 's/,updated$/,new/' | sort > /tmp/check-streaming.txt
java -cp "$OUT_DIR" BatchValidator "$CHECK_DATA" | sort > /tmp/check-batch.txt

if diff -q /tmp/check-streaming.txt /tmp/check-batch.txt > /dev/null 2>&1; then
    echo "CHECKS PASSED" >&2
else
    echo "CHECKS FAILED: output mismatch" >&2
    diff /tmp/check-streaming.txt /tmp/check-batch.txt | head -20 >&2
    exit 1
fi
