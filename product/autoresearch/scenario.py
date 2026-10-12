"""Owned, finite Docker services for an approved benchmark scenario.

Every execution gets a fresh internal network and fresh tmpfs state. Services
run below an immutable root-owned PID 1 deadline, without host mounts or ports.
The coordinator reaper removes owned resources if its parent disappears.
"""

from __future__ import annotations

from datetime import datetime
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import time

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Covers the reaper's deadline + 15s, five 10s cleanup attempts and four
# 2s retries, plus bounded output draining and coordinator cleanup overhead.
RESOURCE_CLEANUP_GRACE_SECONDS = 120


def watchdog_script(daemon_deadline):
    """Use the single calibrated daemon epoch; delayed admission adds no time."""
    if type(daemon_deadline) is not int or not 0 < daemon_deadline <= 2**63 - 1:
        raise ValueError("Docker watchdog requires a calibrated absolute deadline")
    # Absolute paths keep writable tmpfs/PATH out of privileged PID 1.
    return f'left=$(({daemon_deadline} - $(/bin/date +%s))); [ "$left" -le 0 ] || exec /bin/sleep "$left"'


def verify_daemon_clock(
    value, started, finished, *, deadline, wall_started=None, wall_finished=None
):
    """Translate a monotonic task deadline using a bounded daemon-clock sample.

    SystemTime was generated before probe completion. Adding only the budget
    remaining at completion therefore expires conservatively, even with static
    coordinator/daemon skew. Integer truncation never adds a rounding grace.
    Coordinator wall times are receipts only, never admission inputs.
    """
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("missing timezone")
        stamp = parsed.timestamp()
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("Docker daemon clock cannot be verified") from exc
    if (
        any(
            type(v) not in {int, float} or not math.isfinite(v)
            for v in (started, finished, deadline)
        )
        or not math.isfinite(stamp)
        or stamp <= 0
        or not 0 <= finished - started <= 5
    ):
        raise ValueError("Docker daemon clock probe is invalid or exceeds 5 seconds")
    remaining = deadline - finished
    if remaining <= 0:
        raise TimeoutError("Execution deadline exhausted during Docker clock probe")
    translated = stamp + remaining + 10
    if not math.isfinite(translated) or translated > 2**63 - 1:
        raise ValueError("Docker daemon clock deadline cannot be represented")
    end = math.floor(translated)
    return {
        "daemon_time": value,
        "daemon_timestamp_seconds": stamp,
        "probe_monotonic_before": started,
        "probe_monotonic_after": finished,
        "probe_roundtrip_seconds": finished - started,
        "remaining_budget_seconds": remaining,
        "watchdog_grace_seconds": 10,
        "daemon_deadline_seconds": end,
        "coordinator_before": wall_started,
        "coordinator_after": wall_finished,
        "observed_offset_seconds": stamp - wall_finished
        if wall_finished is not None
        else None,
        "policy": "single daemon epoch deadline translated from bounded probe and monotonic remaining budget",
    }


def environment(value):
    if not isinstance(value, dict):
        raise ValueError("scenario environment must be a mapping")
    for key, item in value.items():
        if (
            not isinstance(key, str)
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key)
            or not isinstance(item, str)
            or "\x00" in item
            or re.search(
                r"TOKEN|SECRET|PASSWORD|CREDENTIAL|API_KEY|ACCESS_KEY|PRIVATE_KEY|SSH_AUTH|DOCKER_HOST",
                key,
                re.I,
            )
            or key == "HOME"
        ):
            raise ValueError("invalid or credential-bearing scenario environment")
    return value


class Service(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,30}$")
    image: str = Field(min_length=1, max_length=300)
    command: list[str] = Field(min_length=1, max_length=100)
    ready_command: list[str] = Field(min_length=1, max_length=100)
    environment: dict[str, str] = Field(default_factory=dict)
    ready_timeout_seconds: float = Field(default=60, gt=0, le=180)
    cpus: float = Field(default=1, gt=0, le=16)
    memory_mb: int = Field(default=512, ge=32, le=16384)
    pids_limit: int = Field(default=256, ge=16, le=4096)
    user: str = "65534:65534"
    tmpfs: dict[str, int] = Field(default_factory=lambda: {"/tmp": 64})

    @field_validator("command", "ready_command")
    @classmethod
    def argv(cls, value):
        if any("\x00" in arg for arg in value) or not value[0]:
            raise ValueError("service commands must be valid argv")
        return value

    @field_validator("environment")
    @classmethod
    def env(cls, value):
        return environment(value)

    @field_validator("user")
    @classmethod
    def unprivileged(cls, value):
        if not re.fullmatch(r"[1-9][0-9]{0,8}:[1-9][0-9]{0,8}", value):
            raise ValueError("service user must be a non-root numeric UID:GID")
        return value

    @model_validator(mode="after")
    def bounded_storage(self):
        self.tmpfs.setdefault("/tmp", 64)
        if len(self.tmpfs) > 8 or sum(self.tmpfs.values()) > 16384:
            raise ValueError("service tmpfs storage exceeds bound")
        paths = []
        for name, size in self.tmpfs.items():
            path = PurePosixPath(name)
            if (
                not re.fullmatch(r"/[A-Za-z0-9_./-]+", name)
                or str(path) != name
                or ".." in path.parts
                or path.parts[1]
                in {"proc", "sys", "dev", "bin", "sbin", "lib", "lib64"}
                or name in {"/etc", "/usr", "/opt", "/var", "/run"}
                or type(size) is not int
                or not 1 <= size <= 16384
            ):
                raise ValueError("invalid service tmpfs path or size")
            if any(path.is_relative_to(p) or p.is_relative_to(path) for p in paths):
                raise ValueError("service tmpfs paths must not overlap")
            paths.append(path)
        return self


def services(value):
    if not isinstance(value, list) or len(value) > 4:
        raise ValueError("scenario must define at most four services")
    result = [Service.model_validate(row) for row in value]
    if len({s.name for s in result}) != len(result):
        raise ValueError("service names must be unique")
    return result


_REAPER = r"""
import json,os,subprocess,sys,time
spec=json.loads(sys.argv[1]); end=time.monotonic()+spec['seconds']
while time.monotonic()<end:
    try: os.kill(spec['owner'],0)
    except ProcessLookupError: break
    time.sleep(.2)
# An in-flight Docker admission may complete just after its coordinator dies.
# Retry cleanup through the admission command's own bounded lifetime.
for attempt in range(5):
    try:
        subprocess.run(spec['docker']+['rm','--force',*spec['containers']],
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=5)
        subprocess.run(spec['docker']+['network','rm',spec['network']],
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=5)
    except (OSError,subprocess.SubprocessError): pass
    if attempt<4: time.sleep(2)
"""


class Scenario:
    def __init__(
        self,
        docker,
        specs,
        owner,
        deadline,
        cancel,
        execute,
        artifact_dir,
        *,
        daemon_deadline,
    ):
        self.docker, self.specs, self.owner = docker, specs, owner
        self.deadline, self.cancel, self.execute = deadline, cancel, execute
        self.daemon_deadline = daemon_deadline
        watchdog_script(
            daemon_deadline
        )  # Require calibration before allocating resources.
        self.artifact_dir = Path(artifact_dir)
        self.network = owner + "-network"
        self.names = [owner + "-" + s.name for s in specs]
        self.images, self.records = [], []
        self.reaper = None
        self.admitted = False
        self.uncertain = False

    def remaining(self):
        if self.cancel is not None and self.cancel.is_set():
            raise InterruptedError("Scenario admission cancelled")
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("Scenario execution deadline exhausted")
        return left

    def call(self, argv, *, timeout=10, **kwargs):
        result = self.execute(
            self.docker + argv,
            timeout=min(timeout, self.remaining()),
            cancel=self.cancel,
            **kwargs,
        )
        # Only delayed allocating requests can recreate a resource after cleanup.
        # Exec/inspection may fail, but cannot outlive removal of their container.
        allocating = argv[:1] in (["run"], ["create"]) or argv[:2] == [
            "network",
            "create",
        ]
        if allocating and result["status"] in {
            "timed_out",
            "cancelled",
            "output_limit",
        }:
            self.uncertain = True
        return result

    def require(self, argv, **kwargs):
        result = self.call(argv, **kwargs)
        if result["status"] != "succeeded":
            self.remaining()
            raise RuntimeError("Scenario command failed: " + result["stderr"][-2000:])
        return result

    def record(self):
        (self.artifact_dir / "scenario.json").write_text(
            json.dumps(
                {
                    "network": self.network,
                    "owner": self.owner,
                    "daemon_deadline_seconds": self.daemon_deadline,
                    "services": self.records,
                },
                indent=2,
            )
        )

    def start(self):
        # Resolve every image before allocating any scenario resource.
        for spec in self.specs:
            probe = self.require(
                ["image", "inspect", spec.image, "--format", "{{json .}}"]
            )
            self.images.append(json.loads(probe["stdout"]))
        self.reaper = subprocess.Popen(
            [
                sys.executable,
                "-c",
                _REAPER,
                json.dumps(
                    {
                        "owner": os.getpid(),
                        "seconds": self.remaining() + 15,
                        "docker": self.docker,
                        "containers": [self.owner, *self.names],
                        "network": self.network,
                    }
                ),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        self.admitted = True
        self.require(
            [
                "network",
                "create",
                "--internal",
                "--driver",
                "bridge",
                "--opt",
                "com.docker.network.bridge.gateway_mode_ipv4=isolated",
                "--label",
                "org.autoresearch.owner=" + self.owner,
                self.network,
            ]
        )
        net = json.loads(self.require(["network", "inspect", self.network])["stdout"])[
            0
        ]
        if (
            not net.get("Internal")
            or net.get("Options", {}).get("com.docker.network.bridge.gateway_mode_ipv4")
            != "isolated"
        ):
            raise RuntimeError("Docker did not establish isolated scenario networking")
        for spec, name, image in zip(self.specs, self.names, self.images):
            record = {
                "name": spec.name,
                "container": name,
                "image_id": image["Id"],
                "status": "starting",
            }
            self.records.append(record)
            self.record()
            args = [
                "run",
                "--detach",
                "--pull",
                "never",
                "--name",
                name,
                "--network",
                self.network,
                "--network-alias",
                spec.name,
                "--dns",
                "127.0.0.1",
                "--label",
                "org.autoresearch.owner=" + self.owner,
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--user",
                "0:0",
                "--cpus",
                str(spec.cpus),
                "--memory",
                f"{spec.memory_mb}m",
                "--memory-swap",
                f"{spec.memory_mb}m",
                "--pids-limit",
                str(spec.pids_limit),
                "--ulimit",
                "nofile=1024:1024",
                "--log-driver",
                "none",
                "--env",
                "HOME=/tmp",
                "--env",
                "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            ]
            for path, size in spec.tmpfs.items():
                args += [
                    "--tmpfs",
                    f"{path}:rw,exec,nosuid,nodev,size={size}m,mode=1777,nr_inodes=20000",
                ]
            args += [
                "--entrypoint",
                "/bin/sh",
                image["Id"],
                "-c",
                watchdog_script(self.daemon_deadline),
            ]
            self.require(args)
            exec_env = []
            for key, value in spec.environment.items():
                exec_env += ["--env", key + "=" + value]
            self.require(
                [
                    "exec",
                    "--detach",
                    "--user",
                    spec.user,
                    *exec_env,
                    name,
                    "/bin/sh",
                    "-c",
                    'exec "$@" > /tmp/autoresearch-service.log 2>&1',
                    "service",
                    *spec.command,
                ]
            )
            ready_by = min(self.deadline, time.monotonic() + spec.ready_timeout_seconds)
            while time.monotonic() < ready_by:
                probe = self.call(
                    ["exec", "--user", spec.user, *exec_env, name, *spec.ready_command],
                    timeout=3,
                )
                if probe["status"] == "succeeded":
                    record["status"] = "ready"
                    self.record()
                    break
                self.remaining()
                if self.cancel is not None:
                    self.cancel.wait(min(0.2, max(0, ready_by - time.monotonic())))
                else:
                    time.sleep(min(0.2, max(0, ready_by - time.monotonic())))
            else:
                record["status"] = "readiness_failed"
                self.record()
                raise RuntimeError("Service readiness deadline exceeded: " + spec.name)
        return {
            "network": "internal bridge; isolated gateway; no published ports",
            "services": [
                {**s.model_dump(), "image_id": image["Id"]}
                for s, image in zip(self.specs, self.images)
            ],
        }

    def close(self):
        if not self.admitted:
            return None
        for spec, record in zip(self.specs, self.records):
            logs = self.execute(
                self.docker
                + [
                    "exec",
                    "--user",
                    spec.user,
                    record["container"],
                    "/usr/bin/tail",
                    "-c",
                    "200000",
                    "/tmp/autoresearch-service.log",
                ],
                timeout=2,
                limit=250000,
            )
            (self.artifact_dir / ("service-" + record["name"] + ".log")).write_text(
                logs["stdout"]
            )
        removed = self.execute(
            self.docker + ["rm", "--force", self.owner, *self.names], timeout=10
        )
        network = self.execute(self.docker + ["network", "rm", self.network], timeout=5)
        # Missing containers are harmless after a failed admission. Network
        # removal proves no remaining owned container is attached.
        success = network["status"] == "succeeded"
        for record in self.records:
            record["cleanup"] = "removed" if success else "unconfirmed"
        self.record()
        if success and not self.uncertain and self.reaper:
            self.reaper.terminate()
            try:
                self.reaper.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.reaper.kill()
                self.reaper.wait(timeout=2)
        if not success or self.uncertain:
            return (
                "Scenario cleanup unconfirmed; independent service deadlines and owned-resource reaper remain active. "
                + (network["stderr"] or removed["stderr"])[-1000:]
            )
        return None
