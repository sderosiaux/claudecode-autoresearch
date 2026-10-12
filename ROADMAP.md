# Product roadmap

Reviewed against the code and product specification on 12 October 2026.

The workbench is a functional prototype. The full specification and the first public release are **not complete**. The largest gaps are a verified live Claude Code workflow, strict spending enforcement, accessible repository setup and adaptive exploration.

This file tracks remaining work in priority order. The [product specification](docs/plans/2026-10-11-performance-research-product-spec.md) defines the intended behavior; [build status](docs/build/STATUS.md) records what has actually been checked; the [delivery summary](docs/build/DELIVERY.md) describes the current snapshot. Implementation and live validation are separate obligations. A milestone stays open until its acceptance evidence exists.

## What already works

- Self-hosted Python service, SQLite persistence, shared CLI/API and bundled web UI.
- Campaigns, hypotheses, bounded parallel proposal/preparation tasks, serialized controlled measurements, KPI views, comparisons, failed attempts and history.
- Protocol approval, protected evaluators, isolated Docker workloads, repeated baselines and independent confirmation.
- A thin native Claude Code connector that delegates navigation and edits to Claude Code. Campaign budget reservations, pause/stop/recovery and evidence exports.
- Real measurements on a public synthetic JVM fixture, including a five-minute memory baseline, plus fresh package installation checks.

These are foundations with documented limits. Controlled connector tests do not prove a completed live optimization. Budget reservations do not establish a strict provider invoice cap. Preparing a draft-PR payload does not prove remote publication.

## Delivery order

| Priority | Milestone | Current gap |
| --- | --- | --- |
| P0 | M0. Enforce spending limits | Campaign reservations exist; strict Claude billing guarantees and installation-wide quotas do not. |
| P0 | M1. Complete the native agent workflow | Connector and preparation paths exist; accepted live optimization and preparation remain unverified. |
| P1 | M2. Make an unfamiliar Java repository usable | Guided setup and goal-dependent preparation still require workload-specific expertise. |
| P1 | M3. Make exploration respond to evidence | Local strategy cards and parallel tasks exist; adaptive planning, stagnation handling and attributed imports remain incomplete. |
| P1 | M4. Reproduce and deliver results elsewhere | Exports and publication code exist; dependency-complete replay, another-host execution and actual draft publication need proof. |
| P1 | M5. Qualify the first public release | Installation, upgrades, access controls, documentation and realistic public scenarios need a release validation pass. |
| P2 | M6. Complete broader specification capabilities | Candidate composition, richer reuse and compatibility with the original plugin remain unfinished. |

M0 and M1 are the next priorities. Work on other milestones can proceed independently, but M0–M5 must pass before claiming the first public release in specification §18. A live test in explicitly selected estimated mode can validate M1; it cannot satisfy M0.

## M0: Enforce spending limits

Specification §§10, 12, 18, 20. Existing contracts: [native connector](docs/build/CLAUDE-CODE.md) and [provider limits](docs/build/PROVIDER.md).

- [ ] Establish a Claude execution path with a verifiable upper bound on all billable operations, including retries, resumed sessions and supported descendants. Preserve Claude Code's native exploration loop.
- [ ] Reconcile authoritative usage and billing categories on success, failure, cancellation and lost responses. Keep estimated usage, outstanding commitments and actual billing distinct in the UI and exports.
- [ ] Add an installation-wide quota over concurrent campaigns, with atomic admission and durable accounting across restarts.
- [ ] Quarantine a connector across campaigns when its claimed bound is violated; require resolution before re-enabling the guarantee.
- [ ] Verify that credentials, paid tools and descendants cannot bypass the controlled billing path. Keep unknown commitments reserved until resolved.

**Done when:** adversarial concurrency/recovery tests and a real connector integration demonstrate the advertised bound, including late billing. A Claude Code soft stop alone does not qualify. If a strict path cannot be established, keep strict launches refused and record the release blocker; changing the release promise requires an explicit specification decision.

## M1: Complete the native agent workflow

Specification §§5–7, 18, 20. Current boundary: [build status](docs/build/STATUS.md).

- [ ] Complete a live campaign through the installed UI: goal → approved protocol → baseline → Claude Code proposal → accepted diff → correctness → comparison → independent confirmation → report/export.
- [ ] Complete a second live campaign that starts without the required measurements: diagnose the gap, propose instrumentation and benchmark changes, validate their output, obtain protocol approval and continue the campaign.
- [ ] Retain native task provenance, model/tool actions, failures, cost commitments and every attempted candidate. Fix import or lifecycle failures in the product and rerun the affected path.
- [ ] Exercise pause, stop, restart and exhausted budget during native work, with the resulting states understandable from the UI.

**Done when:** reproducible public evidence includes an accepted native candidate with a confirmed gain on a suitable demonstration workload, and a successful native preparation flow. A maintainer manually repairing the candidate or campaign record does not count. On unfamiliar applications, an honest `no_confirmed_gain`, `preparation_only` or explained block is a valid outcome; it does not replace the successful-path proof.

## M2: Make an unfamiliar Java repository usable

Specification §§3, 5–7, 14, 16. Follow the existing [product design guidelines](docs/design/DESIGN-GUIDELINES.md).

- [ ] Guide a developer from a local/Git repository, plain-language goal and finite budget to a concrete campaign. Ask only for missing workload, measurement-boundary and correctness decisions.
- [ ] Make Maven/Gradle, JDK, dependencies, benchmark commands and runner requirements actionable. Report unsupported configurations and exact missing prerequisites.
- [ ] Extend readiness beyond static discovery: distinguish missing instrumentation, unreliable measurements and external blockers, with evidence and a proposed next action.
- [ ] Prepare only the instrumentation needed for the goal. Keep benchmark, temporary profiling and application changes distinguishable; validate signal coverage and profiling overhead.
- [ ] Support importing approved benchmarks and proposing new ones through readable forms, with exact protocol-version approval and reuse checks.
- [ ] Validate the common objectives: completed throughput, p99 at specified offered load, and total process memory over five minutes. Keep heap, RSS, backlog, errors and measurement windows explicit.

**Done when:** a developer unfamiliar with the workbench can onboard a public JVM application without editing the supervisor or hand-authoring internal JSON. A missing p99 or RSS measurement produces a useful preparation path or a precise blocker. The full workflow remains available through both UI and CLI.

## M3: Make exploration respond to evidence

Specification §§8–9, 13–14.

- [ ] Rank initial hypotheses from measured profiles, workload constraints and validation cost. Record the proposed mechanism, supporting evidence and what would refute it.
- [ ] Update the plan after outcomes; detect repeated uninformative failures and stagnation, then reprofile within budget, change strategy or stop with a reason.
- [ ] Let users prioritize, exclude or defer strategy families and preserve these decisions across resume.
- [ ] Add attributed strategy imports with source commit, section, applicability and licensing checks. Treat imported documents as data, and keep private strategies local.
- [ ] Expose the evolving plan, task concurrency, lineage and reasons for rejection in the UI. Preserve failed, duplicate and inconclusive attempts alongside successful ones.
- [ ] Help users choose among admissible memory/throughput/latency tradeoffs using their constraints, uncertainty and review cost. Never combine the best metrics from different candidates into a fictional result.

**Done when:** a bounded public campaign changes its next action because of retained evidence, stops when no justified work remains, and lets a reviewer follow each decision back to its measurements. Parallel preparation must continue to respect measurement isolation. More sophisticated search policies need comparisons at equal budgets before becoming defaults.

## M4: Reproduce and deliver results elsewhere

Specification §§7, 11, 13, 15, 18. Existing paths: [scenario execution](docs/build/SCENARIOS.md) and [distribution](docs/build/DISTRIBUTION.md).

- [ ] Make replay dependency-complete: exact source, protocol, runner configuration, tool/image versions, public data and setup/reset commands. List required external credentials without exporting them.
- [ ] Verify a user-controlled Linux runner on another host, including environment drift, cancellation, coordinator loss and owned-resource cleanup.
- [ ] Replay an exported campaign from a clean installation. Refuse incompatible or incomplete context with a specific explanation and require a fresh baseline when necessary.
- [ ] Publish an actual draft PR through the product into a designated public test repository, after review of the exact confirmed payload. Exercise retry after uncertain publication and a changed remote base.
- [ ] Verify standalone patch/report export and preparation-only delivery, including a separate instrumentation draft when appropriate.

**Done when:** another machine can reconstruct and rerun the documented workload, and the published draft's tree and parameters match the confirmed evidence. Reproduction preserves the protocol and provenance; it does not promise identical numbers on different hardware. GitHub remains optional for local review and export.

## M5: Qualify the first public release

Specification §§14, 16, 18, 20–21.

- [ ] Validate the supported installation matrix, including a Linux supervisor, clean upgrades, database/schema migration, backup and restore. Document platform and runner limits.
- [ ] Provide authentication and protected transport before supporting a network-exposed browser UI. Document local trusted-team operation and review credential, source and export boundaries.
- [ ] Audit remaining specification §20 cases against actual tests and live evidence. Close gaps in workload validity, uncertainty defaults and failure recovery; publish the coverage limits.
- [ ] Add public demonstrations for allocation/memory and a genuine throughput/latency tradeoff. Run bounded campaigns on additional public Java repositories without modifying the core.
- [ ] Report all evaluation outcomes, spend/accounting mode, reproducibility and required human intervention. Keep the synthetic fixture's claims separate from real-application results.
- [ ] Finish the newcomer quickstart, troubleshooting, contribution and extension guides. Confirm the public name and license for new components; review bundled dependencies and assets.
- [ ] Validate the complete UI with unfamiliar users, including keyboard navigation, narrow layouts, blocked states and recovery. Retain the documented ElevenLabs-inspired product direction.

**Done when:** the public quickstart and the specification's first-release acceptance scenarios pass on declared environments with retained, shareable evidence. Test counts and attractive screens alone do not close this milestone. No private application material is required for installation, examples or release proof.

## M6: Complete broader specification capabilities

These remain part of the intended product. They can follow the narrower first release described in §18; they must not disappear from the backlog.

- [ ] Combine candidate branches with multiple parents, conflict handling and fresh measurement/confirmation. Never inherit a parent's verdict as proof of a combination.
- [ ] Expand reusable, versioned benchmark/scenario libraries, approved parameter ranges and comparisons across workload sizes and environments.
- [ ] Reuse negative results only in their recorded context; explain when a changed JDK, workload or environment justifies trying again.
- [ ] Add explicit artifact-retention policies, missing-artifact visibility and richer histogram, memory-series and profile inspection.
- [ ] Schedule independent measurement resource groups on suitable runners. Current parallel research does not mean simultaneous benchmarks on a shared machine are safe.
- [ ] Add an optional public Kafka/encryption scenario with explicit correctness and cryptographic invariants.
- [ ] Migrate original-plugin commands onto the supervisor and import historical experiments as partial evidence when provenance is missing. Prevent the plugin from bypassing product budgets and approvals.

## Later, outside the first self-hosted release

SaaS and hostile multi-tenant isolation, managed runner provisioning, team roles, cloud billing, broader language support, a learned search policy and an extension marketplace are separate work. Stabilize the self-hosted workflow and its guarantees first. A future hosted service must preserve private campaign ownership and explicit sharing controls.

## Keeping this roadmap current

Close a checkbox only with an implementation and appropriate evidence linked from the build documentation. Record controlled tests, synthetic benchmarks and real integrations separately. Review priorities after each milestone; update the specification explicitly when changing its promises. Keep private code, traces, account details and application measurements outside this public roadmap.
