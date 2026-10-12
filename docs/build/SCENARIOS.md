# Local service scenarios

A benchmark can require a broker, database or another service. Define those dependencies under `runner.services`. Every invocation creates its own network, containers and tmpfs state, including paired reference runs and confirmation runs. Images must be installed before admission; the runner never pulls images.

```yaml
runner:
  kind: docker
  image: your-offline-jvm-build-image
  cpus: 4
  memory_mb: 8192
  workspace_mb: 2048
  temporary_mb: 1024
  workspace_inodes: 100000
  temporary_inodes: 60000
  pids_limit: 1024
  services:
    - name: broker
      image: your-prepared-broker-image
      command: ["/opt/fixture/start-broker"]
      ready_command: ["/opt/fixture/check-broker"]
      ready_timeout_seconds: 60
      user: "1000:1000"
      cpus: 1
      memory_mb: 2048
      pids_limit: 256
      tmpfs:
        /tmp: 1024
```

This is a configuration shape, not an executable fixture: its images must contain those commands and their offline dependencies. The workload resolves the service through its name, `broker`. Service commands run under the declared non-root UID and GID. Service environment entries apply only to unprivileged service and readiness commands. Secrets do not belong in manifests.

## Execution contract

At most four services are admitted. Each has bounded CPU, memory, processes, writable storage and readiness time. Images and effective definitions enter the execution fingerprint; random resource names do not. The command deadline includes service startup, source staging, compilation, warmup and measurement. Size the approved deadline for that whole sequence.

No host paths, Docker socket or ports are mounted or published. A scenario uses an internal Docker bridge with the isolated gateway mode. Docker's ordinary internal mode alone can retain a host bridge address, so the runner requires and inspects the isolated option. External DNS forwarding is disabled while Docker service-name resolution remains available. Unsupported isolation fails admission. [Docker bridge gateway modes](https://docs.docker.com/engine/network/port-publishing/#gateway-modes).

The root filesystem is read-only. Writable directories are bounded tmpfs mounts, which also consume the container's memory allowance. Build caches must therefore be available in the image or copied into an appropriately sized writable directory before compilation. [Docker tmpfs behavior](https://docs.docker.com/engine/storage/tmpfs/).

Both workload and service processes run below a separate root-owned PID 1 deadline. User environment entries cannot change that command. Before allocating resources, the runner samples Docker `SystemTime` with a monotonic roundtrip bounded to five seconds. It computes one absolute daemon-clock deadline: the sampled daemon timestamp plus the task budget remaining at probe completion plus the existing ten-second watchdog grace, rounded down to whole seconds. The sample predates completion, so probe latency makes the deadline conservative. All service and workload admissions use that same deadline; a delayed admission never receives a new timeout. Static coordinator/daemon wall-clock skew is accepted, including remote Docker, and coordinator wall time is retained only as a receipt. Missing, malformed, timezone-free or slow clock samples fail closed. A daemon clock jump after the probe and before PID 1 computes its sleep remains unsupported; no host or VM clock is changed.

An independent local reaper removes owned containers and their network when the coordinator disappears or the execution deadline expires. Normal cleanup retains bounded service logs, removes the containers and then removes the network. If cleanup is uncertain, the supervisor blocks new resource admission across campaigns until explicit reconciliation after the recorded lease. The lease includes 120 seconds for cleanup and reaper retries. A failed Docker daemon can prevent resource removal; it is not evidence that removal succeeded.

## Evidence and approval

Each invocation retains `scenario.json`, service logs, runner output and image/environment identifiers. `execution.json` records the raw daemon timestamp, monotonic probe interval, remaining budget, observed wall-clock offset and translated absolute deadline under `daemon_clock_check`; `scenario.json` records the same deadline. These per-run timestamps do not enter the stable environment fingerprint. Workload artifacts belong in `/artifacts`; temporary build files belong in `/work` or `/tmp`. Service readiness and cleanup failures remain failed attempts. Missing memory samples or failed correctness checks must invalidate the result, never produce an invented zero.

Campaign creation validates service definitions, and readiness checks all required images. Readiness is a static prerequisite check; the baseline proves that the actual scenario starts and measures the requested behavior.

Ordinary approval records `authority: operator`. A user can delegate review of a bounded protocol to an agent; that approval must record `authority: delegated_agent`, the exact protocol hash and the scope of delegation in `rationale`. This is declared provenance from the local operator, not an identity-attestation system. Changing the protocol clears the approval.

Manual patches, external worker patches and native product-provider proposals retain distinct origins. The local validation workflow does not label a worker-authored experiment as a Claude-generated proposal.

## Verification

`product_tests/test_scenario.py` verifies static clock-skew translation, rejected malformed/slow samples, conservative probe-budget accounting, one shared deadline across all admissions, and real service-name connectivity, blocked external DNS, absence of a host gateway route, fresh execution state, stable fingerprints, failed readiness, cleanup after coordinator SIGKILL, and a service-controlled PATH attack against log collection. `product_tests/test_recovery.py` checks that uncertain cleanup blocks subsequent admission. Docker tests require preinstalled test images and report a skip if they are absent.

Clock-translation validation on 2026-10-12: the complete scenario suite plus the workload PID 1 coordinator-SIGKILL check passed **28 tests**. Functional receipts and the exact command are retained at `/tmp/autoresearch-clock-final-20261012/verification.json`. An earlier first-container attempt exited early after a probe observed a 349-second offset; later probes matched the host, consistent with a post-probe forward clock resync but not proof of its timing. Its isolated repeat and the final full suite passed. These checks establish measurement transport behavior, not performance gains or support for clock jumps. No host or VM clock was changed.
