import json
import pytest
from autoresearch.jvm import discover_jvm, jfr_command, jmh_command, parse_jmh
from autoresearch.measurement import InvalidMeasurement, validate_sample


def test_jmh_options_and_independent_forks():
    command = jmh_command("benchmarks.jar", forks=3)
    assert command[command.index("-w") + 1] == "1000ms"
    assert (
        command[-2:] == ["-foe", "true"] and command[command.index("-rf") + 1] == "json"
    )
    raw = [
        {
            "benchmark": "demo.Work.run",
            "forks": 2,
            "mode": "thrpt",
            "primaryMetric": {
                "score": 105,
                "scoreUnit": "ops/s",
                "rawData": [[90, 110], [100, 120]],
                "scorePercentiles": {"99.0": 123},
            },
            "secondaryMetrics": {},
        }
    ]
    sample = parse_jmh(json.dumps(raw))
    assert sample["jmh"][0]["measurements"]["primary"]["fork_means"] == [100, 110]
    assert sample["jmh"][0]["measurements"]["primary"]["score_percentiles"] == {
        "99.0": 123
    }
    assert sample["checks"] == {}
    with pytest.raises(InvalidMeasurement):
        validate_sample(sample)
    assert validate_sample(parse_jmh(raw, checks={"separate_evaluator": True}))
    raw[0]["primaryMetric"]["score"] = float("nan")
    with pytest.raises(ValueError):
        parse_jmh(raw)


def test_jfr_inserts_real_flag_and_discovery_does_not_execute_project(tmp_path):
    (tmp_path / "gradlew").write_text("do not execute")
    result = discover_jvm(tmp_path)
    assert result["build_files"] == ["gradlew"]
    command = jfr_command(["java", "-jar", "app.jar"])
    assert command[1].startswith("-XX:StartFlightRecording=")
    assert "dumponexit=true" in command[1]
    with pytest.raises(ValueError):
        jfr_command(["sh", "run.sh"])
