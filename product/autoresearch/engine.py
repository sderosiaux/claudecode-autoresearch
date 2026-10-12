"""Single-coordinator supervisor. Agents propose; this engine admits and verifies."""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from functools import wraps
from inspect import signature

from .models import Approval, CampaignCreate, ExperimentCreate, Protocol, digest
from .store import Store, Conflict, BudgetExceeded, now
from .scenario import RESOURCE_CLEANUP_GRACE_SECONDS

LEGACY_CONTEXT_WARNING = (
    "Protocol and revisions verified from recorded runs; matching recorded execution "
    "environments. Requested runner configuration identity was not recorded."
)
_git_limits = threading.local()


class PhaseBarrier:
    """Parallel controlled research, or one exclusive measurement, across campaigns.

    Never acquire this barrier while holding Engine._lock. Readers may join an
    existing research phase (including a wave's child threads); each admitted
    operation is bounded and a measurement waits for all readers to finish.
    """

    def __init__(self):
        self.condition = threading.Condition()
        self.readers = 0
        self.measuring = False
        self.local = threading.local()

    @contextmanager
    def research(self, cancel=None, deadline=None, *, wait=True):
        nested = getattr(self.local, "research", 0)
        if not nested:
            with self.condition:
                while self.measuring:
                    if not wait:
                        raise Conflict(
                            "Another campaign is measuring. No work was queued. "
                            "Retry after it finishes."
                        )
                    self._check_wait(cancel, deadline)
                    self.condition.wait(0.1)
                self.readers += 1
        self.local.research = nested + 1
        try:
            yield
        finally:
            self.local.research -= 1
            if not nested:
                with self.condition:
                    self.readers -= 1
                    self.condition.notify_all()

    @contextmanager
    def measurement(self, cancel=None, deadline=None):
        with self.condition:
            while self.measuring or self.readers:
                self._check_wait(cancel, deadline)
                self.condition.wait(0.1)
            self.measuring = True
        try:
            yield
        finally:
            with self.condition:
                self.measuring = False
                self.condition.notify_all()

    @staticmethod
    def _check_wait(cancel, deadline):
        if cancel is not None and cancel.is_set():
            raise InterruptedError("Phase admission cancelled")
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError(
                "Deadline exhausted while waiting for exclusive measurement phase"
            )


def research_work(method=None, *, wait=True):
    if method is None:
        return lambda method: research_work(method, wait=wait)
    parameters = signature(method)

    @wraps(method)
    def guarded(self, *args, **kwargs):
        bound = parameters.bind(self, *args, **kwargs).arguments
        with self._phase_barrier.research(
            bound.get("cancel"), bound.get("deadline"), wait=wait
        ):
            return method(self, *args, **kwargs)

    return guarded


def git(repository: Path | str, *args: str) -> str:
    deadline = getattr(_git_limits, "deadline", None)
    remaining = 30 if deadline is None else min(30, deadline - time.monotonic())
    if remaining <= 0:
        raise TimeoutError("Candidate preparation task deadline exhausted")
    env = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "Autoresearch",
        "GIT_AUTHOR_EMAIL": "local@autoresearch.invalid",
        "GIT_COMMITTER_NAME": "Autoresearch",
        "GIT_COMMITTER_EMAIL": "local@autoresearch.invalid",
    }
    result = subprocess.run(
        ["git", "-c", "core.hooksPath=" + os.devnull, "-C", str(repository), *args],
        capture_output=True,
        text=True,
        timeout=remaining,
        env=env,
    )
    if result.returncode:
        raise ValueError(result.stderr.strip()[:2000] or "Git operation failed")
    return result.stdout.strip()


class Engine:
    def __init__(self, data_dir: Path | str):
        self.root = Path(data_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.root / "state.sqlite")
        self._lock = threading.RLock()
        self._measurement_lock = threading.Lock()
        self._phase_barrier = PhaseBarrier()
        self._local = threading.local()
        self._threads: dict[str, threading.Thread] = {}
        self._cancellations: dict[str, threading.Event] = {}
        self._provider_resources = {}
        # One process owns this directory. Admission after a crash requires an
        # explicit resume; no unknown model call is retried automatically.
        import fcntl

        self._lease = (self.root / "coordinator.lock").open("a+")
        try:
            fcntl.flock(self._lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Conflict("A coordinator already owns this data directory")
        for campaign in self.store.campaigns():
            if campaign["state"] in {
                "assessing",
                "preparing",
                "baselining",
                "exploring",
                "confirming",
                "stopping",
            } or campaign.get("active_started_at"):
                elapsed = (
                    max(
                        0,
                        (
                            datetime.now(timezone.utc)
                            - datetime.fromisoformat(campaign["active_started_at"])
                        ).total_seconds(),
                    )
                    if campaign.get("active_started_at")
                    else 0
                )
                self.store.update(
                    campaign["id"],
                    {
                        "state": "blocked",
                        "outcome": "incomplete",
                        "recovery_required": True,
                        "recovery_detected_at": now(),
                        "recovery_previous_state": campaign["state"],
                        "active_started_at": None,
                        "active_seconds": campaign.get("active_seconds", 0) + elapsed,
                    },
                    event="recovery.required",
                )
                for exp in self.store.experiments(campaign["id"]):
                    if exp["status"] == "running":
                        self.store.update_experiment(
                            campaign["id"],
                            exp["id"],
                            {"status": "unknown", "verdict": "execution_failed"},
                        )
                scheduler = campaign.get("scheduler") or self._scheduler_default(
                    campaign
                )
                for task in scheduler["tasks"]:
                    if task["status"] in {"queued", "running"}:
                        task["status"] = "unknown"
                scheduler.update(phase="unknown", active_tasks=0)
                self.store.update(
                    campaign["id"],
                    {"scheduler": scheduler},
                    event="scheduler.recovered",
                )

    def close(self):
        for event in self._cancellations.values():
            event.set()
        for thread in list(self._threads.values()):
            thread.join(timeout=5)
        if not any(t.is_alive() for t in self._threads.values()):
            self._lease.close()

    def capabilities(self):
        from .provider import ClaudeProvider
        from .claude_code import ClaudeCodeProvider

        return {
            "provider": ClaudeProvider({}, self.store).capabilities(),
            "claude_code": ClaudeCodeProvider({}, self.store).capabilities(),
            "runners": {"docker": bool(shutil.which("docker")), "host_trusted": True},
            "version": "0.1.0.dev0",
            "release_ready": False,
            "limitations": [
                "Strict Claude billing is not verified and is disabled.",
                "Host runner is trusted development only.",
            ],
        }

    @research_work
    def create(self, spec: CampaignCreate):
        repository = Path(spec.repository).expanduser().resolve()
        if not repository.is_dir():
            raise ValueError("Repository directory does not exist on the supervisor")
        revision = git(repository, "rev-parse", "--verify", spec.base_ref + "^{commit}")
        payload = spec.model_dump(mode="json")
        from .strategies import plan

        payload.update(
            repository=str(repository),
            base_revision=revision,
            protocol_hash=digest(payload["protocol"]) if payload["protocol"] else None,
            approved_hash=None,
            readiness=None,
        )
        payload["strategy_plan"] = plan(spec.goal, payload["protocol"])
        payload["scheduler"] = self._scheduler_default(payload)
        limit = int(spec.budget_usd * 1_000_000)
        result = self.store.create(payload, limit, int(limit * spec.protected_fraction))
        (self.root / result["id"]).mkdir()
        return self.detail(result["id"])

    def detail(self, cid):
        campaign = self.store.get(cid)
        campaign.setdefault("scheduler", self._scheduler_default(campaign))
        exps = self.store.experiments(cid)
        campaign["scheduler"]["waiting_for_measurement"] = sum(
            e["status"] == "queued" and e.get("phase") == "waiting_for_measurement"
            for e in exps
        )
        legacy_baseline = not campaign.get(
            "baseline_context"
        ) and self._recorded_runs_match(
            campaign,
            [
                (
                    campaign.get("baseline"),
                    self._measurement_context(campaign)["baseline_revision"],
                    (campaign.get("protocol") or {}).get("repetitions", 1),
                )
            ],
        )
        if campaign.get("baseline_context") != self._measurement_context(campaign):
            if legacy_baseline and campaign.get("baseline_summary"):
                campaign["baseline_summary"] = {
                    **campaign["baseline_summary"],
                    "protocol_hash": campaign["protocol_hash"],
                    "measurement_context_source": "legacy_recorded_runs",
                    "measurement_context_warning": LEGACY_CONTEXT_WARNING,
                }
            else:
                campaign["baseline"] = None
                campaign["baseline_summary"] = None
        comparison = []
        for experiment in exps:
            if not experiment.get("comparison"):
                continue
            if self._current_evidence(campaign, experiment):
                comparison.append(experiment)
            elif not experiment.get("evaluation_context") and self._legacy_evidence(
                campaign, experiment
            ):
                comparison.append(
                    {
                        **experiment,
                        "measurement_context_source": "legacy_recorded_runs",
                        "measurement_context_warning": LEGACY_CONTEXT_WARNING,
                    }
                )
        result = {
            "campaign": campaign,
            "protocol": campaign.get("protocol"),
            "protocol_hash": campaign.get("protocol_hash"),
            "readiness": campaign.get("readiness"),
            "budget": self.store.budget(cid),
            "experiments": exps,
            "events": self.store.events(cid),
            "comparison": comparison,
        }
        from .insights import build_insights

        result["insights"] = build_insights(result)
        return result

    @staticmethod
    def _scheduler_default(campaign):
        return {
            "phase": "idle",
            "max_parallel_tasks": campaign.get("limits", {}).get(
                "max_parallel_tasks", 1
            ),
            "measurement_concurrency": 1,
            "active_tasks": 0,
            "waiting_for_measurement": 0,
            "tasks": [],
        }

    def _scheduler(self, cid, *, phase=None, task=None):
        with self._lock:
            campaign = self.store.get(cid)
            scheduler = campaign.get("scheduler") or self._scheduler_default(campaign)
            scheduler["max_parallel_tasks"] = campaign["limits"].get(
                "max_parallel_tasks", 1
            )
            if phase is not None:
                scheduler["phase"] = phase
            if task:
                tasks = scheduler["tasks"]
                previous = next((t for t in tasks if t["id"] == task["id"]), None)
                if previous is None:
                    tasks.append(task)
                else:
                    previous.update(task)
            scheduler["active_tasks"] = sum(
                t["status"] == "running" for t in scheduler["tasks"]
            )
            scheduler["waiting_for_measurement"] = sum(
                e["status"] == "queued" and e.get("phase") == "waiting_for_measurement"
                for e in self.store.experiments(cid)
            )
            self.store.update(cid, {"scheduler": scheduler}, event="scheduler.updated")
            return scheduler

    @contextmanager
    def _scheduled_task(self, cid, kind, *, experiment_id=None, strategy_id=None):
        task = {
            "id": uuid.uuid4().hex,
            "kind": kind,
            "status": "running",
            "started_at": now(),
        }
        if experiment_id:
            task["experiment_id"] = experiment_id
        if strategy_id:
            task["strategy_id"] = strategy_id
        self._scheduler(cid, task=task)
        try:
            yield task
        except BaseException as exc:
            self._scheduler(
                cid,
                task={
                    "id": task["id"],
                    "status": "cancelled"
                    if isinstance(exc, InterruptedError)
                    else "failed",
                    "finished_at": now(),
                },
            )
            raise
        else:
            self._scheduler(
                cid,
                task={"id": task["id"], "status": "succeeded", "finished_at": now()},
            )

    def _parallel_wave(self, cid, jobs, operation, cancel):
        """Join every worker before returning, including failures and cancellation."""
        results, errors = [], []

        def invoke(job):
            self._local.parallel_worker = True
            try:
                return operation(job)
            finally:
                self._local.parallel_worker = False

        with ThreadPoolExecutor(
            max_workers=len(jobs), thread_name_prefix="research-" + cid[:8]
        ) as pool:
            futures = [pool.submit(invoke, job) for job in jobs]
            for future in futures:
                try:
                    result = future.result()
                    if result is not None:
                        results.append(result)
                except BaseException as exc:
                    errors.append(exc)
                    cancel.set()
        if errors:
            from .provider import ProviderContractViolation

            raise next(
                (e for e in errors if isinstance(e, ProviderContractViolation)),
                errors[0],
            )
        return results

    @research_work
    def _proposal_wave(self, cid, cancel, deadline):
        if not self._continue(cid, cancel, deadline):
            return []
        context = self._context(cid, "propose optimization")
        with self._lock:
            campaign = self.store.get(cid)
            existing = self.store.experiments(cid)
            count = min(
                campaign["limits"].get("max_parallel_tasks", 1),
                campaign["limits"]["max_experiments"] - len(existing),
            )
            strategies = campaign.get("strategy_plan") or [
                {"id": "general", "title": "Measured hot path"}
            ]
            jobs = []
            for index in range(count):
                strategy = strategies[(len(existing) + index) % len(strategies)]
                # Reserve durable attempt capacity before paid admission. An
                # unanswered proposal is an attempt, never an inferred hypothesis.
                attempt = self.store.add_experiment(
                    cid,
                    {
                        "hypothesis": "Proposal pending: " + strategy["title"],
                        "strategy": strategy["id"],
                        "strategy_id": strategy["id"],
                        "patch": "",
                        "parent_id": None,
                        "protocol_hash": campaign["protocol_hash"],
                        "phase": "preparing",
                        "proposal_pending": True,
                        "origin": "claude_code"
                        if campaign["agent"].get("kind") == "claude_code"
                        else "claude_provider",
                    },
                )
                self.store.update_experiment(cid, attempt["id"], {"status": "running"})
                jobs.append((attempt, strategy))
        if not jobs:
            return []
        self._scheduler(cid, phase="proposing")

        def propose(job):
            attempt, strategy = job
            eid = attempt["id"]
            try:
                with self._scheduled_task(
                    cid, "proposal", experiment_id=eid, strategy_id=strategy["id"]
                ):
                    if not self._continue(cid, cancel, deadline):
                        raise InterruptedError("Proposal admission revoked")
                    self._local.proposal_context = {
                        **context,
                        "strategy_focus": strategy,
                        "instructions": context["instructions"]
                        + " Focus this independent proposal on the assigned strategy. Check its preconditions and state a falsifier; do not assume it is a bottleneck.",
                    }
                    proposal = self._propose(
                        cid, "propose optimization: " + strategy["id"], cancel, deadline
                    )
                    if not proposal.get("patch"):
                        return self.store.update_experiment(
                            cid,
                            eid,
                            {
                                **{
                                    field: proposal[field]
                                    for field in (
                                        "hypothesis",
                                        "strategy",
                                        "expected_effect",
                                    )
                                    if isinstance(proposal.get(field), str)
                                },
                                "status": "cancelled",
                                "phase": "finished",
                                "verdict": "inconclusive",
                                "proposal_pending": False,
                                "termination_reason": "no_proposal",
                            },
                        )
                    spec = ExperimentCreate.model_validate(
                        {k: v for k, v in proposal.items() if k != "protocol"}
                    )
                    return self.store.update_experiment(
                        cid,
                        eid,
                        {
                            **spec.model_dump(),
                            "origin": "claude_code"
                            if campaign["agent"].get("kind") == "claude_code"
                            else "claude_provider",
                            "status": "queued",
                            "proposal_pending": False,
                            "patch_hash": digest(
                                {
                                    "patch": spec.patch,
                                    "parent": spec.parent_id,
                                    "protocol": campaign["protocol_hash"],
                                }
                            ),
                        },
                    )
            except BaseException as exc:
                self.store.update_experiment(
                    cid,
                    eid,
                    {
                        "status": "cancelled"
                        if isinstance(exc, InterruptedError)
                        else "failed",
                        "phase": "finished",
                        "verdict": "inconclusive",
                        "error": str(exc)[:3000],
                    },
                )
                from .provider import ProviderContractViolation

                if isinstance(exc, ProviderContractViolation):
                    self.store.update(
                        cid,
                        {"budget_incident": True},
                        event="provider.contract_violation",
                    )
                cancel.set()
                raise
            finally:
                self._local.proposal_context = None

        return [
            e
            for e in self._parallel_wave(cid, jobs, propose, cancel)
            if e["status"] == "queued"
        ]

    @research_work
    def _preparation_wave(
        self, cid, queued, baseline_ws, protocol, evaluation_context, cancel, deadline
    ):
        self._scheduler(cid, phase="preparing")
        # Git preparation has coordinator timeouts, but no independent orphan
        # watchdog. A crash within this phase requires verified manual cleanup.
        self._admit_resource_lease(
            cid,
            "candidate_preparation",
            max(0.1, deadline - time.monotonic()),
            recoverable=False,
        )

        def prepare(exp):
            eid = exp["id"]
            task_deadline = min(
                deadline,
                time.monotonic() + self.store.get(cid)["limits"]["max_task_seconds"],
            )
            _git_limits.deadline = task_deadline
            try:
                with self._scheduled_task(
                    cid,
                    "candidate_preparation",
                    experiment_id=eid,
                    strategy_id=exp.get("strategy_id"),
                ):
                    return self._prepare_candidate(
                        cid,
                        exp,
                        baseline_ws,
                        protocol,
                        evaluation_context,
                        cancel,
                        task_deadline,
                    )
            except InterruptedError:
                # Unadmitted/prepared attempts remain queued for explicit resume.
                self.store.update_experiment(cid, eid, {"status": "queued"})
                return None
            except Exception as exc:
                self.store.update_experiment(
                    cid,
                    eid,
                    {
                        "status": "failed",
                        "phase": "finished",
                        "verdict": "execution_failed",
                        "error": str(exc)[:3000],
                    },
                )
                return None
            finally:
                _git_limits.deadline = None

        results = self._parallel_wave(cid, queued, prepare, cancel)
        self.store.update(
            cid, {"resource_lease_completed": True}, event="preparation.wave_finished"
        )
        self._scheduler(cid, phase="waiting_for_measurement")
        return results

    def _prepare_candidate(
        self, cid, exp, baseline_ws, protocol, evaluation_context, cancel, deadline
    ):
        if not self._continue(cid, cancel, deadline):
            raise InterruptedError("Candidate preparation admission revoked")
        eid = exp["id"]
        self.store.update_experiment(
            cid, eid, {"status": "running", "phase": "preparing"}
        )
        if exp["protocol_hash"] != evaluation_context["protocol_hash"]:
            raise Conflict(
                "Experiment belongs to a different protocol; propose it again explicitly"
            )
        parent = (
            self.store.experiment(cid, exp["parent_id"])
            if exp.get("parent_id")
            else None
        )
        if parent and parent.get("protocol_hash") != exp["protocol_hash"]:
            raise Conflict("Parent belongs to a different approved protocol")
        if exp.get("revision") and exp.get("workspace"):
            workspace = Path(exp["workspace"])
            revision = git(workspace, "rev-parse", "HEAD")
            if revision != exp["revision"] or git(workspace, "status", "--porcelain"):
                raise Conflict("Prepared candidate workspace changed")
        else:
            workspace = self._checkout(
                cid,
                eid,
                revision=parent["revision"] if parent else None,
                source=parent.get("workspace") if parent else None,
            )
            if not self._continue(cid, cancel, deadline):
                raise InterruptedError("Candidate patch admission revoked")
            revision = self._apply(workspace, exp["patch"], protocol["protected_paths"])
        for protected in protocol["protected_paths"]:
            reference = git(baseline_ws, "ls-tree", "-r", "HEAD", "--", protected)
            candidate = git(workspace, "ls-tree", "-r", "HEAD", "--", protected)
            if reference != candidate:
                raise Conflict(
                    "Candidate evaluator differs from approved baseline: " + protected
                )
        tree = git(workspace, "rev-parse", "HEAD^{tree}")
        context_hash = digest(evaluation_context)
        return self.store.update_experiment(
            cid,
            eid,
            {
                "revision": revision,
                "tree": tree,
                "workspace": str(workspace),
                "evaluation_context": evaluation_context,
                "evaluation_context_hash": context_hash,
                "program_identity": digest(
                    {"tree": tree, "evaluation_context_hash": context_hash}
                ),
                "phase": "waiting_for_measurement",
                "status": "queued",
            },
        )

    @staticmethod
    def _recorded_runs_match(campaign, groups):
        """Read-only legacy display proof; not proof of requested runner config."""
        fingerprints = set()
        for samples, revision, minimum in groups:
            if not revision or not samples or len(samples) < minimum:
                return False
            for sample in samples:
                execution = sample.get("execution") or {}
                fingerprint = sample.get("environment_fingerprint")
                if (
                    execution.get("protocol_hash") != campaign.get("protocol_hash")
                    or execution.get("revision") != revision
                    or not isinstance(fingerprint, str)
                    or not fingerprint
                ):
                    return False
                fingerprints.add(fingerprint)
        return len(fingerprints) == 1

    def _legacy_evidence(self, campaign, experiment):
        if experiment.get("protocol_hash") != campaign.get("protocol_hash"):
            return False
        protocol = campaign.get("protocol") or {}
        revision = self._measurement_context(campaign)["baseline_revision"]
        n = protocol.get("repetitions", 1)
        groups = [
            (campaign.get("baseline"), revision, n),
            (experiment.get("reference_runs"), revision, n),
            (experiment.get("runs"), experiment.get("revision"), n),
        ]
        if experiment.get("confirmation") or experiment.get("verdict") == "confirmed":
            n = protocol.get("confirmation_repetitions", 1)
            groups.extend(
                [
                    (experiment.get("confirmation_reference_runs"), revision, n),
                    (
                        experiment.get("confirmation_runs"),
                        experiment.get("revision"),
                        n,
                    ),
                ]
            )
        return self._recorded_runs_match(campaign, groups)

    @staticmethod
    def _measurement_context(campaign):
        return {
            "protocol_hash": campaign.get("protocol_hash"),
            "baseline_revision": campaign.get("prepared_revision")
            or campaign["base_revision"],
            "runner_config_hash": digest(campaign["runner"]),
        }

    def _current_evidence(self, campaign, experiment):
        context = experiment.get("evaluation_context") or {}
        return experiment.get("protocol_hash") == campaign.get("protocol_hash") and all(
            context.get(key) == value
            for key, value in self._measurement_context(campaign).items()
        )

    def _idle(self, cid):
        if self._threads.get(cid) and self._threads[cid].is_alive():
            raise Conflict("Campaign already has an active task")
        campaign = self.store.get(cid)
        if campaign.get("budget_incident"):
            raise Conflict("Campaign has an unresolved budget incident")
        if campaign.get("recovery_required"):
            raise Conflict(
                "Explicit reconcile is required before admitting work after recovery"
            )

    def _admit_resource_lease(self, cid, kind, timeout_seconds, *, recoverable=True):
        """Durably record the latest possible resource lifetime before spawning.

        Recovery anywhere blocks resource admission: an orphan benchmark could
        contaminate another campaign even though it has a different budget.
        """
        with self._lock:
            self._resource_recovery_gate()
            campaign = self.store.get(cid)
            if campaign.get("budget_incident") or campaign["state"] in {
                "paused",
                "stopping",
                "stopped",
                "failed",
                "blocked",
                "reviewable",
            }:
                raise Conflict("Resource admission revoked by campaign state")
            admitted = datetime.now(timezone.utc)
            expires = admitted + timedelta(
                seconds=timeout_seconds + RESOURCE_CLEANUP_GRACE_SECONDS
            )
            previous = campaign.get("resource_lease_expires_at")
            if previous and not campaign.get("resource_lease_completed"):
                expires = max(expires, datetime.fromisoformat(previous))
                recoverable = recoverable and campaign.get(
                    "resource_lease_recoverable", False
                )
            self.store.update(
                cid,
                {
                    "resource_lease_expires_at": expires.isoformat(),
                    "resource_lease_admitted_at": admitted.isoformat(),
                    "resource_lease_kind": kind,
                    "resource_lease_recoverable": recoverable,
                    "resource_lease_completed": False,
                    "resource_lease_cleanup_grace_seconds": RESOURCE_CLEANUP_GRACE_SECONDS,
                },
                event="resource.admitted",
            )

    def _resource_recovery_gate(self):
        if any(c.get("recovery_required") for c in self.store.campaigns()):
            raise Conflict(
                "Resource admission blocked until recovered campaigns are explicitly reconciled"
            )

    def _admit_provider_resource(self, cid, key, timeout):
        with self._lock:
            previous = self.store.get(cid)
            self._admit_resource_lease(cid, "provider", timeout)
            group = self._provider_resources.setdefault(
                cid,
                {
                    "pending": set(),
                    "unknown": bool(
                        previous.get("resource_lease_expires_at")
                        and not previous.get("resource_lease_completed")
                    ),
                },
            )
            group["pending"].add(key)
            group["admitted_at"] = self.store.get(cid)["resource_lease_admitted_at"]

    def _finish_provider_resource(self, cid, key, confirmed):
        with self._lock:
            group = self._provider_resources[cid]
            group["pending"].remove(key)
            group["unknown"] |= not confirmed
            if group["pending"]:
                return
            del self._provider_resources[cid]
            campaign = self.store.get(cid)
            if (
                not group["unknown"]
                and not campaign.get("recovery_required")
                and campaign.get("resource_lease_kind") == "provider"
                and campaign.get("resource_lease_admitted_at") == group["admitted_at"]
            ):
                # This acknowledges local processes only. Remote usage and all
                # monetary reservations retain their independent ledger state.
                self.store.update(
                    cid, {"resource_lease_completed": True}, event="resource.released"
                )

    @staticmethod
    def _provider_processes_returned(result, request):
        def normal_return(value):
            return (
                isinstance(value, dict)
                and value.get("status") in {"succeeded", "failed"}
                and type(value.get("returncode")) is int
                and value["returncode"] >= 0
            )

        if not normal_return(result):
            return False
        if request["config"].get("kind") != "claude_code":
            return True
        # Native CLI owns a separate process group. A reaped provider worker
        # alone cannot establish that its child returned. Its unique session
        # receipt is written only after synchronous native _execute returns.
        try:
            path = Path(request["artifact_dir"]) / "metadata.json"
            if path.is_symlink() or path.stat().st_size > 65536:
                return False
            metadata = json.loads(path.read_text())
            return normal_return(metadata)
        except (OSError, ValueError):
            return False

    def _record_runner_cleanup(self, cid, result):
        if result.get("cleanup_warning"):
            self.store.update(
                cid,
                {
                    "recovery_required": True,
                    "state": "blocked",
                    "outcome": "incomplete",
                    "cleanup_warning": result["cleanup_warning"],
                },
                event="resource.cleanup_unconfirmed",
            )
            raise Conflict(
                "Runner cleanup is unconfirmed; resource admission requires reconciliation after its recorded lease"
            )
        self.store.update(
            cid, {"resource_lease_completed": True}, event="resource.released"
        )

    def _execute_runner(self, cid, *args, on_result=None, **kwargs):
        from .runner import run_command

        try:
            result = run_command(*args, **kwargs)
        except Exception as exc:
            # Output persistence can fail after cleanup. No return value means
            # resource ownership is uncertain even if the subprocess exited.
            result = {
                "status": "failed",
                "stdout": "",
                "stderr": str(exc)[:1000],
                "cleanup_warning": "Runner did not return resource-cleanup evidence: "
                + str(exc)[:1000],
            }
        try:
            self._record_runner_cleanup(cid, result)
        finally:
            # Diagnostic history survives a cleanup gate. Record it only after
            # resource safety is durable; never admit comparison evidence here.
            if on_result is not None:
                on_result(result)
        return result

    def _active_update(self, cid, changes, *, event="campaign.updated"):
        """Control commands win races against work admitted just before them."""
        with self._lock:
            if self.store.get(cid)["state"] not in {
                "assessing",
                "preparing",
                "baselining",
                "exploring",
                "confirming",
            }:
                return False
            self.store.update(cid, changes, event=event)
            return True

    @research_work
    def _checkout(self, cid, name, revision=None, source=None):
        c = self.store.get(cid)
        destination = self.root / cid / "workspaces" / name
        wanted = revision or c.get("prepared_revision") or c["base_revision"]
        if destination.exists():
            if git(destination, "rev-parse", "HEAD") != wanted or git(
                destination, "status", "--porcelain"
            ):
                raise Conflict(
                    "Existing workspace has a different revision or uncommitted changes; refusing reuse"
                )
            return destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        repository = source or c.get("prepared_repository") or c["repository"]
        git(
            destination.parent,
            "clone",
            "--local",
            "--no-hardlinks",
            "--no-checkout",
            "--",
            str(repository),
            str(destination),
        )
        git(destination, "checkout", "--detach", wanted)
        return destination

    @research_work
    def assess(self, cid):
        with self._lock:
            self._idle(cid)
            self._resource_recovery_gate()
            c = self.store.get(cid)
            if c["state"] in {"baselining", "exploring", "confirming", "stopping"}:
                raise Conflict("Cannot assess during execution")
            self.store.update(cid, {"state": "assessing"})
            from .jvm import discover_jvm

            findings = []
            # These read-only coordinator probes lack the runner's independent
            # watchdog. A crash during them therefore requires manual cleanup.
            self._admit_resource_lease(
                cid, "coordinator_diagnostic", 10, recoverable=False
            )
            jvm = discover_jvm(Path(c["repository"]))
            if not c.get("protocol"):
                findings.append(
                    {
                        "code": "protocol_missing",
                        "severity": "blocking",
                        "message": "No approved workload or metric definitions are available.",
                        "remedy": "Import a benchmark protocol, or prepare one with Claude before approval.",
                    }
                )
            else:
                names = " ".join(m["name"].lower() for m in c["protocol"]["metrics"])
                if "p99" in c["goal"].lower() and "p99" not in names:
                    findings.append(
                        {
                            "code": "requested_metric_missing",
                            "severity": "blocking",
                            "message": "The goal explicitly requests p99, but the protocol does not measure p99.",
                            "remedy": "Prepare a latency distribution at the required load and approve its metric definition.",
                        }
                    )
                if "rss" in c["goal"].lower() and "rss" not in names:
                    findings.append(
                        {
                            "code": "requested_metric_missing",
                            "severity": "blocking",
                            "message": "The goal explicitly requests RSS, but the protocol does not measure process RSS.",
                            "remedy": "Add process memory measurement; heap or allocations cannot substitute for RSS.",
                        }
                    )
            if c["runner"].get("kind", "docker") == "docker":
                if not shutil.which("docker"):
                    findings.append(
                        {
                            "code": "runner_missing",
                            "severity": "blocking",
                            "message": "Docker is unavailable on the coordinator.",
                            "remedy": "Install Docker or configure a supported remote Docker host.",
                        }
                    )
                else:
                    command = ["docker"]
                    if c["runner"].get("docker_host"):
                        command += ["--host", c["runner"]["docker_host"]]
                    try:
                        probe = subprocess.run(
                            command
                            + [
                                "image",
                                "inspect",
                                c["runner"].get("image", "eclipse-temurin:25-jdk"),
                                *[s["image"] for s in c["runner"].get("services", [])],
                            ],
                            capture_output=True,
                            timeout=5,
                        )
                        if probe.returncode:
                            findings.append(
                                {
                                    "code": "runner_image_missing",
                                    "severity": "blocking",
                                    "message": "The configured Docker daemon, workload image or scenario service image is unavailable.",
                                    "remedy": "Start the daemon and install the configured image; workloads do not pull images automatically.",
                                }
                            )
                    except (OSError, subprocess.TimeoutExpired):
                        findings.append(
                            {
                                "code": "runner_unreachable",
                                "severity": "blocking",
                                "message": "Docker capability probe failed or timed out.",
                            }
                        )
            else:
                findings.append(
                    {
                        "code": "trusted_host",
                        "severity": "warning",
                        "message": "Host commands run as the current user. Isolation guarantees do not apply.",
                    }
                )
            if c["agent"].get("kind") in {"claude", "claude_code"}:
                from .provider import ClaudeProvider
                from .claude_code import ClaudeCodeProvider

                provider_type = (
                    ClaudeCodeProvider
                    if c["agent"].get("kind") == "claude_code"
                    else ClaudeProvider
                )
                caps = provider_type(c["agent"], self.store).capabilities()
                if (
                    c["agent"].get("enforcement", "strict") == "strict"
                    and not caps["strict_budget"]
                ):
                    findings.append(
                        {
                            "code": "strict_provider_unavailable",
                            "severity": "blocking",
                            "message": caps["reason"],
                            "remedy": "Keep manual experiments or explicitly choose estimated accounting; no silent fallback.",
                        }
                    )
            blocked = any(f["severity"] == "blocking" for f in findings)
            status = (
                "needs_preparation"
                if any(
                    f["code"] in {"protocol_missing", "requested_metric_missing"}
                    for f in findings
                )
                else "blocked_external"
                if blocked
                else "ready"
            )
            readiness = {
                "status": status,
                "findings": findings,
                "jvm": jvm,
                "assessed_at": now(),
                "coverage": "Static prerequisites only; baseline execution validates actual measurement.",
            }
            self.store.update(
                cid,
                {
                    "state": "awaiting_approval" if not blocked else "blocked",
                    "readiness": readiness,
                    "resource_lease_completed": True,
                },
                event="readiness.assessed",
            )
            return self.detail(cid)

    def set_protocol(self, cid, protocol: Protocol):
        with self._lock:
            self._idle(cid)
            payload = protocol.model_dump(mode="json")
            self.store.update(
                cid,
                {
                    "protocol": payload,
                    "protocol_hash": digest(payload),
                    "approved_hash": None,
                    "approval": None,
                    "state": "draft",
                    "baseline": None,
                    "baseline_summary": None,
                    "baseline_context": None,
                    "readiness": None,
                },
                event="protocol.proposed",
            )
            return self.detail(cid)

    def approve(self, cid, protocol_hash, *, authority="operator", rationale=""):
        approval = Approval(
            protocol_hash=protocol_hash, authority=authority, rationale=rationale
        )
        with self._lock:
            self._idle(cid)
            c = self.store.get(cid)
            if not c.get("protocol") or protocol_hash != c["protocol_hash"]:
                raise Conflict("Approve the current protocol hash")
            self.store.update(
                cid,
                {
                    "approved_hash": protocol_hash,
                    "state": "awaiting_approval",
                    "approved_at": now(),
                    "approval": {**approval.model_dump(), "recorded_at": now()},
                },
                event="protocol.approved",
            )
            return self.detail(cid)

    @research_work
    def _context(self, cid, mode):
        c = self.store.get(cid)
        workspace = self._checkout(
            cid,
            "source-context-" + (c.get("prepared_revision") or c["base_revision"])[:12],
        )
        files = {}
        # Explicitly bounded source selection. Never read untracked files or env files.
        names = (
            []
            if c["agent"].get("kind") == "claude_code"
            else git(workspace, "ls-files").splitlines()
        )
        for name in names:
            path = workspace / name
            if (
                path.suffix not in {".java", ".xml", ".gradle", ".kt", ".md"}
                or path.is_symlink()
            ):
                continue
            if len(files) >= 30:
                break
            if path.stat().st_size > 16000:
                continue
            files[name] = path.read_text(errors="replace")
            if sum(len(v.encode()) for v in files.values()) > 200000:
                break
        return {
            "task": mode,
            "goal": c["goal"],
            "protocol": c.get("protocol"),
            "source_files": files,
            "baseline_summary": c.get("baseline_summary"),
            "diagnostics": c.get("diagnostics", []),
            "strategy_plan": c.get("strategy_plan", []),
            "history": [
                {
                    "hypothesis": e["hypothesis"],
                    "verdict": e.get("verdict"),
                    "comparison": e.get("comparison"),
                    "duplicate_of": e.get("duplicate_of"),
                    "duplicate_reason": e.get("duplicate_reason"),
                }
                for e in self.store.experiments(cid)[-12:]
            ],
            "instructions": "Return one independently testable patch. Preserve required behavior and protected benchmark files. A preparation proposal must include a complete protocol.",
        }

    @research_work
    def prepare(self, cid):
        with self._lock:
            self._idle(cid)
            self._resource_recovery_gate()
            c = self.store.get(cid)
            if c.get("active_seconds", 0) >= c["limits"]["max_active_seconds"]:
                raise Conflict("Campaign duration exhausted")
            if c["agent"].get("kind") not in {"claude", "claude_code"}:
                self.store.event(
                    cid,
                    "preparation.requested",
                    {
                        "message": "Import a protocol with its benchmark files, then assess and approve. Automatic preparation requires an explicitly configured Claude connector."
                    },
                )
                return self.assess(cid)
            if (
                c.get("preparation_attempts", 0)
                >= c["limits"]["max_preparation_attempts"]
            ):
                raise Conflict("Preparation attempt limit reached")
            self.store.update(
                cid,
                {
                    "state": "preparing",
                    "preparation_attempts": c.get("preparation_attempts", 0) + 1,
                },
            )
            self._launch(cid, self._prepare_task)
            return self.detail(cid)

    @research_work
    def _prepare_task(self, cid, cancel):
        c = self.store.get(cid)
        deadline = (
            time.monotonic()
            + c["limits"]["max_active_seconds"]
            - c.get("active_seconds", 0)
        )
        proposal = self._propose(cid, "prepare measurement", cancel, deadline)
        protocol = Protocol.model_validate(proposal.get("protocol"))
        workspace = self._checkout(cid, "preparation-" + str(c["preparation_attempts"]))
        if proposal.get("patch"):
            self._apply(workspace, proposal["patch"], [])
        revision = git(workspace, "rev-parse", "HEAD")
        if cancel.is_set():
            return
        self._active_update(
            cid,
            {
                "prepared_repository": str(workspace),
                "prepared_revision": revision,
                "protocol": protocol.model_dump(mode="json"),
                "protocol_hash": digest(protocol.model_dump(mode="json")),
                "approved_hash": None,
                "approval": None,
                "baseline": None,
                "baseline_summary": None,
                "baseline_context": None,
                "readiness": None,
                "state": "awaiting_approval",
                "preparation": proposal,
                "outcome": "preparation_only",
            },
            event="preparation.proposed",
        )

    def add_experiment(self, cid, spec: ExperimentCreate):
        with self._lock:
            c = self.store.get(cid)
            if len(self.store.experiments(cid)) >= c["limits"]["max_experiments"]:
                raise Conflict("Experiment limit reached")
            if spec.parent_id:
                parent = self.store.experiment(cid, spec.parent_id)
                if not parent.get("revision"):
                    raise Conflict("Parent has no reproducible code revision")
            patch_hash = digest(
                {
                    "patch": spec.patch,
                    "parent": spec.parent_id,
                    "protocol": c.get("protocol_hash"),
                }
            )
            # Text-identical proposals can follow failed/unknown executions or
            # changed baseline evidence. Preserve attempts; suppress repeated
            # measurements only after resolving their actual program tree.
            return self.store.add_experiment(
                cid,
                {
                    **spec.model_dump(),
                    "patch_hash": patch_hash,
                    "protocol_hash": c.get("protocol_hash"),
                },
            )

    @research_work(wait=False)
    def run(self, cid):
        with self._lock:
            self._idle(cid)
            self._resource_recovery_gate()
            c = self.store.get(cid)
            if c.get("recovery_required"):
                raise Conflict("Explicit resume is required after coordinator recovery")
            if (
                not c.get("protocol_hash")
                or c.get("approved_hash") != c["protocol_hash"]
            ):
                raise Conflict("Current protocol requires human approval")
            if c.get("active_seconds", 0) >= c["limits"]["max_active_seconds"]:
                raise Conflict("Campaign active duration exhausted")
            if c.get("budget_incident"):
                raise Conflict("Budget incident requires investigation")
            readiness = self.assess(cid)["readiness"]
            if readiness["status"] != "ready":
                raise Conflict("Readiness assessment requires action before execution")
            if c["agent"].get("kind") in {"claude", "claude_code"}:
                if not c["protocol"]["protected_paths"]:
                    raise Conflict(
                        "Autonomous optimization requires protected evaluator paths in the approved protocol"
                    )
                from .provider import ClaudeProvider, StrictBudgetUnavailable
                from .claude_code import ClaudeCodeProvider

                provider_type = (
                    ClaudeCodeProvider
                    if c["agent"].get("kind") == "claude_code"
                    else ClaudeProvider
                )
                provider = provider_type(c["agent"], self.store)
                if (
                    c["agent"].get("enforcement", "strict") == "strict"
                    and not provider.capabilities()["strict_budget"]
                ):
                    raise StrictBudgetUnavailable(provider.capabilities()["reason"])
            self.store.update(
                cid,
                {"state": "baselining", "termination_reason": None, "outcome": None},
                event="campaign.started",
            )
            self._launch(cid, self._run_task)
            return self.detail(cid)

    def profile(self, cid):
        with self._lock:
            self._idle(cid)
            self._resource_recovery_gate()
            c = self.store.get(cid)
            if not c.get("protocol") or c.get("approved_hash") != c["protocol_hash"]:
                raise Conflict(
                    "Approve the diagnostic command as part of the protocol first"
                )
            if not c["protocol"].get("diagnostic_command"):
                raise Conflict("No diagnostic command is defined in this protocol")
            if c.get("active_seconds", 0) >= c["limits"]["max_active_seconds"]:
                raise Conflict("Campaign duration exhausted")
            previous = c["state"]
            self.store.update(cid, {"state": "preparing"}, event="profile.started")

            def task(cid, cancel):
                latest = self.store.get(cid)
                deadline = (
                    time.monotonic()
                    + latest["limits"]["max_active_seconds"]
                    - latest.get("active_seconds", 0)
                )
                with self._phase_barrier.research(cancel, deadline):
                    workspace = self._checkout(
                        cid,
                        "profile-"
                        + (latest.get("prepared_revision") or latest["base_revision"])[
                            :12
                        ],
                    )
                label = "profile-" + uuid.uuid4().hex

                def record_diagnostic(result):
                    diagnostic = {
                        "label": label,
                        "status": result["status"],
                        "stdout": result.get("stdout", "")[:8000],
                        "stderr": result.get("stderr", "")[:2000],
                        "artifact": f"artifacts/{label}",
                        "included_in_comparison": False,
                        "cleanup_warning": result.get("cleanup_warning"),
                    }
                    self.store.event(cid, "profile.finished", diagnostic)
                    self.store.update(
                        cid,
                        {
                            "diagnostics": [
                                *self.store.get(cid).get("diagnostics", []),
                                diagnostic,
                            ]
                        },
                    )

                self._scheduler(cid, phase="profiling")
                with (
                    self._scheduled_task(cid, "diagnostic"),
                    self._phase_barrier.measurement(cancel, deadline),
                    self._measurement_lock,
                ):
                    if cancel.is_set() or self.store.get(cid)["state"] != "preparing":
                        return
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            "Campaign duration exhausted while waiting for diagnostic runner"
                        )
                    timeout = min(
                        latest["limits"]["max_task_seconds"],
                        deadline - time.monotonic(),
                    )
                    self._admit_resource_lease(
                        cid,
                        "diagnostic",
                        timeout,
                        recoverable=latest["runner"].get(
                            "kind", latest["runner"].get("mode", "docker")
                        )
                        == "docker",
                    )
                    self._execute_runner(
                        cid,
                        latest["protocol"]["diagnostic_command"],
                        workspace,
                        self.root / cid / "artifacts" / label,
                        timeout_seconds=timeout,
                        cancel=cancel,
                        runner={
                            **latest["runner"],
                            "environment": latest["protocol"].get("environment", {}),
                        },
                        on_result=record_diagnostic,
                    )
                self._active_update(cid, {"state": previous})

            self._launch(cid, task)
            return self.detail(cid)

    def _launch(self, cid, target):
        cancel = threading.Event()
        self._cancellations[cid] = cancel
        self.store.update(cid, {"active_started_at": now()}, event="task.admitted")

        def execute():
            from .provider import ProviderContractViolation

            started = time.monotonic()
            try:
                target(cid, cancel)
            except ProviderContractViolation as exc:
                self.store.update(
                    cid,
                    {
                        "state": "failed",
                        "budget_incident": True,
                        "outcome": "incomplete",
                        "termination_reason": "error",
                    },
                    event="provider.contract_violation",
                )
                self.store.event(cid, "task.error", {"message": str(exc)})
            except TimeoutError as exc:
                self._active_update(
                    cid,
                    {
                        "state": "reviewable",
                        "termination_reason": "time_limit",
                        "outcome": "incomplete",
                    },
                    event="campaign.time_limit",
                )
                self.store.event(cid, "task.error", {"message": str(exc)})
            except BudgetExceeded as exc:
                if not self.store.get(cid).get("budget_incident"):
                    self._active_update(
                        cid,
                        {
                            "state": "reviewable",
                            "termination_reason": "budget_exhausted",
                            "outcome": "confirmed_candidates"
                            if any(
                                e.get("verdict") == "confirmed"
                                for e in self.store.experiments(cid)
                            )
                            else "incomplete",
                        },
                        event="campaign.budget_exhausted",
                    )
                self.store.event(cid, "task.error", {"message": str(exc)})
            except Exception as exc:
                self._active_update(
                    cid,
                    {
                        "state": "blocked",
                        "outcome": "incomplete",
                        "termination_reason": "error",
                    },
                    event="campaign.blocked",
                )
                self.store.event(
                    cid,
                    "task.error",
                    {"type": type(exc).__name__, "message": str(exc)[:3000]},
                )
            finally:
                self._scheduler(cid, phase="finished")
                with self._lock:
                    c = self.store.get(cid)
                    updates = {
                        "active_seconds": c.get("active_seconds", 0)
                        + time.monotonic()
                        - started,
                        "active_started_at": None,
                    }
                    if c["state"] == "stopping":
                        updates.update(
                            state="stopped",
                            termination_reason="cancelled",
                            outcome="incomplete",
                        )
                    self.store.update(cid, updates, event="task.finished")

        thread = threading.Thread(target=execute, name="campaign-" + cid, daemon=True)
        self._threads[cid] = thread
        thread.start()

    def pause(self, cid):
        with self._lock:
            self.store.update(
                cid,
                {"state": "paused"},
                expected_states={"baselining", "exploring", "confirming", "preparing"},
                event="campaign.paused",
            )
            return self.detail(cid)

    def stop(self, cid):
        with self._lock:
            if cid in self._cancellations:
                self._cancellations[cid].set()
            active = bool(self._threads.get(cid) and self._threads[cid].is_alive())
            self.store.update(
                cid,
                {
                    "state": "stopping" if active else "stopped",
                    "termination_reason": "cancelled",
                },
                event="campaign.stopped",
            )
            return self.detail(cid)

    @research_work(wait=False)
    def resume(self, cid):
        with self._lock:
            self._idle(cid)
            c = self.store.get(cid)
            if c["state"] not in {"paused", "stopped", "blocked", "reviewable"}:
                raise Conflict("Campaign is not resumable")
            if any(e["status"] == "unknown" for e in self.store.experiments(cid)):
                raise Conflict(
                    "Unknown executions require explicit reconciliation; they are never automatically retried"
                )
            self.store.update(
                cid,
                {"state": "awaiting_approval"},
                event="campaign.resumed",
            )
            return self.run(cid)

    def reconcile(self, cid):
        """Acknowledge interrupted work after its recorded local resource lease.

        This never retries a call, starts a task, refunds money, or asserts that
        remote model generation/billing stopped. Resume is a separate action.
        """
        with self._lock:
            if self._threads.get(cid) and self._threads[cid].is_alive():
                raise Conflict("Cannot reconcile while a coordinator task is active")
            campaign = self.store.get(cid)
            if not campaign.get("recovery_required"):
                return self.detail(cid)
            runner = campaign.get("runner", {})
            if runner.get("kind", runner.get("mode", "docker")) != "docker":
                raise Conflict(
                    "Trusted host recovery requires verified manual cleanup; escaped descendants cannot be proven stopped"
                )
            expiry = campaign.get("resource_lease_expires_at")
            if not expiry:
                raise Conflict(
                    "Interrupted task has no durable resource lease; manual cleanup evidence is required"
                )
            if not campaign.get("resource_lease_recoverable") and not campaign.get(
                "resource_lease_completed"
            ):
                raise Conflict(
                    "Interrupted coordinator diagnostic has no independent watchdog; verified manual cleanup is required"
                )
            try:
                expires = datetime.fromisoformat(expiry)
                if expires.tzinfo is None:
                    raise ValueError("naive resource lease")
            except (TypeError, ValueError):
                raise Conflict(
                    "Resource lease evidence is invalid; manual reconciliation is required"
                ) from None
            if datetime.now(timezone.utc) < expires:
                raise Conflict(
                    "Resource lease has not expired; reconcile after " + expiry
                )
            reconciled = []
            for experiment in self.store.experiments(cid):
                if experiment["status"] == "unknown":
                    self.store.update_experiment(
                        cid,
                        experiment["id"],
                        {
                            "status": "cancelled",
                            "verdict": "inconclusive",
                            "phase": "finished",
                            "reconciliation": "Interrupted execution was not retried; recorded local resource lease expired",
                        },
                    )
                    reconciled.append(experiment["id"])
            self.store.update(
                cid,
                {
                    "state": "blocked",
                    "outcome": "incomplete",
                    "recovery_required": False,
                    "reconciled_at": now(),
                    "reconciliation": {
                        "cancelled_experiments": reconciled,
                        "resource_lease_expires_at": expiry,
                        "budget_reservations": "retained without refund",
                        "remote_provider_outcome": "unknown; no remote cancellation inferred",
                        "next_action": "resume explicitly when remaining limits and protocol allow",
                    },
                },
                event="recovery.reconciled",
            )
            scheduler = self.store.get(cid).get("scheduler") or self._scheduler_default(
                campaign
            )
            for task in scheduler["tasks"]:
                if task["status"] == "unknown":
                    task.update(status="cancelled", finished_at=now())
            scheduler.update(phase="idle", active_tasks=0)
            self.store.update(
                cid, {"scheduler": scheduler}, event="scheduler.reconciled"
            )
            return self.detail(cid)

    def _continue(self, cid, cancel, deadline):
        campaign = self.store.get(cid)
        if (
            campaign.get("recovery_required")
            or cancel.is_set()
            or campaign["state"]
            in {
                "paused",
                "stopping",
                "stopped",
                "failed",
                "blocked",
                "reviewable",
            }
        ):
            return False
        if time.monotonic() >= deadline:
            if getattr(self._local, "parallel_worker", False):
                raise TimeoutError("Parallel research task exceeded campaign deadline")
            with self._lock:
                if self.store.get(cid)["state"] not in {
                    "paused",
                    "stopping",
                    "stopped",
                }:
                    self.store.update(
                        cid,
                        {
                            "state": "reviewable",
                            "termination_reason": "time_limit",
                            "outcome": "incomplete",
                        },
                    )
            return False
        return True

    @research_work
    def _propose(self, cid, task, cancel, deadline):
        from .runner import _execute
        from .provider import ProviderError, ProviderContractViolation

        if not self._continue(cid, cancel, deadline):
            raise InterruptedError("Provider admission revoked")
        c = self.store.get(cid)
        key = "provider-" + uuid.uuid4().hex
        context = getattr(self._local, "proposal_context", None) or self._context(
            cid, task
        )
        request = {
            "config": c["agent"],
            "database": str(self.store.path),
            "campaign_id": cid,
            "key": key,
            "context": context,
            "purpose": "exploration",
        }
        if c["agent"].get("kind") == "claude_code":
            from .runner import _snapshot

            source = self._checkout(
                cid,
                "agent-source-"
                + (c.get("prepared_revision") or c["base_revision"])[:12],
            )
            workspace = self.root / cid / "agent-workspaces" / key
            workspace.mkdir(parents=True)
            excluded = _snapshot(
                source,
                workspace,
                min(
                    int(c["runner"].get("source_bytes", 64 * 1024 * 1024)), 2 * 1024**3
                ),
                file_limit=min(int(c["runner"].get("source_files", 10000)), 100000),
                excluded_paths=c["agent"].get("excluded_paths", []),
            )
            git(workspace, "init")
            git(workspace, "add", ".")
            git(workspace, "commit", "-m", "Filtered source for isolated native agent")
            artifact_dir = self.root / cid / "artifacts" / key
            artifact_dir.mkdir(parents=True)
            request.update(
                workspace=str(workspace),
                artifact_dir=str(artifact_dir),
                deadline=deadline,
            )
            request["context"] = {
                **context,
                "source_revision": git(source, "rev-parse", "HEAD"),
                "excluded_files": excluded,
                "protected_paths": (c.get("protocol") or {}).get("protected_paths", []),
            }
        self.store.event(
            cid,
            "provider.requested",
            {
                "key": key,
                "task": task,
                "source_files": list(context["source_files"]),
                "agent": c["agent"],
            },
        )
        timeout = min(
            c["limits"]["max_task_seconds"], max(0.1, deadline - time.monotonic())
        )
        self._admit_provider_resource(cid, key, timeout)
        try:
            result = _execute(
                [sys.executable, "-m", "autoresearch.provider_worker"],
                env={
                    **os.environ,
                    "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                },
                timeout=timeout,
                cancel=cancel,
                input_bytes=json.dumps(request).encode(),
            )
        except BaseException:
            self._finish_provider_resource(cid, key, False)
            raise
        self._finish_provider_resource(
            cid, key, self._provider_processes_returned(result, request)
        )
        if result["status"] == "timed_out":
            raise TimeoutError(
                "Provider task exceeded deadline; any reservation is retained"
            )
        if result["status"] == "cancelled":
            raise InterruptedError(
                "Provider task cancelled; any reservation is retained"
            )
        try:
            response = json.loads(result["stdout"])
        except ValueError:
            raise ProviderError(
                "Provider worker did not return a valid result; reservations retained"
            ) from None
        if response.get("error"):
            kind = response.get("type")
            if kind == "ProviderContractViolation":
                raise ProviderContractViolation(response["error"])
            if kind == "BudgetExceeded":
                raise BudgetExceeded(response["error"])
            raise ProviderError(response["error"])
        if result["status"] != "succeeded":
            raise ProviderError(
                f"Provider worker {result['status']}; any reservation is retained"
            )
        self.store.event(
            cid, "provider.proposed", {"key": key, "proposal": response["proposal"]}
        )
        return response["proposal"]

    @research_work
    def _apply(self, workspace, patch, protected):
        patchfile = workspace.parent / (workspace.name + ".patch")
        patchfile.write_text(patch)
        git(workspace, "apply", "--index", "--whitespace=error", str(patchfile))
        changed = git(workspace, "diff", "--cached", "--name-only", "-z").split("\0")
        for path in filter(None, changed):
            if path.startswith(".git") or any(
                path == p.rstrip("/") or path.startswith(p.rstrip("/") + "/")
                for p in protected
            ):
                raise Conflict("Patch modifies protected evaluator path: " + path)
            if (workspace / path).is_symlink():
                raise Conflict("Candidate symlinks are not accepted")
        git(workspace, "commit", "-m", "Autoresearch candidate")
        return git(workspace, "rev-parse", "HEAD")

    def _measure(self, cid, workspace, label, protocol, cancel, deadline):
        from .measurement import parse_benchmark

        if not self._continue(cid, cancel, deadline):
            raise InterruptedError("Campaign paused, stopped, or duration exhausted")
        with self._phase_barrier.measurement(cancel, deadline), self._measurement_lock:
            if not self._continue(cid, cancel, deadline):
                raise InterruptedError("Measurement admission revoked")
            c = self.store.get(cid)
            artifact = self.root / cid / "artifacts" / label
            timeout = min(
                protocol["timeout_seconds"],
                c["limits"]["max_task_seconds"],
                max(0.1, deadline - time.monotonic()),
            )
            runner = {
                **c["runner"],
                "environment": protocol.get("environment", {}),
                "warmup_command": protocol.get("warmup_command", []),
            }
            self._admit_resource_lease(
                cid,
                "measurement",
                timeout,
                recoverable=runner.get("kind", runner.get("mode", "docker"))
                == "docker",
            )
            result = self._execute_runner(
                cid,
                protocol["command"],
                workspace,
                artifact,
                timeout_seconds=timeout,
                cancel=cancel,
                runner=runner,
            )
            # Runner outputs are immutable evidence even when parsing fails.
            artifact.mkdir(parents=True, exist_ok=True)
            (artifact / "supervisor.json").write_text(
                json.dumps(result, indent=2, allow_nan=False)
            )
            self.store.event(
                cid,
                "execution.finished",
                {
                    "label": label,
                    "status": result["status"],
                    "artifact": str(artifact.relative_to(self.root / cid)),
                },
            )
            if result["status"] != "succeeded":
                raise ValueError(
                    f"Execution {label} {result['status']}: {str(result.get('stderr', ''))[-1000:]}"
                )
            sample = parse_benchmark(result["stdout"], protocol["metrics"])
            sample["environment_fingerprint"] = digest(result["environment"])
            sample["execution"] = {
                "label": label,
                "duration_seconds": result["duration_seconds"],
                "artifact": str(artifact.relative_to(self.root / cid)),
                "revision": git(workspace, "rev-parse", "HEAD"),
                "protocol_hash": self.store.get(cid)["protocol_hash"],
            }
            return sample

    def _run_task(self, cid, cancel):
        from .measurement import summarize, compare, pareto_front

        c = self.store.get(cid)
        protocol = c["protocol"]
        deadline = (
            time.monotonic()
            + c["limits"]["max_active_seconds"]
            - c.get("active_seconds", 0)
        )
        self._scheduler(cid, phase="preparing")
        try:
            with self._scheduled_task(cid, "baseline_preparation"):
                if not self._continue(cid, cancel, deadline):
                    raise InterruptedError("Baseline preparation admission cancelled")
                with self._phase_barrier.research(cancel, deadline):
                    baseline_ws = self._checkout(
                        cid,
                        "baseline-"
                        + (c.get("prepared_revision") or c["base_revision"])[:12],
                    )
                if not self._continue(cid, cancel, deadline):
                    raise InterruptedError("Baseline preparation admission cancelled")
        except InterruptedError:
            return
        token = uuid.uuid4().hex[:10]
        self._scheduler(cid, phase="measuring")
        with self._scheduled_task(cid, "measurement"):
            baseline = []
            for i in range(protocol["repetitions"]):
                baseline.append(
                    self._measure(
                        cid,
                        baseline_ws,
                        f"{token}-baseline-{i}",
                        protocol,
                        cancel,
                        deadline,
                    )
                )
        summary = summarize(baseline, protocol["metrics"])
        summary["protocol_hash"] = c["protocol_hash"]
        self.store.update(
            cid,
            {
                "baseline": baseline,
                "baseline_summary": summary,
                "baseline_context": self._measurement_context(c),
            },
            event="baseline.measured",
        )
        if not self._continue(cid, cancel, deadline):
            return
        if not summary["valid"]:
            self._active_update(
                cid,
                {
                    "state": "blocked",
                    "readiness": {
                        "status": "measurement_unreliable",
                        "findings": [
                            {"message": r, "severity": "blocking"}
                            for r in summary["reasons"]
                        ],
                    },
                    "outcome": "incomplete",
                },
            )
            return
        with self._phase_barrier.research(cancel, deadline):
            evaluation_context = {
                "schema_version": 1,
                "protocol_hash": c["protocol_hash"],
                "baseline_revision": git(baseline_ws, "rev-parse", "HEAD"),
                "baseline_tree": git(baseline_ws, "rev-parse", "HEAD^{tree}"),
                "runner_config_hash": digest(c["runner"]),
                # Supervisor execution labels/timestamps are not new evidence. All
                # measured content, checks and environment fingerprints still are.
                "baseline_evidence_hash": digest(
                    [
                        {k: v for k, v in sample.items() if k != "execution"}
                        for sample in baseline
                    ]
                ),
            }
        evaluation_context_hash = digest(evaluation_context)
        while self._continue(cid, cancel, deadline):
            if not self._active_update(cid, {"state": "exploring"}):
                return
            queued = [e for e in self.store.experiments(cid) if e["status"] == "queued"]
            if not queued and c["agent"].get("kind") in {"claude", "claude_code"}:
                queued = self._proposal_wave(cid, cancel, deadline)
            if not queued:
                break
            if not self._continue(cid, cancel, deadline):
                return
            prepared = self._preparation_wave(
                cid,
                queued[: c["limits"].get("max_parallel_tasks", 1)],
                baseline_ws,
                protocol,
                evaluation_context,
                cancel,
                deadline,
            )
            for exp in prepared:
                if not self._continue(cid, cancel, deadline):
                    return
                eid = exp["id"]
                workspace = Path(exp["workspace"])
                tree, program_identity = exp["tree"], exp["program_identity"]
                self.store.update_experiment(
                    cid, eid, {"status": "running", "phase": "measuring"}
                )
                try:
                    # One coordinator evaluates a campaign serially. A queued/running
                    # attempt is not evidence; only a completed comparison can let
                    # the next attempt skip measurement. Keep its own empty results.
                    duplicate = next(
                        (
                            previous
                            for previous in self.store.experiments(cid)
                            if previous["id"] != eid
                            and previous.get("status") == "succeeded"
                            and previous.get("program_identity") == program_identity
                            and previous.get("tree") == tree
                            and previous.get("evaluation_context_hash")
                            == evaluation_context_hash
                            and isinstance(previous.get("comparison"), dict)
                            and previous["comparison"].get("verdict")
                            in {"promising", "inconclusive", "constraint_failed"}
                            and previous["comparison"].get("baseline", {}).get("valid")
                            is True
                            and previous["comparison"].get("candidate", {}).get("valid")
                            is True
                            and previous.get("runs")
                            and previous.get("reference_runs")
                        ),
                        None,
                    )
                    if duplicate:
                        self.store.update_experiment(
                            cid,
                            eid,
                            {
                                "status": "cancelled",
                                "verdict": "inconclusive",
                                "termination_reason": "duplicate_program",
                                "phase": "finished",
                                "duplicate_of": duplicate["id"],
                                "duplicate_reason": "Identical Git tree already evaluated under the same approved protocol, reference revision, runner and baseline evidence; no new measurements or confirmation were performed",
                            },
                        )
                        continue
                    self._scheduler(cid, phase="measuring")
                    with self._scheduled_task(cid, "measurement", experiment_id=eid):
                        before = []
                        after = []
                        for i in range(protocol["repetitions"]):
                            # Alternating order reduces a systematic drift/order effect.
                            order = [
                                ("reference", baseline_ws, before),
                                ("candidate", workspace, after),
                            ]
                            if i % 2:
                                order.reverse()
                            for kind, ws, samples in order:
                                samples.append(
                                    self._measure(
                                        cid,
                                        ws,
                                        f"{token}-{eid}-{kind}-{i}",
                                        protocol,
                                        cancel,
                                        deadline,
                                    )
                                )
                    result = compare(before, after, protocol["metrics"])
                    self.store.update_experiment(
                        cid,
                        eid,
                        {
                            "runs": after,
                            "reference_runs": before,
                            "comparison": result,
                            "verdict": result["verdict"],
                        },
                    )
                    if result["verdict"] == "promising" and self._continue(
                        cid, cancel, deadline
                    ):
                        if not self._active_update(cid, {"state": "confirming"}):
                            return
                        self.store.update_experiment(cid, eid, {"phase": "confirming"})
                        self._scheduler(cid, phase="confirming")
                        with self._scheduled_task(
                            cid, "confirmation", experiment_id=eid
                        ):
                            confirm_before = []
                            confirm_after = []
                            for i in range(protocol["confirmation_repetitions"]):
                                for kind, ws, samples in [
                                    ("reference", baseline_ws, confirm_before),
                                    ("candidate", workspace, confirm_after),
                                ]:
                                    samples.append(
                                        self._measure(
                                            cid,
                                            ws,
                                            f"{token}-{eid}-confirmation-{kind}-{i}",
                                            protocol,
                                            cancel,
                                            deadline,
                                        )
                                    )
                        confirmation = compare(
                            confirm_before, confirm_after, protocol["metrics"]
                        )
                        verdict = (
                            "confirmed"
                            if confirmation["verdict"] == "promising"
                            else confirmation["verdict"]
                        )
                        self.store.update_experiment(
                            cid,
                            eid,
                            {
                                "confirmation": confirmation,
                                "confirmation_runs": confirm_after,
                                "confirmation_reference_runs": confirm_before,
                                "verdict": verdict,
                            },
                        )
                    self.store.update_experiment(
                        cid, eid, {"status": "succeeded", "phase": "finished"}
                    )
                except InterruptedError:
                    self.store.update_experiment(
                        cid,
                        eid,
                        {
                            "status": "cancelled",
                            "verdict": "inconclusive",
                            "phase": "finished",
                        },
                    )
                    return
                except Exception as exc:
                    self.store.update_experiment(
                        cid,
                        eid,
                        {
                            "status": "failed",
                            "phase": "finished",
                            "verdict": "execution_failed",
                            "error": str(exc)[:3000],
                        },
                    )
        if not self._continue(cid, cancel, deadline):
            return
        experiments = self.store.experiments(cid)
        confirmed = [e for e in experiments if e.get("verdict") == "confirmed"]
        front = pareto_front(
            [{**e, "summary": e["confirmation"]["candidate"]} for e in confirmed],
            protocol["metrics"],
        )
        self._active_update(
            cid,
            {
                "state": "reviewable",
                "outcome": "confirmed_candidates" if confirmed else "no_confirmed_gain",
                "termination_reason": "completed",
                "pareto_ids": [e["id"] for e in front],
            },
            event="campaign.reviewable",
        )

    def report(self, cid):
        d = self.detail(cid)
        c = d["campaign"]
        b = d["budget"]
        lines = [
            f"# {c['name']}",
            "",
            c["goal"],
            "",
            f"State: {c['state']}; outcome: {c.get('outcome')}; reason: {c.get('termination_reason')}",
            f"Reference: `{c['base_revision']}`",
            f"Protocol: `{c.get('protocol_hash')}`",
            "",
            f"Budget ({b['enforcement']}): retained ${b['consumed'] / 1e6:.6f}, engaged ${b['engaged'] / 1e6:.6f}, available ${b['available'] / 1e6:.6f}.",
            f"Scope: {b['scope']}",
            "",
            "Results apply only to this workload, environment and tested correctness checks.",
        ]
        for exp in d["experiments"]:
            lines += [
                "",
                f"## {exp['hypothesis']}",
                f"Strategy: {exp['strategy']}; status: {exp['status']}; verdict: {exp.get('verdict')}",
                f"Proposal origin: {exp.get('origin', 'not recorded')}",
                f"Revision: {exp.get('revision', 'not created')}",
            ]
            if exp.get("expected_effect"):
                lines += [
                    "",
                    "**Proposed effect and tradeoffs (unmeasured):**",
                    "",
                    exp["expected_effect"],
                    "",
                ]
            if exp.get("error"):
                lines.append(exp["error"])
            if exp.get("duplicate_of"):
                lines.append(
                    f"Duplicate of `{exp['duplicate_of']}`: {exp['duplicate_reason']}"
                )
            for name, m in (
                (exp.get("confirmation") or exp.get("comparison") or {})
                .get("metrics", {})
                .items()
            ):
                lines.append(
                    f"- {name}: {m['baseline']:.6g} → {m['candidate']:.6g}; gain interval {m['gain_ci95']}"
                )
        return "\n".join(lines) + "\n"

    def reproduction(self, cid, eid=None):
        c = self.store.get(cid)
        experiment = self.store.experiment(cid, eid) if eid else None
        if experiment and not experiment.get("revision"):
            raise Conflict("Experiment has no executable revision")
        if experiment and not self._current_evidence(c, experiment):
            raise Conflict(
                "Historical experiment measurement context differs or is unverified; "
                "the current protocol cannot reproduce its recorded evaluation"
            )
        revision = (
            experiment["revision"]
            if experiment
            else c.get("prepared_revision") or c["base_revision"]
        )
        expected_tree = experiment.get("tree") if experiment else None
        recorded_checkouts = (
            [experiment.get("workspace")]
            if experiment
            else [
                c.get("prepared_repository"),
                str(self.root / cid / "workspaces" / ("baseline-" + revision[:12])),
            ]
        )
        checkout_repository, tree = None, None
        for locator in dict.fromkeys(
            path for path in [*recorded_checkouts, c["repository"]] if path
        ):
            path = Path(locator)
            if not path.is_dir():
                continue
            try:
                recorded_revision = git(
                    path, "rev-parse", "--verify", revision + "^{commit}"
                )
                recorded_tree = git(path, "rev-parse", revision + "^{tree}")
            except ValueError:
                continue
            if recorded_revision != revision or (
                expected_tree and recorded_tree != expected_tree
            ):
                raise Conflict("Recorded reproduction revision or tree does not match")
            if locator in recorded_checkouts and (
                git(path, "rev-parse", "HEAD") != revision
                or git(path, "status", "--porcelain")
            ):
                raise Conflict("Recorded reproduction checkout changed")
            checkout_repository, tree = str(path.resolve()), recorded_tree
            break
        if checkout_repository is None:
            raise Conflict(
                "Recorded reproduction revision is unavailable in local repositories; "
                "restore an accessible clone containing the exact Git objects"
            )
        samples = (
            (experiment or {}).get("runs")
            or (c.get("baseline") if not experiment else [])
            or []
        )
        return {
            "schema_version": 1,
            "campaign_id": cid,
            "repository": c["repository"],
            "checkout_repository": checkout_repository,
            "base_revision": c.get("prepared_revision") or c["base_revision"],
            "revision": revision,
            "tree": tree,
            "patch": experiment.get("patch") if experiment else None,
            "parent_id": experiment.get("parent_id") if experiment else None,
            "protocol_hash": c["protocol_hash"],
            "protocol": c["protocol"],
            "runner": c["runner"],
            "environment": samples[0].get("environment_fingerprint")
            if samples
            else None,
            "instructions": [
                "Fresh-clone checkout_repository with --no-hardlinks, then detach at revision and verify tree; never substitute the original repository when the recorded commit is absent.",
                "checkout_repository is a local locator, not a portable bundle; it requires retained local campaign data or an accessible clone containing the exact recorded Git objects.",
                "Install the recorded runner image and required dependencies.",
                "Execute the approved command and environment; record independent repetitions and compare runner fingerprints.",
                "Any changed protocol or environment requires approval and a new baseline.",
            ],
        }

    def draft(self, cid, eid):
        from .publisher import prepare_draft

        return prepare_draft(
            self.store.get(cid), self.store.experiment(cid, eid), self.report(cid)
        )

    def publish(self, cid, request):
        from .publisher import GitHubPublisher

        with self._lock:
            self._idle(cid)
            payload = self.draft(cid, request.candidate_id)
            if payload["review_hash"] != request.review_hash:
                raise Conflict(
                    "Draft contents changed; review the current payload before publishing"
                )
            self.store.event(
                cid,
                "publication.requested",
                {
                    "candidate_id": request.candidate_id,
                    "remote_repository": request.remote_repository,
                    "base": request.base_branch,
                    "head": request.head_branch,
                    "review_hash": request.review_hash,
                },
            )
            result = GitHubPublisher(self.root / cid / "publications").publish_draft(
                payload,
                selected_candidate_id=request.candidate_id,
                remote_repository=request.remote_repository,
                base_branch=request.base_branch,
                head_branch=request.head_branch,
            )
            self.store.event(cid, "publication.completed", result)
            return result

    def export(self, cid):
        detail = self.detail(cid)
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "manifest.json",
                json.dumps({"schema_version": 1, **detail}, indent=2, allow_nan=False),
            )
            archive.writestr(
                "events.jsonl",
                "\n".join(json.dumps(e) for e in detail["events"]) + "\n",
            )
            archive.writestr("report.md", self.report(cid))
            checksums = {}
            for path in sorted((self.root / cid / "artifacts").rglob("*")):
                if path.is_file() and not path.is_symlink():
                    relative = str(path.relative_to(self.root / cid))
                    content = path.read_bytes()
                    checksums[relative] = hashlib.sha256(content).hexdigest()
                    archive.writestr(relative, content)
            for exp in detail["experiments"]:
                archive.writestr(f"patches/{exp['id']}.patch", exp["patch"])
            archive.writestr("checksums.json", json.dumps(checksums, indent=2))
        return output.getvalue()
