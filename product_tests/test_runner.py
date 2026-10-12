import subprocess
import sys
import threading
import time
import pytest
from autoresearch.runner import run_command

TRUSTED = {"kind": "host", "trusted": True}


def test_host_requires_explicit_trust_and_strips_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-leak")
    with pytest.raises(ValueError):
        run_command(
            ["true"],
            tmp_path,
            tmp_path / "out",
            timeout_seconds=1,
            runner={"kind": "host"},
        )
    result = run_command(
        [
            sys.executable,
            "-c",
            'import os;print(os.environ.get("ANTHROPIC_API_KEY","absent"))',
        ],
        tmp_path,
        tmp_path / "out",
        timeout_seconds=2,
        runner=TRUSTED,
    )
    assert result["status"] == "succeeded" and result["stdout"].strip() == "absent"


def test_timeout_cancellation_output_bound_and_same_workspace_warmup(tmp_path):
    timeout = run_command(
        [sys.executable, "-c", "import time;time.sleep(20)"],
        tmp_path,
        tmp_path / "timeout",
        timeout_seconds=0.2,
        runner=TRUSTED,
    )
    assert timeout["status"] == "timed_out" and timeout["duration_seconds"] < 3
    event = threading.Event()
    threading.Timer(0.1, event.set).start()
    cancelled = run_command(
        [sys.executable, "-c", "import time;time.sleep(20)"],
        tmp_path,
        tmp_path / "cancel",
        timeout_seconds=3,
        cancel=event,
        runner=TRUSTED,
    )
    assert cancelled["status"] == "cancelled"
    bounded = run_command(
        [sys.executable, "-c", 'import sys;sys.stdout.write("x"*1000000)'],
        tmp_path,
        tmp_path / "output",
        timeout_seconds=3,
        runner={**TRUSTED, "output_bytes": 1024},
    )
    assert bounded["status"] == "output_limit" and len(bounded["stdout"]) == 1024
    prepared = run_command(
        [
            sys.executable,
            "-c",
            'from pathlib import Path;print(Path("prepared").read_text())',
        ],
        tmp_path,
        tmp_path / "prepared-out",
        timeout_seconds=3,
        runner={
            **TRUSTED,
            "warmup_command": [
                sys.executable,
                "-c",
                'from pathlib import Path;Path("prepared").write_text("yes")',
            ],
        },
    )
    assert prepared["stdout"].strip() == "yes"


def docker_available():
    try:
        return (
            subprocess.run(
                ["docker", "image", "inspect", "eclipse-temurin:25-jdk"],
                capture_output=True,
                timeout=5,
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False


@pytest.mark.skipif(
    not docker_available(), reason="preinstalled Docker JVM image required"
)
def test_real_docker_isolation_artifacts_and_timeout(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    out = tmp_path / "out"
    (source / ".env").write_text("SECRET=hidden")
    (source / "input").write_text("original")
    (source / "link").symlink_to(source / ".env")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "hidden")
    script = 'test "$(id -u)" = 65534; test ! -e .env; test ! -e link; test -z "${ANTHROPIC_API_KEY:-}"; test ! -e /var/run/docker.sock; test ! -e /root/.ssh; test "$(cat /proc/net/route | wc -l)" = 1; if touch /outside 2>/dev/null; then exit 9; fi; if kill -STOP 1 2>/dev/null; then exit 9; fi; echo changed > input; echo evidence > /artifacts/result; cat /sys/fs/cgroup/memory.max; cat /sys/fs/cgroup/pids.max; cat /sys/fs/cgroup/cpu.max; cat /proc/net/route'
    result = run_command(["sh", "-ec", script], source, out, timeout_seconds=5)
    assert result["status"] == "succeeded", result["stderr"]
    assert (source / "input").read_text() == "original"
    assert (out / "files/result").read_text().strip() == "evidence"
    assert "536870912" in result["stdout"] and "128" in result["stdout"]
    assert "100000 100000" in result["stdout"]
    assert result["environment"]["network"] == "none"
    assert ".env" in result["source_excluded"] and "link" in result["source_excluded"]
    timeout = run_command(
        ["sh", "-c", "sleep 100 & wait"],
        source,
        tmp_path / "timed",
        timeout_seconds=0.3,
    )
    assert timeout["status"] == "timed_out"
    remaining = subprocess.run(
        [
            "docker",
            "ps",
            "--filter",
            "name=" + timeout["container_name"],
            "--format",
            "{{.Names}}",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert not remaining.stdout.strip()


@pytest.mark.skipif(
    not docker_available(), reason="preinstalled Docker JVM image required"
)
def test_container_deadline_survives_coordinator_sigkill(tmp_path):
    # Observe a real allocated container, kill the process coordinating it, and
    # verify its independently owned PID1 deadline still stops the workload.
    helper = """
from pathlib import Path
import sys
import autoresearch.runner as runner
original=runner._execute
def observe(command, **kwargs):
    result=original(command, **kwargs)
    if command[:2]==["docker","run"] and result["status"]=="succeeded":
        print(command[command.index("--name")+1],flush=True)
    return result
runner._execute=observe
runner.run_command(["sh","-c","sleep 100 & wait"],Path(sys.argv[1]),Path(sys.argv[2]),timeout_seconds=2)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", helper, str(tmp_path), str(tmp_path / "out")],
        stdout=subprocess.PIPE,
        text=True,
    )
    name = process.stdout.readline().strip()
    assert name.startswith("autoresearch-")
    process.kill()
    process.wait(timeout=3)
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            state = subprocess.run(
                ["docker", "inspect", "--format", "{{.State.Running}}", name],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if state.returncode or state.stdout.strip() == "false":
                break
            time.sleep(0.2)
        else:
            pytest.fail("orphaned workload outlived independent container deadline")
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=5)
