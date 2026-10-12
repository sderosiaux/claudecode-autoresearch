"""Exercise real short JVM windows; the five-minute proof is a separate artifact."""

from pathlib import Path
import json
import math
import os
import shutil
import subprocess

import pytest

from autoresearch.measurement import parse_benchmark

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "jvm-streaming"


@pytest.fixture(scope="module")
def compiled_workload(tmp_path_factory):
    if not shutil.which("java") or not shutil.which("javac"):
        pytest.skip("a JDK is required for the real memory-window harness")
    work = tmp_path_factory.mktemp("memory-window-java")
    for name in ("StreamWorkload.java", "StreamProcessor.java"):
        shutil.copy2(EXAMPLE / name, work / name)
    subprocess.run(
        [
            "javac",
            "-d",
            str(work),
            str(work / "StreamProcessor.java"),
            str(work / "StreamWorkload.java"),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return work


def execute_window(work, out, *, quiescence="0.05"):
    out.mkdir()
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "AUTORESEARCH_ARTIFACT_DIR": str(out),
    }
    return subprocess.run(
        [
            "java",
            "-Xms32m",
            "-Xmx64m",
            "-cp",
            str(work),
            "StreamWorkload",
            "--duration",
            "0.15",
            "--warmup",
            "0.1",
            "--quiescence",
            quiescence,
        ],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_memory_samples_match_timed_window_and_post_drain_phases(
    compiled_workload, tmp_path
):
    execution = execute_window(compiled_workload, tmp_path / "artifacts")
    assert execution.returncode == 0, execution.stderr
    result = parse_benchmark(execution.stdout)
    metrics, phases = result["metrics"], result["phases"]
    series = json.loads((tmp_path / "artifacts" / "memory-series.json").read_text())[
        "samples"
    ]
    assert (
        json.loads((tmp_path / "artifacts" / "memory-phases.json").read_text())
        == phases
    )
    duration = metrics["measurement.elapsed_seconds"]
    assert duration >= 0.15
    assert phases["measurement"]["elapsed_seconds"] == duration
    assert phases["measurement"]["sample_count"] == len(series) > 0
    assert all(0 <= row[0] <= duration * 1e9 for row in series)
    assert [row[0] for row in series] == sorted(row[0] for row in series)
    assert metrics["heap.sampled_peak_bytes"] == max(row[1] for row in series)
    assert all(math.isfinite(value) for value in metrics.values())
    if any(row[2] is not None for row in series):
        assert metrics["process.rss.sampled_peak_bytes"] == max(
            row[2] for row in series if row[2] is not None
        )
    else:
        assert "process.rss.sampled_peak_bytes" not in metrics
    drain, quiet = phases["drain"], phases["quiescence"]
    assert drain["model"] == "synchronous-no-queue" and drain["pending_messages"] == 0
    assert quiet["forced_gc"] is False and quiet["elapsed_seconds"] >= 0.05
    assert (
        quiet["reading"]["elapsed_after_measurement_ns"]
        > drain["reading"]["elapsed_after_measurement_ns"]
        >= 0
    )
    assert metrics["heap.after_drain_bytes"] == drain["reading"]["heap_bytes"]
    assert metrics["heap.after_quiescence_bytes"] == quiet["reading"]["heap_bytes"]
    assert all(result["checks"].values())
    assert result["checks"]["measurement_window_completed"]
    assert metrics["application.completed_messages"] > 0
    assert "application.latency.p99_ns" not in metrics


def test_quiescence_bound_rejected_before_measurement(compiled_workload, tmp_path):
    execution = execute_window(
        compiled_workload, tmp_path / "artifacts", quiescence="60.1"
    )
    assert execution.returncode != 0
    assert "invalid bounded workload settings" in execution.stderr
    assert not (tmp_path / "artifacts" / "memory-series.json").exists()


def test_candidate_patches_keep_protected_memory_harness_unchanged(tmp_path):
    for name in ("avoid-copy.patch", "batch-128.patch"):
        content = (EXAMPLE / name).read_text()
        assert "--- a/StreamProcessor.java" in content
        assert "StreamWorkload.java" not in content and "run.sh" not in content
        fixture = tmp_path / name
        fixture.mkdir()
        shutil.copy2(EXAMPLE / "StreamProcessor.java", fixture / "StreamProcessor.java")
        subprocess.run(
            ["git", "apply", "--check", str(EXAMPLE / name)],
            cwd=fixture,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
