import java.io.*;
import java.time.Instant;
import java.util.*;

public class StreamingAggregator {

    static final long TUMBLING_WINDOW_MS = 60_000;
    static final long SLIDING_WINDOW_MS = 5 * 60_000;
    static final long SLIDING_STEP_MS = 60_000;
    static final long WATERMARK_SLACK_MS = 10 * 60_000;
    static final long ALLOWED_LATENESS_MS = 10 * 60_000;

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

    private final Map<WindowKey, TumblingState> tumblingWindows = new HashMap<>();
    private final Map<WindowKey, SlidingState> slidingWindows = new HashMap<>();
    private final Set<WindowKey> emittedWindows = new HashSet<>();
    private final List<String> results = new ArrayList<>();
    private long watermarkMs = Long.MIN_VALUE;

    public static void main(String[] args) throws IOException {
        if (args.length < 1) {
            System.err.println("Usage: StreamingAggregator <input-file>");
            System.exit(1);
        }

        StreamingAggregator aggregator = new StreamingAggregator();
        aggregator.run(args[0]);
    }

    void run(String inputFile) throws IOException {
        try (BufferedReader reader = new BufferedReader(new FileReader(inputFile))) {
            String line;
            long lineCount = 0;
            long maxEventTime = Long.MIN_VALUE;

            while ((line = reader.readLine()) != null) {
                Event event = parseLine(line);
                if (event == null) continue;

                lineCount++;
                maxEventTime = Math.max(maxEventTime, event.timestampMs);

                if (lineCount % 10_000 == 0) {
                    long newWatermark = maxEventTime - WATERMARK_SLACK_MS;
                    if (newWatermark > watermarkMs) {
                        watermarkMs = newWatermark;
                        emitReadyWindows();
                    }
                }

                long tumblingStart = event.timestampMs - (event.timestampMs % TUMBLING_WINDOW_MS);
                WindowKey tumblingKey = new WindowKey(event.sensorId, tumblingStart, "tumbling");
                tumblingWindows.computeIfAbsent(tumblingKey, k -> new TumblingState()).add(event.value);

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

            watermarkMs = Long.MAX_VALUE;
            emitReadyWindows();
        }

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

                if (windowEnd + ALLOWED_LATENESS_MS <= watermarkMs) {
                    tIt.remove();
                }
            }
        }

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

                if (windowEnd + ALLOWED_LATENESS_MS <= watermarkMs) {
                    sIt.remove();
                }
            }
        }
    }
}
