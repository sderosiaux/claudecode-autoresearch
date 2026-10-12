# Prototype delivery status

This is a functional development snapshot for one trusted local user/team. It does not complete the [product specification](../plans/2026-10-11-performance-research-product-spec.md). Application-specific source, patches, configuration, performance results, session traces and account usage remain local and are excluded from this public repository.

## Implemented and checked

- UI, CLI and durable campaign state, hypotheses, comparisons, history and evidence export.
- Bounded parallel research preparation with serialized controlled measurements.
- Protocol approval, protected evaluator files, isolated Docker workloads and scenario services.
- Repeated baseline/candidate observations, uncertainty, correctness constraints and independent confirmation gates.
- Native Claude Code navigation and edits through a thin connector; controlled tests for authentication, proposal validation, retained reservations and process completion.
- Pause/stop/recovery and conservative budget accounting with unknown costs retained.
- Public synthetic JVM fixture, including actual Docker measurements, memory sampling and diagnostic artifacts.
- Bundled distribution installed outside the checkout with a new environment and empty data directory.

The final local checks passed **366 Python tests**, **44 UI tests**, Ruff lint/format checks over 38 Python files and the production UI build. One upstream Starlette TestClient deprecation warning remains. The 75 focused native adapter/engine tests belong to this verification history and are not an additional full-suite count.

The wheel installation passed nine checks, including fourteen linked assets and CLI/API agreement, without a model call or benchmark. The source archive rebuilt offline without Node/npm, with all 41 runtime files matching the original wheel. Commands, hashes and limits are in [distribution validation](DISTRIBUTION.md). Public synthetic results and their scope are in [build status](STATUS.md).

## Remaining product and validation work

The [product roadmap](../../ROADMAP.md) gives these gaps a delivery order and acceptance criteria.

- Accessible onboarding for arbitrary repositories without workload-specific manual setup.
- More general goal-dependent readiness analysis and autonomous instrumentation/protocol preparation.
- Adaptive research strategies, stagnation diagnosis, attributed strategy imports and combining candidate branches.
- A complete accepted live-native optimization and live-native preparation flow.
- Installation-wide quotas and authoritative provider-billing enforcement; current native accounting is estimated.
- Remote Docker validation on another host and real draft-PR publication through the product connector.
- Dependency-complete replay, environment migrations and remote-browser authentication.
- Multiple-user authorization and any future SaaS capabilities.

External-worker, manual and native proposals retain distinct origins. A proposal, successful model response or isolated replay is not a confirmed optimization. Failed and inconclusive outcomes remain evidence. The published tests establish the behaviors they exercise, not full specification completion or performance claims for private applications.
