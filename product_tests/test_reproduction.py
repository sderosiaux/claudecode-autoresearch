"""Reproduction locators must contain the recorded program, not just its origin."""

import shutil

import pytest

from autoresearch.engine import Engine, git
from autoresearch.models import CampaignCreate, ExperimentCreate, Protocol
from autoresearch.store import Conflict


@pytest.fixture
def replay_campaign(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    git(repository, "init")
    (repository / "value.txt").write_text("10\n")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "reference")
    engine = Engine(tmp_path / "data")
    detail = engine.create(
        CampaignCreate(
            name="replay",
            goal="reduce value",
            repository=str(repository),
            protocol=Protocol(
                command=["true"], metrics=[{"name": "value", "unit": "n"}]
            ),
            runner={"kind": "host", "trusted": True},
        )
    )
    yield engine, detail["campaign"]["id"], repository
    engine.close()


def candidate(engine, cid):
    experiment = engine.add_experiment(
        cid,
        ExperimentCreate(
            hypothesis="reduce retained value",
            patch="--- a/value.txt\n+++ b/value.txt\n@@ -1 +1 @@\n-10\n+5\n",
        ),
    )
    workspace = engine._checkout(cid, experiment["id"])
    revision = engine._apply(workspace, experiment["patch"], [])
    engine.store.update_experiment(
        cid,
        experiment["id"],
        {
            "revision": revision,
            "tree": git(workspace, "rev-parse", "HEAD^{tree}"),
            "workspace": str(workspace),
            "evaluation_context": engine._measurement_context(engine.store.get(cid)),
        },
    )
    return experiment["id"], workspace, revision


def test_baseline_returns_exact_tree_even_when_original_worktree_has_advanced(
    replay_campaign,
):
    engine, cid, repository = replay_campaign
    revision = engine.store.get(cid)["base_revision"]
    tree = git(repository, "rev-parse", revision + "^{tree}")
    (repository / "value.txt").write_text("later unrelated work\n")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "later work")
    recipe = engine.reproduction(cid)
    assert recipe["repository"] == str(repository)
    assert recipe["checkout_repository"] == str(repository)
    assert recipe["revision"] == revision
    assert recipe["tree"] == tree
    assert "local locator" in " ".join(recipe["instructions"])


def test_baseline_prefers_retained_clean_exact_checkout(replay_campaign):
    engine, cid, _ = replay_campaign
    revision = engine.store.get(cid)["base_revision"]
    workspace = engine._checkout(cid, "baseline-" + revision[:12])
    engine.store.update(cid, {"baseline": [{"environment_fingerprint": "recorded"}]})
    recipe = engine.reproduction(cid)
    assert recipe["checkout_repository"] == str(workspace)
    assert recipe["environment"] == "recorded"


def test_candidate_locator_contains_commit_absent_from_original_repository(
    replay_campaign,
):
    engine, cid, repository = replay_campaign
    eid, workspace, revision = candidate(engine, cid)
    with pytest.raises(ValueError):
        git(repository, "cat-file", "-e", revision + "^{commit}")
    recipe = engine.reproduction(cid, eid)
    assert recipe["repository"] == str(repository)
    assert recipe["checkout_repository"] == str(workspace)
    assert recipe["tree"] == git(workspace, "rev-parse", revision + "^{tree}")


def test_prepared_reference_locator_preserves_preparation_commit(replay_campaign):
    engine, cid, _ = replay_campaign
    workspace = engine._checkout(cid, "preparation")
    (workspace / "value.txt").write_text("prepared evaluator\n")
    git(workspace, "add", ".")
    git(workspace, "commit", "-m", "preparation")
    revision = git(workspace, "rev-parse", "HEAD")
    engine.store.update(
        cid, {"prepared_revision": revision, "prepared_repository": str(workspace)}
    )
    recipe = engine.reproduction(cid)
    assert recipe["checkout_repository"] == str(workspace)
    assert recipe["revision"] == revision
    assert recipe["tree"] == git(workspace, "rev-parse", "HEAD^{tree}")


def test_missing_preparation_locator_uses_retained_exact_baseline_checkout(
    replay_campaign,
):
    engine, cid, _ = replay_campaign
    prepared = engine._checkout(cid, "preparation")
    (prepared / "value.txt").write_text("prepared evaluator\n")
    git(prepared, "add", ".")
    git(prepared, "commit", "-m", "preparation")
    revision = git(prepared, "rev-parse", "HEAD")
    engine.store.update(
        cid, {"prepared_revision": revision, "prepared_repository": str(prepared)}
    )
    baseline = engine._checkout(cid, "baseline-" + revision[:12])
    shutil.rmtree(prepared)
    assert engine.reproduction(cid)["checkout_repository"] == str(baseline)


@pytest.mark.parametrize("mutation", ["dirty", "tree", "missing"])
def test_altered_or_unavailable_candidate_is_not_silently_reconstructed(
    replay_campaign, mutation
):
    engine, cid, _ = replay_campaign
    eid, workspace, _ = candidate(engine, cid)
    if mutation == "dirty":
        (workspace / "value.txt").write_text("unrecorded edit\n")
    elif mutation == "tree":
        engine.store.update_experiment(cid, eid, {"tree": "0" * 40})
    else:
        shutil.rmtree(workspace)
    with pytest.raises(Conflict, match="changed|does not match|unavailable"):
        engine.reproduction(cid, eid)


def test_historical_context_gate_precedes_checkout_resolution(replay_campaign):
    engine, cid, _ = replay_campaign
    eid, workspace, _ = candidate(engine, cid)
    shutil.rmtree(workspace)
    engine.store.update_experiment(cid, eid, {"protocol_hash": "prior-protocol"})
    with pytest.raises(Conflict, match="Historical experiment"):
        engine.reproduction(cid, eid)
