# Autoresearch workbench

An executable development version of the public, self-hosted performance research product. It is separate from the existing Claude Code plugin. The full release gates are tracked in [build status](build/STATUS.md).

## Install and start

Runtime prerequisites: Python 3.12+, Git and Docker. A built wheel includes the UI and public fixture; installing it does not require Node.js or a source checkout. See [distribution and clean installation](build/DISTRIBUTION.md) for the build and verification commands. Workloads run in a Linux container even when the coordinator is on macOS. The initial installation serves one trusted user/team; it is not a multi-tenant service.

For development, also install uv and Node.js 22+, then run from the repository root:

```sh
uv sync --extra dev
npm --prefix web ci
npm --prefix web run build
docker pull eclipse-temurin:25-jdk
uv run autoresearch serve
```

Open <http://127.0.0.1:8765>. The SQLite state and artifacts default to `.autoresearch-data/`, outside the application source package. Close the browser without losing work; use Pause or Stop to control a campaign. Only one coordinator may own a data directory.

The server binds loopback. A remote deployment requires `AUTORESEARCH_AUTH_TOKEN` and a TLS reverse proxy; the CLI sends the token as a bearer header. The browser UI currently targets the local deployment, so remote browser authentication is not yet a supported onboarding path. No code or campaign telemetry is sent to this project's maintainer. Fonts are served locally.

For UI development, run `npm --prefix web run dev` alongside the API; Vite proxies `/api` to port 8765. After rebuilding the UI for a server first started without `web/dist`, restart the server to mount the build.

## First real Java experiment

The demo copies a public synthetic stream processor into a new, separate Git repository. It does not modify an application checkout.

```sh
uv run autoresearch demo /tmp/my-performance-demo
uv run autoresearch init /tmp/my-performance-demo/campaign.json
```

`init` returns the campaign ID. Open that campaign in the UI, assess readiness, review the complete protocol and approve its exact hash. The CLI resolves a relative repository path in a manifest against that manifest's directory.

Add the demo's `avoid-copy.patch` as an experiment with a hypothesis, or use the CLI:

```sh
uv run autoresearch experiment CAMPAIGN_ID \
  /tmp/my-performance-demo/avoid-copy.patch \
  --hypothesis 'Remove redundant payload copies while preserving CRC correctness.' \
  --strategy allocation-copy
```

Start the campaign from the UI. The engine measures a repeated baseline, alternates reference/candidate measurements and, for promising results, performs new confirmation measurements. It retains failed experiments and unresolved comparisons. The protected benchmark evaluator lives in `StreamWorkload.java` and `run.sh`; patches change `StreamProcessor.java`.

The demo uses actual completed throughput, processing-thread allocations, error checks and Linux process memory samples. It emits p99 only for its explicitly paced arrival scenario. The short default workload is a smoke test, not an application-wide performance claim or proof against memory leaks. See [JVM validation](build/JVM-VALIDATION.md) for actual observations and limits.

## Follow several research paths

Overview opens by default. It brings together attempt outcomes, current tasks, metric values and research tracks. Each track retains its hypotheses, expected effects, observed decisions and links to the exact experiment evidence. Filter confirmed, unresolved, failed, skipped or historical attempts to inspect what happened. Opening or refreshing the page does not start work.

Each metric's best confirmed value comes from an independently confirmed experiment with a resolved improvement for that metric. Its reference is the paired reference measured with that candidate. Different metrics may point to different candidates; the table does not describe one combined winning program. Constraints display their thresholds and latest accepted observations. Uncertainty intervals retain the metric's unit. Earlier protocol results remain in history and cannot become current KPI claims.

Set `limits.max_parallel_tasks` from one to four in a manifest or the campaign creation form. New campaigns default to two; existing records without the field remain sequential. Independent proposals and candidate checkouts run in bounded batches, with separate attempt records and budget reservations. The scheduler shows active tasks and candidates waiting for measurement.

Benchmarks and confirmation runs share one measurement slot across campaigns. The supervisor waits for controlled preparation work to finish before measuring, and blocks new preparation during a measurement. This prevents its own workers from contaminating the benchmark; unrelated activity on the developer machine can still affect results. Parallel measurement on separate hosts is not implemented. See the [workspace contract](build/RESEARCH-WORKSPACE.md) for the scheduling and evidence rules.

## Bring a benchmark

Create a campaign against a Git repository with an existing commit. The coordinator uses the specified committed revision; uncommitted application edits are preserved and are not silently included in the baseline. A preparation proposal creates an isolated revision for review.

A protocol supplies an argv command, metrics with units and roles, independent repetition counts, timeouts, protected evaluator paths, and optional `warmup_command`, `environment` and `diagnostic_command`. It is approved by hash. Changing it invalidates approval. Commands run in a fresh Docker snapshot without provider credentials, host mounts or Docker socket. Network access is disabled by default. A benchmark that needs a broker or database can declare [isolated scenario services](build/SCENARIOS.md); these share a fresh private network with no external access or published ports. The configured images must contain the required build tools and offline dependencies.

The UI's guided benchmark setup accepts a command, objective metric, unit, direction, protected evaluator paths and repetition settings. It creates a draft protocol for review; it does not approve or execute it. Use JSON import for several metrics, hard constraints or additional environment settings. Editing an existing complex protocol preserves its complete JSON instead of reducing it to the guided form's fields.

The command must emit exactly one JSON result document, or one JSON result line among ordinary logs:

```json
{
  "metrics": {"completed_per_second": 12000, "errors": 0},
  "units": {"completed_per_second": "messages/s", "errors": "count"},
  "checks": {"reference_output_matches": true}
}
```

These numbers illustrate the format. Actual evidence must come from executed benchmarks. Missing/nonfinite values invalidate a measurement. Correctness checks must be explicit; JMH timing output alone is insufficient. The JVM adapter decodes JMH JSON, units and raw forks, and provides JFR command generation. An application wrapper still needs to connect its timing output to its functional evaluator.

Metric roles determine the decision. Objectives require a resolved useful improvement; constraints must pass in every candidate run. Informational metrics retain their values, intervals and noise flags, but their variation does not decide whether an objective is reliable. A generator-rate requirement belongs in a constraint or correctness check, even when generator lateness is also retained as a diagnostic.

For a separate diagnostic command approved in the protocol, `autoresearch profile CAMPAIGN_ID` collects artifacts without adding those runs to the comparison. For the demo, a diagnostic argv can be `['env', 'JFR=1', 'sh', 'run.sh']`. Heavy profiling should remain separate from confirmation.

The runner accepts `docker_host: ssh://user@runner` for an administrator-configured remote Docker endpoint. It uses bounded archive streams rather than coordinator-local bind mounts. The remote path is implemented but has not yet been verified on a separate remote machine. Explicit `kind: host, trusted: true` is available for trusted development commands and provides no security isolation from the coordinator account.

## Claude and budget accounting

Identical candidate programs are not measured again when their exact Git tree, approved protocol, reference revision, runner configuration and baseline evidence match an already completed valid comparison. The new attempt stays in the history with a link to the earlier experiment, an inconclusive verdict and a reason for skipping execution. It gains no copied confirmation and still counts toward the experiment limit. A model call that proposed the duplicate remains accounted for. A changed measurement context permits another evaluation; failed or unknown executions cannot supply reusable evidence.

Manual experiments make no model calls. Their monetary ledger still tracks the campaign limit, retained spend, in-flight engagements, protected finishing reserve and available amount. Existing-machine costs are excluded explicitly.

The native Claude Code connector lets Claude Code navigate and edit a filtered checkout with its own tools. The workbench supplies the goal and evidence, extracts the actual changes, and runs the approved measurements. It does not implement another code explorer. Set `agent.kind: claude_code` and explicitly choose `enforcement: estimated`; the default authentication uses the supervisor account's existing Claude Code login. Credentials stay under native authentication and are not copied into benchmark workers. Review the [native connector contract](build/CLAUDE-CODE.md), including source exclusions and its currently reviewed CLI version.

The earlier `agent.kind: claude` direct Messages adapter remains available for bounded context requests. It uses `ANTHROPIC_API_KEY` from the server environment. Never put a credential in campaign JSON.

Each connector reserves a conservative model cost before transmission. Duplicate call identities are never resent; uncertain outcomes retain their reservation. The supervisor runs provider calls in bounded subprocesses. Native Claude Code also receives a lower session stop threshold, with headroom inside the reservation. Its reported subscription-equivalent cost is distinct from the retained ledger amount and from an actual invoice.

**A strict Claude invoice cap is not verified.** Native budget checks happen after responses and may overshoot; final billing after failed/interrupted requests has not been established. Strict Claude mode is rejected before spending; there is no automatic downgrade. [Build status](build/STATUS.md) distinguishes controlled tests from live validation. See also [provider evidence and limits](build/PROVIDER.md).

Preparation and optimization share the same durable campaign budget. Restarting the supervisor, opening a new agent call or retrying an action does not reset it. An unresolved budget incident disables further admission for that campaign. Recovery marks interrupted executions as unknown and does not retry them automatically.

After a coordinator interruption, use the UI's Reconcile action or `uv run autoresearch reconcile CAMPAIGN_ID`. The supervisor requires the persisted Docker/provider watchdog deadline to have expired before it accepts reconciliation. It records unknown experiment executions as cancelled and inconclusive, preserves monetary reservations, and waits for a separate Resume action. New execution remains blocked while recovery is unresolved. Expiry only establishes the local execution bound; it cannot prove that a remote model request stopped or was free. Lease expiry assumes a stable coordinator clock. Trusted host workloads, interrupted coordinator capability probes without an independent watchdog, and older interrupted records without a verifiable deadline require operator investigation; automatic reconciliation is refused.

## Inspect, reproduce and publish

```sh
uv run autoresearch list
uv run autoresearch status CAMPAIGN_ID
uv run autoresearch compare CAMPAIGN_ID
uv run autoresearch report CAMPAIGN_ID
uv run autoresearch reproduce CAMPAIGN_ID --experiment EXPERIMENT_ID
uv run autoresearch export CAMPAIGN_ID --output campaign.zip
```

`reproduce` returns the exact revision, protocol, runner configuration and required steps; it does not silently execute another benchmark. The export includes the manifest, event log, patches, report, raw artifacts and file hashes. Original repository access, recorded dependencies and required credentials remain prerequisites for reproduction. Review logs and source-derived reports before sharing an archive outside your installation; arbitrary application secrets cannot be reliably recognized in benchmark output.

Changing a protocol clears its prior readiness and baseline summary. Historical experiments remain in the campaign and export, but current comparisons require matching measurement context. Older records can still be displayed when their recorded runs prove the protocol, revisions and consistent execution environments; any missing runner-configuration identity is stated. Reproduction refuses mismatched or unverified context rather than pairing an old candidate with new settings.

Only independently confirmed candidates can produce a draft payload. The selected workspace must still match its confirmed clean tree and approved protocol.

```sh
uv run autoresearch draft CAMPAIGN_ID --experiment EXPERIMENT_ID
```

The result is a reviewable title/body and `review_hash`. Publishing is an explicit subsequent action with GitHub CLI authentication and permission to push to the selected repository:

```sh
uv run autoresearch publish CAMPAIGN_ID \
  --experiment EXPERIMENT_ID --review-hash REVIEW_HASH \
  --repository OWNER/REPOSITORY --base main \
  --head autoresearch/selected-candidate
```

This pushes the exact confirmed commit and creates a draft PR. It never merges. If the remote base has advanced, publication refuses until the candidate is revalidated. Durable publication intent and marker lookup prevent repeated creation after an uncertain response. Remote publication behavior has been tested with controlled transports; this development session did not push or create a PR.

## Development checks

UI changes follow the [product design guidelines](design/DESIGN-GUIDELINES.md), based on observed ElevenLabs application screens. `web/AGENTS.md` points future contributors to the same rules.

```sh
uv run pytest
npm --prefix web test -- --run
npm --prefix web run build
```

Docker-dependent checks require the demo image and exercise actual containers, interruption and independent watchdog termination. Do not run them concurrently with a performance campaign on the same host. The original plugin's code and tests remain separate.
