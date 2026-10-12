# Claude provider: implemented path and remaining strict-budget gate

Reviewed 2026-10-11. Implementation: `product/autoresearch/provider.py`.

**The direct Messages adapter is implemented and tested with simulated HTTP responses. Strict invoice enforcement is not verified and is refused. The specification's strict-Claude delivery criterion remains open.** There is no automatic downgrade: both provider configuration and the campaign ledger must explicitly say `enforcement: estimated`.

## What runs

`ClaudeProvider(config, ledger).propose(campaign_id, key, context, purpose="exploration")` sends one HTTPS POST to Anthropic's first-party `/v1/messages`. It returns strings `hypothesis`, `strategy`, `patch`, and `expected_effect`, optionally a `protocol` object for preparation. The supervisor must validate patches and protocol proposals before execution or approval. A prompt does not enforce benchmark integrity; the supervisor does.

The adapter uses `httpx` directly. It does not start Claude Code, run a shell, expose tools, compact a session, create subagents, or use an autonomous tool loop. The body is constructed from an allowlist. The supplied context is JSON-encoded text, so a repository field named `tools` cannot become an API tool. Caching and thinking are disabled, service tier is `standard_only`, and no beta features are enabled. Redirects, environment proxy discovery, and automatic retries are disabled. Arbitrary endpoints, prices, unknown models and extra config keys are rejected.

Only `claude-haiku-4-5-20251001` is admitted. This is a versioned model with a small reviewed surface, not a claim that it is the best optimization model. Its documented context limit is 200,000 tokens, with input $1/million and output $5/million. Its published retirement promise is only “not sooner than October 15, 2026”; availability must be reviewed before future deployments. A retired-model error is not silently switched to another model. [Haiku 4.5 specifications](https://platform.claude.com/docs/en/models/haiku-4-5/overview).

Example campaign agent configuration:

```json
{
  "kind": "claude",
  "enforcement": "estimated",
  "model": "claude-haiku-4-5-20251001",
  "max_tokens": 4096,
  "timeout_seconds": 60,
  "api_key_env": "ANTHROPIC_API_KEY"
}
```

`adapter: claude` is also accepted. Defaults other than enforcement are optional; enforcement defaults to strict and therefore refuses paid calls. Set the credential in the supervisor's environment, never in campaign JSON. No credential is read for capabilities inspection. No real credentials were inspected during implementation.

## Reservation proof and its boundary

The reviewed price identifier is `anthropic-haiku-4.5-standard-global-2026-10-11-v1`. The adapter reserves the whole input context, not an approximate count of this prompt:

```text
R_microUSD = 200000 × 1 + max_tokens × 5
default R = 220480 microUSD = USD 0.220480
```

This arithmetic uses integer micro-USD throughout. Under the reviewed rates, a single accepted request's input and output token charge cannot exceed this amount: the input cannot exceed the documented context ceiling and output is bounded by `max_tokens`. Reserving the entire input context plus maximum output is deliberately conservative, including cases where both cannot fit simultaneously. [Context overflow behavior](https://platform.claude.com/docs/en/build-with-claude/context-windows), [Messages output limit](https://platform.claude.com/docs/en/api/messages/create), [pricing categories](https://platform.claude.com/docs/en/about-claude/pricing).

This is a **conditional token-price bound**, not a proven cap on the actual provider invoice. The reviewed public price snapshot is not locked by the API request. The integration has not established final billing behavior for every failed, interrupted, or delayed request, nor verified invoices against this adapter. No final invoice reconciliation exists. These gaps are exposed separately from `implemented: true`: capabilities report `strict_budget: false`, `strict_billing_verified: false`, `live_verified: false`.

A configured upper price bound could support strict accounting if there were evidence that the provider cannot charge more than that rate for every admitted request, including failures and delayed billing. The input/output architecture does not inherently make strict mode impossible. This implementation does not have that evidence, so changing configuration or acknowledging risk cannot enable strict mode.

Scope is model API consumption. Taxes, subscriptions, existing runners and external services are excluded. These exclusions do not cure uncertainty about the model API charge itself.

The token counting endpoint is intentionally not used to lower reservations: Anthropic documents its result as an estimate that can differ from the actual count. Counting is free, but that does not make the result a strict upper bound. [Token counting](https://platform.claude.com/docs/en/build-with-claude/token-counting).

## Durable admission and unknown outcomes

The provider requires the ledger contract in `CONTRACTS.md`, including `reserve(...) -> {created: bool, ...}` and `budget(...).enforcement`.

1. Validate configuration, campaign enforcement, context size and credential presence.
2. Atomically reserve the full amount. Only literal `created: true` authorizes transmission.
3. Send one request, without retries.
4. Validate response model, usage bounds, content and proposal shape.
5. On success, settle the **full reservation as conservative retained cost** using a deterministic, campaign-scoped event ID. The returned usage does not release money.

A second invocation with the same key never sends, including after a crash between reservation and transmission, after a lost response, or after settlement. This favors missed work over duplicate spending. Responses are not cached for replay; the supervisor must persist a received proposal. An explicit retry needs a new key and a new reservation.

Timeouts, connection errors, malformed bodies, absent usage, HTTP errors, truncated proposals and failed settlement retain the reservation. Even a 400 or 401 does not release money based on an unverified billing assumption. There is no automatic refund or retry when an error appears cheap. No network operation occurs if budget access or reservation fails.

Response token overrun, a changed model, unexpected cache usage or server-tool usage raises `ProviderContractViolation` and disables that provider instance. The supervisor persists the incident and blocks the campaign; the adapter's instance flag is not a durable installation-wide quarantine. The strict guarantee is always unavailable, including after a restart.

## SDK and CLI findings

Local read-only inspection found Claude Code `2.1.289` with `--max-budget-usd`, and Python `anthropic` `0.75.0` with `max_retries=2`. No SDK client was initialized and no paid request was made. The implementation does not depend on either installed version.

Anthropic documents that Claude Code checks its budget after model responses. One response and already-running subagent work can cross the cap. `/clear` also restarts the session budget. Therefore `--max-budget-usd` alone does not prove the campaign invariant. [Agent loop budget headroom](https://code.claude.com/docs/en/agent-sdk/agent-loop#budget-headroom).

Agent SDK costs are client-side estimates calculated from a bundled price table and can differ from billing. Usage and Cost API data or the provider console is the authoritative source. [Cost tracking](https://code.claude.com/docs/en/agent-sdk/cost-tracking).

The Python SDK retries selected connection errors, timeouts and HTTP errors twice by default; automatic retries would need distinct reservations. Direct HTTP avoids that default. [Python SDK retries](https://platform.claude.com/docs/en/cli-sdks-libraries/sdks/python#retries).

Anthropic also documents a separate Managed Agents session-budget API. It is not the Messages/Claude Code path implemented here and has not been evaluated for this product's execution, data or billing requirements. This report does not claim that all Anthropic products lack budget controls. [Managed Agents session budgets](https://platform.claude.com/docs/en/managed-agents/budgets).

## Validation and remaining work

Run without network or paid credentials:

```sh
PYTHONPATH=product python3 -m pytest -q --tb=short product_tests/test_provider.py
```

44 tests passed at implementation time. They cover reservation before transmission; insufficient and inaccessible budgets; concurrent admission; duplicate identities; duplicate calls after SQLite reopen; explicit retry accounting; strict campaign protection; success retaining conservative cost; settlement failure; lost responses; 3xx/4xx/5xx without retry or redirect; usage missing, invalid or above bounds; model changes; unexpected paid features; malformed, excessive or truncated responses; and invalid configuration. All HTTP tests use `httpx.MockTransport`.

Remaining limitations:

- Paid integration and authoritative billing reconciliation remain untested. Strict release acceptance remains blocked.
- HTTP connect/read/write timeouts bound inactivity. An elapsed check runs between response chunks; this is not an operating-system hard wall-clock watchdog or proof that the remote generation stopped. The supervisor now runs `provider_worker` in a separate process with the runner's deadline/watchdog; direct library callers must supply their own task deadline. Unknown remote spend remains reserved.
- API usage and request IDs are not yet persisted as reconciliation evidence by this minimal ledger interface. Settled values are conservative retained charges, not observed invoices.
- Credential isolation and blocked alternative paid endpoints must be verified by runner integration. The provider never hands credentials to the runner, but that alone does not prove runner isolation.
- The supervisor owns durable campaign incident quarantine, review, protocol integrity, context selection and response persistence. Installation-wide quarantine is not implemented. The provider does not execute a proposal.

To remove the strict gate, supply evidence for effective rate and billing-category bounds, test real success/failure/timeout billing within an explicitly authorized test budget, add durable invoice evidence and incident quarantine, and verify the runner cannot use an alternative credential or paid endpoint. Merely increasing the safety margin is insufficient.
