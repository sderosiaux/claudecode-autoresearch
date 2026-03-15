#!/bin/bash
set -euo pipefail

SCALE="${1:-10m}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="$SCRIPT_DIR/data"
DATA="$DATA_DIR/measurements-${SCALE}.txt"
OUT_DIR="$SCRIPT_DIR/out"

export JAVA_HOME="${JAVA_HOME:-$(dirname $(dirname $(readlink -f $(which javac) 2>/dev/null || echo /opt/homebrew/opt/openjdk/libexec/openjdk.jdk/Contents/Home/bin/javac)))}"
export PATH="$JAVA_HOME/bin:$PATH"

mkdir -p "$OUT_DIR"

javac -d "$OUT_DIR" "$SCRIPT_DIR"/src/*.java 2>&1

if [[ ! -f "$DATA" ]]; then
    echo "Generating $SCALE dataset..." >&2
    java -cp "$OUT_DIR" DataGenerator "$SCALE" "$DATA"
fi

ROWS=$(wc -l < "$DATA" | tr -d ' ')

START_NS=$(date +%s%N)
java -cp "$OUT_DIR" StreamingAggregator "$DATA" > /tmp/autoresearch-streaming-output.txt
END_NS=$(date +%s%N)

ELAPSED_MS=$(( (END_NS - START_NS) / 1000000 ))
THROUGHPUT=$(echo "scale=0; $ROWS * 1000 / $ELAPSED_MS" | bc)

echo "METRIC throughput=$THROUGHPUT"
echo "METRIC elapsed_ms=$ELAPSED_MS"
echo "METRIC rows=$ROWS"
