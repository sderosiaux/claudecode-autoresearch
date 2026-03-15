# Columnar Query Engine Benchmark

Autoresearch benchmark target: mini columnar query engine in Java.

## Concept

Load a 500M-row CSV into columnar layout in memory. Run a fixed analytical query suite (filter, group by, aggregate, order by, limit).

## Optimization surface

Dictionary encoding, vectorized execution, predicate pushdown, batch processing, SIMD-friendly layouts, column compression, parallel partition scans.

## Status

Not yet implemented. See streaming-aggregation/ for a working example.
