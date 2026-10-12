"""Bounded benchmark execution. Docker is default; host execution requires trust.

Docker workers receive a filtered source snapshot, no host mounts, no host
environment, no external network, a read-only root, and bounded tmpfs storage. Their PID 1
is an independent wall-clock watchdog, so a crashed coordinator leaves at most a
bounded orphan. Remote SSH Docker endpoints use the same streaming copy path.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import platform
import re
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import uuid


_WATCHDOG = """
import os,signal,sys,time
target,owner=int(sys.argv[1]),int(sys.argv[2]); deadline=time.monotonic()+float(sys.argv[3])
while time.monotonic()<deadline:
    try: os.kill(target,0)
    except ProcessLookupError: sys.exit(0)
    try: os.kill(owner,0)
    except ProcessLookupError: break
    time.sleep(.1)
try: os.killpg(target,signal.SIGKILL)
except ProcessLookupError: pass
"""


def _execute(
    command,
    *,
    cwd=None,
    env=None,
    timeout=30,
    cancel=None,
    limit=2_000_000,
    binary=False,
    input_bytes=None,
):
    start = time.monotonic()
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    # This detached watchdog survives coordinator exit and closes no campaign FD.
    watchdog = subprocess.Popen(
        [
            sys.executable,
            "-c",
            _WATCHDOG,
            str(process.pid),
            str(os.getpid()),
            str(timeout + 1),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    buffers = [bytearray(), bytearray()]
    exceeded = threading.Event()

    def read(stream, buffer):
        while True:
            block = stream.read(65536)
            if not block:
                break
            room = limit - len(buffer)
            buffer.extend(block[: max(0, room)])
            if len(block) > room:
                exceeded.set()
        stream.close()

    threads = [
        threading.Thread(target=read, args=(stream, buf), daemon=True)
        for stream, buf in zip((process.stdout, process.stderr), buffers)
    ]
    if input_bytes is not None:

        def feed():
            try:
                process.stdin.write(input_bytes)
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass

        threads.append(threading.Thread(target=feed, daemon=True))
    for thread in threads:
        thread.start()
    status = "succeeded"
    while process.poll() is None:
        if cancel is not None and cancel.is_set():
            status = "cancelled"
        elif time.monotonic() - start >= timeout:
            status = "timed_out"
        elif exceeded.is_set():
            status = "output_limit"
        if status != "succeeded":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            break
        time.sleep(0.03)
    process.wait()
    # Descendants can keep pipes open after their parent exits. Kill the group,
    # including children that remain in the command's process group. Docker
    # cleanup additionally kills processes that create new sessions inside it.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    for thread in threads:
        thread.join(timeout=2)
    watchdog.terminate()
    try:
        watchdog.wait(timeout=2)
    except subprocess.TimeoutExpired:
        watchdog.kill()
        watchdog.wait()
    if exceeded.is_set() and status == "succeeded":
        status = "output_limit"
    if process.returncode and status == "succeeded":
        status = "failed"
    return {
        "status": status,
        "returncode": process.returncode,
        "stdout": bytes(buffers[0])
        if binary
        else bytes(buffers[0]).decode("utf-8", "replace"),
        "stderr": bytes(buffers[1]).decode("utf-8", "replace"),
        "duration_seconds": time.monotonic() - start,
    }


def source_exclusions(value):
    """Explicit repository-relative file/directory exclusions, without globs."""
    if not isinstance(value, list) or len(value) > 256:
        raise ValueError("Source exclusions require at most 256 relative paths")
    for name in value:
        if (
            not isinstance(name, str)
            or not name
            or len(name) > 1000
            or "\x00" in name
            or "\\" in name
            or any(c in name for c in "*?[]")
            or PurePosixPath(name).is_absolute()
            or ".." in PurePosixPath(name).parts
            or name == "."
            or str(PurePosixPath(name)) != name
        ):
            raise ValueError("Source exclusions must be normalized relative paths")
    return list(dict.fromkeys(value))


def _snapshot(
    source: Path,
    destination: Path,
    limit: int,
    exclude: Path | None = None,
    file_limit: int = 10000,
    *,
    excluded_paths: list[str] | None = None,
) -> list[str]:
    excluded, total, count = [], 0, 0
    explicit_exclusions = source_exclusions(excluded_paths or [])

    def explicitly_excluded(relative):
        name = relative.as_posix()
        return any(name == p or name.startswith(p + "/") for p in explicit_exclusions)

    ignored_dirs = {
        ".git",
        ".ssh",
        ".aws",
        ".azure",
        ".config",
        ".docker",
        ".kube",
        ".gcp",
        ".claude",
        ".codex",
        ".m2",
        ".gradle",
        ".venv",
        "node_modules",
        "__pycache__",
    }
    for base, dirs, files in os.walk(source, followlinks=False):
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in ignored_dirs
            and not (Path(base) / d).is_symlink()
            and (Path(base) / d) != exclude
        )
        for filename in sorted(files):
            path = Path(base) / filename
            relative = path.relative_to(source)
            if (
                path.is_symlink()
                or explicitly_excluded(relative)
                or filename.startswith(".env")
                or any(
                    part in filename.lower()
                    for part in ("privatekey", "private_key", "private-key")
                )
                or (
                    "credential" in filename.lower()
                    and path.suffix in {".json", ".yaml", ".yml", ".toml"}
                )
                or filename.endswith((".pem", ".key", ".p12", ".jks"))
                or filename
                in {
                    "credentials",
                    "credentials.json",
                    "id_rsa",
                    "id_ed25519",
                    ".netrc",
                    ".npmrc",
                    ".pypirc",
                }
            ):
                excluded.append(str(relative))
                continue
            if not path.is_file():
                continue
            total += path.stat().st_size
            count += 1
            if total > limit or count > file_limit:
                raise ValueError("source snapshot exceeds byte/file limit")
            output = destination / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive binary copying gives no hardlinks to the user's checkout.
            output.write_bytes(path.read_bytes())
            output.chmod(0o755 if path.stat().st_mode & 0o111 else 0o644)
    return excluded


def _fingerprint(environment: dict) -> dict:
    environment["fingerprint"] = hashlib.sha256(
        json.dumps(environment, sort_keys=True).encode()
    ).hexdigest()
    return environment


def _collect(artifact_dir: Path, limit: int) -> list[dict]:
    artifacts, total = [], 0
    for path in sorted(artifact_dir.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        size = path.stat().st_size
        total += size
        if total > limit:
            raise ValueError("collected artifacts exceed limit")
        artifacts.append(
            {
                "path": str(path.resolve()),
                "name": str(path.relative_to(artifact_dir)),
                "size_bytes": size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    return artifacts


def run_command(
    command: list[str],
    cwd: Path,
    artifact_dir: Path,
    *,
    timeout_seconds: float,
    cancel: threading.Event | None = None,
    runner: dict | None = None,
) -> dict:
    """Run argv; return status/returncode/stdout/stderr/duration/environment/artifacts.

    Runner config: mode=docker (default), image, docker_host=ssh://user@runner or
    authenticated tcp:// endpoint, cpus=1, memory_mb=512, pids_limit=128,
    workspace_mb=256, artifact_mb=64, output_bytes=2MB, source_bytes=64MB.
    Host mode additionally requires trusted=True and offers no resource isolation.
    Images must already exist; pulling images is an administrator setup action.
    """
    start = time.monotonic()
    if not command or any(not isinstance(a, str) or "\x00" in a for a in command):
        raise ValueError("command must be a nonempty argv of strings")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive and finite")
    deadline = start + timeout_seconds

    def remaining():
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("whole execution deadline exceeded")
        return left

    config = dict(runner or {})
    from .scenario import Scenario, services, watchdog_script, verify_daemon_clock

    service_specs = services(config.get("services", []))
    source_bytes = int(config.get("source_bytes", 64 * 1024 * 1024))
    source_files = int(config.get("source_files", 10000))
    if not 1 <= source_bytes <= 2 * 1024**3 or not 1 <= source_files <= 100000:
        raise ValueError("source snapshot bounds exceed 2GiB/100000 files")
    cwd, artifact_dir = Path(cwd).resolve(), Path(artifact_dir).resolve()
    if not cwd.is_dir():
        raise ValueError("working directory does not exist")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    if "kind" in config and "mode" in config and config["kind"] != config["mode"]:
        raise ValueError("conflicting runner kind/mode")
    mode = config.get("kind", config.get("mode", "docker"))
    if (
        "environment" in config
        and "env" in config
        and config["environment"] != config["env"]
    ):
        raise ValueError("conflicting runner environment/env")
    explicit_env = config.get("environment", config.get("env", {}))
    if not isinstance(explicit_env, dict):
        raise ValueError("runner env must be a mapping")
    for key, value in explicit_env.items():
        if (
            not isinstance(key, str)
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key)
            or not isinstance(value, str)
            or "\x00" in value
        ):
            raise ValueError("invalid explicit runner environment entry")
        if (
            re.search(
                r"TOKEN|SECRET|PASSWORD|CREDENTIAL|API_KEY|ACCESS_KEY|PRIVATE_KEY|SSH_AUTH|DOCKER_HOST",
                key,
                re.I,
            )
            or key == "HOME"
        ):
            raise ValueError(
                "credentials/control environment cannot be passed to benchmark"
            )
    warmup = config.get("warmup_command", [])
    if not isinstance(warmup, list) or any(
        not isinstance(a, str) or "\x00" in a for a in warmup
    ):
        raise ValueError("warmup_command must be argv")
    result = {
        "status": "failed",
        "returncode": None,
        "stdout": "",
        "stderr": "",
        "duration_seconds": 0.0,
        "environment": {},
        "artifacts": [],
    }
    output_limit = int(config.get("output_bytes", 2_000_000))
    if not 1024 <= output_limit <= 16_000_000:
        raise ValueError("output_bytes must be 1024..16000000")
    if cancel is not None and cancel.is_set():
        result["status"] = "cancelled"
        return result
    if mode == "host":
        if service_specs:
            raise ValueError("scenario services require the isolated Docker runner")
        if config.get("trusted") is not True:
            raise ValueError(
                "host mode requires explicit trusted=True; benchmark code has host privileges"
            )
        with tempfile.TemporaryDirectory(prefix="autoresearch-home-") as home:
            workspace = Path(home) / "work"
            workspace.mkdir()
            result["source_excluded"] = _snapshot(
                cwd,
                workspace,
                source_bytes,
                artifact_dir,
                source_files,
            )
            env = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": home,
                "LANG": "C.UTF-8",
                "AUTORESEARCH_ARTIFACT_DIR": str(artifact_dir),
            }
            env.update(explicit_env)
            try:
                warmed = (
                    _execute(
                        warmup,
                        cwd=workspace,
                        env=env,
                        timeout=remaining(),
                        cancel=cancel,
                        limit=output_limit,
                    )
                    if warmup
                    else None
                )
                if warmed and warmed["status"] != "succeeded":
                    result.update(warmed)
                else:
                    result.update(
                        _execute(
                            command,
                            cwd=workspace,
                            env=env,
                            timeout=remaining(),
                            cancel=cancel,
                            limit=output_limit,
                        )
                    )
                if warmed:
                    result["warmup"] = warmed
            except TimeoutError as exc:
                result.update(status="timed_out", stderr=str(exc))
        result["environment"] = _fingerprint(
            {
                "mode": "host",
                "trusted": True,
                "os": platform.platform(),
                "architecture": platform.machine(),
                "cpu_count": os.cpu_count(),
                "isolation": "none; explicitly trusted development",
                "explicit_env": explicit_env,
            }
        )
        result["duration_seconds"] = time.monotonic() - start
        (artifact_dir / "stdout.log").write_text(result["stdout"])
        (artifact_dir / "stderr.log").write_text(result["stderr"])
        (artifact_dir / "execution.json").write_text(
            json.dumps({**result, "command": command}, indent=2)
        )
        result["artifacts"] = _collect(
            artifact_dir, 64 * 1024 * 1024 + 2 * output_limit + 1_000_000
        )
        return result
    if mode != "docker":
        raise ValueError("runner mode must be docker or explicitly trusted host")
    docker = ["docker"]
    if config.get("docker_host"):
        endpoint = config["docker_host"]
        if not isinstance(endpoint, str) or not endpoint.startswith(
            ("ssh://", "tcp://", "unix://")
        ):
            raise ValueError("docker_host must be an explicit Docker endpoint")
        docker += ["--host", endpoint]
    name = "autoresearch-" + uuid.uuid4().hex
    result["container_name"] = name
    created = False
    scenario = None
    artifact_limit = int(config.get("artifact_mb", 64)) * 1024 * 1024
    limits = {
        "cpus": float(config.get("cpus", 1)),
        "memory_mb": int(config.get("memory_mb", 512)),
        "pids_limit": int(config.get("pids_limit", 128)),
        "workspace_mb": int(config.get("workspace_mb", 256)),
        "artifact_mb": int(config.get("artifact_mb", 64)),
        "temporary_mb": int(config.get("temporary_mb", 32)),
    }
    if not (
        0 < limits["cpus"] <= 64
        and all(0 < v <= 65536 for k, v in limits.items() if k != "cpus")
    ):
        raise ValueError("resource bounds must be positive and finite")
    workspace_inodes = int(config.get("workspace_inodes", max(60000, source_files * 3)))
    temporary_inodes = int(config.get("temporary_inodes", 40000))
    if not all(
        4096 <= value <= 1000000 for value in (workspace_inodes, temporary_inodes)
    ):
        raise ValueError("workspace and temporary inode limits must be 4096..1000000")
    limits.update(workspace_inodes=workspace_inodes, temporary_inodes=temporary_inodes)
    try:
        probe = _execute(
            docker
            + [
                "image",
                "inspect",
                config.get("image", "eclipse-temurin:25-jdk"),
                "--format",
                "{{json .}}",
            ],
            timeout=min(15, remaining()),
            limit=1_000_000,
        )
        if probe["status"] != "succeeded":
            raise RuntimeError(
                "Docker image unavailable; install/pull configured image before running: "
                + probe["stderr"]
            )
        image = json.loads(probe["stdout"])
        clock_wall_started = time.time()
        clock_started = time.monotonic()
        daemon = _execute(
            docker + ["info", "--format", "{{json .}}"],
            timeout=min(5, remaining()),
            limit=1_000_000,
        )
        clock_finished = time.monotonic()
        clock_wall_finished = time.time()
        if daemon["status"] != "succeeded":
            raise RuntimeError("Docker daemon unavailable: " + daemon["stderr"])
        daemon_info = json.loads(daemon["stdout"])
        result["daemon_clock_check"] = verify_daemon_clock(
            daemon_info.get("SystemTime"),
            clock_started,
            clock_finished,
            deadline=deadline,
            wall_started=clock_wall_started,
            wall_finished=clock_wall_finished,
        )
        daemon_deadline = result["daemon_clock_check"]["daemon_deadline_seconds"]
        scenario_environment = None
        if service_specs:
            scenario = Scenario(
                docker,
                service_specs,
                name,
                deadline,
                cancel,
                _execute,
                artifact_dir,
                daemon_deadline=daemon_deadline,
            )
            scenario_environment = scenario.start()
        result["environment"] = _fingerprint(
            {
                "mode": "docker",
                "image_id": image["Id"],
                "os": image["Os"],
                "architecture": image["Architecture"],
                "daemon": {
                    k: daemon_info.get(k)
                    for k in (
                        "ID",
                        "KernelVersion",
                        "Architecture",
                        "NCPU",
                        "MemTotal",
                        "ServerVersion",
                        "OperatingSystem",
                    )
                },
                "limits": limits,
                "network": scenario_environment["network"] if scenario else "none",
                **({"scenario": scenario_environment} if scenario else {}),
                "remote": bool(config.get("docker_host")),
                "jdk": next(
                    (
                        v.split("=", 1)[1]
                        for v in image.get("Config", {}).get("Env", [])
                        if v.startswith("JAVA_VERSION=")
                    ),
                    None,
                ),
                "explicit_env": explicit_env,
                "watchdog": "container PID1 absolute deadline; coordinator-independent",
                "clock_policy": "single daemon epoch deadline from <=5s probe and monotonic budget; static wall-clock skew accepted; post-probe clock jumps unsupported",
            }
        )
        with tempfile.TemporaryDirectory(prefix="autoresearch-source-") as temporary:
            staged = Path(temporary) / "source"
            staged.mkdir()
            excluded = _snapshot(
                cwd,
                staged,
                source_bytes,
                artifact_dir,
                source_files,
            )
            args = docker + [
                "run",
                "--detach",
                "--name",
                name,
                "--pull",
                "never",
                "--network",
                scenario.network if scenario else "none",
                "--dns",
                "127.0.0.1",
                "--label",
                "org.autoresearch.owner=" + name,
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--user",
                "0:0",
                "--cpus",
                str(limits["cpus"]),
                "--memory",
                f"{limits['memory_mb']}m",
                "--memory-swap",
                f"{limits['memory_mb']}m",
                "--pids-limit",
                str(limits["pids_limit"]),
                "--ulimit",
                "nofile=4096:4096" if scenario else "nofile=256:256",
                "--log-driver",
                "none",
                "--tmpfs",
                f"/work:rw,exec,nosuid,nodev,size={limits['workspace_mb']}m,mode=1777,nr_inodes={workspace_inodes}",
                "--tmpfs",
                f"/tmp:rw,exec,nosuid,nodev,size={limits['temporary_mb']}m,mode=1777,nr_inodes={temporary_inodes}",
                "--tmpfs",
                f"/artifacts:rw,nosuid,nodev,noexec,size={limits['artifact_mb']}m,mode=1777,nr_inodes=4096",
                "--env",
                "HOME=/tmp",
                "--env",
                "AUTORESEARCH_ARTIFACT_DIR=/artifacts",
                "--workdir",
                "/work",
                "--entrypoint",
                "/bin/sh",
                image["Id"],
                "-c",
                watchdog_script(daemon_deadline),
            ]
            if cancel is not None and cancel.is_set():
                result["status"] = "cancelled"
                return result
            created = True  # Even an uncertain CLI response must attempt cleanup.
            launched = _execute(args, timeout=min(15, remaining()), cancel=cancel)
            if scenario and launched["status"] in {
                "timed_out",
                "cancelled",
                "output_limit",
            }:
                scenario.uncertain = True
            if launched["status"] != "succeeded":
                raise RuntimeError("Docker start failed: " + launched["stderr"])
            source_archive = io.BytesIO()
            with tarfile.open(fileobj=source_archive, mode="w") as tar:
                for child in sorted(staged.iterdir()):
                    tar.add(child, arcname=child.name)
            copied = _execute(
                docker
                + [
                    "exec",
                    "--interactive",
                    "--user",
                    "65534:65534",
                    name,
                    "tar",
                    "--no-same-owner",
                    "-xf",
                    "-",
                    "-C",
                    "/work",
                ],
                timeout=min(15, remaining()),
                input_bytes=source_archive.getvalue(),
            )
            if copied["status"] != "succeeded":
                raise RuntimeError("source staging failed: " + copied["stderr"])
            exec_args = docker + ["exec", "--user", "65534:65534"]
            for key, value in explicit_env.items():
                exec_args += ["--env", f"{key}={value}"]
            exec_args += [name]
            warmed = (
                _execute(
                    exec_args + warmup,
                    timeout=remaining(),
                    cancel=cancel,
                    limit=output_limit,
                )
                if warmup
                else None
            )
            if warmed and warmed["status"] != "succeeded":
                result.update(warmed)
            else:
                result.update(
                    _execute(
                        exec_args + command,
                        timeout=remaining(),
                        cancel=cancel,
                        limit=output_limit,
                    )
                )
            if warmed:
                result["warmup"] = warmed
            result["source_excluded"] = excluded
            # Docker cp streams a tar archive. Validate every member ourselves;
            # no workload-controlled symlinks, devices, paths or hardlinks escape.
            exported = _execute(
                docker
                + [
                    "exec",
                    "--user",
                    "65534:65534",
                    name,
                    "tar",
                    "-cf",
                    "-",
                    "-C",
                    "/artifacts",
                    ".",
                ],
                timeout=10,
                limit=artifact_limit + 8 * 1024 * 1024,
                binary=True,
            )
            if exported["status"] == "succeeded":
                total = 0
                with tarfile.open(fileobj=io.BytesIO(exported["stdout"])) as tar:
                    members = tar.getmembers()
                    if len(members) > 4096:
                        raise ValueError("too many artifact files")
                    for member in members:
                        rel = Path(member.name)
                        if (
                            rel.is_absolute()
                            or ".." in rel.parts
                            or member.issym()
                            or member.islnk()
                        ):
                            raise ValueError("unsafe artifact archive member")
                        if member.isdir():
                            continue
                        if not member.isfile():
                            raise ValueError("non-regular artifact member")
                        total += member.size
                        if total > artifact_limit:
                            raise ValueError("artifact size limit exceeded")
                        destination = artifact_dir / "files" / rel
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.write_bytes(tar.extractfile(member).read())
            else:
                result["artifact_warning"] = (
                    "artifact collection unavailable: " + exported["stderr"][:1000]
                )
    except InterruptedError as exc:
        result.update(status="cancelled", stderr=result["stderr"] + "\n" + str(exc))
    except TimeoutError as exc:
        result.update(status="timed_out", stderr=result["stderr"] + "\n" + str(exc))
    except (
        OSError,
        ValueError,
        RuntimeError,
        subprocess.SubprocessError,
        tarfile.TarError,
    ) as exc:
        result.update(status="failed", stderr=result["stderr"] + "\n" + str(exc))
        if time.monotonic() >= deadline:
            result["status"] = "timed_out"
    finally:
        if scenario:
            try:
                warning = scenario.close()
            except (
                OSError,
                ValueError,
                RuntimeError,
                subprocess.SubprocessError,
            ) as exc:
                warning = (
                    "Scenario cleanup interrupted; independent deadlines and reaper remain active: "
                    + str(exc)[:1000]
                )
            if warning:
                result["cleanup_warning"] = warning
        elif created:
            cleanup = _execute(docker + ["rm", "--force", name], timeout=10)
            if cleanup["status"] != "succeeded":
                result["cleanup_warning"] = (
                    "container cleanup unconfirmed; independent deadline still applies"
                )
    result["duration_seconds"] = time.monotonic() - start
    (artifact_dir / "stdout.log").write_text(result["stdout"])
    (artifact_dir / "stderr.log").write_text(result["stderr"])
    (artifact_dir / "execution.json").write_text(
        json.dumps({**result, "command": command}, indent=2)
    )
    result["artifacts"] = _collect(
        artifact_dir, artifact_limit + 2 * output_limit + 1_000_000
    )
    return result
