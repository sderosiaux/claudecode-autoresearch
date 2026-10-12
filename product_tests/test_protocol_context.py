"""Historical evidence must never acquire a newer measurement definition."""

import io
import json
import threading
import zipfile

import pytest

from autoresearch.engine import Engine, git
from autoresearch.models import CampaignCreate, ExperimentCreate, Protocol
from autoresearch.store import Conflict


@pytest.fixture
def measured_campaign(tmp_path, monkeypatch):
    repository = tmp_path / "repository"
    repository.mkdir()
    git(repository, "init")
    (repository / "value.txt").write_text("10\n")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "fixture")
    engine = Engine(tmp_path / "state")
    protocol = Protocol(
        command=["unused-fixture-command"], metrics=[{"name": "duration", "unit": "ms"}]
    )
    detail = engine.create(
        CampaignCreate(
            name="context",
            goal="lower duration",
            repository=str(repository),
            protocol=protocol,
            runner={"kind": "host", "trusted": True},
        )
    )
    cid = detail["campaign"]["id"]
    engine.approve(cid, detail["protocol_hash"])
    attempt = engine.add_experiment(
        cid,
        ExperimentCreate(
            hypothesis="synthetic value reduction",
            patch="--- a/value.txt\n+++ b/value.txt\n@@ -1 +1 @@\n-10\n+5\n",
        ),
    )

    def measure(cid, workspace, label, protocol, cancel, deadline):
        return {
            "metrics": {"duration": int((workspace / "value.txt").read_text())},
            "checks": {"valid": True},
            "environment_fingerprint": "fixture",
        }

    monkeypatch.setattr(engine, "_measure", measure)
    engine.store.update(cid, {"state": "baselining"})
    engine._run_task(cid, threading.Event())
    engine.store.update(cid, {"readiness": {"status": "ready"}})
    yield engine, cid, attempt["id"], protocol
    engine.close()


def test_current_context_keeps_measurements_and_reproduction(measured_campaign):
    engine, cid, eid, _ = measured_campaign
    detail = engine.detail(cid)
    assert detail["campaign"]["baseline_summary"]["valid"]
    assert [e["id"] for e in detail["comparison"]] == [eid]
    assert engine.reproduction(cid, eid)["protocol_hash"] == detail["protocol_hash"]


@pytest.mark.parametrize(
    "expected_effect",
    [
        "May reduce allocation. Compatibility tradeoff: custom overloads need review.\n"
        "Falsifier: changed per-record configuration behavior or no resolved gain.",
        "",
    ],
)
def test_report_export_and_draft_preserve_unmeasured_proposal_tradeoffs(
    measured_campaign, expected_effect
):
    engine, cid, eid, _ = measured_campaign
    engine.store.update_experiment(cid, eid, {"expected_effect": expected_effect})
    report = engine.report(cid)
    label = "**Proposed effect and tradeoffs (unmeasured):**"
    if expected_effect:
        assert f"{label}\n\n{expected_effect}\n" in report
        assert report.index(label) < report.index("- duration:")
    else:
        assert label not in report
    assert report in engine.draft(cid, eid)["body"]
    with zipfile.ZipFile(io.BytesIO(engine.export(cid))) as archive:
        assert archive.read("report.md").decode() == report
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["experiments"][0]["expected_effect"] == expected_effect


def test_protocol_edit_clears_readiness_and_current_evidence_preserves_export(
    measured_campaign,
):
    engine, cid, eid, protocol = measured_campaign
    before = engine.store.experiment(cid, eid)
    revised = protocol.model_dump()
    revised["metrics"][0]["unit"] = "s"
    detail = engine.set_protocol(cid, Protocol.model_validate(revised))
    assert detail["comparison"] == []
    assert detail["readiness"] is None
    assert detail["campaign"]["baseline_summary"] is None
    assert detail["campaign"]["baseline"] is None
    assert detail["campaign"]["approved_hash"] is None
    assert detail["experiments"] == [before]
    with pytest.raises(Conflict, match="Historical experiment"):
        engine.reproduction(cid, eid)
    with zipfile.ZipFile(io.BytesIO(engine.export(cid))) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["experiments"] == [before]
        events = [
            json.loads(line) for line in archive.read("events.jsonl").splitlines()
        ]
        assert any(
            e["type"] == "baseline.measured" and e["data"]["baseline_summary"]["valid"]
            for e in events
        )


@pytest.mark.parametrize("change", ["reference", "runner", "legacy"])
def test_mismatched_or_unverified_context_is_not_current_evidence(
    measured_campaign, change
):
    engine, cid, eid, _ = measured_campaign
    if change == "reference":
        engine.store.update(cid, {"prepared_revision": "different-reference"})
    elif change == "runner":
        engine.store.update(
            cid, {"runner": {"kind": "host", "trusted": True, "cpus": 2}}
        )
    else:
        engine.store.update(cid, {"baseline_context": None})
        engine.store.update_experiment(cid, eid, {"evaluation_context": None})
    detail = engine.detail(cid)
    assert detail["comparison"] == []
    assert detail["campaign"]["baseline_summary"] is None
    assert detail["experiments"][0]["comparison"]
    with pytest.raises(Conflict, match="Historical experiment"):
        engine.reproduction(cid, eid)


def test_preparation_invalidates_old_readiness_and_baseline(
    measured_campaign, monkeypatch
):
    engine, cid, _, protocol = measured_campaign
    engine.store.update(cid, {"state": "preparing", "preparation_attempts": 1})
    monkeypatch.setattr(
        engine,
        "_propose",
        lambda *args: {"protocol": protocol.model_dump(), "patch": ""},
    )
    engine._prepare_task(cid, threading.Event())
    campaign = engine.store.get(cid)
    assert campaign["baseline"] is None
    assert campaign["baseline_summary"] is None
    assert campaign["baseline_context"] is None
    assert campaign["readiness"] is None
    assert campaign["approved_hash"] is None


def legacy_recorded_runs(engine, cid, eid):
    campaign = engine.store.get(cid)
    experiment = engine.store.experiment(cid, eid)
    baseline = campaign["baseline"]
    groups = [(baseline, campaign["base_revision"])]
    for field in [
        "reference_runs",
        "runs",
        "confirmation_reference_runs",
        "confirmation_runs",
    ]:
        groups.append(
            (
                experiment[field],
                campaign["base_revision"]
                if "reference" in field
                else experiment["revision"],
            )
        )
    for samples, revision in groups:
        for sample in samples:
            sample["execution"] = {
                "revision": revision,
                "protocol_hash": campaign["protocol_hash"],
            }
    engine.store.update(cid, {"baseline_context": None, "baseline": baseline})
    experiment["evaluation_context"] = None
    engine.store.update_experiment(cid, eid, experiment)


def test_verified_legacy_runs_display_without_mutation_or_false_reproduction(
    measured_campaign,
):
    engine, cid, eid, _ = measured_campaign
    legacy_recorded_runs(engine, cid, eid)
    campaign_before = engine.store.get(cid)
    attempt_before = engine.store.experiment(cid, eid)
    detail = engine.detail(cid)
    assert (
        detail["campaign"]["baseline_summary"]["measurement_context_source"]
        == "legacy_recorded_runs"
    )
    assert (
        detail["campaign"]["baseline_summary"]["protocol_hash"]
        == campaign_before["protocol_hash"]
    )
    assert len(detail["comparison"]) == 1
    assert (
        detail["comparison"][0]["measurement_context_source"] == "legacy_recorded_runs"
    )
    assert "not recorded" in detail["comparison"][0]["measurement_context_warning"]
    assert engine.store.get(cid) == campaign_before
    assert engine.store.experiment(cid, eid) == attempt_before
    with pytest.raises(Conflict, match="unverified"):
        engine.reproduction(cid, eid)


@pytest.mark.parametrize(
    "mismatch",
    [
        "protocol",
        "reference",
        "candidate",
        "environment",
        "missing",
        "confirmation",
        "explicit_context",
    ],
)
def test_legacy_fallback_requires_every_recorded_run(measured_campaign, mismatch):
    engine, cid, eid, _ = measured_campaign
    legacy_recorded_runs(engine, cid, eid)
    experiment = engine.store.experiment(cid, eid)
    if mismatch == "protocol":
        experiment["runs"][0]["execution"]["protocol_hash"] = "other-protocol"
    elif mismatch == "reference":
        experiment["reference_runs"][0]["execution"]["revision"] = "other-reference"
    elif mismatch == "candidate":
        experiment["confirmation_runs"][0]["execution"]["revision"] = "other-candidate"
    elif mismatch == "environment":
        experiment["confirmation_runs"][0]["environment_fingerprint"] = (
            "different-environment"
        )
    elif mismatch == "missing":
        experiment["runs"][0].pop("execution")
    elif mismatch == "confirmation":
        experiment["confirmation_runs"] = []
    else:
        experiment["evaluation_context"] = {
            "runner_config_hash": "mismatched-explicit-proof"
        }
    engine.store.update_experiment(cid, eid, experiment)
    assert engine.detail(cid)["comparison"] == []
