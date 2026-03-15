# Log Analytics Benchmark

Autoresearch benchmark target: log analytics engine in Java.

## Concept

Process 100M nginx access log lines. Compute simultaneously: top-100 URLs by p99 latency, error rate per 5-min window, session reconstruction by IP, anomaly detection (p99 > 3x rolling median).

## Optimization surface

Complex parsing, concurrent data structures, streaming percentile algorithms (t-digest/HDR histogram), time-series bucketing, memory-efficient top-K.

## Status

Not yet implemented. See streaming-aggregation/ for a working example.
