import java.io.*;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.Random;

public class DataGenerator {

    private static final int SENSOR_COUNT = 1000;
    private static final long SEED = 42L;
    private static final double LATE_EVENT_RATIO = 0.05;
    private static final int MAX_LATE_MINUTES = 10;

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
                double progress = (double) i / rowCount;
                long baseOffsetSeconds = (long) (progress * SPAN_SECONDS);

                long jitter = (long) (rng.nextGaussian() * 15);
                long offsetSeconds = Math.max(0, Math.min(SPAN_SECONDS - 1, baseOffsetSeconds + jitter));

                Instant timestamp = BASE_TIME.plusSeconds(offsetSeconds);

                if (rng.nextDouble() < LATE_EVENT_RATIO) {
                    int lateMinutes = 1 + rng.nextInt(MAX_LATE_MINUTES);
                    timestamp = timestamp.minus(lateMinutes, ChronoUnit.MINUTES);
                }

                String sensorId = sensorIds[rng.nextInt(SENSOR_COUNT)];
                double value = 20.0 + rng.nextGaussian() * 10.0;

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
