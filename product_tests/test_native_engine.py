"""Native engine/worker/Git integration with a controlled process and no API calls."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import threading
import time

import pytest
from pydantic import ValidationError

from autoresearch.claude_code import ClaudeCodeProvider, MODEL, TOOLS
from autoresearch.engine import Engine, git
from autoresearch.models import CampaignCreate, Protocol
from autoresearch.provider import ProviderError
from autoresearch.store import Conflict

PROPOSAL = {
    "hypothesis": "One redundant payload copy can be removed",
    "strategy": "payload reuse",
    "expected_effect": "Allocation may decrease; failed checksum refutes it",
}
FILTERED = [
    ".env",
    ".env.local",
    "nested/.env.test",
    "privatekey.txt",
    "private_key.txt",
    "private-key.txt",
    "credentials.json",
    "nested/my-credentials.yaml",
    "id_rsa",
    "server.pem",
    "client.key",
    "client.p12",
    "client.jks",
    ".npmrc",
    ".claude/settings.json",
    ".codex/config.toml",
]
EXPLICIT_FILTERED = ["README.md", "fixtures/access.properties"]


@pytest.fixture
def native(tmp_path):
    repository = tmp_path / "original"
    repository.mkdir()
    git(repository, "init")
    (repository / "removed-history.txt").write_text(
        "public unit fixture, old revision\n"
    )
    git(repository, "add", ".")
    git(repository, "commit", "-m", "Earlier history must not enter agent repository")
    (repository / "removed-history.txt").unlink()
    (repository / "value.txt").write_text("10\n")
    (repository / "benchmark.py").write_text("# fixed evaluator\n")
    for name in FILTERED + EXPLICIT_FILTERED:
        path = repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("PUBLIC_UNIT_FIXTURE_NOT_A_SECRET\n")
    git(repository, "add", "-A")
    git(repository, "commit", "-m", "Public test fixture")
    git(
        repository,
        "config",
        "nativefixture.privateMetadata",
        "DO_NOT_COPY_ORIGINAL_CONFIG",
    )
    protocol = Protocol(
        command=[sys.executable, "benchmark.py"],
        metrics=[{"name": "allocation", "unit": "bytes/op"}],
        protected_paths=["benchmark.py"],
    )
    engine = Engine(tmp_path / "state")
    detail = engine.create(
        CampaignCreate(
            name="native integration",
            repository=str(repository),
            goal="reduce allocation",
            protocol=protocol,
            budget_usd="2.5",
            protected_fraction=0,
            limits={"max_parallel_tasks": 1, "max_experiments": 1},
            agent={
                "kind": "claude_code",
                "enforcement": "estimated",
                "excluded_paths": ["README.md", "fixtures"],
            },
            runner={"kind": "host", "trusted": True},
        )
    )
    cid = detail["campaign"]["id"]
    engine.store.update(cid, {"state": "exploring"})
    yield engine, cid, repository, protocol
    engine.close()


@pytest.fixture
def controlled_process(monkeypatch):
    """Run the real worker/adapter, replacing only native process transport."""
    from autoresearch import provider_worker, runner

    requests, native_calls = [], []
    behavior = {"edit": lambda workspace: None, "proposal": dict(PROPOSAL)}
    original_init = ClaudeCodeProvider.__init__

    def execute_native(command, **kwargs):
        if command[-1] == "--version":
            return {"status": "succeeded", "stdout": "2.1.289 (Claude Code)"}
        if command[-2:] == ["auth", "status"]:
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
        native_calls.append((command, kwargs))
        behavior["edit"](Path(kwargs["cwd"]))
        rows = [
            {
                "type": "system",
                "subtype": "init",
                "model": MODEL,
                "tools": list(TOOLS),
                "mcp_servers": [],
            },
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "structured_output": behavior["proposal"],
                "modelUsage": {MODEL: {}},
                "total_cost_usd": "0.10",
                "num_turns": 2,
            },
        ]
        return {
            "status": "succeeded",
            "returncode": 0,
            "stderr": "",
            "stdout": b"".join((json.dumps(row) + "\n").encode() for row in rows),
        }

    def initialize(self, config, ledger, **kwargs):
        return original_init(
            self,
            config,
            ledger,
            execute=execute_native,
            executable="/controlled/native-claude",
            **kwargs,
        )

    def execute_worker(command, **kwargs):
        assert command[-1] == "autoresearch.provider_worker"
        request = json.loads(kwargs["input_bytes"])
        requests.append(request)
        output = io.StringIO()
        code = 0
        with monkeypatch.context() as context, redirect_stdout(output):
            context.setattr(sys, "stdin", io.StringIO(json.dumps(request)))
            try:
                provider_worker.main()
            except SystemExit as exc:
                code = exc.code
        return {
            "status": behavior.get("worker_status", "succeeded")
            if code == 0
            else "failed",
            "returncode": code,
            "stdout": output.getvalue(),
            "stderr": "",
        }

    monkeypatch.setattr(ClaudeCodeProvider, "__init__", initialize)
    monkeypatch.setattr(runner, "_execute", execute_worker)
    return behavior, requests, native_calls


def propose(native):
    engine, cid, _, _ = native
    return engine._propose(
        cid, "propose optimization", threading.Event(), time.monotonic() + 30
    )


def test_native_context_has_no_custom_source_selector(native):
    engine, cid, _, protocol = native
    context = engine._context(cid, "propose optimization")
    assert context["source_files"] == {}
    assert context["protocol"] == protocol.model_dump(mode="json")
    assert context["goal"] == "reduce allocation"
    caps = engine.capabilities()["claude_code"]
    assert caps["adapter"] == "claude_code" and caps["model"] == MODEL
    assert caps["strict_budget"] is False


def test_filtered_agent_checkout_clean_tracked_base_and_fresh_history(
    native, controlled_process
):
    engine, cid, repository, _ = native
    # Dirty and untracked operator changes are excluded by committed-base checkout.
    (repository / "value.txt").write_text("operator uncommitted change\n")
    (repository / "operator-only.txt").write_text("untracked public fixture\n")
    _, requests, _ = controlled_process
    assert propose(native)["patch"] == ""
    request = requests[0]
    workspace = Path(request["workspace"])
    assert (workspace / "value.txt").read_text() == "10\n"
    assert not (workspace / "operator-only.txt").exists()
    assert not (workspace / "removed-history.txt").exists()
    assert all(not (workspace / name).exists() for name in FILTERED)
    assert all(not (workspace / name).exists() for name in EXPLICIT_FILTERED)
    assert all((repository / name).exists() for name in EXPLICIT_FILTERED)
    assert set(EXPLICIT_FILTERED) <= set(request["context"]["excluded_files"])
    assert git(workspace, "rev-list", "--count", "HEAD") == "1"
    assert git(workspace, "remote") == ""
    assert "DO_NOT_COPY_ORIGINAL_CONFIG" not in (workspace / ".git/config").read_text()
    assert git(workspace, "status", "--porcelain") == ""
    assert request["context"]["source_files"] == {}
    assert request["context"]["protected_paths"] == ["benchmark.py"]
    assert (
        request["context"]["source_revision"] == engine.store.get(cid)["base_revision"]
    )
    assert not Path(request["artifact_dir"]).is_relative_to(workspace)


def test_actual_native_file_edits_and_new_files_become_patch_on_full_original(
    native, controlled_process
):
    behavior, requests, calls = controlled_process

    def edit(workspace):
        (workspace / "value.txt").write_text("5\n")
        (workspace / "Helper.java").write_text("final class Helper {}\n")

    behavior["edit"] = edit
    proposal = propose(native)
    assert proposal["hypothesis"] == PROPOSAL["hypothesis"]
    assert "--- a/value.txt" in proposal["patch"]
    assert "+++ b/Helper.java" in proposal["patch"]
    engine, cid, original, _ = native
    target = engine._checkout(cid, "candidate")
    engine._apply(target, proposal["patch"], ["benchmark.py"])
    assert (target / "value.txt").read_text() == "5\n"
    assert (target / "Helper.java").read_text() == "final class Helper {}\n"
    assert (target / ".env").exists(), (
        "Filtered agent view must not delete original source files"
    )
    assert (original / "value.txt").read_text() == "10\n"
    assert len(calls) == len(requests) == 1
    assert engine.store.budget(cid)["consumed"] == 2_500_000


def test_native_protected_evaluator_edits_are_rejected_by_existing_apply(
    native, controlled_process
):
    controlled_process[0]["edit"] = lambda workspace: (
        workspace / "benchmark.py"
    ).write_text("# forged checks\n")
    proposal = propose(native)
    engine, cid, repository, _ = native
    candidate = engine._checkout(cid, "protected-rejection")
    with pytest.raises(Conflict, match="protected evaluator"):
        engine._apply(candidate, proposal["patch"], ["benchmark.py"])
    assert (repository / "benchmark.py").read_text() == "# fixed evaluator\n"


def test_worker_rejects_native_created_symlink_before_patch_return(
    native, controlled_process
):
    controlled_process[0]["edit"] = lambda workspace: (workspace / "link").symlink_to(
        "value.txt"
    )
    with pytest.raises(ProviderError, match="symlink"):
        propose(native)


def test_measurement_preparation_validates_protocol_and_requires_approval(
    native, controlled_process
):
    engine, cid, _, protocol = native
    engine.store.update(
        cid,
        {
            "state": "preparing",
            "preparation_attempts": 1,
            "protocol": None,
            "protocol_hash": None,
        },
    )
    behavior, requests, _ = controlled_process
    behavior["proposal"] = {**PROPOSAL, "protocol": protocol.model_dump(mode="json")}
    behavior["edit"] = lambda workspace: (workspace / "measure.py").write_text(
        "# real instrumentation placeholder for contract test\n"
    )
    engine._prepare_task(cid, threading.Event())
    current = engine.store.get(cid)
    assert requests[0]["context"]["task"] == "prepare measurement"
    assert current["state"] == "awaiting_approval"
    assert current["protocol"] == protocol.model_dump(mode="json")
    assert current["approved_hash"] is None and current["approval"] is None
    assert (Path(current["prepared_repository"]) / "measure.py").exists()
    assert current["prepared_revision"] != current["base_revision"]
    assert current["baseline"] is None and current["baseline_summary"] is None


def test_invalid_native_preparation_protocol_cannot_be_accepted(
    native, controlled_process
):
    engine, cid, _, _ = native
    engine.store.update(cid, {"state": "preparing", "preparation_attempts": 1})
    controlled_process[0]["proposal"] = {
        **PROPOSAL,
        "protocol": {"command": [], "metrics": []},
    }
    with pytest.raises(ValidationError):
        engine._prepare_task(cid, threading.Event())
    assert not engine.store.get(cid).get("prepared_revision")
    assert not engine.store.get(cid).get("approved_hash")


@pytest.mark.parametrize("edited", [False, True])
def test_native_wave_records_true_origin_and_uses_actual_changes(
    native, controlled_process, edited
):
    if edited:
        controlled_process[0]["edit"] = lambda workspace: (
            workspace / "value.txt"
        ).write_text("5\n")
    engine, cid, _, _ = native
    rows = engine._proposal_wave(cid, threading.Event(), time.monotonic() + 30)
    assert len(rows) == int(edited)
    attempt = engine.store.experiments(cid)[0]
    assert attempt["origin"] == "claude_code"
    assert attempt["status"] == ("queued" if edited else "cancelled")
    assert bool(attempt.get("patch")) is edited
    if not edited:
        assert attempt["termination_reason"] == "no_proposal"
        assert attempt["verdict"] == "inconclusive"
        assert attempt["hypothesis"] == PROPOSAL["hypothesis"]
        assert attempt["expected_effect"] == PROPOSAL["expected_effect"]


def test_native_run_admission_selects_native_factory_without_legacy_api_credentials(
    native, monkeypatch
):
    engine, cid, _, _ = native
    current = engine.store.get(cid)
    engine.store.update(
        cid, {"state": "awaiting_approval", "approved_hash": current["protocol_hash"]}
    )
    monkeypatch.setattr(engine, "assess", lambda _: {"readiness": {"status": "ready"}})
    launched = []
    monkeypatch.setattr(
        engine, "_launch", lambda campaign, task: launched.append(campaign)
    )
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert engine.run(cid)["campaign"]["state"] == "baselining"
    assert launched == [cid]


@pytest.mark.parametrize("status", ["failed", "output_limit"])
def test_worker_transport_failure_never_admits_even_complete_proposal_json(
    native, controlled_process, status
):
    controlled_process[0]["worker_status"] = status
    with pytest.raises(ProviderError):
        propose(native)
