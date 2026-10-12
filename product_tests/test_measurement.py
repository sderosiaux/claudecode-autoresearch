import json
import pytest
from autoresearch.measurement import (
    InvalidMeasurement,
    compare,
    parse_benchmark,
    summarize,
    pareto_front,
)

SPECS = [
    {
        "name": "throughput",
        "unit": "msg/s",
        "direction": "maximize",
        "role": "objective",
        "min_repetitions": 3,
        "minimum_improvement": 0.02,
        "max_cv": 0.15,
    }
]


def samples(values, **kwargs):
    return [
        {"metrics": {"throughput": v}, "checks": {"correct": True}, **kwargs}
        for v in values
    ]


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), True, "7"])
def test_missing_nonfinite_and_boolean_metrics_rejected(value):
    with pytest.raises(InvalidMeasurement):
        parse_benchmark(
            json.dumps({"metrics": {"throughput": value}, "checks": {"ok": True}}),
            SPECS,
        )


def test_explicit_checks_and_unambiguous_output_required():
    with pytest.raises(InvalidMeasurement):
        parse_benchmark('{"metrics":{"throughput":1}}')
    result = json.dumps(samples([1])[0])
    assert parse_benchmark("ordinary log\n" + result)["metrics"]["throughput"] == 1
    with pytest.raises(InvalidMeasurement):
        parse_benchmark(result + "\n" + result)


def test_confidence_and_noise_do_not_convert_exploration_to_confirmation():
    verdict = compare(samples([100, 100, 100]), samples([125, 125, 125]), SPECS)
    assert verdict["verdict"] == "promising"
    assert verdict["confirmation_required"] is True
    assert (
        compare(samples([50, 100, 150]), samples([120, 125, 130]), SPECS)["verdict"]
        == "inconclusive"
    )
    assert (
        compare(samples([100, 100, 100]), samples([101, 101, 101]), SPECS)["verdict"]
        == "inconclusive"
    )
    assert compare(samples([100]), samples([125]), SPECS)["verdict"] == "inconclusive"


def test_units_checks_environment_and_constraints():
    broken = samples([100, 100, 100])
    broken[0]["units"] = {"throughput": "bytes/s"}
    assert (
        compare(broken, samples([120, 120, 120]), SPECS)["verdict"]
        == "invalid_measurement"
    )
    broken = samples([120, 120, 120])
    broken[1]["checks"]["correct"] = False
    assert (
        compare(samples([100, 100, 100]), broken, SPECS)["verdict"]
        == "constraint_failed"
    )
    assert (
        compare(
            samples([100] * 3, environment_fingerprint="old"),
            samples([120] * 3, environment_fingerprint="new"),
            SPECS,
        )["verdict"]
        == "invalid_measurement"
    )
    constrained = [{**SPECS[0], "role": "constraint", "operator": "gte", "value": 80}]
    assert (
        compare(samples([100] * 3), samples([70, 120, 170]), constrained)["verdict"]
        == "constraint_failed"
    )


def test_diagnostic_noise_is_retained_without_deciding_objective_verdict():
    diagnostic = {
        "name": "generator_lateness",
        "unit": "ms",
        "direction": "minimize",
        "role": "info",
        "max_cv": 0.15,
    }
    before, after = samples([100] * 3), samples([125] * 3)
    for group in (before, after):
        for sample, lateness in zip(group, [0.001, 0.02, 0.5]):
            sample["metrics"]["generator_lateness"] = lateness
    result = compare(before, after, [*SPECS, diagnostic])
    assert result["verdict"] == "promising"
    assert result["baseline"]["metrics"]["generator_lateness"]["noise_exceeded"]
    assert result["baseline"]["metrics"]["generator_lateness"]["values"] == [
        0.001,
        0.02,
        0.5,
    ]
    assert (
        compare(before, after, [*SPECS, {**diagnostic, "role": "objective"}])["verdict"]
        == "inconclusive"
    )


def test_tradeoffs_and_pareto_keep_useful_candidates():
    specs = SPECS + [
        {
            "name": "memory",
            "unit": "bytes",
            "direction": "minimize",
            "role": "objective",
        }
    ]

    def runs(t, m):
        return [
            {"metrics": {"throughput": t, "memory": m}, "checks": {"ok": True}}
            for _ in range(3)
        ]

    baseline = runs(100, 100)
    fast = runs(130, 130)
    small = runs(100, 80)
    dominated = runs(90, 140)
    comparison = compare(baseline, fast, specs)
    assert comparison["verdict"] == "promising" and comparison["tradeoff"]
    candidates = [
        {"id": n, "summary": summarize(s, specs)}
        for n, s in [("fast", fast), ("small", small), ("dominated", dominated)]
    ]
    assert {c["id"] for c in pareto_front(candidates, specs)} == {"fast", "small"}
