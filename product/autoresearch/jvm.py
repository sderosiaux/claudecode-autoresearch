"""JVM discovery and adapters; correctness is supplied by a separate evaluator."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import shutil
import statistics
import subprocess


def discover_jvm(repository: Path) -> dict:
    """Inspect project files and installed tools without running project code."""
    repository = Path(repository)
    tools = {
        name: shutil.which(name)
        for name in ("java", "javac", "jfr", "jcmd", "mvn", "gradle", "sbt")
    }
    version, error = None, None
    if tools["java"]:
        try:
            probe = subprocess.run(
                [tools["java"], "-version"], capture_output=True, text=True, timeout=5
            )
            version = (
                (probe.stderr or probe.stdout).strip()
                if probe.returncode == 0
                else None
            )
            if probe.returncode:
                error = probe.stderr.strip()
        except (OSError, subprocess.SubprocessError) as exc:
            error = str(exc)
    files = [
        name
        for name in (
            "pom.xml",
            "build.gradle",
            "build.gradle.kts",
            "gradlew",
            "mvnw",
            "build.sbt",
            "project/build.properties",
        )
        if (repository / name).is_file()
    ]
    return {
        "tools": tools,
        "java_version": version,
        "error": error,
        "build_files": files,
        "capabilities": {
            "compile": bool(tools["javac"]),
            "jfr": bool(tools["jfr"]),
            "jmh": "adapter available; project must provide built JMH executable and correctness checks",
        },
        "scope": "coordinator discovery only; actual runner image/JDK fingerprint is recorded per execution",
    }


def jmh_command(
    jar: str,
    *,
    benchmark: str = ".*",
    forks: int = 3,
    warmup_iterations: int = 3,
    measurement_iterations: int = 5,
    iteration_seconds: float = 1,
    output: str = "/artifacts/jmh.json",
) -> list[str]:
    """JMH CLI options -f forks, -wi warmup iterations, -i measured iterations,
    -w/-r iteration durations, -rf json, -rff output, -foe true.
    Each fork is an independent JVM; iterations inside a fork are not replicas.
    """
    if (
        any(
            type(v) is not int or not 1 <= v <= 100
            for v in (forks, warmup_iterations, measurement_iterations)
        )
        or not math.isfinite(iteration_seconds)
        or not 0.001 <= iteration_seconds <= 3600
    ):
        raise ValueError("JMH requires positive bounded fork/iteration settings")
    # JMH TimeValue accepts integer quantities; decimal '1.0s' is not portable.
    duration = f"{math.ceil(iteration_seconds * 1000)}ms"
    return [
        "java",
        "-jar",
        jar,
        benchmark,
        "-f",
        str(forks),
        "-wi",
        str(warmup_iterations),
        "-i",
        str(measurement_iterations),
        "-w",
        duration,
        "-r",
        duration,
        "-rf",
        "json",
        "-rff",
        output,
        "-foe",
        "true",
    ]


def parse_jmh(data: str | list[dict], *, checks: dict | None = None) -> dict:
    """Decode real JMH JSON, retaining per-fork raw values and reported units.

    No correctness is inferred from performance output. Callers must supply the
    separate correctness evaluator's checks before generic sample validation.
    Percentile maps are retained verbatim rather than averaging their percentiles.
    """
    rows = json.loads(data) if isinstance(data, str) else data
    if not isinstance(rows, list) or not rows:
        raise ValueError("JMH output must be a nonempty JSON array")
    result = {
        "metrics": {},
        "units": {},
        "checks": dict(checks or {}),
        "jmh": [],
        "coverage": "JMH timers only; supplied external checks define correctness coverage",
    }
    for row in rows:
        if not isinstance(row, dict) or not row.get("benchmark"):
            raise ValueError("malformed JMH benchmark row")
        params = row.get("params", {})
        suffix = (
            "."
            + hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[
                :12
            ]
            if params
            else ""
        )
        prefix = row["benchmark"] + suffix
        record = {
            k: row.get(k)
            for k in (
                "benchmark",
                "mode",
                "threads",
                "forks",
                "jvm",
                "jdkVersion",
                "jmhVersion",
                "params",
            )
        }
        record["measurements"] = {}
        measurements = {
            "primary": row.get("primaryMetric"),
            **row.get("secondaryMetrics", {}),
        }
        for label, metric in measurements.items():
            if not isinstance(metric, dict):
                raise ValueError("missing JMH primary metric")
            score = metric.get("score")
            if (
                isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(score)
            ):
                raise ValueError("JMH score must be finite")
            unit = metric.get("scoreUnit")
            if not isinstance(unit, str) or not unit:
                raise ValueError("JMH metric unit is missing")
            name = prefix + "." + label
            if name in result["metrics"]:
                raise ValueError("duplicate JMH benchmark/parameters/metric")
            result["metrics"][name] = score
            result["units"][name] = unit
            raw = metric.get("rawData", [])
            if not isinstance(raw, list):
                raise ValueError("malformed JMH rawData")
            for fork in raw:
                if (
                    not isinstance(fork, list)
                    or not fork
                    or any(
                        isinstance(v, bool)
                        or not isinstance(v, (int, float))
                        or not math.isfinite(v)
                        for v in fork
                    )
                ):
                    raise ValueError("invalid JMH fork samples")
            record["measurements"][label] = {
                "raw_forks": raw,
                "fork_means": [statistics.mean(fork) for fork in raw],
                "score_percentiles": metric.get("scorePercentiles"),
                "score_confidence": metric.get("scoreConfidence"),
                "unit": unit,
            }
        result["jmh"].append(record)
    return result


def jfr_command(
    java_command: list[str], output_path: str = "/artifacts/profile.jfr"
) -> list[str]:
    """Return a separate diagnostic invocation; never silently profile confirmation."""
    if not java_command or Path(java_command[0]).name != "java":
        raise ValueError("JFR adapter needs a java argv")
    if not output_path or any(c in output_path for c in (",", "\n", "\x00")):
        raise ValueError("unsafe JFR output filename")
    return [
        java_command[0],
        f"-XX:StartFlightRecording=filename={output_path},settings=profile,dumponexit=true,maxsize=32m",
        *java_command[1:],
    ]
