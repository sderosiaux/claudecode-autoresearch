import java.util.Arrays;
import java.util.zip.CRC32;

/** Candidate-owned processor. The harness, input and reference evaluator stay fixed. */
public final class StreamProcessor {
    static final boolean COPY_PAYLOAD = true;
    static final int BATCH_SIZE = 1;
    private final CRC32 crc = new CRC32();

    long process(byte[] input) {
        byte[] payload = COPY_PAYLOAD ? Arrays.copyOf(input, input.length) : input;
        crc.reset();
        crc.update(payload);
        return crc.getValue();
    }
}
