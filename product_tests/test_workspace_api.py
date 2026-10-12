"""Campaign overview reads evidence without admitting work or changing history."""

import pytest
from fastapi.testclient import TestClient

from autoresearch.api import create_app
from autoresearch.engine import git


@pytest.fixture
def workspace_api(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "value.txt").write_text("10\n")
    git(repository, "init")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "API contract fixture")
    app = create_app(tmp_path / "data")
    with TestClient(app) as client:
        yield (
            client,
            app.state.engine,
            {
                "name": "Research overview",
                "goal": "Reduce the measured score",
                "repository": str(repository),
                "budget_usd": "1",
                "limits": {"max_parallel_tasks": 2},
                "protocol": {
                    "command": ["true"],
                    "metrics": [{"name": "score", "unit": "test units"}],
                },
            },
        )


def test_overview_polling_retains_queued_hypothesis_and_never_starts_work(
    workspace_api,
):
    client, engine, payload = workspace_api
    created = client.post("/api/campaigns", json=payload)
    assert created.status_code == 201, created.text
    cid = created.json()["campaign"]["id"]
    added = client.post(
        f"/api/campaigns/{cid}/experiments",
        json={
            "hypothesis": "Halving the retained value should reduce the score",
            "expected_effect": "A lower score under the same evaluator",
            "strategy": "reduce-retention",
            "patch": "--- a/value.txt\n+++ b/value.txt\n@@ -1 +1 @@\n-10\n+5\n",
        },
    )
    assert added.status_code == 201, added.text
    eid = added.json()["experiments"][0]["id"]
    before = (
        engine.store.get(cid),
        engine.store.experiments(cid),
        engine.store.events(cid),
        engine.store.budget(cid),
    )
    for _ in range(3):
        response = client.get(f"/api/campaigns/{cid}")
        assert response.status_code == 200
        detail = response.json()
        assert detail["campaign"]["limits"]["max_parallel_tasks"] == 2
        insights = detail["insights"]
        assert insights["summary"]["attempts"] == 1
        assert insights["summary"]["queued"] == 1
        assert insights["summary"]["confirmed"] == 0
        assert any(
            experiment["id"] == eid
            for track in insights["tracks"]
            for experiment in track["experiments"]
        )
        assert all(metric["best"] is None for metric in insights["metrics"])
        assert not engine._threads
    assert before == (
        engine.store.get(cid),
        engine.store.experiments(cid),
        engine.store.events(cid),
        engine.store.budget(cid),
    )


@pytest.mark.parametrize("limit", [0, 5, 2.5])
def test_parallel_task_limit_is_validated_before_campaign_creation(
    workspace_api, limit
):
    client, engine, payload = workspace_api
    payload["limits"]["max_parallel_tasks"] = limit
    response = client.post("/api/campaigns", json=payload)
    assert response.status_code == 422
    assert engine.store.campaigns() == []
