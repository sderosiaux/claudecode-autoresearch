# JVM execution validation

Executed on 11 October 2026 (local America/New_York; JFR timestamp 12 October UTC). These are actual local smoke measurements, not estimates or broad application performance claims. No model API calls were made.

## Commands and environment

```sh
docker pull eclipse-temurin:25-jdk
PYTHONPATH=product python3 examples/jvm-streaming/demo.py --output /tmp/autoresearch-jvm-proof
PYTHONPATH=product python3 -m pytest -q --tb=short product_tests/test_runner.py product_tests/test_measurement.py product_tests/test_jvm.py
jfr summary /tmp/autoresearch-jvm-proof/diagnostic-jfr/files/profile.jfr
```

The demonstration completed **43 real Docker JVM executions**: two scenarios, three exploratory repetitions of baseline and two candidates, then three independent baseline/candidate confirmation pairs for each candidate in each scenario, plus one separate diagnostic JFR run. Each invocation compiles the Java sources inside its fresh isolated workspace. The configured one-second measurement/half-second warmup is an illustrative smoke protocol. Other development validation used the same Docker Desktop machine, so this is not an exclusive production-grade benchmark host.

The container image was `sha256:8c0a84ea11c8f6ed52600fc19f1040121f2a162998e9f50a5faebbbad9172dcc`, with JDK `jdk-25.0.4.1+1`, Linux/aarch64, Docker Engine 29.8.2 and LinuxKit kernel 7.0.14. The Docker Desktop daemon reported 12 CPUs and 33,598,197,760 bytes memory; each workload was capped at **1 CPU, 512 MiB memory, 128 processes, 256 MiB workspace and 64 MiB artifacts**, with network disabled and a read-only root filesystem. Sources were streamed into tmpfs; no host directory or credential mount was exposed. The workload ran as UID/GID 65534 while its independent deadline process ran as root with all Linux capabilities dropped.

## Observations and decisions

| Scenario/candidate | Exploration | Independent confirmation | Decision |
| --- | --- | --- | --- |
| Saturation / avoid copy | promising | promising | Confirmed under this smoke protocol |
| Saturation / batch 128 | inconclusive | promising | Not confirmed: exploration unresolved |
| Fixed arrivals / avoid copy | promising | inconclusive | Not confirmed: p99 variation exceeded noise bound |
| Fixed arrivals / batch 128 | inconclusive | inconclusive | Not confirmed: p99 variation exceeded noise bound |

For saturation/avoid-copy, independent confirmation means were **6,955,224.32 → 8,047,980.99 completed messages/s (+15.71%)**, and **1,040.01919278 → 0.01664365 allocated bytes/message** on the main processing thread. The latter includes bounded harness setup allocations and excludes sampler-thread allocations. Both phases passed the independently implemented bitwise CRC reference checks and completed-message checks. This improvement concerns the synthetic CRC processor on this image and machine.

Saturation/batch-128 confirmation observed **6,970,856.28 → 9,773,709.15 completed messages/s (+40.21%)**, but the exploratory comparison was inconclusive, so the demonstration did not publish this as confirmed. Paced runs fixed offered load at 10,000 messages/s, retained every scheduled-arrival-to-completion latency observation, and exposed batching waits. Their noisy p99 comparisons were left inconclusive. No pooled percentile or missing RSS was invented.

Heap/RSS were sampled approximately every 20 ms in the measurement window. Their reported peaks are sampled peaks, not exact maxima or native-memory attribution. Each execution retained the series and environment fingerprint. Allocation and CPU readings used actual JDK management counters; unavailable signals are omitted. Timing/reference checks reside in protected `StreamWorkload.java`, compilation/JVM flags in protected `run.sh`; candidate patches affect only `StreamProcessor.java`.

## JFR and JMH proof

The separate diagnostic recording contained a real **589,262-byte JFR file**, successfully parsed by `jfr summary`: version 2.1, one chunk, two-second duration, 103 execution samples, 480 allocation samples and 522 garbage collection events. Its SHA-256 was `5aeba67fb7e37f9d282013c708de6136c8273f1292b993616ab0327fac67ec09`. That diagnostic invocation was excluded from confirmation.

A separate actual **OpenJDK JMH 1.37** smoke fixture was generated with the official Maven archetype in `/tmp/autoresearch-jmh-fixture/jmh-smoke`, built outside the isolated workload, and its self-contained jar executed inside the same Docker runner using:

```text
java -jar benchmarks.jar .* -f 2 -wi 1 -i 2 -w 100ms -r 100ms -rf json -rff /artifacts/jmh.json -foe true
```

The JSON adapter decoded two distinct forks with two measured values each and retained `ops/s` units. The archetype's empty benchmark is only an adapter transport/parsing test; its score is not a useful performance result. Correctness checks remained empty, intentionally preventing generic benchmark validation from treating JMH timing output as application correctness proof. Actual execution exposed two integration details: JMH duration strings must use integer units (`100ms`, rather than decimal seconds), and Java 25 requires explicitly enabling JMH annotation processing (`-proc:full`) when building the archetype. The fixture is not a product dependency.

## Safety and proof limits

Runner tests exercise actual Docker resource limits, network configuration, unprivileged execution, absence of inherited model credentials and Docker socket, credential-file filtering, host-checkout preservation, bounded output, timeouts and cancellation. A real coordinator subprocess was killed with SIGKILL after container creation; the independently running container deadline still stopped the orphan, after which the test removed the stopped container. Cleanup assertions are scoped to that test's own container name and permit other campaigns to remain active.

Remote `docker_host=ssh://...` transport is implemented through the same bounded archive streams but was **not remotely exercised**. Trusted host mode is an explicit development option and has no host security/resource isolation; process-group termination cannot contain deliberately detached host processes. Arbitrary secrets embedded in ordinary source cannot be recognized automatically, so the selected source tree and trusted image require administrator review. JFR on abrupt JVM termination can be absent. A long-running five-minute memory protocol, Kafka service topology, production application coverage and independent host replication remain future validations.

Raw proof files are local artifacts, deliberately excluded from Git: `/tmp/autoresearch-jvm-proof/report.json`, per-execution `execution.json`, stdout/stderr logs, memory/latency observations and the JFR file. The report SHA-256 at validation was `d296655e61786c456f0532b1ed4c9b3d181afc10fdb959a4327fa65cb09ebc89`. JMH logs and real output are under `/tmp/autoresearch-jmh-proof`. These `/tmp` locations are ephemeral; rerun the documented commands to regenerate evidence.
