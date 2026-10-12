# Implementation contracts

Status: executable development version. See `STATUS.md` for observed behavior and open release gates. The product specification remains authoritative.

The new Python package lives in `product/autoresearch`, tests in `product_tests`, frontend in `web`, and public workloads in `examples`. Existing plugin files and user-owned `meta/` are preserved.

## Ownership

- Leader: `pyproject.toml`, models, SQLite store/budget, engine, API, CLI, integration and status.
- Provider worker: `provider.py`, provider tests, `docs/build/PROVIDER.md`.
- Runner worker: `runner.py`, `measurement.py`, `jvm.py`, workloads and their tests.
- UI worker: `web/` only.

## Agent working behavior

The leader owns architecture, shared contracts, integration and final verification. Workers receive a bounded task, owned files, observable acceptance criteria and the evidence required for a handoff. They inspect current behavior before editing, implement within their boundary, run relevant checks and report remaining uncertainty. Model choice follows task complexity; it does not replace verification.

When a check fails, preserve the failure, identify the violated invariant and repair the cause before claiming success. The leader verifies the integrated behavior independently. Simulated transports, real executions and untested capabilities must be named separately. A prompt may guide proposals; code enforces cost admission, execution limits and benchmark integrity.

## Runtime boundaries

Money is integer micro-USD. Store exposes `reserve(campaign_id, key, amount_micro_usd, purpose='exploration')`, `settle(campaign_id, key, actual_micro_usd, event_id)`, and `budget(campaign_id)`. Reservations are durable and idempotent. Unknown calls retain their reservation. Calls and retries have distinct keys. Final usage is not an authoritative bill; retain conservative cost if necessary.

Runner API: `run_command(command: list[str], cwd: Path, artifact_dir: Path, *, timeout_seconds: float, cancel: threading.Event | None = None, runner: dict | None = None) -> dict`. Return `status`, `returncode`, `stdout`, `stderr`, `duration_seconds`, `environment`, `artifacts`. Timeout/cancel kills descendants. Docker is the isolated mode; host is explicitly trusted development only. Docker gets no credentials/network by default. Protocol commands are controlled by supervisor. A benchmark emits a JSON object with `metrics` mapping names to finite numerical values, optional `series`/`histograms`, and `checks` mapping names to booleans.

Provider API: `ClaudeProvider(config: dict, ledger).propose(campaign_id: str, key: str, context: dict, *, purpose='exploration') -> dict`. Proposals are JSON objects with `hypothesis`, `strategy`, `patch` (unified diff), and `expected_effect`; preparation can return `protocol` proposals. No provider shell tools or autonomous child API access. Provider exposes `capabilities() -> dict`. No real paid call during development without an explicitly configured test budget and credentials.

Frontend REST base `/api`: GET `/capabilities`, GET/POST `/campaigns`, GET `/campaigns/{id}`, POST `/campaigns/{id}/{assess|prepare|profile|approve|run|pause|stop|resume|reconcile}`, POST `/campaigns/{id}/experiments` with `{hypothesis,strategy,patch,parent_id?}`, GET `/campaigns/{id}/events?after=0`, GET `/campaigns/{id}/export`, GET `/campaigns/{id}/report`. JSON errors have `detail`. Approve accepts `{protocol_hash}`. Create accepts `name`, `repository`, `goal`, `budget_usd`, optional `protocol` and `runner`; richer fields defined by models. Detail includes `campaign`, `protocol`, `protocol_hash`, `readiness`, `budget`, `experiments`, `events`, `comparison`. Campaign fields include `id`, `name`, `goal`, `repository`, `state`, `outcome`, `termination_reason`, `created_at`. Budget exposes `limit`, `consumed`, `engaged`, `protected`, `available` in micro-USD plus `currency`, `enforcement`, `scope`. `events` rows have `seq`, `type`, `created_at`, `data`.

API mutations operate the actual engine; mock data is allowed only in explicit UI tests. Execution runs in a supervisor background thread, never browser lifecycle. API serves built frontend from `web/dist`; development uses Vite proxy. Bind loopback by default; remote binding requires authentication.
