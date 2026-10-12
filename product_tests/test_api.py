from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
import pytest

from autoresearch.api import create_app
from autoresearch.engine import Engine, git


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    (root / "source.txt").write_text("original\n")
    git(root, "init")
    git(root, "add", ".")
    git(root, "commit", "-m", "fixture")
    (root / "personal-untracked.txt").write_text("keep me")
    return root


def test_approval_hash_and_api_admission(tmp_path, repository):
    with TestClient(create_app(tmp_path / "data")) as client:
        response = client.post(
            "/api/campaigns",
            json={
                "name": "memory",
                "repository": str(repository),
                "goal": "less memory",
                "budget_usd": 100,
            },
        )
        assert response.status_code == 201, response.text
        cid = response.json()["campaign"]["id"]
        assert client.post(f"/api/campaigns/{cid}/run").status_code == 409
        result = client.post(f"/api/campaigns/{cid}/assess").json()
        assert result["readiness"]["status"] == "needs_preparation"
        protocol = {
            "command": ["echo", "test"],
            "metrics": [{"name": "rss", "unit": "bytes"}],
        }
        result = client.put(
            f"/api/campaigns/{cid}/protocol", json={"protocol": protocol}
        ).json()
        assert (
            client.post(
                f"/api/campaigns/{cid}/approve", json={"protocol_hash": "old"}
            ).status_code
            == 409
        )
        assert (
            client.post(
                f"/api/campaigns/{cid}/approve",
                json={"protocol_hash": result["protocol_hash"]},
            ).status_code
            == 200
        )
        protocol["command"] = ["echo", "changed"]
        result = client.put(
            f"/api/campaigns/{cid}/protocol", json={"protocol": protocol}
        ).json()
        assert result["campaign"]["approved_hash"] is None
        assert client.post(f"/api/campaigns/{cid}/run").status_code == 409
        assert (repository / "personal-untracked.txt").read_text() == "keep me"


def test_browser_origin_and_remote_auth(tmp_path):
    with TestClient(create_app(tmp_path / "data")) as client:
        assert (
            client.post(
                "/api/campaigns", json={}, headers={"origin": "https://evil.example"}
            ).status_code
            == 403
        )
        assert (
            client.get(
                "/api/capabilities", headers={"host": "evil.example"}
            ).status_code
            == 403
        )
    with TestClient(create_app(tmp_path / "data", token="local-test-only")) as client:
        assert client.get("/api/capabilities").status_code == 401
        assert (
            client.get(
                "/api/capabilities", headers={"Authorization": "Bearer local-test-only"}
            ).status_code
            == 200
        )


def test_delegated_approval_and_worker_origin_are_explicit(tmp_path, repository):
    with TestClient(create_app(tmp_path / "data")) as client:
        detail = client.post(
            "/api/campaigns",
            json={
                "name": "delegated local validation",
                "goal": "latency",
                "repository": str(repository),
                "protocol": {
                    "command": ["true"],
                    "metrics": [{"name": "latency", "unit": "ms"}],
                },
            },
        ).json()
        route = "/api/campaigns/" + detail["campaign"]["id"]
        approval = {
            "protocol_hash": detail["protocol_hash"],
            "authority": "delegated_agent",
        }
        assert client.post(route + "/approve", json=approval).status_code == 422
        approval["rationale"] = (
            "Operator delegated review of this bounded local synthetic-data protocol."
        )
        result = client.post(route + "/approve", json=approval).json()
        assert result["campaign"]["approval"]["authority"] == "delegated_agent"
        assert result["campaign"]["approval"]["rationale"] == approval["rationale"]
        proposal = {
            "hypothesis": "less work",
            "patch": "reviewed externally",
            "origin": "external_worker",
        }
        attempt = client.post(route + "/experiments", json=proposal)
        assert attempt.status_code == 201
        assert attempt.json()["experiments"][0]["origin"] == "external_worker"
        proposal["origin"] = "claude_provider"
        assert client.post(route + "/experiments", json=proposal).status_code == 422
        changed = detail["campaign"]["protocol"]
        changed["command"] = ["false"]
        result = client.put(route + "/protocol", json={"protocol": changed}).json()
        assert result["campaign"]["approval"] is None


def test_only_one_supervisor_owns_state(tmp_path):
    engine = Engine(tmp_path)
    try:
        from autoresearch.store import Conflict

        with pytest.raises(Conflict):
            Engine(tmp_path)
    finally:
        engine.close()


def test_retried_create_returns_same_campaign(tmp_path, repository):
    with TestClient(create_app(tmp_path / "data")) as client:
        payload = {
            "name": "idempotent",
            "goal": "less memory",
            "repository": str(repository),
        }
        first = client.post(
            "/api/campaigns", json=payload, headers={"Idempotency-Key": "same-create"}
        )
        second = client.post(
            "/api/campaigns", json=payload, headers={"Idempotency-Key": "same-create"}
        )
        assert first.status_code == second.status_code == 201
        assert first.json()["campaign"]["id"] == second.json()["campaign"]["id"]
        assert len(client.get("/api/campaigns").json()) == 1
        payload["name"] = "different"
        assert (
            client.post(
                "/api/campaigns",
                json=payload,
                headers={"Idempotency-Key": "same-create"},
            ).status_code
            == 409
        )


@pytest.mark.parametrize("action", ["run", "resume"])
def test_measurement_busy_rejection_is_completed_idempotently_without_delayed_work(
    tmp_path, repository, monkeypatch, action
):
    app = create_app(tmp_path / "data")
    engine = app.state.engine
    launches = []
    monkeypatch.setattr(engine, "_launch", lambda *args: launches.append(args))
    with TestClient(app) as client:
        created = client.post(
            "/api/campaigns",
            json={
                "name": "prompt admission",
                "repository": str(repository),
                "goal": "reduce synthetic score",
                "runner": {"kind": "host", "trusted": True},
                "protocol": {
                    "command": ["true"],
                    "metrics": [{"name": "score", "unit": "test units"}],
                },
            },
        ).json()
        cid = created["campaign"]["id"]
        engine.approve(cid, created["protocol_hash"])
        if action == "resume":
            engine.store.update(cid, {"state": "stopped"})
        route = f"/api/campaigns/{cid}/{action}"
        headers = {"Idempotency-Key": "busy-" + action}
        before = engine.store.get(cid), engine.store.events(cid)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with engine._phase_barrier.measurement():
                future = pool.submit(client.post, route, json={}, headers=headers)
                rejected = future.result(timeout=1)
                assert rejected.status_code == 409
                assert "No work was queued" in rejected.json()["detail"]
            assert future.done()
        repeated = client.post(route, json={}, headers=headers)
        assert repeated.status_code == rejected.status_code
        assert repeated.json() == rejected.json()
        assert launches == []
        assert before == (engine.store.get(cid), engine.store.events(cid))
        accepted = client.post(
            route, json={}, headers={"Idempotency-Key": "new-intent-" + action}
        )
        assert accepted.status_code == 200, accepted.text
        assert len(launches) == 1


@pytest.mark.parametrize(
    ("goal", "available_metric", "unit"),
    [("Reduce p99 latency", "latency_mean", "ms"), ("Reduce RSS", "heap", "bytes")],
)
def test_goal_requires_its_actual_measurement(
    tmp_path, repository, goal, available_metric, unit
):
    with TestClient(create_app(tmp_path / "data")) as client:
        created = client.post(
            "/api/campaigns",
            json={
                "name": "missing measurement",
                "repository": str(repository),
                "goal": goal,
                "runner": {"kind": "host", "trusted": True},
                "protocol": {
                    "command": ["echo", "must not execute"],
                    "metrics": [{"name": available_metric, "unit": unit}],
                },
            },
        )
        assert created.status_code == 201, created.text
        detail = created.json()
        route = f"/api/campaigns/{detail['campaign']['id']}"
        result = client.post(route + "/assess").json()
        assert result["readiness"]["status"] == "needs_preparation"
        assert any(
            f["code"] == "requested_metric_missing"
            for f in result["readiness"]["findings"]
        )
        client.post(route + "/approve", json={"protocol_hash": detail["protocol_hash"]})
        assert client.post(route + "/run").status_code == 409
        assert client.get(route).json()["experiments"] == []


def test_reconcile_endpoint_preserves_spending_and_requires_separate_resume(
    tmp_path, repository
):
    engine = Engine(tmp_path / "data")
    try:
        with TestClient(create_app(engine=engine)) as client:
            created = client.post(
                "/api/campaigns",
                json={
                    "name": "recovered",
                    "goal": "lower latency",
                    "repository": str(repository),
                },
            ).json()
            cid = created["campaign"]["id"]
            route = f"/api/campaigns/{cid}"
            engine.store.reserve(cid, "unknown-request", 100_000)
            before = engine.store.budget(cid)
            engine.store.update(
                cid,
                {
                    "state": "blocked",
                    "recovery_required": True,
                    "resource_lease_expires_at": "2020-01-01T00:00:00+00:00",
                    "resource_lease_recoverable": True,
                },
            )
            assert client.post(route + "/resume").status_code == 409
            result = client.post(route + "/reconcile")
            assert result.status_code == 200, result.text
            assert result.json()["campaign"]["recovery_required"] is False
            assert result.json()["campaign"]["state"] == "blocked"
            assert result.json()["budget"] == before
            assert not engine._threads
    finally:
        engine.close()
