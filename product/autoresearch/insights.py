"""Read-only Overview projection of supervisor-accepted campaign evidence.

The supervisor owns context validation and verdicts. This module neither upgrades
an exploratory result nor reinterprets a provenance-verified legacy comparison.
Metric best means best observed confirmed value, not statistical dominance over
other candidates, and never assembles a program from different metric winners.
"""

from __future__ import annotations

import math

from .models import digest


_CATEGORIES = (
    "confirmed",
    "promising",
    "inconclusive",
    "constraint_failed",
    "failed",
    "queued",
    "running",
    "skipped",
    "historical",
    "invalid_measurement",
    "unmeasured",
)
_RUN_FIELDS = (
    "runs",
    "reference_runs",
    "confirmation_runs",
    "confirmation_reference_runs",
)


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _rows(value):
    return (
        [row for row in value if isinstance(row, dict)]
        if isinstance(value, list)
        else []
    )


def _number(value):
    try:
        return (
            float(value)
            if isinstance(value, (float, int))
            and not isinstance(value, bool)
            and math.isfinite(value)
            else None
        )
    except (OverflowError, ValueError):
        return None


def _interval(value):
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        return None
    lower, upper = map(_number, value)
    return (
        [lower, upper]
        if lower is not None and upper is not None and lower <= upper
        else None
    )


def _text(value):
    return value if isinstance(value, str) else ""


def _analysis(experiment):
    confirmation = _mapping(experiment.get("confirmation"))
    return confirmation or _mapping(experiment.get("comparison"))


def _reason(experiment):
    for key in ("duplicate_reason", "error", "reason", "termination_reason"):
        value = experiment.get(key)
        if isinstance(value, str) and value:
            return value
    reasons = _analysis(experiment).get("reasons")
    recorded = (
        "; ".join(r for r in reasons if isinstance(r, str))
        if isinstance(reasons, list)
        else ""
    )
    if recorded:
        return recorded
    confirmation = _mapping(experiment.get("confirmation"))
    if (
        experiment.get("status") == "succeeded"
        and experiment.get("verdict") == "confirmed"
        and confirmation.get("verdict") == "promising"
        and all(
            _mapping(confirmation.get(side)).get("valid") is True
            for side in ("baseline", "candidate")
        )
    ):
        return "Independent confirmation recorded an objective improvement with hard constraints satisfied."
    verdict = _text(experiment.get("verdict")) or _text(
        _analysis(experiment).get("verdict")
    )
    return f"Recorded verdict: {verdict}." if verdict else ""


def _source(experiment):
    return _text(experiment.get("measurement_context_source")) or "recorded_context"


def _counts(rows):
    result = {"attempts": len(rows), "current": sum(r["current_context"] for r in rows)}
    result.update(
        {
            category: sum(r["category"] == category for r in rows)
            for category in _CATEGORIES
        }
    )
    result["execution"] = {
        status: sum(r["status"] == status for r in rows)
        for status in (
            "queued",
            "running",
            "succeeded",
            "failed",
            "cancelled",
            "unknown",
        )
    }
    result["evidence"] = {
        verdict: sum(
            r["current_context"]
            and r["status"] == "succeeded"
            and r["category"] == verdict
            for r in rows
        )
        for verdict in (
            "confirmed",
            "promising",
            "inconclusive",
            "constraint_failed",
            "invalid_measurement",
        )
    }
    return result


def _measured_runs(campaign, experiments, events):
    # Join label and artifact aliases, so an event retained after protocol change
    # and a raw sample describing that same execution contribute exactly once.
    aliases, parents = {}, []
    anonymous = 0

    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def record(execution):
        nonlocal anonymous
        keys = [
            (key, execution[key])
            for key in ("label", "artifact")
            if isinstance(execution.get(key), str) and execution[key]
        ]
        if not keys:
            anonymous += 1
            return
        groups = [root(aliases[key]) for key in keys if key in aliases]
        if groups:
            index = min(groups)
            for other in groups:
                parents[other] = index
        else:
            index = len(parents)
            parents.append(index)
        for key in keys:
            aliases[key] = index

    for event in _rows(events):
        if event.get("type") == "execution.finished":
            record(_mapping(event.get("data")))
    groups = [campaign.get("baseline")]
    groups.extend(
        experiment.get(field) for experiment in experiments for field in _RUN_FIELDS
    )
    for group in groups:
        for sample in _rows(group):
            execution = _mapping(sample.get("execution"))
            if execution or _mapping(sample.get("metrics")):
                record(execution)
    return len({root(index) for index in range(len(parents))}) + anonymous


def _unmeasured_current(experiment, campaign, protocol_hash):
    if experiment.get("protocol_hash") != protocol_hash:
        return False
    context = _mapping(experiment.get("evaluation_context"))
    if not context:
        return True  # Includes new proposals before any protocol is assigned.
    expected = {"protocol_hash": protocol_hash}
    revision = campaign.get("prepared_revision") or campaign.get("base_revision")
    if revision is not None:
        expected["baseline_revision"] = revision
    if isinstance(campaign.get("runner"), dict):
        expected["runner_config_hash"] = digest(campaign["runner"])
    return all(context.get(key) == value for key, value in expected.items())


def build_insights(detail: dict) -> dict:
    """Project accepted detail into summary counts, metric observations and tracks.

    ``detail.comparison`` is the sole authority for current measured evidence,
    including legacy rows whose complete run provenance the supervisor verified.
    Unmeasured proposals can be current by protocol; old measured rows absent
    from that accepted list remain history. Input objects are never modified.
    """
    detail = _mapping(detail)
    campaign = _mapping(detail.get("campaign"))
    protocol = _mapping(detail.get("protocol") or campaign.get("protocol"))
    protocol_hash = detail.get("protocol_hash") or campaign.get("protocol_hash")
    experiments = _rows(detail.get("experiments"))
    accepted = {
        e["id"]: e
        for e in _rows(detail.get("comparison"))
        if isinstance(e.get("id"), str)
    }
    baseline_summary = _mapping(
        campaign.get("baseline_summary") or detail.get("baseline_summary")
    )
    baseline_metrics = _mapping(baseline_summary.get("metrics"))
    specs = [
        m
        for m in _rows(protocol.get("metrics"))
        if isinstance(m.get("name"), str) and m["name"]
    ]
    available = bool(specs)
    projected, merged = [], []
    for sequence, original in enumerate(experiments, 1):
        eid = original.get("id")
        experiment = {**accepted.get(eid, {}), **original}
        for key in ("measurement_context_source", "measurement_context_warning"):
            if key in accepted.get(eid, {}):
                experiment[key] = accepted[eid][key]
        merged.append(experiment)
        accepted_current = eid in accepted
        has_measurements = bool(
            experiment.get("comparison")
            or experiment.get("confirmation")
            or any(experiment.get(field) for field in _RUN_FIELDS)
        )
        current = accepted_current or (
            not has_measurements
            and _unmeasured_current(experiment, campaign, protocol_hash)
        )
        status = _text(experiment.get("status")) or "unknown"
        verdict = (
            _text(experiment.get("verdict"))
            or _text(_analysis(experiment).get("verdict"))
            or None
        )
        if not current:
            category = "historical"
        elif (
            experiment.get("duplicate_of")
            or experiment.get("termination_reason") == "duplicate_program"
            or status in {"cancelled", "skipped"}
        ):
            category = "skipped"
        elif status in {"queued", "running"}:
            category = status
        elif status == "failed" or verdict == "execution_failed":
            category = "failed"
        elif (
            status == "succeeded"
            and accepted_current
            and verdict
            in {
                "confirmed",
                "promising",
                "inconclusive",
                "constraint_failed",
                "invalid_measurement",
            }
        ):
            category = verdict
        else:
            category = "unmeasured"
        phase = _text(experiment.get("phase")) or (
            "confirmation"
            if experiment.get("confirmation") or experiment.get("confirmation_runs")
            else "proposal"
            if status == "queued"
            else "exploration"
        )
        projected.append(
            {
                "id": eid,
                "sequence": sequence,
                "parent_id": experiment.get("parent_id"),
                "hypothesis": _text(experiment.get("hypothesis")),
                "expected_effect": _text(experiment.get("expected_effect")),
                "status": status,
                "phase": phase,
                "verdict": verdict,
                "category": category,
                "current_context": current,
                "reason": _reason(experiment),
                "created_at": experiment.get("created_at"),
                "updated_at": experiment.get("updated_at"),
                "measurement_context_source": _source(experiment)
                if accepted_current
                else None,
                "measurement_context_warning": experiment.get(
                    "measurement_context_warning"
                ),
            }
        )
    metrics = []
    for spec in specs:
        name = spec["name"]
        initial = _number(_mapping(baseline_metrics.get(name)).get("mean"))
        metric = {
            "name": name,
            "unit": spec.get("unit"),
            "role": spec.get("role", "objective"),
            "direction": spec.get("direction", "minimize"),
            "operator": spec.get("operator"),
            "threshold": _number(spec.get("value")),
            "value": _number(spec.get("value")),
            "max_cv": _number(spec.get("max_cv")),
            "minimum_improvement": _number(spec.get("minimum_improvement")),
            "baseline": initial,
            "baseline_valid": baseline_summary.get("valid") is True
            if baseline_summary
            else None,
            "baseline_context_source": _source(baseline_summary)
            if initial is not None
            else None,
            "baseline_context_warning": baseline_summary.get(
                "measurement_context_warning"
            ),
            "best": None,
            "points": [],
        }
        for experiment, row in zip(merged, projected):
            if (
                experiment.get("id") not in accepted
                or row["status"] != "succeeded"
                or row["category"] in {"failed", "skipped", "historical"}
            ):
                continue
            analysis = _analysis(experiment)
            observed = _mapping(_mapping(analysis.get("metrics")).get(name))
            before, after = (
                _number(observed.get("baseline")),
                _number(observed.get("candidate")),
            )
            if before is None or after is None:
                continue
            point = {
                "experiment_id": row["id"],
                "sequence": row["sequence"],
                "hypothesis": row["hypothesis"],
                "baseline": before,
                "candidate": after,
                "improvement_fraction": _number(observed.get("improvement_fraction"))
                if before != 0
                else None,
                "gain_ci95": _interval(observed.get("gain_ci95")),
                "evidence": row["verdict"] or "unmeasured",
                "verdict": row["verdict"],
                "resolved_improvement": observed.get("resolved_improvement") is True,
                "resolved_regression": observed.get("resolved_regression") is True,
                "measurement_context_source": row["measurement_context_source"],
                "measurement_context_warning": row["measurement_context_warning"],
            }
            metric["points"].append(point)
            valid = all(
                _mapping(analysis.get(side)).get("valid") is True
                for side in ("baseline", "candidate")
            )
            improves = (
                after > before if metric["direction"] == "maximize" else after < before
            )
            if (
                row["category"] != "confirmed"
                or not valid
                or not point["resolved_improvement"]
                or not improves
            ):
                continue
            best = metric["best"]
            better = best is None or (
                after > best["candidate"]
                if metric["direction"] == "maximize"
                else after < best["candidate"]
            )
            if better:
                metric["best"] = {
                    key: point[key]
                    for key in (
                        "experiment_id",
                        "hypothesis",
                        "baseline",
                        "candidate",
                        "improvement_fraction",
                        "gain_ci95",
                        "evidence",
                        "measurement_context_source",
                        "measurement_context_warning",
                    )
                }
        metrics.append(metric)
    tracks, aliases, planned_ids = [], {}, {}
    for card in _rows(campaign.get("strategy_plan") or detail.get("strategy_plan")):
        strategy = _text(card.get("id")) or _text(card.get("title"))
        if not strategy or strategy.casefold() in aliases:
            continue
        track = {
            "id": strategy,
            "title": _text(card.get("title")) or strategy,
            "strategy": strategy,
            "falsifier": _text(card.get("falsifier")),
            "tradeoffs": [v for v in card.get("tradeoffs", []) if isinstance(v, str)]
            if isinstance(card.get("tradeoffs"), list)
            else [],
            "experiments": [],
            "planned": True,
        }
        tracks.append(track)
        planned_ids[strategy] = track
        for alias in (strategy, track["title"]):
            aliases[alias.casefold()] = track
    for experiment, row in zip(merged, projected):
        strategy = _text(experiment.get("strategy")) or "user-proposed"
        key = strategy.strip().casefold()
        assigned = planned_ids.get(_text(experiment.get("strategy_id")))
        if assigned is not None:
            assigned["experiments"].append(row)
            continue
        if key not in aliases:
            track = {
                "id": "manual:" + strategy,
                "title": strategy,
                "strategy": strategy,
                "falsifier": "",
                "tradeoffs": [],
                "experiments": [],
                "planned": False,
            }
            aliases[key] = track
            tracks.append(track)
        aliases[key]["experiments"].append(row)
    for track in tracks:
        track["counts"] = _counts(track["experiments"])
        track["status"] = (
            "tested"
            if any(
                r["current_context"]
                and r["id"] in accepted
                and r["status"] == "succeeded"
                and r["category"] != "skipped"
                for r in track["experiments"]
            )
            else "attempted"
            if track["experiments"]
            else "not_tested"
        )
    summary = _counts(projected)
    summary["measured_runs"] = _measured_runs(
        campaign, experiments, detail.get("events")
    )
    return {
        "available": available,
        "reason": None
        if available
        else "No protocol metric definitions are available.",
        "context": {
            "protocol_hash": protocol_hash,
            "comparison_source": "supervisor-accepted",
            "baseline_available": any(m["baseline"] is not None for m in metrics),
            "legacy_evidence_count": sum(
                e.get("measurement_context_source") == "legacy_recorded_runs"
                for e in accepted.values()
            ),
            "measured_runs_scope": "Recorded finished execution events across history plus retained sample fallback, deduplicated by execution label/artifact; includes failed or parse-invalid executions, never comparison summaries.",
            "best_scope": "Best observed confirmed value per metric; not dominance or reproduction readiness.",
        },
        "summary": summary,
        "metrics": metrics,
        "tracks": tracks,
    }
