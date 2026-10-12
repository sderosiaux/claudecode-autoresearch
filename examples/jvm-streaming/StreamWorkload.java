import java.lang.management.ManagementFactory;
import java.lang.management.MemoryMXBean;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.locks.LockSupport;

/** Public synthetic stream: deterministic payloads, real processing and bounded state. */
public final class StreamWorkload {
    static final int CORPUS_SIZE = 4096;
    static final int PAYLOAD_SIZE = 1024;
    static final byte[][] CORPUS = new byte[CORPUS_SIZE][PAYLOAD_SIZE];
    static final long[] EXPECTED_CRC = new long[CORPUS_SIZE];
    static volatile boolean sampling;
    static long sampledHeapPeak, sampledRssPeak;
    static final List<long[]> MEMORY = new ArrayList<>();
    static final MemoryMXBean MEMORY_BEAN = ManagementFactory.getMemoryMXBean();
    static final com.sun.management.ThreadMXBean THREADS =
        (com.sun.management.ThreadMXBean) ManagementFactory.getThreadMXBean();
    static final com.sun.management.OperatingSystemMXBean OS =
        (com.sun.management.OperatingSystemMXBean) ManagementFactory.getOperatingSystemMXBean();

    static long referenceCrc(byte[] data) {
        int crc = -1;
        for (byte b : data) {
            crc ^= b & 255;
            for (int bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ ((crc & 1) != 0 ? 0xedb88320 : 0);
        }
        return Integer.toUnsignedLong(~crc);
    }
    static void initialize() {
        long seed = 0x5eed;
        for (int i = 0; i < CORPUS_SIZE; i++) {
            for (int j = 0; j < PAYLOAD_SIZE; j++) {
                seed = seed * 6364136223846793005L + 1442695040888963407L;
                CORPUS[i][j] = (byte) (seed >>> 56);
            }
            EXPECTED_CRC[i] = referenceCrc(CORPUS[i]);
        }
    }
    static long rss() {
        try {
            for (String line : Files.readAllLines(Path.of("/proc/self/status")))
                if (line.startsWith("VmRSS:")) return Long.parseLong(line.trim().split("\\s+")[1]) * 1024;
        } catch (Exception unavailable) { /* Absent metrics are omitted, never zero-filled. */ }
        return -1;
    }
    static void sample(long start) {
        long heap = MEMORY_BEAN.getHeapMemoryUsage().getUsed();
        long rss = rss();
        sampledHeapPeak = Math.max(sampledHeapPeak, heap);
        if (rss >= 0) sampledRssPeak = Math.max(sampledRssPeak, rss);
        MEMORY.add(new long[]{System.nanoTime() - start, heap, rss});
    }
    record Result(long count, long checksum, long expected, double seconds, long allocated,
                  long cpuNanos, long[] latency, long startNanos, long endNanos) {}
    record MemoryReading(long elapsedAfterMeasurementNanos, long heap, long rss) {
        String json() {
            return "{\"elapsed_after_measurement_ns\":" + elapsedAfterMeasurementNanos +
                ",\"heap_bytes\":" + heap + ",\"rss_bytes\":" + (rss < 0 ? "null" : rss) + "}";
        }
    }
    static MemoryReading readMemoryAfter(long endNanos) {
        long heap = MEMORY_BEAN.getHeapMemoryUsage().getUsed();
        long rss = rss();
        return new MemoryReading(System.nanoTime() - endNanos, heap, rss);
    }
    static Result run(double duration, long rate, boolean measure) throws Exception {
        long maxMessages = rate == 0 ? Long.MAX_VALUE : (long) (duration * rate);
        if (rate > 0 && (maxMessages < 1 || maxMessages > 2_000_000))
            throw new IllegalArgumentException("paced scenario requires 1..2000000 messages");
        long[] latency = measure && rate > 0 ? new long[(int) maxMessages] : null;
        long count = 0, checksum = 0, expected = 0;
        StreamProcessor processor = new StreamProcessor();
        long tid = Thread.currentThread().threadId();
        boolean allocatedSupported = THREADS.isThreadAllocatedMemorySupported();
        if (allocatedSupported && !THREADS.isThreadAllocatedMemoryEnabled()) THREADS.setThreadAllocatedMemoryEnabled(true);
        long beforeAllocated = allocatedSupported ? THREADS.getThreadAllocatedBytes(tid) : -1;
        long beforeCpu = OS.getProcessCpuTime();
        long start = System.nanoTime();
        Thread sampler = null;
        if (measure) {
            sample(start); sampling = true;
            sampler = Thread.ofPlatform().daemon().start(() -> {
                while (sampling) {
                    LockSupport.parkNanos(20_000_000);
                    if (sampling) sample(start);
                }
            });
        }
        do {
            int batch = (int) Math.min(StreamProcessor.BATCH_SIZE, maxMessages - count);
            if (rate > 0) {
                // Batch waits for its final scheduled arrival. Latency starts at
                // each original due time, exposing batching and generator stalls.
                long lastDue = start + (long) ((count + batch - 1) * (1_000_000_000.0 / rate));
                while (System.nanoTime() < lastDue) {
                    long remaining = lastDue - System.nanoTime();
                    if (remaining > 100_000) LockSupport.parkNanos(remaining - 50_000);
                    else Thread.onSpinWait();
                }
            }
            for (int i = 0; i < batch; i++) {
                int index = (int) (count % CORPUS_SIZE);
                checksum += processor.process(CORPUS[index]); expected += EXPECTED_CRC[index];
                if (latency != null) {
                    long due = start + (long) (count * (1_000_000_000.0 / rate));
                    latency[(int) count] = System.nanoTime() - due;
                }
                count++;
            }
        } while (count < maxMessages && (rate > 0 || System.nanoTime() - start < duration * 1e9));
        long end = System.nanoTime();
        double seconds = (end - start) / 1e9;
        long allocated = allocatedSupported ? THREADS.getThreadAllocatedBytes(tid) - beforeAllocated : -1;
        long afterCpu = OS.getProcessCpuTime();
        long cpu = beforeCpu >= 0 && afterCpu >= 0 ? afterCpu - beforeCpu : -1;
        if (measure) {
            sampling = false; sampler.join();
            // A sampler read may finish concurrently with the measurement end.
            // Keep only readings whose timestamps fall inside the timed window.
            MEMORY.removeIf(point -> point[0] > end - start);
            sampledHeapPeak = MEMORY.stream().mapToLong(point -> point[1]).max().orElseThrow();
            sampledRssPeak = MEMORY.stream().mapToLong(point -> point[2]).max().orElse(-1);
        }
        return new Result(count, checksum, expected, seconds, allocated, cpu, latency, start, end);
    }
    public static void main(String[] args) throws Exception {
        Locale.setDefault(Locale.ROOT);
        double duration = 2, warmup = 1, quiescence = 1;
        long rate = 0;
        for (int i = 0; i < args.length; i += 2) {
            if (i + 1 >= args.length) throw new IllegalArgumentException("arguments require values");
            switch (args[i]) {
                case "--duration" -> duration = Double.parseDouble(args[i + 1]);
                case "--warmup" -> warmup = Double.parseDouble(args[i + 1]);
                case "--rate" -> rate = Long.parseLong(args[i + 1]);
                case "--quiescence" -> quiescence = Double.parseDouble(args[i + 1]);
                default -> throw new IllegalArgumentException("unknown argument " + args[i]);
            }
        }
        if (!Double.isFinite(duration) || !Double.isFinite(warmup) || !Double.isFinite(quiescence) || duration <= 0 || duration > 300 || warmup <= 0 || warmup > 60 || quiescence < 0 || quiescence > 60 || rate < 0 || rate > 1_000_000)
            throw new IllegalArgumentException("invalid bounded workload settings");
        if (StreamProcessor.BATCH_SIZE < 1 || StreamProcessor.BATCH_SIZE > 4096) throw new IllegalArgumentException("invalid batch size");
        initialize();
        Result warmed = run(warmup, rate, false);
        Result r = run(duration, rate, true);
        // Processing is synchronous: every counted message has already returned
        // from the processor. There is no queue or asynchronous drain operation.
        MemoryReading afterDrain = readMemoryAfter(r.endNanos);
        long quiescenceStart = System.nanoTime();
        long quiescenceDeadline = quiescenceStart + (long) (quiescence * 1e9);
        while (System.nanoTime() < quiescenceDeadline)
            LockSupport.parkNanos(quiescenceDeadline - System.nanoTime());
        double quiescenceElapsed = (System.nanoTime() - quiescenceStart) / 1e9;
        // No forced GC: this is observed process state, not an estimate of live
        // retained objects. The corpus and measurement samples intentionally live.
        MemoryReading afterQuiescence = readMemoryAfter(r.endNanos);
        String phases = "{\"warmup\":{\"requested_seconds\":" + warmup + ",\"elapsed_seconds\":" + warmed.seconds +
            "},\"measurement\":{\"requested_seconds\":" + duration + ",\"elapsed_seconds\":" + r.seconds +
            ",\"sample_interval_target_ms\":20,\"sample_count\":" + MEMORY.size() +
            "},\"drain\":{\"model\":\"synchronous-no-queue\",\"pending_messages\":0,\"reading\":" + afterDrain.json() +
            "},\"quiescence\":{\"requested_seconds\":" + quiescence + ",\"elapsed_seconds\":" + quiescenceElapsed +
            ",\"forced_gc\":false,\"reading\":" + afterQuiescence.json() + "}}";
        StringBuilder metrics = new StringBuilder();
        metrics.append(String.format("\"application.completed_messages_per_second\":%.8f,", r.count / r.seconds));
        metrics.append(String.format("\"application.completed_messages\":%d,\"application.error_rate\":0,", r.count));
        metrics.append("\"heap.sampled_peak_bytes\":" + sampledHeapPeak);
        metrics.append(",\"measurement.elapsed_seconds\":" + r.seconds + ",\"quiescence.elapsed_seconds\":" + quiescenceElapsed);
        metrics.append(",\"heap.after_drain_bytes\":" + afterDrain.heap + ",\"heap.after_quiescence_bytes\":" + afterQuiescence.heap);
        if (afterDrain.rss >= 0) metrics.append(",\"process.rss.after_drain_bytes\":" + afterDrain.rss);
        if (afterQuiescence.rss >= 0) metrics.append(",\"process.rss.after_quiescence_bytes\":" + afterQuiescence.rss);
        if (r.cpuNanos >= 0) metrics.append(String.format(",\"process.cpu_seconds\":%.8f", r.cpuNanos / 1e9));
        if (r.allocated >= 0) metrics.append(String.format(",\"main_thread.allocated_bytes_per_message\":%.8f", (double) r.allocated / r.count));
        if (sampledRssPeak > 0) metrics.append(",\"process.rss.sampled_peak_bytes\":" + sampledRssPeak);
        if (r.latency != null) {
            Arrays.sort(r.latency);
            long p99 = r.latency[(int) Math.ceil(r.latency.length * .99) - 1];
            metrics.append(",\"application.latency.p99_ns\":" + p99 + ",\"application.offered_messages_per_second\":" + rate);
        }
        StringBuilder series = new StringBuilder("[");
        for (int i = 0; i < MEMORY.size(); i++) {
            if (i > 0) series.append(',');
            long[] point = MEMORY.get(i);
            series.append("[" + point[0] + "," + point[1] + "," + (point[2] < 0 ? "null" : point[2]) + "]");
        }
        series.append(']');
        String artifactDir = System.getenv("AUTORESEARCH_ARTIFACT_DIR");
        if (artifactDir != null) {
            Files.createDirectories(Path.of(artifactDir));
            Files.writeString(Path.of(artifactDir, "memory-phases.json"), phases);
            Files.writeString(Path.of(artifactDir, "memory-series.json"), "{\"columns\":[\"elapsed_ns\",\"heap_bytes\",\"rss_bytes\"],\"samples\":" + series + "}");
            if (r.latency != null) {
                StringBuilder values = new StringBuilder("[");
                for (int i = 0; i < r.latency.length; i++) { if (i > 0) values.append(','); values.append(r.latency[i]); }
                Files.writeString(Path.of(artifactDir, "latency-sorted-ns.json"), values.append(']').toString());
            }
        }
        boolean checksumOk = r.checksum == r.expected && warmed.checksum == warmed.expected;
        boolean countOk = rate == 0 ? r.count > 0 : r.count == (long) (duration * rate);
        boolean windowOk = rate == 0 ? r.seconds >= duration : countOk;
        System.out.println("{\"metrics\":{" + metrics + "},\"checks\":{\"crc32_matches_independent_bitwise_reference\":" + checksumOk +
            ",\"all_scheduled_messages_completed\":" + countOk + ",\"measurement_window_completed\":" + windowOk + "},\"scenario\":{\"model\":\"" + (rate == 0 ? "saturation" : "paced-fixed-arrival") +
            "\",\"rate\":" + rate + ",\"duration_seconds\":" + duration + ",\"warmup_seconds\":" + warmup + ",\"batch_size\":" + StreamProcessor.BATCH_SIZE +
            ",\"payload_bytes\":" + PAYLOAD_SIZE + ",\"copy_payload\":" + StreamProcessor.COPY_PAYLOAD + "},\"phases\":" + phases + ",\"series\":{\"memory\":" + series + "}}");
        if (!checksumOk || !countOk || !windowOk) System.exit(2);
    }
}
