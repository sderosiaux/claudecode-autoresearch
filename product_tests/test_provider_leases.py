"""Local provider cleanup evidence never settles cost or hides unknown children."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from autoresearch.engine import Engine, git
from autoresearch.models import CampaignCreate
from autoresearch.provider import ProviderError


@pytest.fixture
def campaign(tmp_path, monkeypatch):
    repository = tmp_path / "repo"
    repository.mkdir()
    git(repository, "init")
    (repository / "value").write_text("fixture\n")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "fixture")
    engine = Engine(tmp_path / "state")
    detail = engine.create(
        CampaignCreate(
            name="provider lease",
            repository=str(repository),
            goal="lower allocation",
            agent={"enforcement": "estimated"},
        )
    )
    cid = detail["campaign"]["id"]
    engine.store.update(cid, {"state": "exploring"})
    engine.store.reserve(cid, "retained", 100, purpose="exploration")
    monkeypatch.setattr(engine, "_context", lambda *a: {"source_files": {}})
    yield engine, cid
    engine.close()


def propose(engine, cid):
    return engine._propose(cid, "optimize", threading.Event(), time.monotonic() + 30)


@pytest.mark.parametrize(
    "response",
    [
        {
            "status": "succeeded",
            "returncode": 0,
            "stdout": '{"proposal":{"patch":"x"}}',
        },
        {
            "status": "failed",
            "returncode": 1,
            "stdout": '{"error":"auth failed","type":"ProviderError"}',
        },
        {"status": "succeeded", "returncode": 0, "stdout": "{malformed"},
    ],
)
def test_normal_worker_return_releases_local_lease_before_parse(
    campaign, monkeypatch, response
):
    import autoresearch.runner as runner

    engine, cid = campaign
    monkeypatch.setattr(runner, "_execute", lambda *a, **kw: response)
    if response["stdout"].startswith('{"proposal"'):
        assert propose(engine, cid) == {"patch": "x"}
    else:
        with pytest.raises(ProviderError):
            propose(engine, cid)
    assert engine.store.get(cid)["resource_lease_completed"] is True
    assert engine.store.budget(cid)["engaged"] == 100


@pytest.mark.parametrize(
    "status", ["timed_out", "cancelled", "output_limit", "unknown", "exception"]
)
def test_uncertain_worker_keeps_lease_and_reservation(campaign, monkeypatch, status):
    import autoresearch.runner as runner

    engine, cid = campaign

    def execute(*args, **kwargs):
        if status == "exception":
            raise OSError("transport unavailable")
        return {"status": status, "returncode": -9, "stdout": ""}

    monkeypatch.setattr(runner, "_execute", execute)
    with pytest.raises((TimeoutError, InterruptedError, ProviderError, OSError)):
        propose(engine, cid)
    assert engine.store.get(cid)["resource_lease_completed"] is False
    assert engine.store.budget(cid)["engaged"] == 100
    assert not engine._provider_resources


def test_parallel_sibling_must_return_before_shared_lease_release(
    campaign, monkeypatch
):
    import autoresearch.runner as runner

    engine, cid = campaign
    gate = threading.Barrier(2)
    first_finished = threading.Event()
    second_release = threading.Event()
    seen = []
    lock = threading.Lock()

    def execute(*args, **kwargs):
        with lock:
            number = len(seen)
            seen.append(number)
        gate.wait(timeout=3)
        if number:
            assert second_release.wait(timeout=3)
        return {"status": "succeeded", "returncode": 0, "stdout": '{"proposal":{}}'}

    monkeypatch.setattr(runner, "_execute", execute)

    def attempt():
        result = propose(engine, cid)
        first_finished.set()
        return result

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(attempt) for _ in range(2)]
        try:
            assert first_finished.wait(timeout=3)
            assert engine.store.get(cid)["resource_lease_completed"] is False
        finally:
            second_release.set()
        assert [f.result(timeout=3) for f in futures] == [{}, {}]
    assert engine.store.get(cid)["resource_lease_completed"] is True
    assert engine.store.budget(cid)["engaged"] == 100


@pytest.mark.parametrize("unknown_first", [True, False])
def test_unknown_sibling_prevents_release_regardless_finish_order(
    campaign, unknown_first
):
    engine, cid = campaign
    engine._admit_provider_resource(cid, "a", 10)
    engine._admit_provider_resource(cid, "b", 10)
    engine._finish_provider_resource(cid, "a", not unknown_first)
    assert engine.store.get(cid)["resource_lease_completed"] is False
    engine._finish_provider_resource(cid, "b", unknown_first)
    assert engine.store.get(cid)["resource_lease_completed"] is False


def test_existing_unresolved_lease_cannot_be_released_by_later_provider(campaign):
    engine, cid = campaign
    engine._admit_resource_lease(cid, "host_measurement", 100, recoverable=False)
    engine._admit_provider_resource(cid, "later", 1)
    engine._finish_provider_resource(cid, "later", True)
    current = engine.store.get(cid)
    assert current["resource_lease_completed"] is False
    assert current["resource_lease_recoverable"] is False


@pytest.mark.parametrize(
    "metadata,confirmed",
    [
        ({"status": "succeeded", "returncode": 0}, True),
        ({"status": "failed", "returncode": 1}, True),
        ({"status": "transport_exception"}, False),
        ({"status": "cancelled", "returncode": -9}, False),
        ({"status": "timed_out", "returncode": -9}, False),
        ({"status": "succeeded"}, False),
        (None, False),
        ("malformed", False),
    ],
)
def test_native_child_receipt_required_even_when_worker_returned(
    tmp_path, metadata, confirmed
):
    request = {"config": {"kind": "claude_code"}, "artifact_dir": str(tmp_path)}
    if metadata is not None:
        (tmp_path / "metadata.json").write_text(
            metadata if isinstance(metadata, str) else json.dumps(metadata)
        )
    returned = {"status": "failed", "returncode": 1, "stdout": "irrelevant"}
    assert Engine._provider_processes_returned(returned, request) is confirmed
    assert not Engine._provider_processes_returned(
        {**returned, "status": "timed_out"}, request
    )


def test_recovery_required_is_not_cleared_by_late_local_return(campaign):
    engine, cid = campaign
    engine._admit_provider_resource(cid, "running", 10)
    engine.store.update(cid, {"recovery_required": True, "state": "blocked"})
    engine._finish_provider_resource(cid, "running", True)
    current = engine.store.get(cid)
    assert current["recovery_required"] is True
    assert current["resource_lease_completed"] is False
