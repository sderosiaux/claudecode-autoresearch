"""Recovery is explicit and preserves unknown cost; no real paid calls or Docker."""

import json
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from autoresearch.engine import Engine, RESOURCE_CLEANUP_GRACE_SECONDS, git
from autoresearch.models import CampaignCreate, Protocol
from autoresearch.store import Conflict


def timestamp(seconds_from_now):
    return (
        datetime.now(timezone.utc) + timedelta(seconds=seconds_from_now)
    ).isoformat()


@pytest.fixture
def recovery_campaign(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    git(repository, "init")
    (repository / "Demo.java").write_text("example\n")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "fixture")
    engine = Engine(tmp_path / "data")
    protocol = Protocol(
        command=["true"],
        metrics=[{"name": "latency", "unit": "ms"}],
        diagnostic_command=["true"],
    )
    campaign = engine.create(
        CampaignCreate(
            name="recovery",
            goal="lower latency",
            repository=str(repository),
            protocol=protocol,
            runner={"kind": "docker"},
        )
    )
    cid = campaign["campaign"]["id"]
    engine.approve(cid, campaign["protocol_hash"])
    engines = [engine]

    def restart(**changes):
        engine.store.update(
            cid,
            {
                "state": "baselining",
                "active_started_at": timestamp(-10),
                "active_seconds": 3,
                "resource_lease_expires_at": timestamp(-1),
                "resource_lease_kind": "measurement",
                "resource_lease_recoverable": True,
                **changes,
            },
        )
        engine.close()
        recovered = Engine(engine.root)
        engines.append(recovered)
        return recovered

    yield engine, cid, repository, protocol, restart
    for instance in engines:
        instance.close()


@pytest.mark.parametrize("previous_state", ["baselining", "preparing", "paused"])
def test_crash_elapsed_time_includes_paused_active_task_once(
    recovery_campaign, previous_state
):
    engine, cid, _, _, restart = recovery_campaign
    recovered = restart(state=previous_state)
    campaign = recovered.store.get(cid)
    assert campaign["state"] == "blocked"
    assert campaign["recovery_required"] is True
    assert campaign["recovery_previous_state"] == previous_state
    assert campaign["active_started_at"] is None
    assert 13 <= campaign["active_seconds"] < 15
    recovered.close()
    again = Engine(engine.root)
    try:
        assert again.store.get(cid)["active_seconds"] == campaign["active_seconds"]
    finally:
        again.close()


def test_lease_expiry_and_explicit_reconciliation_gate_every_admission(
    recovery_campaign, monkeypatch
):
    _, cid, _, protocol, restart = recovery_campaign
    recovered = restart(resource_lease_expires_at=timestamp(90))
    monkeypatch.setattr(
        recovered, "_launch", lambda *a, **k: pytest.fail("Recovery admitted work")
    )
    for operation in (
        lambda: recovered.reconcile(cid),
        lambda: recovered.resume(cid),
        lambda: recovered.run(cid),
        lambda: recovered.prepare(cid),
        lambda: recovered.profile(cid),
        lambda: recovered.assess(cid),
        lambda: recovered.set_protocol(cid, protocol),
        lambda: recovered.approve(cid, recovered.store.get(cid)["protocol_hash"]),
    ):
        with pytest.raises(Conflict):
            operation()
    assert recovered._continue(cid, threading.Event(), time.monotonic() + 100) is False
    assert recovered.detail(cid)["campaign"]["recovery_required"] is True
    assert "recovery" in recovered.report(cid)
    recovered.stop(cid)
    with pytest.raises(Conflict, match="reconcile"):
        recovered.resume(cid)


def test_expiry_alone_does_not_authorize_resume(recovery_campaign):
    _, cid, _, _, restart = recovery_campaign
    recovered = restart()
    with pytest.raises(Conflict, match="reconcile"):
        recovered.resume(cid)
    assert recovered.store.get(cid)["recovery_required"]


def test_reconcile_cancels_unknown_preserves_reservations_and_never_retries(
    recovery_campaign, monkeypatch
):
    engine, cid, _, _, restart = recovery_campaign
    engine.store.update(cid, {"state": "exploring"})
    engine.store.reserve(cid, "unknown-paid-call", 200_000)
    experiment = engine.store.add_experiment(
        cid, {"hypothesis": "interrupted", "strategy": "test", "patch": "patch"}
    )
    engine.store.update_experiment(cid, experiment["id"], {"status": "running"})
    recovered = restart()
    assert recovered.store.experiment(cid, experiment["id"])["status"] == "unknown"
    before = recovered.store.budget(cid)
    monkeypatch.setattr(
        recovered, "_launch", lambda *a, **k: pytest.fail("Reconciliation started work")
    )
    result = recovered.reconcile(cid)
    assert result["campaign"]["state"] == "blocked"
    assert result["campaign"]["recovery_required"] is False
    assert recovered.store.budget(cid) == before
    assert before["engaged"] == 200_000
    updated = recovered.store.experiment(cid, experiment["id"])
    assert updated["status"] == "cancelled" and updated["verdict"] == "inconclusive"
    assert result["campaign"]["reconciliation"]["remote_provider_outcome"].startswith(
        "unknown"
    )
    events = recovered.store.events(cid)
    assert (
        recovered.reconcile(cid)["events"] == events
    )  # Repeated acknowledgment is read-only.
    resumed = []
    monkeypatch.setattr(
        recovered, "run", lambda cid: resumed.append(cid) or recovered.detail(cid)
    )
    recovered.resume(cid)
    assert resumed == [cid]
    assert recovered.store.experiment(cid, experiment["id"])["status"] == "cancelled"


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"resource_lease_expires_at": None}, "no durable resource lease"),
        ({"resource_lease_expires_at": "invalid"}, "invalid"),
        ({"resource_lease_expires_at": "2020-01-01T00:00:00"}, "invalid"),
        ({"runner": {"kind": "host", "trusted": True}}, "Trusted host"),
        (
            {
                "resource_lease_recoverable": False,
                "resource_lease_kind": "coordinator_diagnostic",
            },
            "diagnostic",
        ),
    ],
)
def test_unproven_resource_cleanup_cannot_be_acknowledged(
    recovery_campaign, changes, reason
):
    _, cid, _, _, restart = recovery_campaign
    recovered = restart(**changes)
    with pytest.raises(Conflict, match=reason):
        recovered.reconcile(cid)
    assert recovered.store.get(cid)["recovery_required"] is True


def test_unrelated_authoring_allowed_but_resource_admission_globally_blocked(
    recovery_campaign,
):
    engine, cid, repository, protocol, restart = recovery_campaign
    other = engine.create(
        CampaignCreate(
            name="other", goal="latency", repository=str(repository), protocol=protocol
        )
    )
    other_id = other["campaign"]["id"]
    recovered = restart()
    recovered.set_protocol(other_id, protocol)
    before_state = recovered.store.get(other_id)["state"]
    with pytest.raises(Conflict, match="recovered campaigns"):
        recovered.assess(other_id)
    assert recovered.store.get(other_id)["state"] == before_state
    recovered.store.update(other_id, {"state": "baselining"})
    with pytest.raises(Conflict, match="recovered campaigns"):
        recovered._admit_resource_lease(other_id, "measurement", 1)
    recovered.reconcile(cid)
    recovered._admit_resource_lease(other_id, "measurement", 1)
    assert recovered.store.get(other_id)["resource_lease_kind"] == "measurement"


def test_legacy_completed_campaign_needs_no_recovery_lease(recovery_campaign):
    engine, cid, _, _, _ = recovery_campaign
    engine.store.update(cid, {"state": "reviewable", "active_started_at": None})
    engine.close()
    recovered = Engine(engine.root)
    try:
        assert not recovered.store.get(cid).get("recovery_required")
        assert recovered.reconcile(cid)["campaign"]["state"] == "reviewable"
    finally:
        recovered.close()


def test_reconcile_does_not_clear_independent_budget_incident(recovery_campaign):
    _, cid, _, _, restart = recovery_campaign
    recovered = restart(budget_incident=True)
    reconciled = recovered.reconcile(cid)
    assert reconciled["campaign"]["recovery_required"] is False
    assert reconciled["campaign"]["budget_incident"] is True
    with pytest.raises(Conflict, match="budget incident"):
        recovered.resume(cid)


def assert_lease(engine, cid, kind, timeout):
    campaign = engine.store.get(cid)
    assert campaign["resource_lease_kind"] == kind
    expiry = datetime.fromisoformat(campaign["resource_lease_expires_at"])
    assert expiry > datetime.now(timezone.utc) + timedelta(
        seconds=timeout + RESOURCE_CLEANUP_GRACE_SECONDS - 2
    )
    assert campaign["resource_lease_completed"] is False


def test_uncertain_cleanup_blocks_other_resource_admission(recovery_campaign):
    engine, cid, _, _, _ = recovery_campaign
    with pytest.raises(Conflict, match="cleanup is unconfirmed"):
        engine._record_runner_cleanup(
            cid, {"cleanup_warning": "Docker removal timed out"}
        )
    assert engine.store.get(cid)["recovery_required"] is True
    with pytest.raises(Conflict, match="recovered campaigns"):
        engine._resource_recovery_gate()


def test_runner_persistence_failure_blocks_resource_admission(
    recovery_campaign, monkeypatch
):
    engine, cid, _, _, _ = recovery_campaign

    def failed(*args, **kwargs):
        raise OSError("artifact persistence failed")

    monkeypatch.setattr("autoresearch.runner.run_command", failed)
    with pytest.raises(Conflict, match="cleanup is unconfirmed"):
        engine._execute_runner(cid)
    assert engine.store.get(cid)["recovery_required"] is True


def test_provider_lease_persisted_before_subprocess(recovery_campaign, monkeypatch):
    import autoresearch.runner as runner

    engine, cid, _, _, _ = recovery_campaign
    engine.store.update(cid, {"state": "exploring"})
    monkeypatch.setattr(engine, "_context", lambda *a: {"source_files": {}})

    def execute(*args, **kwargs):
        assert_lease(engine, cid, "provider", kwargs["timeout"])
        return {
            "status": "succeeded",
            "stdout": json.dumps({"proposal": {"hypothesis": "test"}}),
        }

    monkeypatch.setattr(runner, "_execute", execute)
    assert engine._propose(
        cid, "optimize", threading.Event(), time.monotonic() + 20
    ) == {"hypothesis": "test"}


def test_measurement_lease_persisted_before_runner(recovery_campaign, monkeypatch):
    import autoresearch.runner as runner

    engine, cid, repository, protocol, _ = recovery_campaign
    engine.store.update(cid, {"state": "baselining"})

    def run_command(*args, **kwargs):
        assert_lease(engine, cid, "measurement", kwargs["timeout_seconds"])
        return {
            "status": "succeeded",
            "stdout": json.dumps({"metrics": {"latency": 1}, "checks": {"ok": True}}),
            "environment": {},
            "duration_seconds": 0.1,
        }

    monkeypatch.setattr(runner, "run_command", run_command)
    sample = engine._measure(
        cid,
        repository,
        "test",
        protocol.model_dump(),
        threading.Event(),
        time.monotonic() + 20,
    )
    assert sample["metrics"]["latency"] == 1


def test_diagnostic_lease_persisted_before_runner(recovery_campaign, monkeypatch):
    import autoresearch.runner as runner

    engine, cid, _, _, _ = recovery_campaign
    recorded = []

    def run_command(*args, **kwargs):
        assert_lease(engine, cid, "diagnostic", kwargs["timeout_seconds"])
        scheduler = engine.store.get(cid)["scheduler"]
        assert scheduler["phase"] == "profiling" and scheduler["active_tasks"] == 1
        assert scheduler["tasks"][-1]["kind"] == "diagnostic"
        recorded.append(True)
        return {"status": "succeeded", "stdout": "profile", "stderr": ""}

    monkeypatch.setattr(runner, "run_command", run_command)
    engine.profile(cid)
    engine._threads[cid].join(5)
    assert recorded == [True]
    assert engine.store.get(cid)["scheduler"]["tasks"][-1]["status"] == "succeeded"


def test_diagnostic_evidence_survives_cleanup_gate(recovery_campaign, monkeypatch):
    import autoresearch.runner as runner

    engine, cid, _, _, _ = recovery_campaign
    monkeypatch.setattr(
        runner,
        "run_command",
        lambda *a, **k: {
            "status": "succeeded",
            "stdout": "retained diagnostic measurements",
            "stderr": "",
            "cleanup_warning": "owned service deletion was not confirmed",
        },
    )
    engine.profile(cid)
    engine._threads[cid].join(5)
    campaign = engine.store.get(cid)
    assert campaign["recovery_required"] is True and campaign["state"] == "blocked"
    diagnostic = campaign["diagnostics"][-1]
    assert diagnostic["stdout"] == "retained diagnostic measurements"
    assert diagnostic["cleanup_warning"]
    assert diagnostic["included_in_comparison"] is False
    assert not campaign.get("baseline")
    assert campaign["scheduler"]["tasks"][-1]["status"] == "failed"


def test_lease_maximum_never_shrinks_and_failed_persistence_prevents_launch(
    recovery_campaign, monkeypatch
):
    import autoresearch.runner as runner

    engine, cid, _, _, _ = recovery_campaign
    engine.store.update(cid, {"state": "exploring"})
    engine._admit_resource_lease(cid, "provider", 100)
    original = engine.store.get(cid)["resource_lease_expires_at"]
    engine._admit_resource_lease(cid, "provider", 1)
    assert engine.store.get(cid)["resource_lease_expires_at"] == original
    monkeypatch.setattr(engine, "_context", lambda *a: {"source_files": {}})
    update = engine.store.update

    def unavailable(cid, changes, **kwargs):
        if kwargs.get("event") == "resource.admitted":
            raise OSError("lease persistence unavailable")
        return update(cid, changes, **kwargs)

    monkeypatch.setattr(engine.store, "update", unavailable)
    monkeypatch.setattr(
        runner, "_execute", lambda *a, **k: pytest.fail("Unleased process launched")
    )
    with pytest.raises(OSError):
        engine._propose(cid, "optimize", threading.Event(), time.monotonic() + 20)
