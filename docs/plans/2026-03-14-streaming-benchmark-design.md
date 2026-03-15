# Streaming Aggregation Benchmark

Autoresearch benchmark: streaming aggregation engine processing timestamped sensor events with tumbling + sliding windows, watermark-based late data handling, and incremental updates.

## Goal

Provide a Java benchmark with a wide optimization surface for autoresearch to explore autonomously. Naive baseline, no dependencies, single source file, correctness-checked.

## What it computes

Process a stream of CSV events (`timestamp,sensor_id,value`). Compute simultaneously:

- **Tumbling windows (1min):** count, sum, min, max, avg per sensor per window
- **Sliding windows (5min, 1min slide):** p50 and p99 per sensor
- **Watermark:** tracks event-time progress (oldest unprocessed - 10min slack), triggers emission
- **Late data (5% of events, up to 10min late):** updates already-emitted windows, re-emits with `updated` flag

Output: sorted window results to stdout.

## Metric

- **Primary:** throughput (events/sec, higher is better)
- **Secondary:** elapsed_ms, peak heap

## Files

```
examples/streaming-aggregation/
├── src/
│   ├── StreamingAggregator.java   # All logic (~500 lines naive)
│   ├── DataGenerator.java         # Deterministic synthetic data
│   └── BatchValidator.java        # Batch recomputation for correctness
├── autoresearch.sh                # Benchmark: compile, generate, run, report METRIC
├── autoresearch.checks.sh         # Correctness: compare output hash vs batch
└── README.md
```

## Data generation

Deterministic (seeded PRNG). 1000 sensors, 24h span, roughly monotonic with jitter. 5% late events (1-10min in the past). ~100 bytes/line.

- Dev: 10M events (~1GB)
- Full: 200M events (~20GB)

## Baseline approach

Single-threaded, clean OOP, zero perf thought:

- `BufferedReader.readLine()` + `String.split(",")` + `Instant.parse()`
- `HashMap<WindowKey, WindowState>`
- `List<Double>` for percentile values
- Simple watermark scan
- Collect all results, sort, write

## Optimization surface

Parsing, data structures, windowing algorithms, percentile sketches, parallelism, memory layout, I/O, JVM tuning, late data indexing.

## Decisions

- Java 21+ (latest features are part of the exploration)
- No build tool (javac + shell)
- Single-class monolith (maximum freedom for autoresearch)
- Two data scales (10M fast iteration, 200M validation)
- Textbook-correct naive baseline (not intentionally bad)
