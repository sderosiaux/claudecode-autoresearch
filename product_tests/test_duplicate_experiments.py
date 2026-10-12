"""Exact tree identity suppresses repeated evaluation, never manufactures evidence."""

import sys
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from autoresearch.engine import Engine, git
from autoresearch.models import CampaignCreate, ExperimentCreate, Protocol
from autoresearch.store import Conflict


PATCH = "--- a/value.txt\n+++ b/value.txt\n@@ -1 +1 @@\n-10\n+5\n"
ALTERNATE_PATCH = "diff --git a/value.txt b/value.txt\n" + PATCH


@pytest.fixture
def campaign(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    git(repository, "init")
    (repository / "value.txt").write_text("10\n")
    (repository / "benchmark.py").write_text(
        "import json\nfrom pathlib import Path\n"
        "print(json.dumps({'metrics': {'score': int(Path('value.txt').read_text())}, 'checks': {'valid': True}}))\n"
    )
    git(repository, "add", ".")
    git(repository, "commit", "-m", "fixture")
    engine = Engine(tmp_path / "state")
    protocol = Protocol(
        command=[sys.executable, "benchmark.py"],
        metrics=[{"name": "score", "unit": "test units"}],
        protected_paths=["benchmark.py"],
    )
    detail = engine.create(
        CampaignCreate(
            name="duplicates",
            goal="lower synthetic test score",
            repository=str(repository),
            protocol=protocol,
            runner={"kind": "host", "trusted": True},
        )
    )
    cid = detail["campaign"]["id"]
    engine.approve(cid, detail["protocol_hash"])
    yield engine, cid, repository, protocol
    engine.close()


def propose(engine, cid, patch=PATCH, **kwargs):
    return engine.add_experiment(
        cid,
        ExperimentCreate(
            hypothesis=kwargs.pop("hypothesis", "reduce synthetic score"),
            patch=patch,
            **kwargs,
        ),
    )


def run(engine, cid):
    engine.run(cid)
    engine._threads[cid].join(15)
    assert not engine._threads[cid].is_alive()
    campaign = engine.store.get(cid)
    assert campaign["state"] == "reviewable", engine.store.events(cid)


def measurements(engine, monkeypatch):
    calls = []
    evidence = {"baseline_score": 10, "environment": "test-runner-v1"}

    def measure(cid, workspace, label, protocol, cancel, deadline):
        score = int((workspace / "value.txt").read_text())
        calls.append((label, workspace.name))
        return {
            "metrics": {"score": evidence["baseline_score"] if score == 10 else score},
            "checks": {"valid": True},
            "environment_fingerprint": evidence["environment"],
            "execution": {"label": label, "duration_seconds": len(calls)},
        }

    monkeypatch.setattr(engine, "_measure", measure)
    return calls, evidence, measure


def assert_duplicate(engine, cid, duplicate_id, original_id):
    original = engine.store.experiment(cid, original_id)
    duplicate = engine.store.experiment(cid, duplicate_id)
    assert duplicate["tree"] == original["tree"]
    assert duplicate["program_identity"] == original["program_identity"]
    assert duplicate["status"] == "cancelled"
    assert duplicate["verdict"] == "inconclusive"
    assert duplicate["termination_reason"] == "duplicate_program"
    assert duplicate["duplicate_of"] == original_id
    assert "no new measurements" in duplicate["duplicate_reason"]
    assert duplicate["runs"] == []
    assert not duplicate.get("comparison")
    assert not duplicate.get("confirmation")
    assert duplicate_id not in engine.store.get(cid)["pareto_ids"]
    assert original_id in engine.report(cid)


def test_same_tree_different_patch_text_is_not_measured_twice(campaign, monkeypatch):
    engine, cid, _, _ = campaign
    calls, _, _ = measurements(engine, monkeypatch)
    first = propose(engine, cid)
    second = propose(
        engine, cid, ALTERNATE_PATCH, hypothesis="different words, identical program"
    )
    assert first["patch_hash"] != second["patch_hash"]
    run(engine, cid)
    assert engine.store.experiment(cid, first["id"])["verdict"] == "confirmed"
    assert_duplicate(engine, cid, second["id"], first["id"])
    assert len(calls) == 15  # 3 baseline + 6 exploration + 6 independent confirmation.
    assert all(workspace != second["id"] for _, workspace in calls)


def test_exact_repeated_patch_preserves_attempt_and_cap(campaign, monkeypatch):
    engine, cid, _, _ = campaign
    measurements(engine, monkeypatch)
    limits = {**engine.store.get(cid)["limits"], "max_experiments": 2}
    engine.store.update(cid, {"limits": limits})
    first = propose(engine, cid)
    second = propose(engine, cid)
    assert first["id"] != second["id"]
    run(engine, cid)
    assert_duplicate(engine, cid, second["id"], first["id"])
    with pytest.raises(Conflict, match="limit"):
        propose(engine, cid)


def test_same_baseline_evidence_on_resume_ignores_only_execution_metadata(
    campaign, monkeypatch
):
    engine, cid, _, _ = campaign
    calls, _, _ = measurements(engine, monkeypatch)
    first = propose(engine, cid)
    run(engine, cid)
    calls.clear()
    second = propose(engine, cid, ALTERNATE_PATCH)
    run(engine, cid)
    assert_duplicate(engine, cid, second["id"], first["id"])
    assert (
        len(calls) == 3
    )  # Explicit run obtains baseline evidence; candidate is skipped.


@pytest.mark.parametrize(
    "change",
    ["protocol", "prepared_reference", "runner", "baseline_values", "environment"],
)
def test_new_evaluation_context_allows_same_tree_to_be_measured(
    campaign, monkeypatch, change
):
    engine, cid, repository, protocol = campaign
    calls, evidence, _ = measurements(engine, monkeypatch)
    first = propose(engine, cid)
    run(engine, cid)
    if change == "protocol":
        altered = protocol.model_dump()
        altered["metrics"][0]["minimum_improvement"] = 0.05
        updated = engine.set_protocol(cid, Protocol.model_validate(altered))
        engine.approve(cid, updated["protocol_hash"])
    elif change == "prepared_reference":
        git(repository, "commit", "--allow-empty", "-m", "new prepared reference")
        engine.store.update(
            cid,
            {
                "prepared_repository": str(repository),
                "prepared_revision": git(repository, "rev-parse", "HEAD"),
            },
        )
    elif change == "runner":
        engine.store.update(
            cid, {"runner": {"kind": "host", "trusted": True, "cpus": 2}}
        )
    elif change == "baseline_values":
        evidence["baseline_score"] = 11
    else:
        evidence["environment"] = "test-runner-v2"
    calls.clear()
    second = propose(engine, cid, ALTERNATE_PATCH)
    run(engine, cid)
    a, b = [engine.store.experiment(cid, e["id"]) for e in (first, second)]
    assert a["tree"] == b["tree"]
    assert a["program_identity"] != b["program_identity"]
    assert b["status"] == "succeeded" and b["verdict"] == "confirmed"
    assert not b.get("duplicate_of")
    assert len(calls) == 15


@pytest.mark.parametrize(
    "previous_status", ["unknown", "failed", "cancelled", "running"]
)
def test_noncompleted_attempt_is_never_reused_as_evidence(
    campaign, monkeypatch, previous_status
):
    engine, cid, _, _ = campaign
    calls, _, _ = measurements(engine, monkeypatch)
    first = propose(engine, cid)
    run(engine, cid)
    # Even stale comparison fields must not turn unknown or incomplete work into proof.
    engine.store.update_experiment(
        cid, first["id"], {"status": previous_status, "verdict": "inconclusive"}
    )
    calls.clear()
    second = propose(engine, cid, ALTERNATE_PATCH)
    run(engine, cid)
    result = engine.store.experiment(cid, second["id"])
    assert result["status"] == "succeeded" and not result.get("duplicate_of")
    assert len(calls) == 15


def test_failure_before_evaluation_does_not_suppress_next_attempt(
    campaign, monkeypatch
):
    engine, cid, _, _ = campaign
    calls, _, measure = measurements(engine, monkeypatch)
    first = propose(engine, cid)
    second = propose(engine, cid, ALTERNATE_PATCH)

    def fail_first(cid, workspace, *args):
        if workspace.name == first["id"]:
            raise ValueError("candidate never emitted a valid measurement")
        return measure(cid, workspace, *args)

    monkeypatch.setattr(engine, "_measure", fail_first)
    run(engine, cid)
    assert engine.store.experiment(cid, first["id"])["status"] == "failed"
    assert engine.store.experiment(cid, second["id"])["verdict"] == "confirmed"
    assert any(workspace == second["id"] for _, workspace in calls)


@pytest.mark.parametrize("invalid_kind", ["checks", "environment_drift"])
def test_invalid_completed_comparison_is_not_duplicate_evidence(
    campaign, monkeypatch, invalid_kind
):
    engine, cid, _, _ = campaign
    calls, _, measure = measurements(engine, monkeypatch)
    first = propose(engine, cid)
    second = propose(engine, cid, ALTERNATE_PATCH)

    def invalid_first(cid, workspace, *args):
        sample = measure(cid, workspace, *args)
        if workspace.name == first["id"]:
            if invalid_kind == "checks":
                sample["checks"]["valid"] = False
            else:
                sample["environment_fingerprint"] = "different-runner"
        return sample

    monkeypatch.setattr(engine, "_measure", invalid_first)
    run(engine, cid)
    original = engine.store.experiment(cid, first["id"])
    assert original["status"] == "succeeded"
    assert (
        not original["comparison"]["candidate"]["valid"]
        or original["comparison"]["verdict"] == "invalid_measurement"
    )
    assert engine.store.experiment(cid, second["id"])["verdict"] == "confirmed"
    assert any(workspace == second["id"] for _, workspace in calls)


def test_concurrent_identical_proposals_cannot_exceed_attempt_limit(campaign):
    engine, cid, _, _ = campaign
    engine.store.update(
        cid, {"limits": {**engine.store.get(cid)["limits"], "max_experiments": 1}}
    )

    def attempt(_):
        try:
            return propose(engine, cid)
        except Conflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [1, 2]))
    assert sum(result is not None for result in results) == 1
    assert len(engine.store.experiments(cid)) == 1


def test_autonomous_duplicate_proposals_remain_bounded_and_billed_once_each(
    campaign, monkeypatch
):
    engine, cid, _, _ = campaign
    calls, _, _ = measurements(engine, monkeypatch)
    config = engine.store.get(cid)
    engine.store.update(
        cid,
        {
            "agent": {"kind": "claude", "enforcement": "estimated"},
            "limits": {**config["limits"], "max_experiments": 3},
        },
    )
    proposed = []

    def fake_proposal(cid, task, cancel, deadline):
        key = "test-proposal-" + uuid.uuid4().hex
        engine.store.reserve(cid, key, 1)
        engine.store.settle(cid, key, 1, key + "-usage")
        proposed.append(key)
        return {
            "hypothesis": "same proposal",
            "strategy": "test",
            "patch": PATCH,
            "expected_effect": "lower",
        }

    monkeypatch.setattr(engine, "_propose", fake_proposal)
    run(engine, cid)
    attempts = engine.store.experiments(cid)
    assert len(attempts) == len(proposed) == 3
    assert all(e.get("termination_reason") == "duplicate_program" for e in attempts[1:])
    assert engine.store.budget(cid)["consumed"] == 3
    assert len(calls) == 15
    run(engine, cid)
    assert len(proposed) == 3 and engine.store.budget(cid)["consumed"] == 3


def test_real_host_commands_measure_only_first_equivalent_program(campaign):
    engine, cid, _, _ = campaign
    first = propose(engine, cid)
    second = propose(engine, cid, ALTERNATE_PATCH)
    run(engine, cid)
    assert_duplicate(engine, cid, second["id"], first["id"])
    original = engine.store.experiment(cid, first["id"])
    assert original["confirmation"]["candidate"]["metrics"]["score"]["mean"] == 5
    executed = [
        event
        for event in engine.store.events(cid)
        if event["type"] == "execution.finished"
    ]
    assert len(executed) == 15
    assert all(second["id"] not in event["data"]["label"] for event in executed)
