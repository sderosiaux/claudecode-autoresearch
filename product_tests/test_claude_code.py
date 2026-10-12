"""Controlled native-process contract tests; never launch a model request."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from autoresearch.claude_code import ClaudeCodeProvider, MODEL, SCHEMA, TOOLS
from autoresearch.provider import (
    DuplicateProviderCall,
    ProviderContractViolation,
    ProviderError,
    StrictBudgetUnavailable,
)
from autoresearch.store import BudgetExceeded, Store

PROPOSAL = {
    "hypothesis": "Avoid one redundant immutable payload copy",
    "strategy": "reuse immutable payload",
    "expected_effect": "Allocation may fall; unchanged checks or throughput refute it",
}


def trace(*, proposal=None, tool="Edit", tools=None, cost="0.12", model=MODEL, **final):
    rows = [
        {
            "type": "system",
            "subtype": "init",
            "tools": list(TOOLS) if tools is None else tools,
            "mcp_servers": [],
        },
        {
            "type": "assistant",
            "message": {
                "model": model,
                "content": [
                    {
                        "type": "tool_use",
                        "name": tool,
                        "input": {"file_path": "Processor.java"},
                    }
                ],
            },
        },
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "structured_output": PROPOSAL if proposal is None else proposal,
            "modelUsage": {model: {"inputTokens": 200, "outputTokens": 100}},
            "total_cost_usd": cost,
            "num_turns": 2,
            "session_id": "test-session",
            **final,
        },
    ]
    return b"".join((json.dumps(row) + "\n").encode() for row in rows)


class Transport:
    def __init__(self, raw=None, status="succeeded", callback=None, version="2.1.289"):
        self.raw = trace() if raw is None else raw
        self.status, self.callback, self.version = status, callback, version
        self.calls = []
        self.auth_calls = []

    def __call__(self, command, **kwargs):
        if command[-1] == "--version":
            return {"status": "succeeded", "stdout": self.version + " (Claude Code)"}
        if command[-2:] == ["auth", "status"]:
            self.auth_calls.append((command, kwargs))
            return {
                "status": "succeeded",
                "returncode": 0,
                "stdout": json.dumps(
                    {
                        "loggedIn": True,
                        "authMethod": "claude.ai",
                        "apiProvider": "firstParty",
                    }
                ),
            }
        self.calls.append((command, kwargs))
        if self.callback:
            self.callback(command, kwargs)
        return {
            "status": self.status,
            "stdout": self.raw,
            "stderr": "",
            "returncode": 0,
        }


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / "state.sqlite")
    cid = store.create(
        {"name": "native-test", "agent": {"enforcement": "estimated"}}, 2_500_000, 0
    )["id"]
    workspace = tmp_path / "isolated"
    workspace.mkdir()
    (workspace / "Processor.java").write_text("old\n")
    return store, cid, workspace, tmp_path / "artifacts"


def instance(store, transport, **config):
    return ClaudeCodeProvider(
        {"enforcement": "estimated", **config},
        store,
        execute=transport,
        executable="/test/native-claude",
    )


def run(provider, setup, key="session"):
    _, cid, workspace, artifacts = setup
    return provider.propose(
        cid,
        key,
        {"task": "propose optimization"},
        workspace=workspace,
        artifact_dir=artifacts,
    )


def test_native_environment_uses_effective_os_account_not_inherited_user(
    setup, monkeypatch
):
    from autoresearch import claude_code

    monkeypatch.setenv("USER", "untrusted-inherited-user")
    monkeypatch.setenv("LOGNAME", "untrusted-inherited-logname")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "unit-secret-must-not-leak")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "unit-oauth-must-not-leak")
    monkeypatch.setattr(claude_code.os, "geteuid", lambda: 12345)
    requested_uids = []

    def effective_account(uid):
        requested_uids.append(uid)
        return SimpleNamespace(pw_name="os-account")

    monkeypatch.setattr(claude_code.pwd, "getpwuid", effective_account)
    environment = instance(setup[0], Transport())._environment()
    assert requested_uids == [12345]
    assert environment["USER"] == "os-account"
    assert "LOGNAME" not in environment
    assert "ANTHROPIC_API_KEY" not in environment
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in environment
    assert not any("untrusted-inherited" in value for value in environment.values())


def test_native_navigation_edits_reserved_before_launch_exact_private_trace(
    setup, monkeypatch
):
    store, cid, workspace, artifacts = setup
    for name in (
        "ANTHROPIC_API_KEY",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "AWS_SECRET_ACCESS_KEY",
        "HTTPS_PROXY",
        "ANTHROPIC_BASE_URL",
    ):
        monkeypatch.setenv(name, "never-forward-this-test-value")

    def native_edit(command, kwargs):
        assert store.budget(cid)["engaged"] == 2_500_000
        assert kwargs["cwd"] == workspace
        assert json.loads(kwargs["input_bytes"])["task"] == "propose optimization"
        assert not any("never-forward" in v for v in kwargs["env"].values())
        assert kwargs["env"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "4096"
        assert 0 < kwargs["timeout"] <= 300
        (workspace / "Processor.java").write_text("native edited\n")

    transport = Transport(callback=native_edit)
    provider = instance(store, transport)
    assert run(provider, setup) == {**PROPOSAL, "patch": ""}
    assert (workspace / "Processor.java").read_text() == "native edited\n"
    assert (artifacts / "trace.jsonl").read_bytes() == transport.raw
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in artifacts.iterdir())
    assert store.budget(cid)["consumed"] == 2_500_000
    assert store.budget(cid)["engaged"] == 0
    telemetry = json.loads((artifacts / "result.json").read_text())
    assert telemetry["estimated_cost_usd"] == "0.12"
    assert telemetry["invoice_verified"] is False
    assert "subscription-equivalent" in telemetry["cost_basis"]
    command = transport.calls[0][0]
    for flag in (
        "--safe-mode",
        "--restricted",
        "--disable-slash-commands",
        "--strict-mcp-config",
        "--no-chrome",
        "--no-session-persistence",
    ):
        assert flag in command
    assert "--bare" not in command
    assert command[command.index("--setting-sources") + 1] == ""
    assert command[command.index("--tools") + 1] == ",".join(TOOLS)
    assert command[command.index("--permission-prompts") + 1] == "none"
    assert command[command.index("--max-budget-usd") + 1] == "0.75"
    assert command[command.index("--max-turns") + 1] == "12"
    assert command[command.index("--model") + 1] == MODEL
    settings = json.loads(command[command.index("--settings") + 1])
    assert settings["disableAllHooks"] is True
    assert "Read(**/.env*)" in settings["permissions"]["deny"]
    assert "Edit(**/.git/**)" in settings["permissions"]["deny"]
    assert provider.capabilities()["strict_budget"] is False
    assert provider.capabilities()["global_quota_enforced_by_adapter"] is False
    events = store.events(cid)
    assert any(e["type"] == "claude_code.completed" for e in events)


@pytest.mark.parametrize("field,limit", [("strategy", 200), ("hypothesis", 8000)])
def test_structured_proposal_bounds_match_engine_contract(setup, field, limit):
    from autoresearch.models import ExperimentCreate

    proposal = {**PROPOSAL, field: "x" * limit}
    assert SCHEMA["properties"][field]["maxLength"] == limit
    result = run(instance(setup[0], Transport(raw=trace(proposal=proposal))), setup)
    # Native edits are extracted separately by the engine before model import.
    result["patch"] = "diff --git a/Processor.java b/Processor.java\n"
    assert getattr(ExperimentCreate(**result), field) == proposal[field]


@pytest.mark.parametrize("field,limit", [("strategy", 200), ("hypothesis", 8000)])
def test_oversized_native_proposal_is_retained_without_import(setup, field, limit):
    store, cid, _, artifacts = setup
    transport = Transport(raw=trace(proposal={**PROPOSAL, field: "x" * (limit + 1)}))
    with pytest.raises(ProviderError, match="structured proposal is invalid"):
        run(instance(store, transport), setup)
    assert store.budget(cid)["engaged"] == 2_500_000
    assert store.budget(cid)["consumed"] == 0
    assert (artifacts / "trace.jsonl").read_bytes() == transport.raw


def test_optional_measurement_protocol_returned_for_supervisor_validation(setup):
    store = setup[0]
    protocol = {"command": ["./measure.sh"], "metrics": [{"name": "throughput"}]}
    assert (
        run(
            instance(
                store, Transport(raw=trace(proposal={**PROPOSAL, "protocol": protocol}))
            ),
            setup,
        )["protocol"]
        == protocol
    )


def test_strict_refuses_before_even_version_probe(setup):
    class Never:
        def __call__(self, *args, **kwargs):
            pytest.fail("No native process may start in strict mode")

    provider = ClaudeCodeProvider(
        {}, setup[0], execute=Never(), executable="/test/claude"
    )
    with pytest.raises(StrictBudgetUnavailable):
        run(provider, setup)
    assert setup[0].budget(setup[1])["engaged"] == 0


@pytest.mark.parametrize(
    "status,error",
    [
        ("timed_out", TimeoutError),
        ("cancelled", InterruptedError),
        ("output_limit", ProviderError),
        ("failed", ProviderError),
    ],
)
def test_unknown_execution_retains_full_reservation_never_retries(setup, status, error):
    transport = Transport(raw=b'{"partial":', status=status)
    with pytest.raises(error):
        run(instance(setup[0], transport), setup)
    assert setup[0].budget(setup[1])["engaged"] == 2_500_000
    assert len(transport.calls) == 1
    with pytest.raises(DuplicateProviderCall):
        run(instance(setup[0], transport), setup)
    assert len(transport.calls) == 1
    assert (setup[3] / "trace.jsonl").read_bytes() == b'{"partial":'


@pytest.mark.parametrize(
    "raw",
    [
        trace(tool="Bash"),
        trace(tools=[*TOOLS, "Agent"]),
        trace(model="claude-opus-4-7"),
        trace(cost="2.500001"),
        trace(cost="NaN"),
        trace(proposal={**PROPOSAL, "patch": "fake"}),
        trace(proposal={**PROPOSAL, "protocol": []}),
        trace(permission_denials=[{"tool": "Read"}]),
        trace(subtype="error_max_budget_usd", is_error=True),
        b'{"incomplete":',
        trace().split(b"\n", 1)[1],
        trace() + trace(),
    ],
)
def test_invalid_trace_never_returns_candidate_or_releases_budget(setup, raw):
    transport = Transport(raw=raw)
    with pytest.raises((ProviderError, ProviderContractViolation)):
        run(instance(setup[0], transport), setup)
    assert len(transport.calls) == 1
    assert setup[0].budget(setup[1])["engaged"] == 2_500_000
    assert not (setup[3] / "result.json").exists()


def test_transport_exception_is_sanitized_and_keeps_admission(setup):
    def fail(*_):
        raise OSError("do-not-expose-this-test-value")

    with pytest.raises(ProviderError) as error:
        run(instance(setup[0], Transport(callback=fail)), setup)
    assert "do-not-expose" not in str(error.value)
    assert setup[0].budget(setup[1])["engaged"] == 2_500_000
    assert (
        json.loads((setup[3] / "metadata.json").read_text())["status"]
        == "transport_exception"
    )


def test_api_auth_forwards_only_explicit_credential_not_logged(setup, monkeypatch):
    monkeypatch.setenv("TEST_NATIVE_API_KEY", "unit-only-noncredential")
    transport = Transport()
    run(
        instance(
            setup[0], transport, auth_mode="api_key", api_key_env="TEST_NATIVE_API_KEY"
        ),
        setup,
    )
    command, kwargs = transport.calls[0]
    assert "--bare" in command and "--safe-mode" not in command
    assert kwargs["env"]["ANTHROPIC_API_KEY"] == "unit-only-noncredential"
    for path in setup[3].iterdir():
        assert b"unit-only-noncredential" not in path.read_bytes()


def test_version_deadline_cancel_and_external_artifacts_fail_before_spend(setup):
    store, cid, workspace, _ = setup
    transport = Transport(version="2.1.288")
    with pytest.raises(ProviderError, match="version"):
        run(instance(store, transport), setup)
    cancel = threading.Event()
    cancel.set()
    provider = instance(store, Transport())
    with pytest.raises(InterruptedError):
        provider.propose(
            cid, "c", {}, workspace=workspace, artifact_dir=setup[3], cancel=cancel
        )
    with pytest.raises(TimeoutError):
        provider.propose(
            cid,
            "d",
            {},
            workspace=workspace,
            artifact_dir=setup[3],
            deadline=time.monotonic() - 1,
        )
    with pytest.raises(ProviderError, match="external"):
        provider.propose(
            cid, "a", {}, workspace=workspace, artifact_dir=workspace / "artifacts"
        )
    assert store.budget(cid)["engaged"] == 0
    assert not transport.calls


def test_concurrent_sessions_have_one_durable_admission_no_second_paid_launch(setup):
    store, cid, workspace, artifacts = setup
    count = []
    gate = threading.Event()

    def callback(*_):
        count.append(1)
        gate.wait(timeout=2)

    def attempt(key):
        try:
            return instance(Store(store.path), Transport(callback=callback)).propose(
                cid, key, {}, workspace=workspace, artifact_dir=artifacts / key
            )
        except BudgetExceeded:
            return None

    with ThreadPoolExecutor(2) as pool:
        one, two = pool.submit(attempt, "one"), pool.submit(attempt, "two")
        gate.set()
        results = [one.result(), two.result()]
    assert len(count) == 1
    assert sum(result is not None for result in results) == 1
    assert store.budget(cid)["consumed"] == 2_500_000


@pytest.mark.parametrize(
    "config",
    [
        {"executable": "/evil"},
        {"model": "claude-opus-4-7"},
        {"max_turns": 13},
        {"max_tokens": 4097},
        {"timeout_seconds": 301},
        {"session_softcap_usd": ".76"},
        {"reservation_micro_usd": 750_000},
        {"effort": "high"},
        {"tools": ["Bash"]},
        {"excluded_paths": ["../outside"]},
        {"excluded_paths": ["/absolute"]},
        {"excluded_paths": ["secrets/*"]},
        {"excluded_paths": ".env"},
    ],
)
def test_serialized_configuration_cannot_expand_admission(setup, config):
    with pytest.raises(ProviderError):
        instance(setup[0], Transport(), **config)


def test_known_trailing_system_metadata_is_retained_not_treated_as_second_result(setup):
    raw = trace() + b'{"type":"system","subtype":"turn_duration","duration_ms":12}\n'
    transport = Transport(raw=raw)
    assert run(instance(setup[0], transport), setup)["patch"] == ""
    assert (setup[3] / "trace.jsonl").read_bytes() == raw
    command = transport.calls[0][0]
    assert command[command.index("--prompt-suggestions") + 1] == "false"


@pytest.mark.parametrize(
    "raw",
    [
        trace(model=MODEL + "-evil"),
        trace() + b'{"type":"assistant","message":{"content":[]}}\n',
        trace() + b'{"type":"prompt_suggestion","suggestion":"continue"}\n',
        trace() + b'{"type":"system","subtype":"task_started"}\n',
    ],
)
def test_unknown_model_variants_and_post_result_activity_fail_closed(setup, raw):
    with pytest.raises(ProviderError):
        run(instance(setup[0], Transport(raw=raw)), setup)
    assert setup[0].budget(setup[1])["engaged"] == 2_500_000


def test_documented_dated_sonnet_model_variant_is_accepted(setup):
    assert (
        run(instance(setup[0], Transport(raw=trace(model=MODEL + "-20261001"))), setup)[
            "patch"
        ]
        == ""
    )


def test_cancel_after_atomic_reservation_never_launches_native_model(
    setup, monkeypatch
):
    store, cid, workspace, artifacts = setup
    cancel = threading.Event()
    reserve = store.reserve

    def reserve_and_cancel(*args, **kwargs):
        value = reserve(*args, **kwargs)
        cancel.set()
        return value

    monkeypatch.setattr(store, "reserve", reserve_and_cancel)
    transport = Transport()
    with pytest.raises(InterruptedError):
        instance(store, transport).propose(
            cid,
            "session",
            {},
            workspace=workspace,
            artifact_dir=artifacts,
            cancel=cancel,
        )
    assert not transport.calls
    assert store.budget(cid)["engaged"] == 2_500_000


@pytest.mark.parametrize(
    "identity",
    [
        {},
        [],
        None,
        {"loggedIn": False, "authMethod": "claude.ai", "apiProvider": "firstParty"},
        {"loggedIn": "true", "authMethod": "claude.ai", "apiProvider": "firstParty"},
        {"loggedIn": True, "authMethod": "api_key", "apiProvider": "firstParty"},
        {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "bedrock"},
    ],
)
def test_native_auth_identity_rejected_before_reservation(setup, identity):
    transport = Transport()

    def execute(command, **kwargs):
        if command[-2:] == ["auth", "status"]:
            return {
                "status": "succeeded",
                "returncode": 0,
                "stdout": json.dumps(identity),
            }
        return transport(command, **kwargs)

    with pytest.raises(ProviderError, match="login"):
        run(instance(setup[0], execute), setup)
    assert not transport.calls
    assert setup[0].budget(setup[1])["engaged"] == 0
    assert not any(e["type"] == "budget.reserved" for e in setup[0].events(setup[1]))
    assert not setup[3].exists()


@pytest.mark.parametrize("failure", ["timeout", "malformed", "exit", "exception"])
def test_native_auth_failure_sanitized_without_reservation(setup, failure):
    transport = Transport()

    def execute(command, **kwargs):
        if command[-2:] == ["auth", "status"]:
            if failure == "exception":
                raise OSError("private-account-detail")
            return {
                "status": "timeout" if failure == "timeout" else "succeeded",
                "returncode": 1 if failure == "exit" else 0,
                "stdout": "private-account-detail"
                if failure == "malformed"
                else json.dumps(
                    {
                        "loggedIn": True,
                        "authMethod": "claude.ai",
                        "apiProvider": "firstParty",
                    }
                ),
            }
        return transport(command, **kwargs)

    with pytest.raises(ProviderError) as caught:
        run(instance(setup[0], execute), setup)
    assert "private-account-detail" not in str(caught.value)
    assert not transport.calls
    assert setup[0].budget(setup[1])["engaged"] == 0
    assert not setup[3].exists()


def test_native_auth_uses_same_environment_and_shared_deadline(setup, monkeypatch):
    from autoresearch import claude_code

    clock = [100.0]
    monkeypatch.setattr(claude_code.time, "monotonic", lambda: clock[0])
    transport = Transport()
    observed = []

    def execute(command, **kwargs):
        observed.append((command, kwargs))
        if command[-1] == "--version":
            clock[0] += 1
        elif command[-2:] == ["auth", "status"]:
            assert setup[0].budget(setup[1])["engaged"] == 0
            assert kwargs["timeout"] == 5
            assert "input_bytes" not in kwargs
            clock[0] += 2
        return transport(command, **kwargs)

    run(instance(setup[0], execute, timeout_seconds=10), setup)
    assert len(observed) == 3
    assert observed[2][1]["timeout"] == 7
    assert all(kwargs["env"] == observed[0][1]["env"] for _, kwargs in observed)
    assert all(kwargs["cwd"] == setup[2] for _, kwargs in observed)
    assert len(transport.auth_calls) == 1


@pytest.mark.parametrize("revoke", ["cancel", "deadline"])
def test_native_auth_completion_rechecks_admission(setup, monkeypatch, revoke):
    from autoresearch import claude_code

    clock = [100.0]
    monkeypatch.setattr(claude_code.time, "monotonic", lambda: clock[0])
    cancel = threading.Event()
    transport = Transport()

    def execute(command, **kwargs):
        response = transport(command, **kwargs)
        if command[-2:] == ["auth", "status"]:
            if revoke == "cancel":
                cancel.set()
            else:
                clock[0] = 111
        return response

    with pytest.raises(InterruptedError):
        instance(setup[0], execute, timeout_seconds=10).propose(
            setup[1],
            "session",
            {},
            workspace=setup[2],
            artifact_dir=setup[3],
            cancel=cancel,
            deadline=110,
        )
    assert not transport.calls
    assert setup[0].budget(setup[1])["engaged"] == 0


def test_api_auth_does_not_require_native_subscription_login(setup, monkeypatch):
    monkeypatch.setenv("TEST_NATIVE_API_KEY", "unit-only-noncredential")
    transport = Transport()
    run(
        instance(
            setup[0], transport, auth_mode="api_key", api_key_env="TEST_NATIVE_API_KEY"
        ),
        setup,
    )
    assert not transport.auth_calls
    assert len(transport.calls) == 1
