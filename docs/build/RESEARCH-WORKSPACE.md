# Campaign overview and parallel research

This iteration follows the user's request to run several research paths and inspect KPIs, hypotheses, history and outcomes in one product workspace. The existing measurement, budget and approval contracts still apply.

## Workspace contract

Overview is the campaign entry point. It uses retained measurements and current supervisor state. The detailed Preparation, Experiments, Comparison, Activity and Report views remain reachable.

The API adds a read-only `insights` projection to campaign detail. It summarizes attempts, presents each protocol metric and groups experiments by strategy. It uses context-verified comparison records for current KPI claims. Historical attempts remain visible, but old protocols, failed executions and skipped duplicates cannot become current wins. A confirmed experiment does not prove that every metric improved: resolution is checked per metric. Each gain uses that experiment's paired reference.

The projection preserves explicit hypotheses, expected effects, observed reasons and evidence status. It does not invent a causal explanation or label an unresolved hypothesis false. Untested strategy families remain untested.

## Scheduling contract

`limits.max_parallel_tasks` sets a finite campaign limit between one and four. New campaigns default to two; older campaigns without this field retain one. Budget, attempt and duration limits remain independent admission checks.

Independent proposals and candidate preparation can run concurrently in bounded batches. Each proposal has its own budget reservation and task record. Repository changes use separate owned checkouts. The supervisor drains a preparation batch before entering measurement. A shared gate prevents other controlled research work from overlapping a benchmark on the shared measurement host. This coordinates the supervisor's own tasks; it cannot reserve an entire developer machine against unrelated processes.

Measurements and independent confirmation remain serialized. Additional measurement hosts, simultaneous remote benchmarks and cross-branch composition are outside this iteration. The UI states this distinction explicitly instead of treating every running task as a benchmark.

Cross-campaign admission is not a durable queue: `run` and `resume` return HTTP 409 promptly while another campaign is measuring, with no work queued or campaign state changed. After measurement finishes, an intentional new request may retry admission; replaying the rejected request's idempotency key returns the same rejection.

Scheduler state records its phase, task limit, active tasks and task identities. Experiment phases distinguish preparation, waiting for measurement, measurement and confirmation. Pause, stop, deadline and recovery must leave a truthful history and preserve unknown monetary reservations. A terminal state cannot silently abandon an active owned worker.

## Verification requirements

- Demonstrate actual overlap of independent preparation tasks and enforce the configured task ceiling.
- Show that concurrent proposals cannot oversubscribe budget or exceed the experiment cap.
- Show that a benchmark never overlaps another benchmark or a controlled preparation batch.
- Exercise pause, stop, failure and coordinator recovery with multiple admitted tasks.
- Preserve duplicate suppression, protocol context, protected evaluators and retained unsuccessful attempts.
- Inspect Overview with real JVM evidence, open an experiment from its strategy and inspect narrow layout.
- Run backend and frontend regression checks. Paid provider transport remains unverified without an authorized live test.

Observed results and remaining gaps are recorded in [build status](STATUS.md).
