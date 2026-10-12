#!/usr/bin/env python3
"""Run real Docker samples; no provider, paid API, or generated measurements."""

from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from autoresearch.measurement import compare, parse_benchmark
from autoresearch.runner import run_command

ROOT = Path(__file__).resolve().parent


def metric(name, unit, direction="minimize", role="objective", **extra):
    return dict(
        name=name,
        unit=unit,
        direction=direction,
        role=role,
        min_repetitions=3,
        max_cv=0.30,
        minimum_improvement=0.05,
        **extra,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--duration", type=float, default=1)
    parser.add_argument("--warmup", type=float, default=0.5)
    parser.add_argument("--image", default="eclipse-temurin:25-jdk")
    args = parser.parse_args()
    if not 3 <= args.repetitions <= 20:
        parser.error("repetitions must be 3..20")
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    source = (ROOT / "StreamProcessor.java").read_text()
    variants = {
        "baseline": source,
        "avoid-copy": source.replace("COPY_PAYLOAD = true", "COPY_PAYLOAD = false"),
        "batch-128": source.replace("BATCH_SIZE = 1", "BATCH_SIZE = 128"),
    }
    report = {
        "schema_version": 1,
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "source_hashes": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ("StreamProcessor.java", "StreamWorkload.java", "run.sh")
        },
        "settings": vars(args) | {"output": str(out)},
        "scenarios": {},
        "limitations": [
            "Synthetic CRC32 processing, not Kafka or application-wide evidence.",
            "RSS/heap peaks are sampled every approximately 20 ms during measurement.",
            "Latency is emitted only for fixed scheduled arrivals and includes batching/generator delays.",
            "Exploration is followed by independent new confirmation runs; no universal gain is promised.",
        ],
    }
    with tempfile.TemporaryDirectory(prefix="autoresearch-jvm-demo-") as temporary:
        work = Path(temporary)
        shutil.copy(ROOT / "run.sh", work / "run.sh")
        shutil.copy(ROOT / "StreamWorkload.java", work / "StreamWorkload.java")

        def execute(variant, scenario, key, jfr=False):
            (work / "StreamProcessor.java").write_text(variants[variant])
            argv = [
                "sh",
                "run.sh",
                "--duration",
                str(args.duration),
                "--warmup",
                str(args.warmup),
            ]
            if scenario == "paced":
                argv += ["--rate", "10000"]
            execution = run_command(
                argv,
                work,
                out / key,
                timeout_seconds=30,
                runner={
                    "kind": "docker",
                    "image": args.image,
                    "environment": {"JFR": "1"} if jfr else {},
                },
            )
            if execution["status"] != "succeeded":
                raise RuntimeError(
                    f"{key}: {execution['status']}: {execution['stderr']}"
                )
            sample = parse_benchmark(execution["stdout"])
            sample["environment_fingerprint"] = execution["environment"]["fingerprint"]
            sample["execution_artifacts"] = execution["artifacts"]
            print(json.dumps({"run": key, "metrics": sample["metrics"]}), flush=True)
            return sample

        for scenario in ("saturation", "paced"):
            specs = [
                metric(
                    "application.completed_messages_per_second",
                    "messages/s",
                    "maximize",
                ),
                metric("main_thread.allocated_bytes_per_message", "bytes/message"),
                metric(
                    "application.error_rate",
                    "fraction",
                    role="constraint",
                    operator="eq",
                    value=0,
                ),
            ]
            if scenario == "paced":
                specs = [
                    metric("application.latency.p99_ns", "ns"),
                    metric(
                        "application.completed_messages_per_second",
                        "messages/s",
                        "maximize",
                        role="constraint",
                        operator="gte",
                        value=9000,
                    ),
                    metric(
                        "application.offered_messages_per_second",
                        "messages/s",
                        role="constraint",
                        operator="eq",
                        value=10000,
                    ),
                    metric("main_thread.allocated_bytes_per_message", "bytes/message"),
                ]
            record = {
                "metric_specs": specs,
                "samples": {v: [] for v in variants},
                "comparisons": {},
                "confirmation": {},
            }
            # Reverse each alternating round to reduce simple order bias.
            for repeat in range(args.repetitions):
                order = list(variants) if repeat % 2 == 0 else list(reversed(variants))
                for variant in order:
                    record["samples"][variant].append(
                        execute(
                            variant,
                            scenario,
                            f"{scenario}/exploration/{repeat}-{variant}",
                        )
                    )
            for variant in ("avoid-copy", "batch-128"):
                record["comparisons"][variant] = compare(
                    record["samples"]["baseline"], record["samples"][variant], specs
                )
                before, after = [], []
                for repeat in range(args.repetitions):
                    # Independent baseline/candidate forks reserved after selection.
                    for name, target in (
                        [("baseline", before), (variant, after)]
                        if repeat % 2 == 0
                        else [(variant, after), ("baseline", before)]
                    ):
                        target.append(
                            execute(
                                name,
                                scenario,
                                f"{scenario}/confirmation-{variant}/{repeat}-{name}",
                            )
                        )
                final = compare(before, after, specs)
                final["confirmation_runs"] = {"baseline": before, "candidate": after}
                final["independent_confirmation"] = True
                final["confirmed"] = (
                    record["comparisons"][variant]["verdict"] == "promising"
                    and final["verdict"] == "promising"
                )
                record["confirmation"][variant] = final
            report["scenarios"][scenario] = record
            (out / "report.json").write_text(json.dumps(report, indent=2))
        report["profile"] = execute(
            "baseline", "saturation", "diagnostic-jfr", jfr=True
        )
        (out / "report.json").write_text(json.dumps(report, indent=2))
    print(str(out / "report.json"))


if __name__ == "__main__":
    main()
