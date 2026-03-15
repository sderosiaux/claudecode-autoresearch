# Streaming Aggregation Benchmark — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a naive-but-correct streaming aggregation engine in Java as an autoresearch benchmark target.

**Architecture:** Single monolith Java file (StreamingAggregator.java) processes CSV events through tumbling + sliding windows with watermark-based late data handling. DataGenerator creates deterministic synthetic data. BatchValidator recomputes results for correctness checks. Shell scripts wire everything for autoresearch.

**Tech Stack:** Java 25 (OpenJDK via Homebrew, may need `export JAVA_HOME=$(/usr/libexec/java_home 2>/dev/null || echo /opt/homebrew/opt/openjdk/libexec/openjdk.jdk/Contents/Home)`), no build tool, shell scripts.

**Note:** Java is installed via Homebrew but not linked. Scripts must set JAVA_HOME explicitly.

---

### Task 1: Gitignore and directory structure

**Files:**
- Modify: `/.gitignore`
- Create: `examples/streaming-aggregation/src/` (directory)

**Step 1: Add examples/ to gitignore**

Append `examples/` to `.gitignore`.

**Step 2: Create directory structure**

```bash
mkdir -p examples/streaming-aggregation/src
mkdir -p examples/streaming-aggregation/data
```

**Step 3: Commit**

```bash
git add .gitignore
git commit -m "Add examples/ to gitignore"
```

---

### Task 2: DataGenerator.java

**Files:**
- Create: `examples/streaming-aggregation/src/DataGenerator.java`

Generates deterministic synthetic CSV data. Seeded PRNG for reproducibility.

**Step 1: Write DataGenerator.java**

```java
import java.io.*;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.Random;

public class DataGenerator {

    private static final int SENSOR_COUNT = 1000;
    private static final long SEED = 42L;
    private static final double LATE_EVENT_RATIO = 0.05;
    private static final int MAX_LATE_MINUTES = 10;

    // 24h span starting from a fixed epoch
    private static final Instant BASE_TIME = Instant.parse("2025-01-01T00:00:00Z");
    private static final long SPAN_SECONDS = 24 * 60 * 60;

    public static void main(String[] args) throws IOException {
        if (args.length < 2) {
            System.err.println("Usage: DataGenerator <scale: 10m|200m> <output-file>");
            System.exit(1);
        }

        long rowCount = parseScale(args[0]);
        String outputFile = args[1];

        System.err.printf("Generating %,d events to %s...%n", rowCount, outputFile);

        File parent = new File(outputFile).getParentFile();
        if (parent != null && !parent.exists()) {
            parent.mkdirs();
        }

        Random rng = new Random(SEED);
        String[] sensorIds = new String[SENSOR_COUNT];
        for (int i = 0; i < SENSOR_COUNT; i++) {
            sensorIds[i] = String.format("sensor_%04d", i);
        }

        try (BufferedWriter writer = new BufferedWriter(new FileWriter(outputFile), 1 << 20)) {
            for (long i = 0; i < rowCount; i++) {
                // Progress through the 24h span roughly linearly
                double progress = (double) i / rowCount;
                long baseOffsetSeconds = (long) (progress * SPAN_SECONDS);

                // Add jitter: +/- 30 seconds
                long jitter = (long) (rng.nextGaussian() * 15);
                long offsetSeconds = Math.max(0, Math.min(SPAN_SECONDS - 1, baseOffsetSeconds + jitter));

                Instant timestamp = BASE_TIME.plusSeconds(offsetSeconds);

                // 5% chance of being a late event (1-10 minutes in the past)
                if (rng.nextDouble() < LATE_EVENT_RATIO) {
                    int lateMinutes = 1 + rng.nextInt(MAX_LATE_MINUTES);
                    timestamp = timestamp.minus(lateMinutes, ChronoUnit.MINUTES);
                }

                String sensorId = sensorIds[rng.nextInt(SENSOR_COUNT)];
                double value = 20.0 + rng.nextGaussian() * 10.0; // temperature-like values

                writer.write(timestamp.toString());
                writer.write(',');
                writer.write(sensorId);
                writer.write(',');
                writer.write(String.format("%.2f", value));
                writer.newLine();

                if (i > 0 && i % 1_000_000 == 0) {
                    System.err.printf("  %,d / %,d (%.0f%%)%n", i, rowCount, 100.0 * i / rowCount);
                }
            }
        }

        System.err.printf("Done. %,d events written.%n", rowCount);
    }

    private static long parseScale(String scale) {
        return switch (scale.toLowerCase()) {
            case "10m" -> 10_000_000;
            case "50m" -> 50_000_000;
            case "100m" -> 100_000_000;
            case "200m" -> 200_000_000;
            default -> {
                try {
                    yield Long.parseLong(scale);
                } catch (NumberFormatException e) {
                    System.err.println("Unknown scale: " + scale + ". Use 10m, 50m, 100m, 200m, or a number.");
                    System.exit(1);
                    yield 0;
                }
            }
        };
    }
}
```

**Step 2: Compile and smoke test**

```bash
cd examples/streaming-aggregation
export JAVA_HOME=/opt/homebrew/opt/openjdk/libexec/openjdk.jdk/Contents/Home
export PATH="$JAVA_HOME/bin:$PATH"
javac -d out src/DataGenerator.java
java -cp out DataGenerator 10m data/measurements-10m.txt
head -5 data/measurements-10m.txt
wc -l data/measurements-10m.txt
```

Expected: 10,000,000 lines, each like `2025-01-01T00:00:01.000Z,sensor_0042,23.47`

**Step 3: Commit**

```bash
git add -f examples/streaming-aggregation/src/DataGenerator.java
git commit -m "Add DataGenerator for streaming aggregation benchmark"
```

Note: `-f` because examples/ is gitignored. We force-add source files only (not data or compiled output).

---

### Task 3: StreamingAggregator.java

**Files:**
- Create: `examples/streaming-aggregation/src/StreamingAggregator.java`

The core engine. Naive, correct, single-threaded, ~500 lines.

**Step 1: Write StreamingAggregator.java**

```java
import java.io.*;
import java.time.Instant;
import java.util.*;

public class StreamingAggregator {

    // --- Configuration ---
    static final long TUMBLING_WINDOW_MS = 60_000;          // 1 minute
    static final long SLIDING_WINDOW_MS = 5 * 60_000;       // 5 minutes
    static final long SLIDING_STEP_MS = 60_000;              // 1 minute slide
    static final long WATERMARK_SLACK_MS = 10 * 60_000;      // 10 minutes
    static final long ALLOWED_LATENESS_MS = 10 * 60_000;     // 10 minutes

    // --- Records ---
    record Event(long timestampMs, String sensorId, double value) {}

    record WindowKey(String sensorId, long windowStartMs, String windowType)
            implements Comparable<WindowKey> {
        @Override
        public int compareTo(WindowKey other) {
            int cmp = this.windowType.compareTo(other.windowType);
            if (cmp != 0) return cmp;
            cmp = Long.compare(this.windowStartMs, other.windowStartMs);
            if (cmp != 0) return cmp;
            return this.sensorId.compareTo(other.sensorId);
        }
    }

    static class TumblingState {
        long count = 0;
        double sum = 0;
        double min = Double.MAX_VALUE;
        double max = -Double.MAX_VALUE;

        void add(double value) {
            count++;
            sum += value;
            min = Math.min(min, value);
            max = Math.max(max, value);
        }

        double avg() {
            return count == 0 ? 0 : sum / count;
        }
    }

    static class SlidingState {
        final List<Double> values = new ArrayList<>();

        void add(double value) {
            values.add(value);
        }

        double percentile(double p) {
            if (values.isEmpty()) return 0;
            List<Double> sorted = new ArrayList<>(values);
            Collections.sort(sorted);
            int index = (int) Math.ceil(p / 100.0 * sorted.size()) - 1;
            return sorted.get(Math.max(0, index));
        }
    }

    // --- State ---
    private final Map<WindowKey, TumblingState> tumblingWindows = new HashMap<>();
    private final Map<WindowKey, SlidingState> slidingWindows = new HashMap<>();
    private final Set<WindowKey> emittedWindows = new HashSet<>();
    private final List<String> results = new ArrayList<>();
    private long watermarkMs = Long.MIN_VALUE;

    // --- Main ---
    public static void main(String[] args) throws IOException {
        if (args.length < 1) {
            System.err.println("Usage: StreamingAggregator <input-file>");
            System.exit(1);
        }

        StreamingAggregator aggregator = new StreamingAggregator();
        aggregator.run(args[0]);
    }

    void run(String inputFile) throws IOException {
        // Pass 1: process all events, track watermark, emit windows
        try (BufferedReader reader = new BufferedReader(new FileReader(inputFile))) {
            String line;
            long lineCount = 0;
            long maxEventTime = Long.MIN_VALUE;

            while ((line = reader.readLine()) != null) {
                Event event = parseLine(line);
                if (event == null) continue;

                lineCount++;
                maxEventTime = Math.max(maxEventTime, event.timestampMs);

                // Update watermark periodically (every 10k events)
                if (lineCount % 10_000 == 0) {
                    long newWatermark = maxEventTime - WATERMARK_SLACK_MS;
                    if (newWatermark > watermarkMs) {
                        watermarkMs = newWatermark;
                        emitReadyWindows();
                    }
                }

                // Assign to tumbling window
                long tumblingStart = event.timestampMs - (event.timestampMs % TUMBLING_WINDOW_MS);
                WindowKey tumblingKey = new WindowKey(event.sensorId, tumblingStart, "tumbling");
                tumblingWindows.computeIfAbsent(tumblingKey, k -> new TumblingState()).add(event.value);

                // Assign to all overlapping sliding windows
                long firstSlidingStart = event.timestampMs - SLIDING_WINDOW_MS + SLIDING_STEP_MS;
                firstSlidingStart = firstSlidingStart - (firstSlidingStart % SLIDING_STEP_MS);
                if (firstSlidingStart < 0) firstSlidingStart = 0;

                for (long wStart = firstSlidingStart; wStart <= event.timestampMs; wStart += SLIDING_STEP_MS) {
                    long wEnd = wStart + SLIDING_WINDOW_MS;
                    if (event.timestampMs >= wStart && event.timestampMs < wEnd) {
                        WindowKey slidingKey = new WindowKey(event.sensorId, wStart, "sliding");
                        slidingWindows.computeIfAbsent(slidingKey, k -> new SlidingState()).add(event.value);
                    }
                }
            }

            // Final watermark advance — emit all remaining windows
            watermarkMs = Long.MAX_VALUE;
            emitReadyWindows();
        }

        // Sort and output results
        Collections.sort(results);
        for (String result : results) {
            System.out.println(result);
        }
    }

    private Event parseLine(String line) {
        String[] parts = line.split(",");
        if (parts.length != 3) return null;
        try {
            long timestampMs = Instant.parse(parts[0]).toEpochMilli();
            String sensorId = parts[1];
            double value = Double.parseDouble(parts[2]);
            return new Event(timestampMs, sensorId, value);
        } catch (Exception e) {
            return null;
        }
    }

    private void emitReadyWindows() {
        // Emit tumbling windows whose end <= watermark
        Iterator<Map.Entry<WindowKey, TumblingState>> tIt = tumblingWindows.entrySet().iterator();
        while (tIt.hasNext()) {
            Map.Entry<WindowKey, TumblingState> entry = tIt.next();
            WindowKey key = entry.getKey();
            long windowEnd = key.windowStartMs + TUMBLING_WINDOW_MS;

            if (windowEnd <= watermarkMs) {
                TumblingState state = entry.getValue();
                boolean updated = emittedWindows.contains(key);
                emittedWindows.add(key);

                results.add(String.format("tumbling,%d,%s,%d,%.2f,%.2f,%.2f,%.2f,%s",
                        key.windowStartMs, key.sensorId,
                        state.count, state.sum, state.min, state.max, state.avg(),
                        updated ? "updated" : "new"));

                // Only remove if past allowed lateness
                if (windowEnd + ALLOWED_LATENESS_MS <= watermarkMs) {
                    tIt.remove();
                }
            }
        }

        // Emit sliding windows whose end <= watermark
        Iterator<Map.Entry<WindowKey, SlidingState>> sIt = slidingWindows.entrySet().iterator();
        while (sIt.hasNext()) {
            Map.Entry<WindowKey, SlidingState> entry = sIt.next();
            WindowKey key = entry.getKey();
            long windowEnd = key.windowStartMs + SLIDING_WINDOW_MS;

            if (windowEnd <= watermarkMs) {
                SlidingState state = entry.getValue();
                boolean updated = emittedWindows.contains(key);
                emittedWindows.add(key);

                results.add(String.format("sliding,%d,%s,%.2f,%.2f,%s",
                        key.windowStartMs, key.sensorId,
                        state.percentile(50), state.percentile(99),
                        updated ? "updated" : "new"));

                // Only remove if past allowed lateness
                if (windowEnd + ALLOWED_LATENESS_MS <= watermarkMs) {
                    sIt.remove();
                }
            }
        }
    }
}
```

**Step 2: Compile and smoke test with small data**

```bash
cd examples/streaming-aggregation
export JAVA_HOME=/opt/homebrew/opt/openjdk/libexec/openjdk.jdk/Contents/Home
export PATH="$JAVA_HOME/bin:$PATH"

# Generate tiny test set
java -cp out DataGenerator 1000 data/test-1k.txt
javac -d out src/StreamingAggregator.java
java -cp out StreamingAggregator data/test-1k.txt | head -20
java -cp out StreamingAggregator data/test-1k.txt | wc -l
```

Expected: output lines like `tumbling,1735689600000,sensor_0042,5,123.45,18.20,31.70,24.69,new` and `sliding,1735689600000,sensor_0042,22.50,30.10,new`. Should produce thousands of result lines.

**Step 3: Commit**

```bash
git add -f examples/streaming-aggregation/src/StreamingAggregator.java
git commit -m "Add StreamingAggregator: naive baseline for streaming benchmark"
```

---

### Task 4: BatchValidator.java

**Files:**
- Create: `examples/streaming-aggregation/src/BatchValidator.java`

Recomputes the same aggregation in a simple batch manner (read all events, assign to windows, compute). Used only for correctness checks — perf doesn't matter.

**Step 1: Write BatchValidator.java**

```java
import java.io.*;
import java.time.Instant;
import java.util.*;

/**
 * Batch recomputation of streaming aggregation results.
 * Reads all events, assigns to windows, computes aggregates.
 * Output format matches StreamingAggregator exactly.
 * Performance is irrelevant — correctness is the only goal.
 */
public class BatchValidator {

    static final long TUMBLING_WINDOW_MS = 60_000;
    static final long SLIDING_WINDOW_MS = 5 * 60_000;
    static final long SLIDING_STEP_MS = 60_000;

    record Event(long timestampMs, String sensorId, double value) {}
    record WindowKey(String sensorId, long windowStartMs, String windowType)
            implements Comparable<WindowKey> {
        @Override
        public int compareTo(WindowKey other) {
            int cmp = this.windowType.compareTo(other.windowType);
            if (cmp != 0) return cmp;
            cmp = Long.compare(this.windowStartMs, other.windowStartMs);
            if (cmp != 0) return cmp;
            return this.sensorId.compareTo(other.sensorId);
        }
    }

    public static void main(String[] args) throws IOException {
        if (args.length < 1) {
            System.err.println("Usage: BatchValidator <input-file>");
            System.exit(1);
        }

        // Read all events
        List<Event> events = new ArrayList<>();
        try (BufferedReader reader = new BufferedReader(new FileReader(args[0]))) {
            String line;
            while ((line = reader.readLine()) != null) {
                String[] parts = line.split(",");
                if (parts.length != 3) continue;
                try {
                    long ts = Instant.parse(parts[0]).toEpochMilli();
                    events.add(new Event(ts, parts[1], Double.parseDouble(parts[2])));
                } catch (Exception e) {
                    // skip malformed
                }
            }
        }

        // Tumbling windows
        Map<WindowKey, List<Double>> tumblingValues = new HashMap<>();
        for (Event e : events) {
            long wStart = e.timestampMs - (e.timestampMs % TUMBLING_WINDOW_MS);
            WindowKey key = new WindowKey(e.sensorId, wStart, "tumbling");
            tumblingValues.computeIfAbsent(key, k -> new ArrayList<>()).add(e.value);
        }

        // Sliding windows
        Map<WindowKey, List<Double>> slidingValues = new HashMap<>();
        for (Event e : events) {
            long firstStart = e.timestampMs - SLIDING_WINDOW_MS + SLIDING_STEP_MS;
            firstStart = firstStart - (firstStart % SLIDING_STEP_MS);
            if (firstStart < 0) firstStart = 0;

            for (long wStart = firstStart; wStart <= e.timestampMs; wStart += SLIDING_STEP_MS) {
                long wEnd = wStart + SLIDING_WINDOW_MS;
                if (e.timestampMs >= wStart && e.timestampMs < wEnd) {
                    WindowKey key = new WindowKey(e.sensorId, wStart, "sliding");
                    slidingValues.computeIfAbsent(key, k -> new ArrayList<>()).add(e.value);
                }
            }
        }

        // Format and sort results
        List<String> results = new ArrayList<>();

        for (Map.Entry<WindowKey, List<Double>> entry : tumblingValues.entrySet()) {
            WindowKey key = entry.getKey();
            List<Double> vals = entry.getValue();
            long count = vals.size();
            double sum = vals.stream().mapToDouble(Double::doubleValue).sum();
            double min = vals.stream().mapToDouble(Double::doubleValue).min().orElse(0);
            double max = vals.stream().mapToDouble(Double::doubleValue).max().orElse(0);
            double avg = sum / count;

            // BatchValidator always emits "new" — it has no concept of re-emission
            results.add(String.format("tumbling,%d,%s,%d,%.2f,%.2f,%.2f,%.2f,new",
                    key.windowStartMs, key.sensorId, count, sum, min, max, avg));
        }

        for (Map.Entry<WindowKey, List<Double>> entry : slidingValues.entrySet()) {
            WindowKey key = entry.getKey();
            List<Double> vals = entry.getValue();
            Collections.sort(vals);
            double p50 = percentile(vals, 50);
            double p99 = percentile(vals, 99);

            results.add(String.format("sliding,%d,%s,%.2f,%.2f,new",
                    key.windowStartMs, key.sensorId, p50, p99));
        }

        Collections.sort(results);
        for (String r : results) {
            System.out.println(r);
        }
    }

    private static double percentile(List<Double> sorted, double p) {
        if (sorted.isEmpty()) return 0;
        int index = (int) Math.ceil(p / 100.0 * sorted.size()) - 1;
        return sorted.get(Math.max(0, index));
    }
}
```

**Step 2: Cross-validate with StreamingAggregator on 1k rows**

```bash
cd examples/streaming-aggregation
export JAVA_HOME=/opt/homebrew/opt/openjdk/libexec/openjdk.jdk/Contents/Home
export PATH="$JAVA_HOME/bin:$PATH"
javac -d out src/BatchValidator.java

java -cp out StreamingAggregator data/test-1k.txt | sort > /tmp/streaming-out.txt
java -cp out BatchValidator data/test-1k.txt | sort > /tmp/batch-out.txt

# Strip "updated" flags from streaming output (batch always says "new")
sed 's/,updated$/,new/' /tmp/streaming-out.txt > /tmp/streaming-normalized.txt
diff /tmp/streaming-normalized.txt /tmp/batch-out.txt
```

Expected: no diff. If there is, fix StreamingAggregator until outputs match.

**Step 3: Commit**

```bash
git add -f examples/streaming-aggregation/src/BatchValidator.java
git commit -m "Add BatchValidator for correctness checks"
```

---

### Task 5: autoresearch.sh

**Files:**
- Create: `examples/streaming-aggregation/autoresearch.sh`

**Step 1: Write autoresearch.sh**

```bash
#!/bin/bash
set -euo pipefail

# Autoresearch benchmark script for streaming aggregation
SCALE="${1:-10m}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="$SCRIPT_DIR/data"
DATA="$DATA_DIR/measurements-${SCALE}.txt"
OUT_DIR="$SCRIPT_DIR/out"

# Find Java
export JAVA_HOME="${JAVA_HOME:-/opt/homebrew/opt/openjdk/libexec/openjdk.jdk/Contents/Home}"
export PATH="$JAVA_HOME/bin:$PATH"

# Compile
javac -d "$OUT_DIR" "$SCRIPT_DIR"/src/*.java 2>&1

# Generate data if missing
if [[ ! -f "$DATA" ]]; then
    echo "Generating $SCALE dataset..." >&2
    java -cp "$OUT_DIR" DataGenerator "$SCALE" "$DATA"
fi

ROWS=$(wc -l < "$DATA" | tr -d ' ')

# Run and time
START_NS=$(date +%s%N)
java -cp "$OUT_DIR" StreamingAggregator "$DATA" > /tmp/autoresearch-streaming-output.txt
END_NS=$(date +%s%N)

ELAPSED_MS=$(( (END_NS - START_NS) / 1000000 ))
THROUGHPUT=$(echo "scale=0; $ROWS * 1000 / $ELAPSED_MS" | bc)

echo "METRIC throughput=$THROUGHPUT"
echo "METRIC elapsed_ms=$ELAPSED_MS"
echo "METRIC rows=$ROWS"
```

**Step 2: Make executable and smoke test**

```bash
chmod +x examples/streaming-aggregation/autoresearch.sh
cd examples/streaming-aggregation
./autoresearch.sh 10m
```

Expected: outputs `METRIC throughput=<number>`, `METRIC elapsed_ms=<number>`, `METRIC rows=10000000`

**Step 3: Commit**

```bash
git add -f examples/streaming-aggregation/autoresearch.sh
git commit -m "Add autoresearch.sh benchmark script"
```

---

### Task 6: autoresearch.checks.sh

**Files:**
- Create: `examples/streaming-aggregation/autoresearch.checks.sh`

**Step 1: Write autoresearch.checks.sh**

```bash
#!/bin/bash
set -euo pipefail

# Correctness check: compare streaming output vs batch recomputation
# Uses a small dataset (1k events) for speed — checks run every experiment.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUT_DIR="$SCRIPT_DIR/out"
CHECK_DATA="$SCRIPT_DIR/data/check-1k.txt"

export JAVA_HOME="${JAVA_HOME:-/opt/homebrew/opt/openjdk/libexec/openjdk.jdk/Contents/Home}"
export PATH="$JAVA_HOME/bin:$PATH"

# Generate check dataset if missing (tiny — 1k events)
if [[ ! -f "$CHECK_DATA" ]]; then
    java -cp "$OUT_DIR" DataGenerator 1000 "$CHECK_DATA"
fi

# Run both
java -cp "$OUT_DIR" StreamingAggregator "$CHECK_DATA" | sed 's/,updated$/,new/' | sort > /tmp/check-streaming.txt
java -cp "$OUT_DIR" BatchValidator "$CHECK_DATA" | sort > /tmp/check-batch.txt

# Compare
if diff -q /tmp/check-streaming.txt /tmp/check-batch.txt > /dev/null 2>&1; then
    echo "CHECKS PASSED" >&2
else
    echo "CHECKS FAILED: output mismatch" >&2
    diff /tmp/check-streaming.txt /tmp/check-batch.txt | head -20 >&2
    exit 1
fi
```

**Step 2: Make executable and test**

```bash
chmod +x examples/streaming-aggregation/autoresearch.checks.sh
cd examples/streaming-aggregation
./autoresearch.checks.sh
```

Expected: `CHECKS PASSED`

**Step 3: Commit**

```bash
git add -f examples/streaming-aggregation/autoresearch.checks.sh
git commit -m "Add autoresearch.checks.sh correctness validation"
```

---

### Task 7: README

**Files:**
- Create: `examples/streaming-aggregation/README.md`

**Step 1: Write README**

```markdown
# Streaming Aggregation Benchmark

Autoresearch benchmark target: streaming window aggregation engine in Java.

## What it does

Processes a stream of timestamped sensor events (CSV). Computes tumbling windows (1min: count/sum/min/max/avg per sensor) and sliding windows (5min/1min slide: p50/p99 per sensor). Handles 5% late data with watermark-based emission and window re-computation.

## Quick start

```bash
# Generate 10M events and run benchmark
./autoresearch.sh 10m

# Run correctness checks
./autoresearch.checks.sh
```

## Use with autoresearch

```
/autoresearch:create optimize streaming aggregation throughput
```

Files in scope: `src/StreamingAggregator.java`
Off limits: `src/DataGenerator.java`, `src/BatchValidator.java`
Checks: `./autoresearch.checks.sh`

## Data scales

- `10m` — 10M events (~1GB), ~30s naive baseline. Use for fast iteration.
- `200m` — 200M events (~20GB). Use for final validation.

## Optimization surface

Parsing, data structures, windowing algorithms, percentile sketches, parallelism, memory layout, I/O, JVM tuning, late data indexing.
```

**Step 2: Commit**

```bash
git add -f examples/streaming-aggregation/README.md
git commit -m "Add README for streaming aggregation benchmark"
```

---

### Task 8: Future benchmark stubs

**Files:**
- Create: `examples/columnar-query-engine/README.md`
- Create: `examples/log-analytics/README.md`

**Step 1: Write columnar-query-engine stub**

```markdown
# Columnar Query Engine Benchmark

Autoresearch benchmark target: mini columnar query engine in Java.

## Concept

Load a 500M-row CSV into columnar layout in memory. Run a fixed analytical query suite (filter, group by, aggregate, order by, limit).

## Optimization surface

Dictionary encoding, vectorized execution, predicate pushdown, batch processing, SIMD-friendly layouts, column compression, parallel partition scans.

## Status

Not yet implemented. See streaming-aggregation/ for a working example.
```

**Step 2: Write log-analytics stub**

```markdown
# Log Analytics Benchmark

Autoresearch benchmark target: log analytics engine in Java.

## Concept

Process 100M nginx access log lines. Compute simultaneously: top-100 URLs by p99 latency, error rate per 5-min window, session reconstruction by IP, anomaly detection (p99 > 3x rolling median).

## Optimization surface

Complex parsing, concurrent data structures, streaming percentile algorithms (t-digest/HDR histogram), time-series bucketing, memory-efficient top-K.

## Status

Not yet implemented. See streaming-aggregation/ for a working example.
```

**Step 3: Commit**

```bash
git add -f examples/columnar-query-engine/README.md examples/log-analytics/README.md
git commit -m "Add stub READMEs for future benchmarks: columnar query engine, log analytics"
```

---

Plan complete and saved to `docs/plans/2026-03-14-streaming-benchmark-impl.md`. Two execution options:

**1. Subagent-Driven (this session)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Parallel Session (separate)** — Open new session with executing-plans, batch execution with checkpoints

Which approach?