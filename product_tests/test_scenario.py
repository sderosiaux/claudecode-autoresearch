"""Scenario isolation, ownership and cleanup, including real Docker execution."""

import io
import json
import math
import tarfile
import subprocess
import sys
import time

import pytest
from pydantic import ValidationError

from autoresearch.runner import run_command
from autoresearch.scenario import services, verify_daemon_clock, watchdog_script


def test_watchdog_translates_static_skew_from_monotonic_budget(monkeypatch):
    timestamp = 1791777600.0
    for offset in (-129, 129, 86400):
        receipt = verify_daemon_clock(
            "2026-10-12T04:00:00Z",
            100.0,
            100.4,
            deadline=120.0,
            wall_started=timestamp - offset,
            wall_finished=timestamp - offset + 0.4,
        )
        assert receipt["daemon_deadline_seconds"] == math.floor(timestamp + 19.6 + 10)
        assert receipt["probe_roundtrip_seconds"] == pytest.approx(0.4)
        assert receipt["remaining_budget_seconds"] == pytest.approx(19.6)
        assert receipt["daemon_time"] == "2026-10-12T04:00:00Z"
    # Wall-clock changes after calibration cannot change generated admission scripts.
    monkeypatch.setattr(
        "autoresearch.scenario.time.time",
        lambda: pytest.fail("coordinator wall clock reused"),
    )
    script = watchdog_script(receipt["daemon_deadline_seconds"])
    assert str(math.floor(timestamp + 29.6)) in script
    assert "/bin/date" in script and "exec /bin/sleep" in script
    assert watchdog_script(receipt["daemon_deadline_seconds"]) == script


@pytest.mark.parametrize(
    "value,before,after,deadline",
    [
        (None, 100, 100.1, 120),
        ("invalid", 100, 100.1, 120),
        ("2026-10-12T04:00:00", 100, 100.1, 120),
        ("2026-10-12T04:00:00Z", 100, 105.001, 120),
        ("2026-10-12T04:00:00Z", 100, 99, 120),
        ("2026-10-12T04:00:00Z", float("nan"), 100, 120),
        ("2026-10-12T04:00:00Z", 100, 100.1, float("inf")),
    ],
)
def test_watchdog_rejects_invalid_or_slow_clock_evidence(
    value, before, after, deadline
):
    with pytest.raises(ValueError, match="clock"):
        verify_daemon_clock(value, before, after, deadline=deadline)


def test_watchdog_clock_probe_spends_budget_conservatively():
    receipt = verify_daemon_clock("2026-10-12T04:00:00Z", 100, 105, deadline=120)
    assert receipt["daemon_deadline_seconds"] == 1791777600 + 15 + 10
    with pytest.raises(TimeoutError):
        verify_daemon_clock("2026-10-12T04:00:00Z", 100, 105, deadline=105)
    for invalid in (None, True, 0, -1, 1.5, float("inf")):
        with pytest.raises(ValueError, match="calibrated"):
            watchdog_script(invalid)


def test_all_service_and_workload_admissions_share_one_translated_epoch(
    tmp_path, monkeypatch
):
    commands = []
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w"):
        pass

    class Reaper:
        def __init__(self, *args, **kwargs):
            pass

        def terminate(self):
            pass

        def wait(self, **kwargs):
            pass

    def execute(command, **kwargs):
        commands.append(command)
        stdout = ""
        if command[1:3] == ["image", "inspect"]:
            stdout = json.dumps(
                {
                    "Id": "sha256:fixture",
                    "Os": "linux",
                    "Architecture": "arm64",
                    "Config": {"Env": []},
                }
            )
        elif command[1] == "info":
            assert kwargs["timeout"] <= 5
            stdout = json.dumps({"SystemTime": "2026-10-12T04:00:00Z"})
        elif command[1:3] == ["network", "inspect"]:
            stdout = json.dumps(
                [
                    {
                        "Internal": True,
                        "Options": {
                            "com.docker.network.bridge.gateway_mode_ipv4": "isolated"
                        },
                    }
                ]
            )
        elif kwargs.get("binary"):
            stdout = archive.getvalue()
        return {"status": "succeeded", "stdout": stdout, "stderr": "", "returncode": 0}

    monkeypatch.setattr("autoresearch.runner._execute", execute)
    monkeypatch.setattr("autoresearch.scenario.subprocess.Popen", Reaper)
    result = run_command(
        ["true"],
        tmp_path,
        tmp_path / "out",
        timeout_seconds=30,
        runner={"services": [service(), service(name="other")]},
    )
    assert result["status"] == "succeeded", result
    admissions = [command for command in commands if command[1] == "run"]
    assert len(admissions) == 3
    expected = watchdog_script(result["daemon_clock_check"]["daemon_deadline_seconds"])
    assert all(command[-1] == expected for command in admissions)
    assert sum(command[1] == "info" for command in commands) == 1
    evidence = json.loads((tmp_path / "out/scenario.json").read_text())
    assert (
        evidence["daemon_deadline_seconds"]
        == result["daemon_clock_check"]["daemon_deadline_seconds"]
    )


def service(**changes):
    return {
        "name": "peer",
        "image": "alpine:3.20",
        "command": [
            "sh",
            "-c",
            "echo ready > /tmp/ready; while true; do printf 'pong\\n' | nc -l -p 4321; done",
        ],
        "ready_command": ["test", "-s", "/tmp/ready"],
        "memory_mb": 64,
        **changes,
    }


@pytest.mark.parametrize(
    "change",
    [
        {"name": "../escape"},
        {"user": "0:0"},
        {"user": "root"},
        {"environment": {"ANTHROPIC_API_KEY": "forbidden"}},
        {"tmpfs": {"/var/run/docker.sock": 1, "/var/run": 2}},
        {"tmpfs": {"/proc": 1}},
        {"tmpfs": {"/tmp": 0}},
        {"cpus": float("inf")},
        {"memory_mb": 0},
        {"ready_command": []},
        {"network": "host"},
        {"volumes": ["/:/host"]},
    ],
)
def test_invalid_service_rejected_before_docker(tmp_path, monkeypatch, change):
    monkeypatch.setattr(
        "autoresearch.runner._execute",
        lambda *a, **kw: pytest.fail("invalid scenario allocated resources"),
    )
    with pytest.raises((ValueError, ValidationError)):
        run_command(
            ["true"],
            tmp_path,
            tmp_path / "out",
            timeout_seconds=1,
            runner={"services": [service(**change)]},
        )


def test_service_count_names_and_host_mode_bounded(tmp_path):
    with pytest.raises(ValueError):
        services([service()] * 2)
    with pytest.raises(ValueError):
        services([service(name=f"s{i}") for i in range(5)])
    with pytest.raises(ValueError, match="isolated Docker"):
        run_command(
            ["true"],
            tmp_path,
            tmp_path / "out",
            timeout_seconds=1,
            runner={"kind": "host", "trusted": True, "services": [service()]},
        )


def images_available():
    try:
        return all(
            subprocess.run(
                ["docker", "image", "inspect", image], capture_output=True, timeout=5
            ).returncode
            == 0
            for image in ("alpine:3.20", "eclipse-temurin:25-jdk")
        )
    except (OSError, subprocess.SubprocessError):
        return False


@pytest.mark.skipif(
    not images_available(), reason="preinstalled scenario images required"
)
def test_service_path_cannot_hijack_privileged_cleanup(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    spec = service(
        environment={"PATH": "/tmp:/bin:/usr/bin"},
        command=[
            "/bin/sh",
            "-c",
            "printf '#!/bin/sh\\necho hijacked-root-cleanup\\n' > /tmp/tail; chmod +x /tmp/tail; echo service-ready; touch /tmp/ready; sleep 30",
        ],
        ready_command=["/bin/sh", "-c", "test -e /tmp/ready"],
    )
    out = tmp_path / "out"
    result = run_command(
        ["true"], source, out, timeout_seconds=15, runner={"services": [spec]}
    )
    assert result["status"] == "succeeded", result
    assert (out / "service-peer.log").read_text() == "service-ready\n"
    assert not result.get("cleanup_warning")


def absent(kind, name):
    return (
        subprocess.run(
            ["docker", kind, "inspect", name], capture_output=True, timeout=5
        ).returncode
        != 0
    )


@pytest.mark.skipif(
    not images_available(), reason="preinstalled scenario images required"
)
def test_real_service_network_evidence_and_fresh_state(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    command = [
        "bash",
        "-ec",
        'exec 3<>/dev/tcp/peer/4321; read -r line <&3; test "$line" = pong; echo "$line"; test ! -e /var/run/docker.sock; test ! -e /root/.aws; if getent hosts example.com; then exit 9; fi; test $(awk \'NR>1 && $2=="00000000" {print}\' /proc/net/route | wc -l) = 0',
    ]
    results = []
    for index in range(2):
        out = tmp_path / str(index)
        result = run_command(
            command, source, out, timeout_seconds=15, runner={"services": [service()]}
        )
        assert result["status"] == "succeeded", result
        assert result["stdout"].strip() == "pong"
        evidence = json.loads((out / "scenario.json").read_text())
        assert evidence["services"][0]["status"] == "ready"
        assert evidence["services"][0]["cleanup"] == "removed"
        assert absent("container", evidence["services"][0]["container"])
        assert absent("container", result["container_name"])
        assert absent("network", evidence["network"])
        assert result["environment"]["network"].startswith("internal bridge")
        results.append(result)
    assert (
        results[0]["environment"]["fingerprint"]
        == results[1]["environment"]["fingerprint"]
    )
    assert results[0]["container_name"] != results[1]["container_name"]


@pytest.mark.skipif(
    not images_available(), reason="preinstalled scenario images required"
)
def test_readiness_failure_retains_logs_and_cleans_owned_resources(tmp_path):
    out = tmp_path / "out"
    result = run_command(
        ["true"],
        tmp_path,
        out,
        timeout_seconds=10,
        runner={
            "services": [
                service(
                    command=["sh", "-c", "echo deliberate-start-failure; exit 7"],
                    ready_timeout_seconds=0.2,
                )
            ]
        },
    )
    assert result["status"] == "failed"
    assert "readiness" in result["stderr"]
    evidence = json.loads((out / "scenario.json").read_text())
    assert "deliberate-start-failure" in (out / "service-peer.log").read_text()
    assert absent("network", evidence["network"])
    assert absent("container", evidence["services"][0]["container"])


@pytest.mark.skipif(
    not images_available(), reason="preinstalled scenario images required"
)
def test_scenario_reaper_survives_coordinator_sigkill(tmp_path):
    helper = """
import json,sys
from pathlib import Path
import autoresearch.runner as runner
original=runner._execute
def observe(command, **kwargs):
    result=original(command, **kwargs)
    if command[:2]==["docker","run"] and result["status"]=="succeeded":
        name=command[command.index("--name")+1]
        if not name.endswith("-peer"): print(name,flush=True)
    return result
runner._execute=observe
runner.run_command(["sh","-c","sleep 100"],Path(sys.argv[1]),Path(sys.argv[1])/"out",timeout_seconds=5,runner={"services":[json.loads(sys.argv[2])]})
"""
    process = subprocess.Popen(
        [sys.executable, "-c", helper, str(tmp_path), json.dumps(service())],
        stdout=subprocess.PIPE,
        text=True,
    )
    name = process.stdout.readline().strip()
    assert name.startswith("autoresearch-")
    process.kill()
    process.wait(timeout=3)
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if (
                absent("container", name)
                and absent("container", name + "-peer")
                and absent("network", name + "-network")
            ):
                break
            time.sleep(0.2)
        else:
            pytest.fail("scenario resources survived coordinator crash cleanup")
    finally:
        subprocess.run(
            ["docker", "rm", "--force", name, name + "-peer"],
            capture_output=True,
            timeout=5,
        )
        subprocess.run(
            ["docker", "network", "rm", name + "-network"],
            capture_output=True,
            timeout=5,
        )


@pytest.mark.parametrize("status", ["timed_out", "cancelled", "output_limit"])
@pytest.mark.parametrize(
    "operation", ["readiness", "container_admission", "network_admission"]
)
def test_readiness_timeout_cleanup_proven_but_late_admission_keeps_reaper(
    tmp_path, monkeypatch, status, operation
):
    from autoresearch.scenario import Scenario

    calls = []
    reapers = []
    failed_once = False

    class Reaper:
        def __init__(self, *args, **kwargs):
            self.terminated = False
            reapers.append(self)

        def terminate(self):
            self.terminated = True

        def wait(self, **kwargs):
            return 0

    def execute(command, **kwargs):
        nonlocal failed_once
        calls.append(command)
        argv = command[1:]
        stdout = ""
        if argv[:2] == ["image", "inspect"]:
            stdout = json.dumps({"Id": "sha256:public-unit-fixture"})
        elif argv[:2] == ["network", "inspect"]:
            stdout = json.dumps(
                [
                    {
                        "Internal": True,
                        "Options": {
                            "com.docker.network.bridge.gateway_mode_ipv4": "isolated"
                        },
                    }
                ]
            )
        target = (
            (
                operation == "readiness"
                and argv[:1] == ["exec"]
                and "--detach" not in argv
                and argv[-3:] == ["test", "-s", "/tmp/ready"]
            )
            or (operation == "container_admission" and argv[:1] == ["run"])
            or (operation == "network_admission" and argv[:2] == ["network", "create"])
        )
        result_status = "succeeded"
        if target and not failed_once:
            failed_once = True
            result_status = status
        return {
            "status": result_status,
            "stdout": stdout,
            "stderr": "controlled probe failure",
            "returncode": 0 if result_status == "succeeded" else None,
        }

    monkeypatch.setattr("autoresearch.scenario.subprocess.Popen", Reaper)
    monkeypatch.setattr("autoresearch.scenario.time.sleep", lambda _: None)
    scenario = Scenario(
        ["docker"],
        services([service()]),
        "owned-unit-scenario",
        time.monotonic() + 30,
        None,
        execute,
        tmp_path,
        daemon_deadline=1791777600,
    )
    if operation == "readiness":
        scenario.start()
        assert scenario.records[0]["status"] == "ready"
        assert scenario.uncertain is False
        assert scenario.close() is None
        assert reapers[0].terminated is True
        assert scenario.records[0]["cleanup"] == "removed"
    else:
        with pytest.raises(RuntimeError, match="Scenario command failed"):
            scenario.start()
        assert scenario.uncertain is True
        assert "cleanup unconfirmed" in scenario.close()
        assert reapers[0].terminated is False
    assert failed_once
    assert [
        "docker",
        "rm",
        "--force",
        "owned-unit-scenario",
        "owned-unit-scenario-peer",
    ] in calls
    assert ["docker", "network", "rm", "owned-unit-scenario-network"] in calls


@pytest.mark.parametrize(
    "argv", [["image", "inspect", "fixture"], ["network", "inspect", "owned"]]
)
@pytest.mark.parametrize("status", ["timed_out", "cancelled", "output_limit"])
def test_read_only_inspection_failure_does_not_imply_late_resource_admission(
    tmp_path, argv, status
):
    from autoresearch.scenario import Scenario

    scenario = Scenario(
        ["docker"],
        [],
        "owned-unit-inspection",
        time.monotonic() + 10,
        None,
        lambda *_args, **_kw: {"status": status},
        tmp_path,
        daemon_deadline=1791777600,
    )
    assert scenario.call(argv)["status"] == status
    assert scenario.uncertain is False
