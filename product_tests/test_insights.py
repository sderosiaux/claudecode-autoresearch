from copy import deepcopy
import json

from autoresearch.insights import build_insights
from autoresearch.models import digest


def metric(name, direction="minimize", role="objective", **extra):
    return {
        "name": name,
        "unit": "bytes" if name == "rss" else "messages/s",
        "direction": direction,
        "role": role,
        **extra,
    }


def analysis(values, *, valid=True, verdict="promising"):
    return {
        "verdict": verdict,
        "baseline": {"valid": valid},
        "candidate": {"valid": valid},
        "metrics": values,
        "reasons": [],
    }


def pair(
    before, after, fraction=None, *, improved=True, regressed=False, interval=None
):
    return {
        "baseline": before,
        "candidate": after,
        "improvement_fraction": fraction,
        "gain_ci95": interval,
        "resolved_improvement": improved,
        "resolved_regression": regressed,
    }


def experiment(
    eid,
    values=None,
    *,
    status="succeeded",
    verdict="confirmed",
    strategy="allocation-copy",
    **extra,
):
    result = {
        "id": eid,
        "protocol_hash": "current",
        "status": status,
        "verdict": verdict,
        "strategy": strategy,
        "hypothesis": "Hypothesis " + eid,
        "created_at": "2026-10-11T00:00:00Z",
    }
    if values is not None:
        result["comparison"] = analysis(values)
        if verdict == "confirmed":
            result["confirmation"] = analysis(values)
    result.update(extra)
    return result


def detail(experiments=None, accepted=None, metrics=None):
    experiments = experiments or []
    return {
        "campaign": {
            "protocol_hash": "current",
            "base_revision": "base",
            "runner": {"kind": "docker"},
            "baseline_summary": {
                "valid": True,
                "metrics": {"rss": {"mean": 110}, "throughput": {"mean": 95}},
            },
            "strategy_plan": [
                {
                    "id": "allocation-copy",
                    "title": "Remove copies",
                    "falsifier": "RSS unchanged",
                    "tradeoffs": ["Aliasing"],
                },
                {
                    "id": "batching",
                    "title": "Batch work",
                    "falsifier": "Latency rises",
                    "tradeoffs": ["Waiting"],
                },
            ],
        },
        "protocol_hash": "current",
        "protocol": {
            "metrics": metrics or [metric("rss"), metric("throughput", "maximize")]
        },
        "experiments": experiments,
        "comparison": experiments if accepted is None else accepted,
        "events": [],
    }


def by_name(result):
    return {m["name"]: m for m in result["metrics"]}


def test_individual_confirmed_metric_wins_do_not_become_global_winner():
    small = experiment(
        "small",
        {
            "rss": pair(100, 70, 0.30, interval=[25, 35]),
            "throughput": pair(100, 105, 0.05, improved=False, interval=[-3, 13]),
        },
    )
    fast = experiment(
        "fast",
        {
            "rss": pair(100, 120, -0.20, improved=False, regressed=True),
            "throughput": pair(110, 150, 40 / 110, interval=[30, 50]),
        },
        strategy="batching",
    )
    result = build_insights(detail([small, fast]))
    metrics = by_name(result)
    assert metrics["rss"]["baseline"] == 110
    assert metrics["rss"]["best"]["experiment_id"] == "small"
    assert metrics["throughput"]["best"]["experiment_id"] == "fast"
    assert metrics["throughput"]["best"]["baseline"] == 110
    assert metrics["throughput"]["best"]["improvement_fraction"] == 40 / 110
    assert metrics["rss"]["best"]["gain_ci95"] == [25, 35]
    assert "best" not in result and "best_candidate" not in result
    assert result["summary"]["confirmed"] == 2
    only_small = by_name(build_insights(detail([small])))
    assert only_small["throughput"]["best"] is None
    assert only_small["throughput"]["points"][0]["resolved_improvement"] is False


def test_best_observed_value_not_largest_relative_gain_and_tie_is_stable():
    first = experiment("first", {"rss": pair(100, 70, 0.30)})
    lower = experiment("lower", {"rss": pair(65, 60, 5 / 65)})
    tied = experiment("tied", {"rss": pair(200, 60, 0.70)})
    result = by_name(build_insights(detail([first, lower, tied])))
    assert result["rss"]["best"]["experiment_id"] == "lower"
    assert [p["sequence"] for p in result["rss"]["points"]] == [1, 2, 3]


def test_zero_baseline_and_constraint_are_observations_without_fake_gain():
    metrics = [
        metric("throughput", "maximize"),
        metric("errors", role="constraint", operator="eq", value=0),
    ]
    candidate = experiment(
        "zero",
        {
            "throughput": pair(0, 5, float("inf"), interval=[4, 6]),
            "errors": pair(0, 0, 1, improved=False),
        },
    )
    result = by_name(build_insights(detail([candidate], metrics=metrics)))
    assert result["throughput"]["best"]["improvement_fraction"] is None
    assert result["throughput"]["best"]["baseline"] == 0
    assert result["errors"]["best"] is None
    assert result["errors"]["points"][0]["candidate"] == 0
    assert result["errors"]["points"][0]["improvement_fraction"] is None
    assert (
        result["errors"]["operator"] == "eq"
        and result["errors"]["value"] == result["errors"]["threshold"] == 0
    )


def test_unaccepted_history_and_accepted_legacy_keep_distinct_contexts():
    old = experiment("old", {"rss": pair(100, 1, 0.99)}, protocol_hash="old")
    legacy = experiment("legacy", {"rss": pair(100, 80, 0.2)})
    current = experiment("current", {"rss": pair(100, 90, 0.1)})
    accepted_legacy = {
        **legacy,
        "measurement_context_source": "legacy_recorded_runs",
        "measurement_context_warning": "Runner request hash unavailable",
    }
    d = detail([old, legacy, current], accepted=[accepted_legacy, current])
    d["campaign"]["baseline_summary"].update(
        measurement_context_source="legacy_recorded_runs",
        measurement_context_warning="Legacy baseline",
    )
    result = build_insights(d)
    rss = by_name(result)["rss"]
    assert rss["best"]["experiment_id"] == "legacy"
    assert (
        rss["best"]["measurement_context_warning"] == "Runner request hash unavailable"
    )
    assert rss["baseline_context_warning"] == "Legacy baseline"
    assert [p["sequence"] for p in rss["points"]] == [2, 3]
    rows = result["tracks"][0]["experiments"]
    assert rows[0]["category"] == "historical" and rows[0]["current_context"] is False
    assert rows[1]["current_context"] is True
    assert result["summary"]["historical"] == 1 and result["summary"]["current"] == 2
    assert result["context"]["legacy_evidence_count"] == 1


def test_failures_cancellations_and_duplicates_cannot_supply_wins_even_with_bad_comparison():
    values = {"rss": pair(100, 1, 0.99)}
    failed = experiment("failed", values, status="failed", error="build failed")
    cancelled = experiment(
        "cancelled",
        values,
        status="cancelled",
        verdict="inconclusive",
        reason="user stop",
    )
    duplicate = experiment(
        "duplicate",
        values,
        duplicate_of="other",
        duplicate_reason="Exact tree already tested",
    )
    constraint = experiment("constraint", values, verdict="constraint_failed")
    malformed_accept = {**failed, "status": "succeeded"}
    result = build_insights(
        detail(
            [failed, cancelled, duplicate, constraint],
            accepted=[malformed_accept, cancelled, duplicate, constraint],
        )
    )
    assert by_name(result)["rss"]["best"] is None
    assert [p["experiment_id"] for p in by_name(result)["rss"]["points"]] == [
        "constraint"
    ]
    assert result["summary"]["failed"] == 1 and result["summary"]["skipped"] == 2
    assert result["summary"]["constraint_failed"] == 1
    assert result["summary"]["evidence"]["confirmed"] == 0
    rows = result["tracks"][0]["experiments"]
    assert [r["reason"] for r in rows[:3]] == [
        "build failed",
        "user stop",
        "Exact tree already tested",
    ]
    assert rows[1]["verdict"] == "inconclusive" and rows[1]["category"] == "skipped"


def test_invalid_nonfinite_missing_metrics_and_unknown_validity_do_not_win():
    invalid = experiment(
        "invalid", {"rss": pair(100, float("nan"), 0.2)}, verdict="invalid_measurement"
    )
    missing = experiment("missing", {"throughput": pair(100, 120, 0.2)})
    bad_validity = experiment("bad-validity", {"rss": pair(100, 50, 0.5)})
    bad_validity["confirmation"]["candidate"]["valid"] = False
    unknown_validity = experiment("unknown-validity", {"rss": pair(100, 40, 0.6)})
    unknown_validity["confirmation"].pop("baseline")
    result = by_name(
        build_insights(detail([invalid, missing, bad_validity, unknown_validity]))
    )
    assert result["rss"]["best"] is None
    assert [p["experiment_id"] for p in result["rss"]["points"]] == [
        "bad-validity",
        "unknown-validity",
    ]
    assert result["throughput"]["best"]["experiment_id"] == "missing"
    json.dumps(result, allow_nan=False)


def test_unmeasured_current_pending_old_runner_and_unassigned_new_campaign():
    unmeasured = detail()
    del unmeasured["campaign"]["baseline_summary"]
    assert all(
        metric["baseline_valid"] is None
        for metric in build_insights(unmeasured)["metrics"]
    )
    unmeasured["campaign"]["baseline_summary"] = {"valid": False}
    assert all(
        metric["baseline_valid"] is False
        for metric in build_insights(unmeasured)["metrics"]
    )
    queued = experiment("queued", status="queued", verdict=None)
    running = experiment("running", status="running", verdict=None)
    old_runner = experiment(
        "old-runner",
        status="cancelled",
        verdict=None,
        evaluation_context={
            "protocol_hash": "current",
            "baseline_revision": "base",
            "runner_config_hash": "old",
        },
    )
    result = build_insights(detail([queued, running, old_runner], accepted=[]))
    assert (
        result["summary"]["queued"]
        == result["summary"]["running"]
        == result["summary"]["historical"]
        == 1
    )
    fresh = build_insights(
        {
            "campaign": {},
            "experiments": [{"id": "new", "status": "queued", "protocol_hash": None}],
            "comparison": [],
        }
    )
    assert fresh["available"] is False and fresh["reason"]
    assert fresh["summary"]["queued"] == 1 and fresh["summary"]["historical"] == 0
    assert fresh["tracks"][0]["experiments"][0]["current_context"] is True


def test_strategy_assignment_precedes_provider_prose_and_untried_tracks_remain():
    candidate = experiment(
        "assigned",
        {"rss": pair(100, 80, 0.2)},
        strategy="Prose not matching any card",
        strategy_id="allocation-copy",
    )
    manual = experiment(
        "manual", status="queued", verdict=None, strategy="Novel manual idea"
    )
    result = build_insights(detail([candidate, manual], accepted=[candidate]))
    tracks = result["tracks"]
    assert [t["id"] for t in tracks] == [
        "allocation-copy",
        "batching",
        "manual:Novel manual idea",
    ]
    assert (
        tracks[0]["experiments"][0]["id"] == "assigned"
        and tracks[0]["status"] == "tested"
    )
    assert tracks[1]["status"] == "not_tested" and tracks[1]["counts"]["attempts"] == 0
    assert tracks[0]["falsifier"] == "RSS unchanged" and tracks[0]["tradeoffs"] == [
        "Aliasing"
    ]
    assert tracks[2]["planned"] is False


def test_recorded_execution_history_survives_missing_baselines_and_deduplicates_aliases():
    sample = {
        "metrics": {"rss": 10},
        "execution": {"label": "same", "artifact": "runs/same"},
    }
    candidate = experiment(
        "e",
        {"rss": pair(100, 80, 0.2)},
        runs=[sample],
        reference_runs=[sample],
        confirmation_runs=[
            {"metrics": {"rss": 9}, "execution": {"artifact": "runs/same"}}
        ],
    )
    d = detail([candidate])
    d["campaign"]["baseline"] = [sample]
    d["events"] = [
        {
            "type": "execution.finished",
            "data": {"label": "same", "artifact": "runs/same", "status": "succeeded"},
        },
        {
            "type": "execution.finished",
            "data": {"label": "old-baseline", "status": "succeeded"},
        },
        {
            "type": "execution.finished",
            "data": {"label": "failed-parse", "status": "succeeded"},
        },
        {
            "type": "execution.finished",
            "data": {"label": "timed-out", "status": "timed_out"},
        },
        {"type": "campaign.running", "data": {}},
    ]
    result = build_insights(d)
    assert result["summary"]["measured_runs"] == 4
    d["campaign"].pop("baseline")
    assert build_insights(d)["summary"]["measured_runs"] == 4
    d["events"] = []
    assert build_insights(d)["summary"]["measured_runs"] == 1
    d["experiments"][0]["confirmation_runs"] = [{"metrics": {"rss": 9}}]
    assert build_insights(d)["summary"]["measured_runs"] == 2


def test_projection_is_deterministic_does_not_mutate_input_and_does_not_need_engine():
    candidate = experiment("e", {"rss": pair(100, 80, 0.2)})
    d = detail([candidate])
    d["experiments"].append(
        experiment(
            "queued",
            status="queued",
            verdict=None,
            evaluation_context={
                "protocol_hash": "current",
                "baseline_revision": "base",
                "runner_config_hash": digest(d["campaign"]["runner"]),
            },
        )
    )
    before = deepcopy(d)
    first, second = build_insights(d), build_insights(d)
    assert first == second and d == before
    assert first["summary"]["current"] == 2
    assert (
        first["summary"]["attempts"]
        == first["summary"]["current"] + first["summary"]["historical"]
    )
    empty = build_insights(None)
    assert empty["available"] is False and empty["summary"]["attempts"] == 0


def test_confirmation_reason_fallback_describes_recorded_evidence_not_causality():
    confirmed = experiment("confirmed", {"rss": pair(100, 80, 0.2)})
    unknown = experiment("unknown", {"rss": pair(100, 70, 0.3)})
    unknown["confirmation"]["candidate"]["valid"] = False
    no_confirmation = experiment("no-confirmation", {"rss": pair(100, 60, 0.4)})
    no_confirmation.pop("confirmation")
    stated_reason = experiment("stated", {"rss": pair(100, 50, 0.5)})
    stated_reason["confirmation"]["reasons"] = ["Explicit evaluator note"]
    result = build_insights(
        detail([confirmed, unknown, no_confirmation, stated_reason])
    )
    rows = result["tracks"][0]["experiments"]
    assert (
        rows[0]["reason"]
        == "Independent confirmation recorded an objective improvement with hard constraints satisfied."
    )
    assert rows[1]["reason"] == rows[2]["reason"] == "Recorded verdict: confirmed."
    assert rows[3]["reason"] == "Explicit evaluator note"
