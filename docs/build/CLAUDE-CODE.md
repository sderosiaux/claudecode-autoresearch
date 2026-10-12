# Native Claude Code provider

`product/autoresearch/claude_code.py` admits one headless native Claude Code session in a supervisor-created isolated checkout. Claude Code performs navigation and edits with its own agent loop; the adapter does not select source snippets or recreate an explorer. The supervisor extracts the actual Git diff, rejects protected-file changes and symlinks, validates any proposed protocol, and runs checks and measurements separately. A returned hypothesis is not evidence of a performance gain.

## Interface and configuration

```python
ClaudeCodeProvider(config, ledger, *, execute=None, executable=None)
provider.capabilities() -> dict
provider.propose(
    campaign_id, key, context, *, workspace, artifact_dir,
    purpose="exploration", cancel=None, deadline=None,
) -> dict
```

The return value contains required string fields `hypothesis`, `strategy`, `expected_effect`, an empty `patch`, and optionally a `protocol` object. Native edits remain in `workspace`; the caller derives the patch rather than trusting model-authored diff text. During measurement preparation, the native prompt requests instrumentation and a protocol for validation and approval. The adapter only validates that the optional protocol is an object; the engine applies the full `Protocol` contract.

Reviewed configuration:

```json
{
  "kind": "claude_code",
  "enforcement": "estimated",
  "model": "claude-sonnet-5-5",
  "effort": "medium",
  "auth_mode": "cli_login",
  "session_softcap_usd": "0.75",
  "reservation_micro_usd": 2500000,
  "max_tokens": 4096,
  "max_turns": 12,
  "timeout_seconds": 300
}
```

The model, effort, tool set and executable selection cannot be expanded by serialized configuration. Lower output, turn, timeout, reservation and soft-cap limits are permitted; the soft cap must remain below the reservation. Executable and transport injection exist only in the Python constructor for controlled tests. The adapter checks installed CLI version `2.1.289` before each admission and rejects another version pending review. A dated Sonnet 5.5 telemetry identifier is accepted only with an eight-digit suffix; another model fails verification.

## Admission and accounting

Local version validation and, for native login, a fresh `claude auth status` check precede reservation. The login check requires a logged-in first-party Claude account; its output may contain account identifiers and is not persisted. A failed preflight admits no model session and creates no reservation. After those checks, transactional `Store.reserve` commits the entire configured session envelope **before** the native model process starts. Duplicate keys never resend or resume a session. Successful completion settles the full reservation as conservative consumed budget, while recording the native estimated cost separately. Timeout, cancellation, malformed output, tool/model mismatch and transport failure retain the entire reservation as unknown engaged spend. There is no supervisor retry, fallback model, or automatic refund. Cancellation is checked before admission and again immediately before launch. Local version probing, admission and execution share the task deadline; the existing process-group watchdog bounds the native process when the supervisor exits.

This is **estimated enforcement**, not a strict billing guarantee. Native `--max-budget-usd` is checked after a response and can overshoot; `capabilities().strict_budget` is false, and strict campaigns are refused. Under native subscription login, `total_cost_usd` is a subscription-equivalent estimate, not a verified incremental cash charge. API authentication also supplies estimated telemetry rather than an invoice. See the official [budget-headroom explanation](https://code.claude.com/docs/en/agent-sdk/agent-loop#budget-headroom).

The adapter enforces a campaign ledger, not an installation-wide quota. Concurrent campaigns require explicit coordination of their budgets. The native per-response soft cap does not establish a hard provider-side cap on their combined invoice.

## Native isolation and authentication

Native `--restricted` confines file tools to the working directories; the only configured tools are `Read`, `Glob`, `Grep`, `Edit`, `Write` and structured-result plumbing. Shell, agent, skill, web, browser and MCP tools are excluded. The command preserves the native default system prompt and appends task constraints. Explicit settings disable hooks and deny credential patterns, `.git`, `.claude` and `.codex` reads/edits. Prompt suggestions are disabled to avoid an additional inference path.

`auth_mode="cli_login"` uses `--safe-mode`, preserving native login/keychain handling. The supervisor neither extracts nor copies credentials and forwards no API key or OAuth token from its environment. `auth_mode="api_key"` uses `--bare`, forwards only the credential named by `api_key_env` (default `ANTHROPIC_API_KEY`), and does not use native login. The child environment contains the home directory, the effective OS account name as `USER`, a system PATH, terminal flags and fixed Claude Code controls; inherited endpoint overrides, proxy settings and unrelated credentials are absent. `USER` comes from `pwd.getpwuid(os.geteuid())`, not the inherited environment: native macOS login lookup failed without this account name in local validation. The installed native retry control is set to zero, and the supervisor never retries the session.

This is not an OS sandbox. Managed policy still applies, and file-deny discovery coverage is best effort. The caller must supply a credential-filtered snapshot in a fresh Git repository, excluding secrets, symlinks and original Git configuration/history. The native process necessarily contacts Anthropic for the authorized model session. The official [CLI reference](https://code.claude.com/docs/en/cli-reference) documents safe mode, restricted mode, settings, authentication and print-mode options; [permissions](https://code.claude.com/docs/en/permissions) describes file-rule limits.

Filename filtering cannot discover credentials embedded in ordinary documentation or test files. Audit the committed source before authorizing transmission. Set `agent.excluded_paths` to repository-relative files or directories identified by that review, for example `["docs/internal-setup.md", "fixtures/access"]`. These explicit exclusions apply only to the native agent snapshot, leave the benchmark checkout intact, and are recorded in its context. Paths are exact, normalized and relative; glob patterns are not supported. A redacted local secret scan can inform this list but cannot prove that source contains no secrets.

## Retained evidence and verification

Artifacts must be outside the editable checkout. Files are created exclusively with mode `0600`: `prompt.json`, native stdout `trace.jsonl`, `stderr.log`, `metadata.json`, and successful `result.json` telemetry. Stream output is bounded to 16 MB per output stream; an output-limit result fails closed and preserves the bounded partial trace. Credentials are never included in command arguments or adapter metadata. Artifacts may contain approved source/task text and must remain private.

The parser requires one native initialization with an approved tool inventory, no MCP servers, one successful structured result, matching model usage, finite nonnegative cost within the reserved envelope, and no permission denials. The structured schema bounds `strategy` to 200 characters to match engine import; the prompt directs detailed reasoning into other fields. The engine still validates the imported proposal independently. Known metadata-only trailing system events are accepted; further assistant activity, results or suggestions are rejected. Actual source edits remain subject to the supervisor's independent validation even after a successful parse.

Run the controlled tests without any model request:

```sh
uv run pytest -q product_tests/test_claude_code.py --tb=short
uv run ruff check product/autoresearch/claude_code.py product_tests/test_claude_code.py
```

Verification on 2026-10-12: **75 focused adapter and native-engine tests passed** after the observed authentication and import failures were addressed. Coverage includes pre-reservation authentication, atomic admission, retained unknown spend, cancellation, deadline refusal, bounded traces, authentication environment stripping, tool/model/result violations, strategy bounds and optional preparation protocols. These focused checks are part of the verification history; the final full-product run passed 366 Python tests with one upstream Starlette warning. Ruff check/format covering 38 files also passed after a scheduler formatting-only adjustment. Earlier local version/help checks made zero model requests and proved argument parsing only.

## Validation boundary

The adapter has been exercised locally, but an accepted, correct live-native optimization and live-native measurement-preparation workflow remain unverified. Private session traces, account usage, application-specific patches and measurements are not part of this public repository.

Local validation exposed two adapter integration issues now covered by controlled tests: native macOS authentication needs the effective OS account name in the sanitized environment, and structured proposal bounds must match the engine import contract. The native CLI's configured turn and budget thresholds are not verified hard provider limits. A successful response is still subject to import, correctness and measurement gates.

Controlled tests verify that local resource leases wait for all sibling processes with completion evidence, while preserving unknown monetary reservations. See [delivery evidence](DELIVERY.md) for the prototype's checks and remaining product work.
