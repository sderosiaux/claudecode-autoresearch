# Build status, 12 October 2026

The workbench is an executable development version with a bundled UI, CLI and public JVM fixture. It is an advanced prototype, not the completed product specification. Public synthetic campaigns exercise real Docker measurements and retained confirmation evidence. The local product checks passed 366 Python tests, 44 UI tests, the production build, fresh wheel installation and an offline source-archive rebuild.

**The first public release criteria are not all met.** Native Claude Code uses estimated accounting; strict invoice enforcement is refused. A complete, accepted live-native optimization and autonomous measurement-preparation workflow remain unverified. Private application source, workloads, measurements, session traces and account usage are excluded from this repository.

Start with [the workbench guide](../WORKBENCH.md). The [delivery checklist](DELIVERY.md) tracks the current validation work; the [product specification](../plans/2026-10-11-performance-research-product-spec.md) defines the release gates.

## Implemented behavior

| Area | Current implementation and evidence |
| --- | --- |
| Installation | Python supervisor, SQLite WAL, bundled React UI and shared API/CLI. One coordinator owns each data directory. The original Claude Code plugin remains separate. The latest verified package passed a fresh-venv installation check outside the checkout. The final wheel also passed nine fresh-install checks with the current bundled UI; CLI and API agreed without model spend or benchmarks. |
| Product UI | Overview shows current metric observations, paired references, uncertainty, research tracks, outcome filters and exact experiment evidence. Constraints retain their thresholds; absent baselines display as unmeasured. Diagnostic observations and artifacts remain separate from comparison evidence, including when cleanup blocks a task. |
| Preparation | Static JVM/build/runner discovery; missing protocol or required p99/RSS measurements block optimization. The native connector can return an instrumentation patch and protocol for validation and approval. That preparation path has controlled integration coverage, not a completed live-model proof. |
| Benchmark integrity | Approval binds the protocol hash. Protected evaluator paths, clean candidate trees and exact committed revisions are checked. Uncommitted application edits remain untouched. Native, external-worker and manual proposals retain distinct origins. |
| Execution | Fresh Docker snapshots without host mounts, Docker socket or provider credentials. Default networking is disabled. Declared scenario services share a fresh isolated internal bridge with no published ports or external DNS. CPU, memory, process, filesystem, output and duration bounds apply. Trusted host mode provides no security isolation. |
| Experiments | Hypotheses, local strategy cards, single-parent candidate branches, repeated baselines, alternating reference/candidate measurements and new confirmation runs. Exact program duplicates under the same evaluation context are skipped without inherited measurements. Failed, cancelled and inconclusive attempts remain visible. |
| Parallel research | Bounded proposal and preparation batches, configurable from one to four tasks. Durable attempt slots and cost reservations precede model admission. A shared gate separates controlled research from globally serialized measurements. Unrelated host activity can still affect results. |
| Comparisons | Explicit correctness checks, hard constraints, run intervals, noise flags and Pareto comparisons. Noise gates objective and constraint metrics; informational metrics retain variation without invalidating an otherwise valid objective. Missing or nonfinite measurements still fail validation. Percentiles remain workload-defined. |
| Claude Code | Preferred native connector: Claude Code navigates and edits a filtered checkout with its own loop. The supervisor extracts the actual diff and applies independent evaluator checks. Reviewed CLI flags confine file tools and disable shell, agent, browser, network and MCP tools. Controlled tests cover authentication failures, proposal bounds and retained unknown costs. A complete accepted live-native optimization remains unverified. The earlier tool-free Messages adapter is optional. |
| Source filtering | Native snapshots use a fresh Git repository without original history/configuration, credential-shaped files or symlinks. Explicit `agent.excluded_paths` can remove ordinary files identified by a local source review. Exclusions affect only the agent view; the full benchmark source remains intact. Static scans cannot establish that arbitrary source is secret-free. |
| Budget | Integer micro-USD, atomic campaign reservations, protected finishing allowance, idempotent settlement, retained unknown costs and incident blocks. Native sessions reserve their whole envelope before launch, with a lower stop threshold for headroom. A fresh login-status preflight now precedes reservation; it does not retroactively release earlier unknown reservations. Subscription-equivalent telemetry, retained budget and actual invoices remain distinct. Installation-wide quota enforcement is not implemented. |
| Recovery | Pause/stop, durable interrupted states, explicit reconciliation and separate resume. Unknown monetary reservations remain engaged. Unconfirmed resource cleanup blocks new admission across campaigns. |
| Review/export | Raw runs, event history, reports, reproduction instructions and hashed archives. A confirmed clean candidate can prepare a reviewable PR payload. Publication checks exact code/protocol and remote base; actual remote publication remains unverified. |

See [the native connector contract](CLAUDE-CODE.md), [scenario execution](SCENARIOS.md) and [distribution](DISTRIBUTION.md) for configuration and limits.

## Distribution proof

The final `0.1.0.dev0` wheel bundles production UI assets, local font/icon licenses and the public fixture. It was installed with dependencies in a new virtual environment, then run from an empty working directory with an empty data directory. All nine installation checks passed, including fourteen linked assets and CLI/API agreement. No model spend or benchmark occurred during this proof.

The final regression run passed 366 Python tests, Ruff check, format checks covering 38 files, 44 UI tests and the production UI build. A scheduler formatting-only adjustment was followed by the passing format check. Archive hashes, build commands and the fresh-install receipt are maintained in [distribution validation](DISTRIBUTION.md). The final source archive rebuilt offline without Node/npm; all 41 runtime files match the original wheel byte for byte. Archive selection excludes private validation material, campaign data, user-owned files, caches and `node_modules`.

This proves a local macOS installation and the replayed Linux workload environments. Installation on every supported host, dependency-complete one-command replay for arbitrary repositories and remote Docker on another host remain unverified.

## Scenario and diagnostic validation

Real Docker checks cover service-name connectivity, blocked external DNS, absence of a host gateway route, fresh state, failed readiness, bounded logs and cleanup after coordinator SIGKILL. The clock-translation suite plus the workload PID 1 SIGKILL check passed 28 tests. Static coordinator/daemon clock skew is translated from a probe bounded to five seconds into one shared absolute daemon deadline. Raw receipts remain outside the stable environment fingerprint. An early attempt exited after a probe observed a 349-second offset; later probes matched the host, consistent with a forward resync but not proof of its timing. The isolated repeat and final suite passed. Clock jumps after calibration remain unsupported; no clock was changed.

Readiness-probe timeouts must not mark allocating operations as uncertain when owned-resource cleanup is confirmed. Uncertainty now follows container/network admission, including uncertain output-limit responses. Readiness and inspection failures alone do not override confirmed cleanup; uncertain resource admission still retains the reaper and blocks further work. Controlled scenario and recovery regressions cover the distinction.

Diagnostic observations are now persisted even if cleanup raises a recovery gate. The UI can show the recorded diagnostic status, output, artifact link and cleanup warning without treating the run as comparison evidence. Informational timing variation likewise stays visible without deciding objective validity. Offered-load requirements still need explicit constraints or correctness checks.

## Public synthetic campaign evidence

Campaign `2b3c346861f24002b0a8c57b98ee973e` used the public synthetic JVM stream fixture at `/tmp/autoresearch-public-demo-20261011`. It was created, approved and run through the actual UI/API. It is stored in `.autoresearch-data/` and ends in `reviewable` with `confirmed_candidates`.

The approved protocol targeted processing-thread allocations, with completed-throughput and error-rate constraints. Measurements ran in fresh Docker containers with three independent repetitions per phase, one-second measurement and half-second warmup. This is a smoke protocol on a development machine, not a production workload study.

| Candidate | Observation | Decision |
| --- | --- | --- |
| Remove payload copies (`62c94ff3ed0547169d71984aeb71ce93`) | Independent confirmation: 1,040.01757319 → 0.01613734 allocated bytes/message. Correctness and throughput constraints passed. | Confirmed for the allocation objective. |
| Same candidate, throughput | 7,606,594.25 → 8,323,322.86 completed messages/s; difference interval includes zero. | No confirmed speedup claim. |
| Batch 128 (`ffc80acf40d54a2db5b299a96cd86e8d`) | Throughput rose, but allocation improvement did not meet the approved objective threshold. | Inconclusive; retained with its measurements. |

The exported archive was independently checked: both experiments and all 126 artifact hashes were present and correct. The UI displayed the exact confirmed candidate's draft payload. No branch was pushed and no remote PR was created.

After that code restart, the campaign retained its state, both verdicts and zero model spend. Its earlier reviewed draft had hash `a880ba38a3062d3c2df906b24cca356b87dccff51d8dffa07383b58c3a0d49c6`.

This campaign predates the runner-configuration provenance field. The final UI still displays its baseline and both comparisons after checking every recorded run's protocol hash, exact revision, environment fingerprint and repetition count. It labels the missing requested-runner identity explicitly. Stored records were not backfilled or changed. Reproduction refuses this unverified part of the old context instead of combining historical measurements with a current runner configuration.

Separate runner validation performed 43 real Docker JVM executions, a real JFR recording and a JMH 1.37 forked smoke run. Those observations are documented separately in [JVM validation](JVM-VALIDATION.md); they are not substituted for this campaign's confirmation.

A separate [five-minute memory baseline](MEMORY-VALIDATION.md) completed a measured 300.000000094 seconds in Docker, retaining 12,735 actual heap/RSS samples and separate after-drain and quiescent readings. All correctness checks and artifact hashes passed. This validates the measurement window and evidence transport; it is one baseline, with no before/after optimization or leak conclusion.

## Parallel workspace validation

Campaign `89e0427c91584fbaa2e06600ca56b77b` exercised the new workspace on the same public fixture with four manually supplied hypotheses and `max_parallel_tasks: 2`. It completed 21 real Docker JVM runs, with zero model spend and no outstanding reservation. This validates scheduling and evidence transport; it is a short synthetic development-host protocol, not a production performance claim or a live Claude integration.

| Attempt | Retained outcome |
| --- | --- |
| Remove redundant payload copies | Confirmed allocation objective: paired confirmation 1,040.01851273 → 0.01645241 bytes/message. Hard constraints passed. |
| Batch 128 | Inconclusive for the allocation objective; measurements retained. |
| Repeat the copy-removal program | Skipped as an exact duplicate, linked to the original; no additional measurement or inherited confirmation. |
| Change protected `run.sh` | Failed before measurement, with the evaluator-protection error retained. |

The first two preparation task intervals overlapped by 0.194 seconds; the next pair overlapped by 0.080 seconds. Measurement task intervals began after each preparation wave finished. Eight task records remain, including the preparation failure, with zero active tasks at completion. The exported manifest retains all four attempts and all eight task records; all 126 artifact hashes verified. Local raw observations and the export are in `/tmp/autoresearch-workspace-validation/`.

Safari checks covered active measurement with a waiting candidate, final outcome counts, confirmed-result and failed-attempt filters, and exact evidence drilldown. KPI rows stack into labeled blocks in a narrow window. A resize check also caught and fixed the collapsed sidebar leaving the workspace in a zero-width grid column.

## Validation boundary

Application-specific integration evidence remains local and is not published here. The public synthetic fixture and controlled regression suites establish only their documented behaviors. They do not establish a performance improvement for arbitrary projects or completion of the product specification.

The native connector performs its own navigation and edits. Authentication preflight, structured-output bounds, retained reservations and local-process completion have controlled regression coverage. A successful model response alone does not establish an accepted import, correct candidate or measured gain. Remote billing, accepted live-native optimization and live-native instrumentation remain open validation gates.

## Checks and review

The final full suite passed **366 Python tests**, with one upstream Starlette TestClient deprecation warning. Ruff check and format checks covering **38 files** passed after a scheduler formatting-only adjustment. The current UI passed **44 tests** and its production build. The 75 focused native adapter/engine tests are included in the verification history, not an additional full-suite total.

The verified changes include immediate run/resume rejection during another campaign's measurement, visible initial baseline preparation, cancellable deadline-bound waits for checkout/profiling/post-baseline context, and the final provider-lease fix. Fresh installation, public-fixture UI/export review and graceful restart passed as described above. The final source archive also rebuilt offline without Node/npm, with all 41 runtime files identical to the original wheel.

Earlier validated coverage includes native provider admission, source exclusions, actual edit/new-file diff extraction, preparation approval, parallel research, budget and attempt ceilings, measurement exclusion, duplicate suppression, informational noise, retained diagnostics, memory windows and historical context. Docker-dependent checks use actual containers; provider/publication tests use controlled transports. npm reported no vulnerabilities at the recorded installation/build checks.

Verification commands, run outside performance measurements:

```sh
uv run pytest
ruff check product product_tests
ruff format --check product product_tests examples/jvm-streaming/demo.py
npm --prefix web test -- --run
npm --prefix web run build
```

The UI was inspected in Safari at desktop width and a narrow window of approximately 574 CSS pixels. Browser checks covered comparison, approval, activity, reports, campaign creation/search, sidebar collapse, outcome filters and exact evidence drilldown. Automated UI coverage also checks connector selection, strict-provider refusal, recovery, protocol editing and diagnostic display. This is desktop-browser responsive validation, not a physical mobile-device test. Application-specific integration records are excluded from the public documentation.

Independent review and regression fixes covered phase-gate lock ordering, pause/stop races, dirty workspace reuse, protected evaluator changes, warmup/environment propagation, native provider selection, admission after a budget incident, failed-worker output acceptance, daemon-clock translation and readiness/cleanup uncertainty. The public fixture's evaluator remains separate from mutable processing code.

## Remaining release gates

1. Establish authoritative Claude price/category and final-billing behavior for success, failure and interruption. Current estimated reservations cannot satisfy the strict-provider release gate. See [provider limits](PROVIDER.md).
2. Establish a correct accepted live-native optimization and live-native preparation. Controlled integration tests and public synthetic benchmarks do not satisfy these live product proofs.
3. Dependency-complete one-command replay for arbitrary projects, environment migrations and remote-browser authentication remain unfinished. Full regression, the current wheel, fresh installation and offline no-Node source-archive rebuild passed.
4. Verify remote Docker on a separate host and actual GitHub draft publication. Those implemented paths have no completed live integration proof here. No remote push, PR creation or deployment has been performed.
5. Extend workload-specific readiness/preparation and research policy. Static discovery does not interpret arbitrary goals; strategy cards are locally authored, and pinned attributed imports, stagnation diagnosis and cross-branch composition remain incomplete.
6. Installation-wide quotas/provider quarantine and multiple-user authorization are not implemented. The workbench targets one trusted local user/team. The existing plugin does not yet delegate to the supervisor.

This development snapshot does not complete the first public release. Local campaign data, private integration records and caches are excluded from Git; the public fixture can regenerate its own measurements.
