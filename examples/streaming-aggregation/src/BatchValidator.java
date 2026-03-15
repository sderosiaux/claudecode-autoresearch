import java.io.*;
import java.time.Instant;
import java.util.*;

/**
 * Batch recomputation of streaming aggregation results.
 * Reads all events, assigns to windows, computes aggregates.
 * Output format matches StreamingAggregator exactly (with "new" flag).
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

        Map<WindowKey, List<Double>> tumblingValues = new HashMap<>();
        for (Event e : events) {
            long wStart = e.timestampMs - (e.timestampMs % TUMBLING_WINDOW_MS);
            WindowKey key = new WindowKey(e.sensorId, wStart, "tumbling");
            tumblingValues.computeIfAbsent(key, k -> new ArrayList<>()).add(e.value);
        }

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

        List<String> results = new ArrayList<>();

        for (Map.Entry<WindowKey, List<Double>> entry : tumblingValues.entrySet()) {
            WindowKey key = entry.getKey();
            List<Double> vals = entry.getValue();
            long count = vals.size();
            double sum = vals.stream().mapToDouble(Double::doubleValue).sum();
            double min = vals.stream().mapToDouble(Double::doubleValue).min().orElse(0);
            double max = vals.stream().mapToDouble(Double::doubleValue).max().orElse(0);
            double avg = sum / count;

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
