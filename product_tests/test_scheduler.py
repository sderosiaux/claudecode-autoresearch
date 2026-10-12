"""Real thread overlap and negative admission proofs; never a paid model call."""

import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from autoresearch.engine import Engine, PhaseBarrier, research_work
from autoresearch.models import CampaignCreate, Limits, Protocol
from autoresearch.store import BudgetExceeded, Conflict
from test_duplicate_experiments import (
    PATCH,
    campaign as campaign_fixture,
    measurements,
    propose,
    run,
)


@pytest.fixture(name="campaign")
def scheduler_campaign(tmp_path):
    yield from campaign_fixture.__wrapped__(tmp_path)


def test_parallel_limits_are_bounded_and_legacy_is_serial():
    assert Limits().max_parallel_tasks == 2
    assert Engine._scheduler_default({"limits": {}})["max_parallel_tasks"] == 1
    for value in (0, 5):
        with pytest.raises(ValidationError):
            Limits(max_parallel_tasks=value)


def test_manual_preparations_overlap_but_finish_before_measurement(
    campaign, monkeypatch
):
    engine, cid, _, _ = campaign
    propose(engine, cid)
    propose(engine, cid, PATCH.replace("+5", "+6"))
    _, _, measure = measurements(engine, monkeypatch)
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    active, peak = 0, 0
    original = engine._apply

    def apply(*args):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            barrier.wait(5)
            assert engine.detail(cid)["campaign"]["scheduler"]["active_tasks"] == 2
            return original(*args)
        finally:
            with lock:
                active -= 1

    def isolated_measure(*args):
        assert active == 0, "Measurement overlapped controlled candidate preparation"
        return measure(*args)

    monkeypatch.setattr(engine, "_apply", apply)
    monkeypatch.setattr(engine, "_measure", isolated_measure)
    run(engine, cid)
    assert peak == 2
    scheduler = engine.detail(cid)["campaign"]["scheduler"]
    assert scheduler["active_tasks"] == scheduler["waiting_for_measurement"] == 0
    assert scheduler["measurement_concurrency"] == 1
    assert all(t["status"] == "succeeded" for t in scheduler["tasks"])
    assert all(e["phase"] == "finished" for e in engine.store.experiments(cid))


def test_legacy_absent_parallel_limit_prepares_one_at_a_time(campaign, monkeypatch):
    engine, cid, _, _ = campaign
    current = engine.store.get(cid)
    current["limits"].pop("max_parallel_tasks")
    engine.store.update(cid, {"limits": current["limits"]})
    propose(engine, cid)
    propose(engine, cid, PATCH.replace("+5", "+6"))
    measurements(engine, monkeypatch)
    observed = []
    original = engine._apply

    def apply(*args):
        observed.append(engine.detail(cid)["campaign"]["scheduler"]["active_tasks"])
        return original(*args)

    monkeypatch.setattr(engine, "_apply", apply)
    run(engine, cid)
    assert observed == [1, 1]


def proposal_campaign(engine, cid, cap=2):
    current = engine.store.get(cid)
    engine.store.update(
        cid,
        {
            "state": "exploring",
            "agent": {"kind": "claude", "enforcement": "estimated"},
            "limits": {**current["limits"], "max_experiments": cap},
        },
    )


def test_parallel_proposals_have_distinct_strategies_and_counted_slots(
    campaign, monkeypatch
):
    engine, cid, _, _ = campaign
    proposal_campaign(engine, cid)
    barrier = threading.Barrier(2)
    contexts = []

    def provider(cid, task, cancel, deadline):
        contexts.append(engine._local.proposal_context)
        assert len(engine.store.experiments(cid)) == 2
        barrier.wait(5)
        key = uuid.uuid4().hex
        assert engine.store.reserve(cid, key, 10)["created"]
        return {"hypothesis": task, "patch": PATCH}

    monkeypatch.setattr(engine, "_propose", provider)
    results = engine._proposal_wave(cid, threading.Event(), time.monotonic() + 15)
    assert len(results) == len(contexts) == 2
    assert len({c["strategy_focus"]["id"] for c in contexts}) == 2
    assert contexts[0]["source_files"] == contexts[1]["source_files"]
    assert engine.store.budget(cid)["engaged"] == 20
    assert engine._proposal_wave(cid, threading.Event(), time.monotonic() + 15) == []
    with pytest.raises(Conflict, match="limit"):
        propose(engine, cid)


def test_concurrent_provider_reservations_cannot_overspend(campaign, monkeypatch):
    engine, cid, _, _ = campaign
    proposal_campaign(engine, cid)
    barrier = threading.Barrier(2)

    def provider(cid, task, cancel, deadline):
        barrier.wait(5)
        engine.store.reserve(cid, uuid.uuid4().hex, 60_000_000)
        return {"hypothesis": task, "patch": PATCH}

    monkeypatch.setattr(engine, "_propose", provider)
    with pytest.raises(BudgetExceeded):
        engine._proposal_wave(cid, threading.Event(), time.monotonic() + 15)
    assert engine.store.budget(cid)["engaged"] == 60_000_000
    assert len(engine.store.experiments(cid)) == 2
    assert engine.detail(cid)["campaign"]["scheduler"]["active_tasks"] == 0


@pytest.mark.parametrize("control", ["pause", "stop"])
def test_prepared_unmeasured_attempt_can_resume_exact_tree(
    campaign, monkeypatch, control
):
    engine, cid, _, _ = campaign
    first = propose(engine, cid)
    second = propose(engine, cid, PATCH.replace("+5", "+6"))
    measurements(engine, monkeypatch)
    original = engine._preparation_wave
    paused = False

    def prepare(*args):
        nonlocal paused
        ready = original(*args)
        if not paused:
            paused = True
            getattr(engine, control)(cid)
        return ready

    monkeypatch.setattr(engine, "_preparation_wave", prepare)
    engine.run(cid)
    engine._threads[cid].join(10)
    assert not engine._threads[cid].is_alive()
    before = [engine.store.experiment(cid, e["id"]) for e in (first, second)]
    assert all(
        e["status"] == "queued" and e["phase"] == "waiting_for_measurement"
        for e in before
    )
    assert engine.detail(cid)["campaign"]["scheduler"]["waiting_for_measurement"] == 2
    engine.resume(cid)
    engine._threads[cid].join(10)
    assert not engine._threads[cid].is_alive()
    after = engine.store.experiments(cid)
    assert [e["revision"] for e in after] == [e["revision"] for e in before]
    assert all(e["status"] == "succeeded" for e in after)


def test_stop_joins_all_provider_workers_and_retains_unknown_cost(
    campaign, monkeypatch
):
    engine, cid, _, _ = campaign
    proposal_campaign(engine, cid)
    entered = threading.Barrier(3)
    finished = []

    def provider(cid, task, cancel, deadline):
        key = uuid.uuid4().hex
        engine.store.reserve(cid, key, 10)
        entered.wait(5)
        assert cancel.wait(5)
        finished.append(key)
        raise InterruptedError("unknown remote result")

    monkeypatch.setattr(engine, "_propose", provider)
    engine._launch(
        cid,
        lambda cid, cancel: engine._proposal_wave(cid, cancel, time.monotonic() + 15),
    )
    entered.wait(5)
    engine.stop(cid)
    engine._threads[cid].join(10)
    assert not engine._threads[cid].is_alive()
    assert len(finished) == 2
    assert engine.store.get(cid)["state"] == "stopped"
    assert engine.store.budget(cid)["engaged"] == 20
    assert engine.detail(cid)["campaign"]["scheduler"]["active_tasks"] == 0
    assert all(e["status"] == "cancelled" for e in engine.store.experiments(cid))


def test_global_measurement_barrier_blocks_provider_and_other_measurements(
    campaign, monkeypatch
):
    engine, cid, repository, protocol = campaign
    engine.store.update(cid, {"state": "exploring"})
    second_campaign = engine.create(
        CampaignCreate(
            name="other campaign",
            goal="independent work",
            repository=str(repository),
            protocol=protocol,
            runner={"kind": "host", "trusted": True},
        )
    )["campaign"]["id"]
    engine.store.update(second_campaign, {"state": "exploring"})
    entered, release, provider_started = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    lock = threading.Lock()
    active, peak = 0, 0

    def runner(command, workspace, artifact, **kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        entered.set()
        assert release.wait(5)
        with lock:
            active -= 1
        return {
            "status": "succeeded",
            "stdout": json.dumps({"metrics": {"score": 10}, "checks": {"valid": True}}),
            "stderr": "",
            "duration_seconds": 0.01,
            "environment": {"fixture": "same"},
        }

    def transport(*args, **kwargs):
        assert active == 0
        provider_started.set()
        return {
            "status": "succeeded",
            "stdout": json.dumps({"proposal": {"hypothesis": "test", "patch": PATCH}}),
        }

    monkeypatch.setattr("autoresearch.runner.run_command", runner)
    monkeypatch.setattr("autoresearch.runner._execute", transport)
    cancel = threading.Event()
    deadline = time.monotonic() + 15
    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(
            engine._measure,
            second_campaign,
            repository,
            "first",
            protocol.model_dump(),
            cancel,
            deadline,
        )
        assert entered.wait(5)
        second = pool.submit(
            engine._measure,
            cid,
            repository,
            "second",
            protocol.model_dump(),
            cancel,
            deadline,
        )
        provider = pool.submit(
            engine._propose, second_campaign, "test", cancel, deadline
        )
        assert not provider_started.wait(0.1)
        assert not second.done()
        release.set()
        first.result(5)
        second.result(5)
        provider.result(5)
    assert peak == 1


def test_phase_wait_is_cancellable_and_research_workers_overlap():
    barrier = PhaseBarrier()
    cancel = threading.Event()
    with barrier.research():
        with ThreadPoolExecutor(max_workers=1) as pool:

            def measure():
                with barrier.measurement(cancel, time.monotonic() + 5):
                    pytest.fail("measurement entered during research")

            future = pool.submit(measure)
            cancel.set()
            with pytest.raises(InterruptedError):
                future.result(2)


@pytest.mark.parametrize("action", ["assess", "prepare"])
def test_public_research_admission_never_waits_while_holding_engine_lock(
    campaign, monkeypatch, action
):
    engine, cid, _, _ = campaign
    monkeypatch.setattr(engine, "_launch", lambda *args: None)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with engine._phase_barrier.measurement():
            future = pool.submit(getattr(engine, action), cid)
            # Give the public entry point an opportunity to block on admission.
            time.sleep(0.05)
            acquired = engine._lock.acquire(timeout=0.5)
            assert acquired, (
                "Research acquired Engine._lock before waiting for measurement"
            )
            engine._lock.release()
            assert not future.done()
        future.result(5)


@pytest.mark.parametrize("action", ["run", "resume"])
def test_run_and_resume_reject_measurement_without_queuing_or_mutation(
    campaign, monkeypatch, action
):
    engine, cid, _, _ = campaign
    if action == "resume":
        engine.store.update(cid, {"state": "stopped"})
    calls = []
    monkeypatch.setattr(engine, "assess", lambda *args: calls.append("assess"))
    monkeypatch.setattr(engine, "_launch", lambda *args: calls.append("launch"))
    before = (
        engine.store.get(cid),
        engine.store.events(cid),
        engine.store.budget(cid),
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        with engine._phase_barrier.measurement():
            future = pool.submit(getattr(engine, action), cid)
            with pytest.raises(Conflict, match="No work was queued"):
                future.result(timeout=1)
            assert engine._phase_barrier.readers == 0
        assert future.done()
    assert calls == []
    assert before == (
        engine.store.get(cid),
        engine.store.events(cid),
        engine.store.budget(cid),
    )


@pytest.mark.parametrize("action", ["run", "resume"])
def test_admitted_run_and_resume_protect_readiness_and_launch_once(
    campaign, monkeypatch, action
):
    engine, cid, _, _ = campaign
    if action == "resume":
        engine.store.update(cid, {"state": "stopped"})
    measuring = threading.Event()
    measuring_thread = None
    launches = []

    def measure():
        with engine._phase_barrier.measurement():
            measuring.set()

    original_assess = engine.assess

    def assess(*args):
        nonlocal measuring_thread
        assert engine._phase_barrier.readers == 1
        measuring_thread = threading.Thread(target=measure)
        measuring_thread.start()
        assert not measuring.wait(0.05), "Measurement entered during run admission"
        result = original_assess(*args)
        assert engine._phase_barrier.readers == 1
        assert not measuring.is_set()
        return result

    monkeypatch.setattr(engine, "assess", assess)
    monkeypatch.setattr(engine, "_launch", lambda *args: launches.append(args))
    try:
        detail = getattr(engine, action)(cid)
        assert detail["campaign"]["state"] == "baselining"
        assert len(launches) == 1
        assert measuring.wait(1), "Measurement remained blocked after admission"
    finally:
        if measuring_thread:
            measuring_thread.join(timeout=1)
            assert not measuring_thread.is_alive()


@pytest.mark.parametrize("outcome", ["success", "failure", "cancel", "deadline"])
def test_initial_baseline_preparation_is_visible_while_checkout_waits_and_finishes(
    campaign, monkeypatch, outcome
):
    engine, cid, repository, protocol = campaign
    engine.store.update(cid, {"state": "baselining"})
    if outcome == "deadline":
        limits = {**engine.store.get(cid)["limits"], "max_active_seconds": 1}
        engine.store.update(cid, {"limits": limits, "active_seconds": 0.5})
    engine._scheduler(cid, phase="finished")
    preparation_started = threading.Event()
    checkout_entered = threading.Event()
    original_scheduler = engine._scheduler

    def scheduler(*args, **kwargs):
        result = original_scheduler(*args, **kwargs)
        task = kwargs.get("task") or {}
        if task.get("kind") == "baseline_preparation" and task["status"] == "running":
            preparation_started.set()
        return result

    @research_work
    def checkout(self, campaign_id, name):
        checkout_entered.set()
        if outcome == "failure":
            raise ValueError("Fixture baseline checkout failed")
        return repository

    monkeypatch.setattr(engine, "_scheduler", scheduler)
    monkeypatch.setattr(engine, "_checkout", checkout.__get__(engine, Engine))
    measurement_calls, _, _ = measurements(engine, monkeypatch)
    try:
        with engine._phase_barrier.measurement():
            engine._launch(cid, engine._run_task)
            assert preparation_started.wait(1)
            current = engine.detail(cid)["campaign"]
            assert current["state"] == "baselining"
            assert current["active_started_at"]
            assert current["scheduler"]["phase"] == "preparing"
            assert current["scheduler"]["active_tasks"] == 1
            preparation = next(
                t
                for t in current["scheduler"]["tasks"]
                if t["kind"] == "baseline_preparation"
            )
            assert preparation["status"] == "running"
            assert not checkout_entered.is_set()
            assert measurement_calls == []
            if outcome == "cancel":
                engine.stop(cid)
            if outcome in {"cancel", "deadline"}:
                engine._threads[cid].join(2)
                assert not engine._threads[cid].is_alive(), (
                    "Preparation must finish without releasing the other measurement"
                )
                assert engine._phase_barrier.measuring
                assert not checkout_entered.is_set()
                waiting = engine.store.get(cid)["scheduler"]
                assert waiting["active_tasks"] == 0
                assert waiting["phase"] == "finished"
        engine._threads[cid].join(5)
        assert not engine._threads[cid].is_alive()
    finally:
        if engine._threads.get(cid) and engine._threads[cid].is_alive():
            engine._cancellations[cid].set()
            engine._threads[cid].join(5)
    final = engine.store.get(cid)
    scheduler = final["scheduler"]
    preparation = next(
        t for t in scheduler["tasks"] if t["kind"] == "baseline_preparation"
    )
    assert (
        preparation["status"]
        == {
            "success": "succeeded",
            "failure": "failed",
            "cancel": "cancelled",
            "deadline": "failed",
        }[outcome]
    )
    assert preparation["finished_at"]
    assert scheduler["phase"] == "finished"
    assert scheduler["active_tasks"] == 0
    assert final["active_started_at"] is None
    if outcome == "success":
        assert len(measurement_calls) == protocol.repetitions
        assert final["state"] == "reviewable"
    else:
        assert measurement_calls == []
        assert (
            final["state"]
            == {
                "failure": "blocked",
                "cancel": "stopped",
                "deadline": "reviewable",
            }[outcome]
        )
        if outcome == "deadline":
            assert final["termination_reason"] == "time_limit"


@pytest.mark.parametrize("outcome", ["cancel", "deadline"])
def test_profile_source_wait_finishes_before_other_measurement_releases(
    campaign, monkeypatch, outcome
):
    engine, cid, repository, protocol = campaign
    diagnostic = Protocol.model_validate(
        {**protocol.model_dump(), "diagnostic_command": ["true"]}
    )
    detail = engine.set_protocol(cid, diagnostic)
    engine.approve(cid, detail["protocol_hash"])
    if outcome == "deadline":
        limits = {**engine.store.get(cid)["limits"], "max_active_seconds": 1}
        engine.store.update(cid, {"limits": limits, "active_seconds": 0.5})
    waiting = threading.Event()
    checkout_entered = threading.Event()
    executions = []
    original_check = engine._phase_barrier._check_wait

    def check_wait(cancel, deadline):
        if cancel is not None and deadline is not None:
            waiting.set()
        original_check(cancel, deadline)

    @research_work
    def checkout(self, campaign_id, name):
        checkout_entered.set()
        return repository

    monkeypatch.setattr(engine._phase_barrier, "_check_wait", check_wait)
    monkeypatch.setattr(engine, "_checkout", checkout.__get__(engine, Engine))
    monkeypatch.setattr(
        engine, "_execute_runner", lambda *args, **kwargs: executions.append(args)
    )
    try:
        with engine._phase_barrier.measurement():
            engine.profile(cid)
            assert waiting.wait(1), "Profile did not enter bounded source admission"
            if outcome == "cancel":
                engine.stop(cid)
            engine._threads[cid].join(2)
            assert not engine._threads[cid].is_alive()
            assert engine._phase_barrier.measuring
            assert not checkout_entered.is_set()
            assert executions == []
    finally:
        if engine._threads.get(cid) and engine._threads[cid].is_alive():
            engine._cancellations[cid].set()
            engine._threads[cid].join(5)
    final = engine.store.get(cid)
    assert final["active_started_at"] is None
    assert final["scheduler"]["phase"] == "finished"
    assert final["state"] == ("stopped" if outcome == "cancel" else "reviewable")
    if outcome == "deadline":
        assert final["termination_reason"] == "time_limit"


@pytest.mark.parametrize("outcome", ["cancel", "deadline"])
def test_post_baseline_context_wait_is_bounded_while_other_measurement_is_held(
    campaign, monkeypatch, outcome
):
    engine, cid, _, protocol = campaign
    engine.store.update(cid, {"state": "baselining"})
    if outcome == "deadline":
        limits = {**engine.store.get(cid)["limits"], "max_active_seconds": 2}
        engine.store.update(cid, {"limits": limits, "active_seconds": 0.5})
    baseline_calls, _, original_measure = measurements(engine, monkeypatch)
    measurement_entered = threading.Event()
    release_measurement = threading.Event()
    context_waiting = threading.Event()
    competing_thread = None
    original_check = engine._phase_barrier._check_wait

    def hold_measurement():
        with engine._phase_barrier.measurement():
            measurement_entered.set()
            assert release_measurement.wait(5)

    def measure(*args):
        nonlocal competing_thread
        with engine._phase_barrier.measurement():
            sample = original_measure(*args)
            last = len(baseline_calls) == protocol.repetitions
            if last:
                competing_thread = threading.Thread(target=hold_measurement)
                competing_thread.start()
        if last:
            assert measurement_entered.wait(1)
        return sample

    def check_wait(cancel, deadline):
        if cancel is not None and deadline is not None:
            context_waiting.set()
        original_check(cancel, deadline)

    monkeypatch.setattr(engine, "_measure", measure)
    monkeypatch.setattr(engine._phase_barrier, "_check_wait", check_wait)
    try:
        engine._launch(cid, engine._run_task)
        assert context_waiting.wait(2), (
            "Post-baseline context did not wait for measurement"
        )
        assert len(baseline_calls) == protocol.repetitions
        assert engine.store.get(cid)["baseline_summary"]["valid"]
        if outcome == "cancel":
            engine.stop(cid)
        engine._threads[cid].join(2)
        assert not engine._threads[cid].is_alive()
        assert engine._phase_barrier.measuring
        assert competing_thread.is_alive()
        assert not release_measurement.is_set()
        final = engine.store.get(cid)
        assert final["active_started_at"] is None
        assert final["scheduler"]["phase"] == "finished"
        assert final["scheduler"]["active_tasks"] == 0
        assert final["state"] == ("stopped" if outcome == "cancel" else "reviewable")
        if outcome == "deadline":
            assert final["termination_reason"] == "time_limit"
    finally:
        release_measurement.set()
        if competing_thread:
            competing_thread.join(2)
            assert not competing_thread.is_alive()
        if engine._threads.get(cid) and engine._threads[cid].is_alive():
            engine._cancellations[cid].set()
            engine._threads[cid].join(5)


def test_research_wave_blocks_cross_campaign_measurement_until_all_workers_leave(
    campaign, monkeypatch
):
    engine, cid, _, _ = campaign
    proposal_campaign(engine, cid)
    entered = threading.Barrier(3)
    release = threading.Event()
    measurement_entered = threading.Event()

    def provider(*args):
        entered.wait(5)
        assert release.wait(5)
        return {"hypothesis": "independent", "patch": PATCH}

    monkeypatch.setattr(engine, "_propose", provider)
    with ThreadPoolExecutor(max_workers=2) as pool:
        proposals = pool.submit(
            engine._proposal_wave, cid, threading.Event(), time.monotonic() + 15
        )
        entered.wait(5)

        def measure():
            with engine._phase_barrier.measurement():
                measurement_entered.set()

        measurement = pool.submit(measure)
        assert not measurement_entered.wait(0.1)
        release.set()
        assert len(proposals.result(5)) == 2
        measurement.result(5)
    assert measurement_entered.is_set()


def test_recovery_marks_active_scheduler_tasks_unknown_and_retains_slots(campaign):
    engine, cid, _, _ = campaign
    proposal_campaign(engine, cid)
    pending = propose(engine, cid)
    engine.store.update_experiment(
        cid, pending["id"], {"status": "running", "phase": "preparing"}
    )
    engine.store.reserve(cid, "unknown-parallel-call", 10)
    engine._scheduler(
        cid,
        phase="proposing",
        task={
            "id": "interrupted",
            "kind": "proposal",
            "status": "running",
            "started_at": "2026-10-11T00:00:00+00:00",
        },
    )
    engine.store.update(
        cid,
        {
            "runner": {"kind": "docker"},
            "resource_lease_expires_at": (
                datetime.now(timezone.utc) - timedelta(seconds=1)
            ).isoformat(),
            "resource_lease_recoverable": True,
        },
    )
    engine.close()
    recovered = Engine(engine.root)
    try:
        assert (
            recovered.detail(cid)["campaign"]["scheduler"]["tasks"][0]["status"]
            == "unknown"
        )
        assert recovered.store.experiment(cid, pending["id"])["status"] == "unknown"
        recovered.reconcile(cid)
        assert recovered.store.budget(cid)["engaged"] == 10
        assert len(recovered.store.experiments(cid)) == 1
        assert (
            recovered.detail(cid)["campaign"]["scheduler"]["tasks"][0]["status"]
            == "cancelled"
        )
    finally:
        recovered.close()


def test_unfinished_unrecoverable_lease_cannot_be_overwritten(campaign):
    engine, cid, _, _ = campaign
    engine.store.update(cid, {"state": "exploring"})
    engine._admit_resource_lease(cid, "candidate_preparation", 30, recoverable=False)
    expiry = engine.store.get(cid)["resource_lease_expires_at"]
    engine._admit_resource_lease(cid, "provider", 1, recoverable=True)
    campaign = engine.store.get(cid)
    assert campaign["resource_lease_expires_at"] >= expiry
    assert campaign["resource_lease_recoverable"] is False
