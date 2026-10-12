# Five-minute memory-window validation

This is **one genuine 300-second baseline execution**, validating measurement coverage and artifact transport. It is not an optimization comparison, repeated baseline stability study, or proof of the absence of a memory leak. The public synthetic workload repeatedly processes a seeded 4 MiB in-memory corpus using CRC32 with a 1 KiB copy per message. It has no broker, network traffic or asynchronous queue. No paid calls, remote access or profiling runs were used.

## Frozen fixture and command

Started at `2026-10-12T02:47:31.061409+00:00`. A new isolated Git checkout `/tmp/autoresearch-memory-baseline-l0p3pbg2` was frozen at `62534e8dc6dca2f465891b3d6072d7b9da4fd281`. The existing `/tmp/autoresearch-public-demo-20261011` was not used or changed. The runner copied this new fixture into its own isolated workspace; validation confirmed its source hashes and clean Git status after execution. Both candidate patches passed `git apply --check` before this run but were not applied or measured.

```python
from pathlib import Path
from autoresearch.runner import run_command

result = run_command(
    ["sh", "run.sh", "--duration", "300", "--warmup", "2", "--quiescence", "2"],
    Path("/tmp/autoresearch-memory-baseline-l0p3pbg2"),
    Path("/tmp/autoresearch-memory-proof-20261011/baseline"),
    timeout_seconds=360,
    runner={"kind": "docker", "image": "eclipse-temurin:25-jdk"},
)
assert result["status"] == "succeeded"
```

`StreamWorkload.java` remains the protected timer, seeded corpus, independent bitwise CRC reference and evaluator. `StreamProcessor.java` remains the only candidate-owned code; this baseline left `COPY_PAYLOAD=true` and `BATCH_SIZE=1`. `run.sh` remained protected. The harness addition records actual measurement duration, samples strictly inside that interval, immediate post-processing readings, and a separately timed quiescent phase. It does not force GC.

## Actual observations

| Observation | Value |
| --- | --- |
| Completed messages | 2,512,464,560 |
| Completed messages/second | 8,374,881.8640 |
| Actual timed measurement | 300.000000094 s |
| Runner lifecycle | 306.618725 s |
| Measurement samples | 12735 |
| First sample timestamp | 0.000504500 s |
| Last sample timestamp | 299.978915177 s |
| Sample gap, min/median/max | 20.093667 / 24.786583 / 28.662292 ms |
| Sampled heap peak | 24,372,256 bytes |
| Sampled process RSS peak | 113,086,464 bytes |
| Heap immediately after drain | 10,012,680 bytes |
| RSS immediately after drain | 115,372,032 bytes |
| Heap after quiescence | 10,012,680 bytes |
| RSS after quiescence | 117,592,064 bytes |
| Actual quiescence | 2.003384626 s |

The immediate after-drain reading completed `9,792,458 ns` after the timed processing boundary. The quiescent reading completed `2,013,546,959 ns` after that boundary. Both are outside the measurement interval and excluded from its sampled peaks. Warmup actually lasted `2.000031001 s` and is excluded too.

All emitted checks passed: `crc32_matches_independent_bitwise_reference, all_scheduled_messages_completed, measurement_window_completed`. Every completed message contributed its CRC to the observed checksum, compared with a separate bitwise implementation's precomputed reference. The timing check confirmed at least 300 actual seconds of saturation processing. Processing is synchronous: each counted call returned a CRC, so the drain phase declares `synchronous-no-queue` and zero pending messages. There is no claim that an external service or asynchronous queue was drained.

Validation independently verified finite metrics, completion count versus elapsed-time throughput, strictly bounded ordered sample timestamps, sample count, exact heap/RSS peak equality with the retained series, matching phase artifact/output metadata, source hashes, unchanged checkout and every runner-recorded artifact SHA-256. RSS was actually read from Linux `/proc/self/status` (`VmRSS`), not inferred from heap. On platforms where RSS is unavailable the harness omits RSS metrics and uses `null` in artifact readings.

## Environment and artifact identity

The actual environment fingerprint and limits were:

```json
{
  "mode": "docker",
  "image_id": "sha256:8c0a84ea11c8f6ed52600fc19f1040121f2a162998e9f50a5faebbbad9172dcc",
  "os": "linux",
  "architecture": "arm64",
  "daemon": {
    "ID": "4b8a8c56-9856-4b83-a3d4-ef96a1dec275",
    "KernelVersion": "7.0.14-linuxkit",
    "Architecture": "aarch64",
    "NCPU": 12,
    "MemTotal": 33598197760,
    "ServerVersion": "29.8.2",
    "OperatingSystem": "Docker Desktop"
  },
  "limits": {
    "cpus": 1.0,
    "memory_mb": 512,
    "pids_limit": 128,
    "workspace_mb": 256,
    "artifact_mb": 64
  },
  "network": "none",
  "remote": false,
  "jdk": "jdk-25.0.4.1+1",
  "explicit_env": {},
  "watchdog": "container PID1 absolute deadline; coordinator-independent",
  "fingerprint": "1c62a084b5ec4e9997b38b28f8826259aadfa628ae85975a8fbd807d528c339d"
}
```

The process had a 256 MiB Java heap cap (`-Xms64m -Xmx256m`). Its Docker worker had no network or host mounts and bounded CPU, memory, process count, tmpfs workspace and artifact storage. The 360-second whole-run timeout and independent container deadline bounded execution; normal completion occurred before them. Other workers performed unit tests/builds on the same Docker Desktop host. This was a shared development machine rather than a dedicated measurement host.

Frozen source SHA-256:

- `StreamProcessor.java`: `4400ef8ff2f1d168934a7d4638fa063b1b2e7e45d38560e3686bc77af489a480`.
- `StreamWorkload.java`: `449a2c9bfd50de33e4173fff5ad463506bc71df77e4d337576fa4d2b02e411fd`.
- `run.sh`: `addc78f465cfb3ba3f7bf837e3d1806be1b296ef9742877fbda6873691c31432`.

Proof artifact SHA-256:

- `benchmark.json`: `ffdf15755ec1bb424ff3f2ab986c99126daa6fb1dd50f89b3abd6324ef6268ff`.
- `baseline/execution.json`: `74de3f482f448be73b8ed0f043aeda37a08f6b1ce8284819fbe8942bd64b4b10`.
- `baseline/files/memory-series.json`: `bb954041a12e7d877109a0502753e3316cc794b6ea177a9547a166fc9538298f`.
- `baseline/files/memory-phases.json`: `6186f7d70830ec83f268bc6216ff66ce1b23677cedb4bbfaa748a1fbe997e41c`.

Raw local proof is under `/tmp/autoresearch-memory-proof-20261011`: `request.json`, `runner-result.json`, `benchmark.json`, `validation.json`, per-execution metadata/logs, `memory-series.json` and `memory-phases.json`. Raw proof is intentionally excluded from Git; `/tmp` retention is ephemeral. Short functional coverage is in `product_tests/test_memory_window.py`; reproduce with `uv run pytest -q product_tests/test_memory_window.py`.

## Interpretation limits

These are **sampled peaks**, with the observed sampling gaps shown above. Transient maxima between readings may be missed; heap and RSS reads within a sample are not instantaneous simultaneous measurements. Post-drain and quiescent values are observed usage without forced GC, not live-object retention estimates. The corpus and the sampler's growing observation list remain intentionally reachable; that instrumentation contributes to the process footprint. Serialization/export occurs after the post-work readings and is outside the timed measurement. JVM/native allocation, GC behavior and the sampler's overhead are not separately attributed.

One five-minute execution establishes that the requested window and signals were genuinely collected on this environment. It supplies no confidence interval across independent runs, no before/after gain, no dedicated-host throughput claim, no long-term leak conclusion and no production Kafka coverage. Any future optimization comparison needs an approved protocol and independent repeated baseline/candidate runs in comparable conditions.
