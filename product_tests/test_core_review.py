"""Independent review regressions: expectations describe required safe behavior."""

import threading
import time
import sys

import pytest

from autoresearch.engine import Engine, git
from autoresearch.models import CampaignCreate, ExperimentCreate, Protocol
from autoresearch.provider import ProviderContractViolation
from autoresearch.store import BudgetExceeded, Conflict


@pytest.fixture
def reviewed_campaign(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    git(repository, "init")
    (repository / "Demo.java").write_text("old\n")
    (repository / "benchmark.py").write_text("original evaluator\n")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "baseline")
    engine = Engine(tmp_path / "data")
    protocol = Protocol(
        command=["python3", "benchmark.py"],
        metrics=[{"name": "latency", "unit": "ms"}],
        protected_paths=["benchmark.py"],
    )
    detail = engine.create(
        CampaignCreate(
            name="review",
            repository=str(repository),
            goal="lower latency",
            protocol=protocol,
            runner={"kind": "host", "trusted": True},
        )
    )
    yield engine, detail["campaign"]["id"], repository, protocol
    engine.close()


@pytest.mark.parametrize("state", ["paused", "stopping", "stopped"])
def test_deadline_does_not_overwrite_human_control_state(reviewed_campaign, state):
    engine, cid, _, _ = reviewed_campaign
    engine.store.update(cid, {"state": state})
    assert not engine._continue(cid, threading.Event(), time.monotonic() - 1)
    assert engine.store.get(cid)["state"] == state


def test_checkout_cannot_reuse_old_baseline_after_preparation(reviewed_campaign):
    engine, cid, _, _ = reviewed_campaign
    engine._checkout(cid, "baseline")
    prepared = engine._checkout(cid, "new-preparation")
    (prepared / "benchmark.py").write_text("approved new evaluator\n")
    git(prepared, "add", ".")
    git(prepared, "commit", "-m", "prepare measurement")
    revision = git(prepared, "rev-parse", "HEAD")
    engine.store.update(
        cid, {"prepared_repository": str(prepared), "prepared_revision": revision}
    )
    try:
        selected = engine._checkout(cid, "baseline")
    except Conflict:
        return  # Explicit refusal is also safe; silent reuse is not.
    assert git(selected, "rev-parse", "HEAD") == revision
    assert (selected / "benchmark.py").read_text() == "approved new evaluator\n"


def test_provider_context_refreshes_after_preparation(reviewed_campaign):
    engine, cid, _, _ = reviewed_campaign
    assert engine._context(cid, "prepare")["source_files"]["Demo.java"] == "old\n"
    prepared = engine._checkout(cid, "prepared-source")
    (prepared / "Demo.java").write_text("prepared source\n")
    git(prepared, "add", ".")
    git(prepared, "commit", "-m", "prepare source")
    engine.store.update(
        cid,
        {
            "prepared_repository": str(prepared),
            "prepared_revision": git(prepared, "rev-parse", "HEAD"),
        },
    )
    assert (
        engine._context(cid, "optimize")["source_files"]["Demo.java"]
        == "prepared source\n"
    )


def test_provider_contract_violation_is_durable_incident(reviewed_campaign):
    engine, cid, _, _ = reviewed_campaign
    engine.store.update(cid, {"state": "exploring"})

    def contract_violation(cid, cancel):
        raise ProviderContractViolation("Model response exceeded reserved maximum")

    engine._launch(cid, contract_violation)
    engine._threads[cid].join(timeout=5)
    assert not engine._threads[cid].is_alive()
    assert engine.store.get(cid).get("budget_incident") is True


def test_protected_directory_dot_cannot_silently_protect_nothing(reviewed_campaign):
    engine, cid, _, _ = reviewed_campaign
    workspace = engine._checkout(cid, "candidate")
    patch = "--- a/Demo.java\n+++ b/Demo.java\n@@ -1 +1 @@\n-old\n+new\n"
    with pytest.raises((Conflict, ValueError)):
        # Validated as an accepted protected path today, but should either mean
        # the whole tree or be rejected during protocol validation.
        Protocol(
            command=["true"],
            metrics=[{"name": "latency", "unit": "ms"}],
            protected_paths=["."],
        )
        engine._apply(workspace, patch, ["."])


def test_checkout_rejects_dirty_baseline(reviewed_campaign):
    engine, cid, _, _ = reviewed_campaign
    workspace = engine._checkout(cid, "baseline")
    (workspace / "benchmark.py").write_text("mutated evaluator\n")
    try:
        reused = engine._checkout(cid, "baseline")
    except Conflict:
        return
    assert not git(reused, "status", "--porcelain"), (
        "Dirty benchmark silently becomes baseline"
    )


def test_pause_cannot_be_overwritten_after_worker_admission(
    reviewed_campaign, monkeypatch
):
    engine, cid, repository, _ = reviewed_campaign
    entered = threading.Event()
    release = threading.Event()
    paused = threading.Event()
    real_update = engine.store.update
    real_experiments = engine.store.experiments

    def update(cid, changes, **kwargs):
        if changes.get("state") == "exploring":
            entered.set()
            assert release.wait(5)
        return real_update(cid, changes, **kwargs)

    def experiments(cid):
        if entered.is_set() and threading.current_thread().name == "campaign-" + cid:
            assert paused.wait(5)
        return real_experiments(cid)

    monkeypatch.setattr(engine.store, "update", update)
    monkeypatch.setattr(engine.store, "experiments", experiments)
    monkeypatch.setattr(engine, "_checkout", lambda *a, **k: repository)
    monkeypatch.setattr(
        engine,
        "_measure",
        lambda *a, **k: {"metrics": {"latency": 10}, "checks": {"valid": True}},
    )
    engine.approve(cid, engine.store.get(cid)["protocol_hash"])
    engine.run(cid)

    def request_pause():
        engine.pause(cid)
        paused.set()

    pauser = threading.Thread(target=request_pause)
    try:
        assert entered.wait(5)
        pauser.start()
    finally:
        release.set()
        pauser.join(5)
        engine._threads[cid].join(5)
    assert paused.is_set()
    assert engine.store.get(cid)["state"] == "paused"


def test_child_cannot_inherit_changed_protected_benchmark(
    reviewed_campaign, monkeypatch
):
    engine, cid, _, _ = reviewed_campaign
    parent_ws = engine._checkout(cid, "prior-parent")
    (parent_ws / "benchmark.py").write_text("changed evaluator\n")
    git(parent_ws, "add", ".")
    git(parent_ws, "commit", "-m", "different prior protocol")
    parent = engine.store.add_experiment(
        cid,
        {
            "hypothesis": "prior protocol",
            "strategy": "prior",
            "patch": "prior patch",
            "protocol_hash": "prior-protocol-hash",
            "parent_id": None,
        },
    )
    engine.store.update_experiment(
        cid,
        parent["id"],
        {
            "status": "succeeded",
            "workspace": str(parent_ws),
            "revision": git(parent_ws, "rev-parse", "HEAD"),
        },
    )
    try:
        child = engine.add_experiment(
            cid,
            ExperimentCreate(
                hypothesis="child change",
                parent_id=parent["id"],
                patch="--- a/Demo.java\n+++ b/Demo.java\n@@ -1 +1 @@\n-old\n+new\n",
            ),
        )
    except Conflict:
        return

    def measure(cid, workspace, *args):
        benchmark = (workspace / "benchmark.py").read_text()
        return {
            "metrics": {"latency": 1 if benchmark.startswith("changed") else 10},
            "checks": {"valid": True},
        }

    monkeypatch.setattr(engine, "_measure", measure)
    engine.store.update(cid, {"state": "baselining"})
    engine._run_task(cid, threading.Event())
    assert engine.store.experiment(cid, child["id"])["verdict"] != "confirmed"


@pytest.mark.parametrize("feature", ["warmup", "environment"])
def test_approved_warmup_and_environment_reach_actual_process(
    reviewed_campaign, tmp_path, feature
):
    engine, cid, repository, _ = reviewed_campaign
    marker = tmp_path / "warmup-ran"
    if feature == "warmup":
        setup = {
            "warmup_command": [
                sys.executable,
                "-c",
                f"from pathlib import Path; Path({str(marker)!r}).write_text('done')",
            ]
        }
        check = f"__import__('pathlib').Path({str(marker)!r}).exists()"
    else:
        setup = {"environment": {"BENCHMARK_LOAD": "approved-load"}}
        check = "__import__('os').environ.get('BENCHMARK_LOAD') == 'approved-load'"
    command = [
        sys.executable,
        "-c",
        "import json; print(json.dumps({'metrics': {'latency': 10}, 'checks': {'contract_applied': "
        + check
        + "}}))",
    ]
    protocol = Protocol(
        command=command, metrics=[{"name": "latency", "unit": "ms"}], **setup
    )
    engine.set_protocol(cid, protocol)
    engine.store.update(
        cid,
        {
            "state": "baselining",
            "runner": {"mode": "host", "kind": "host", "trusted": True},
        },
    )
    sample = engine._measure(
        cid,
        repository,
        "actual-contract-test",
        protocol.model_dump(),
        threading.Event(),
        time.monotonic() + 10,
    )
    assert sample["checks"]["contract_applied"], (
        f"Approved {feature} was silently ignored"
    )


def test_stop_during_task_finalization_reaches_stopped(reviewed_campaign, monkeypatch):
    engine, cid, _, _ = reviewed_campaign
    engine.store.update(cid, {"state": "exploring"})
    entered = threading.Event()
    release = threading.Event()
    original = engine.store.update

    def update(cid, changes, **kwargs):
        if kwargs.get("event") == "task.finished":
            entered.set()
            assert release.wait(5)
        return original(cid, changes, **kwargs)

    monkeypatch.setattr(engine.store, "update", update)
    engine._launch(cid, lambda cid, cancel: None)
    stopped = threading.Event()

    def request_stop():
        engine.stop(cid)
        stopped.set()

    stopper = threading.Thread(target=request_stop)
    try:
        assert entered.wait(5)
        stopper.start()
        # If finalization is properly locked, stop is allowed to wait until it
        # finishes. With no lock, it publishes stopping before the stale write.
        stopped.wait(0.1)
    finally:
        release.set()
        stopper.join(5)
        engine._threads[cid].join(5)
    assert stopped.is_set()
    assert engine.store.get(cid)["state"] == "stopped"


def test_budget_exhaustion_cannot_override_pause(reviewed_campaign):
    engine, cid, _, _ = reviewed_campaign
    entered = threading.Event()
    release = threading.Event()
    engine.store.update(cid, {"state": "exploring"})

    def exhausted(cid, cancel):
        entered.set()
        assert release.wait(5)
        raise BudgetExceeded("admission failed")

    engine._launch(cid, exhausted)
    try:
        assert entered.wait(5)
        engine.pause(cid)
    finally:
        release.set()
        engine._threads[cid].join(5)
    assert engine.store.get(cid)["state"] == "paused"


def test_prepare_cannot_reopen_campaign_after_budget_incident(
    reviewed_campaign, monkeypatch
):
    from fastapi.testclient import TestClient
    from autoresearch.api import create_app

    engine, cid, _, _ = reviewed_campaign
    engine.store.update(
        cid,
        {
            "state": "failed",
            "budget_incident": True,
            "agent": {"kind": "claude", "enforcement": "estimated"},
        },
    )
    launched = []
    monkeypatch.setattr(engine, "_launch", lambda *args: launched.append(args))
    with TestClient(create_app(engine=engine)) as client:
        result = client.post(f"/api/campaigns/{cid}/prepare")
    assert result.status_code == 409
    assert not launched


def test_budget_incident_blocks_reservation_independently_of_state(reviewed_campaign):
    engine, cid, _, _ = reviewed_campaign
    engine.store.update(cid, {"budget_incident": True, "state": "awaiting_approval"})
    with pytest.raises(Conflict):
        engine.store.reserve(cid, "after-incident", 1)


@pytest.mark.parametrize(
    "runner",
    [
        {"kind": "unsupported"},
        {"kind": "host", "mode": "docker", "trusted": True},
        {"kind": "host", "trusted": "true"},
        {"kind": "host", "trusted": True, "environment": ["not-a-mapping"]},
        {
            "kind": "host",
            "trusted": True,
            "environment": {"ANTHROPIC_API_KEY": "test-only"},
        },
    ],
)
def test_malformed_runner_contract_never_starts_a_process(
    tmp_path, monkeypatch, runner
):
    import autoresearch.runner as runner_module

    monkeypatch.setattr(
        runner_module,
        "_execute",
        lambda *a, **k: pytest.fail("Invalid runner admitted execution"),
    )
    with pytest.raises(ValueError):
        runner_module.run_command(
            ["true"], tmp_path, tmp_path / "artifacts", timeout_seconds=1, runner=runner
        )
