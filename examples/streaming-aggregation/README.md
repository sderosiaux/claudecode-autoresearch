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
