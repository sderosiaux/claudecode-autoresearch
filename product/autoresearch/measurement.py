"""Deterministic, conservative comparison of independent benchmark executions.

Intervals describe run-to-run means, never individual message percentiles. Selection
is exploratory: independent new runs must be compared before a confirmed verdict.
"""

from __future__ import annotations

import json
import math
import statistics


class InvalidMeasurement(ValueError):
    pass


def _number(value):
    try:
        return (
            isinstance(value, (float, int))
            and not isinstance(value, bool)
            and math.isfinite(value)
        )
    except (OverflowError, TypeError):
        return False


def validate_sample(sample: dict, metric_specs: list[dict] | None = None) -> dict:
    if (
        not isinstance(sample, dict)
        or not isinstance(sample.get("metrics"), dict)
        or not sample["metrics"]
    ):
        raise InvalidMeasurement("benchmark requires a nonempty metrics object")
    for name, value in sample["metrics"].items():
        if not isinstance(name, str) or not _number(value):
            raise InvalidMeasurement(
                f"metric {name!r} must be a finite number (not a boolean)"
            )
    checks = sample.get("checks")
    if (
        not isinstance(checks, dict)
        or not checks
        or any(type(v) is not bool for v in checks.values())
    ):
        raise InvalidMeasurement(
            "benchmark requires explicit nonempty boolean correctness checks"
        )
    if "units" in sample and (
        not isinstance(sample["units"], dict)
        or any(
            not isinstance(k, str) or not isinstance(v, str)
            for k, v in sample["units"].items()
        )
    ):
        raise InvalidMeasurement("units must map metric names to unit strings")
    if "environment_fingerprint" in sample and (
        not isinstance(sample["environment_fingerprint"], str)
        or not sample["environment_fingerprint"]
    ):
        raise InvalidMeasurement("environment fingerprint must be a nonempty string")
    for spec in metric_specs or []:
        name = spec.get("name", spec.get("metric"))
        if name not in sample["metrics"]:
            raise InvalidMeasurement(f"required metric {name!r} is absent")
        if name in sample.get("units", {}) and sample["units"][name] != spec.get(
            "unit"
        ):
            raise InvalidMeasurement(f"unit mismatch for {name}")
    return sample


def parse_benchmark(stdout: str, metric_specs: list[dict] | None = None) -> dict:
    """Parse one JSON document or one JSON result line among ordinary logs."""
    try:
        document = json.loads(stdout)
    except (ValueError, TypeError):
        candidates = []
        for line in stdout.splitlines():
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict) and "metrics" in value:
                candidates.append(value)
        if len(candidates) != 1:
            raise InvalidMeasurement("expected exactly one JSON benchmark result")
        document = candidates[0]
    return validate_sample(document, metric_specs)


def _t95(n: int) -> float:
    # Two-sided 95% Student t critical values, rounded upwards; deliberately
    # conservative when degrees of freedom lie between the supplied entries.
    table = [
        (1, 12.707),
        (2, 4.303),
        (3, 3.183),
        (4, 2.777),
        (5, 2.571),
        (6, 2.447),
        (7, 2.365),
        (8, 2.307),
        (9, 2.263),
        (10, 2.229),
        (15, 2.132),
        (20, 2.086),
        (30, 2.043),
        (60, 2.001),
        (120, 1.981),
    ]
    df = n - 1
    return next((v for d, v in reversed(table) if df >= d), 12.707)


def summarize(samples: list[dict], metric_specs: list[dict]) -> dict:
    reasons, metrics = [], {}
    if not samples:
        return {"valid": False, "count": 0, "metrics": {}, "reasons": ["no samples"]}
    for sample in samples:
        try:
            validate_sample(sample, metric_specs)
        except InvalidMeasurement as exc:
            reasons.append(str(exc))
    if reasons:
        return {
            "valid": False,
            "count": len(samples),
            "metrics": {},
            "reasons": sorted(set(reasons)),
        }
    for index, sample in enumerate(samples):
        failed = [name for name, passed in sample["checks"].items() if not passed]
        if failed:
            reasons.append(
                f"sample {index} failed correctness checks: {', '.join(failed)}"
            )
    for spec in metric_specs:
        name = spec.get("name", spec.get("metric"))
        values = [float(sample["metrics"][name]) for sample in samples]
        try:
            mean = statistics.mean(values)
            sd = statistics.stdev(values) if len(values) > 1 else None
        except ArithmeticError:
            reasons.append(f"{name}: non-finite derived statistics")
            continue
        cv = sd / abs(mean) if sd is not None and mean else (0.0 if sd == 0 else None)
        radius = (
            _t95(len(values)) * sd / math.sqrt(len(values)) if sd is not None else None
        )
        if (
            not _number(mean)
            or (sd is not None and not _number(sd))
            or (radius is not None and not _number(radius))
        ):
            reasons.append(f"{name}: non-finite derived statistics")
            continue
        metrics[name] = {
            "mean": mean,
            "median": statistics.median(values),
            "min": min(values),
            "max": max(values),
            "stdev": sd,
            "cv": cv,
            "count": len(values),
            "ci95": [mean - radius, mean + radius] if radius is not None else None,
            "unit": spec.get("unit"),
            "values": values,
            "noise_exceeded": cv is None or cv > spec.get("max_cv", 0.15),
        }
        if len(values) < spec.get("min_repetitions", 3):
            reasons.append(f"{name}: insufficient independent repetitions")
        if metrics[name]["noise_exceeded"] and spec.get("role") != "info":
            reasons.append(f"{name}: baseline/run variation exceeds permitted noise")
    return {
        "valid": not reasons,
        "count": len(samples),
        "metrics": metrics,
        "reasons": reasons,
        "method": "independent-run means; conservative Student-t 95% intervals v1",
    }


def compare(
    baseline: list[dict], candidate: list[dict], metric_specs: list[dict]
) -> dict:
    before, after = (
        summarize(baseline, metric_specs),
        summarize(candidate, metric_specs),
    )
    result = {
        "baseline": before,
        "candidate": after,
        "metrics": {},
        "reasons": [],
        "verdict": "inconclusive",
        "confirmation_required": True,
    }
    for spec in metric_specs:
        if spec.get("role") != "constraint":
            continue
        name = spec.get("name", spec.get("metric"))
        operator, threshold = spec.get("operator"), spec.get("value")
        if operator not in ("lte", "gte", "eq") or not _number(threshold):
            result.update(
                verdict="invalid_measurement",
                reasons=[f"{name}: invalid constraint definition"],
            )
            return result
        for sample in candidate:
            value = sample.get("metrics", {}).get(name)
            if _number(value) and not (
                value <= threshold
                if operator == "lte"
                else value >= threshold
                if operator == "gte"
                else value == threshold
            ):
                result.update(
                    verdict="constraint_failed",
                    reasons=[f"{name}: hard constraint failed"],
                )
                return result
    if not before["valid"] or not after["valid"]:
        result["reasons"] = before["reasons"] + after["reasons"]
        structural = len(before["metrics"]) != len(metric_specs) or len(
            after["metrics"]
        ) != len(metric_specs)
        correctness = any("correctness" in r for r in result["reasons"])
        result["verdict"] = (
            "invalid_measurement"
            if structural
            else "constraint_failed"
            if correctness
            else "inconclusive"
        )
        return result
    # Explicit environment fingerprints are optional in imported samples, but
    # when present every run must identify the same environment.
    environments = [s.get("environment_fingerprint") for s in baseline + candidate]
    if any(environments) and (not all(environments) or len(set(environments)) != 1):
        result.update(
            verdict="invalid_measurement",
            reasons=["environment fingerprint drift; establish a new baseline"],
        )
        return result
    constraint_failed, improved, regressed = False, False, False
    for spec in metric_specs:
        name = spec.get("name", spec.get("metric"))
        b, c = before["metrics"][name], after["metrics"][name]
        direction = spec.get("direction", "minimize")
        sign = 1 if direction == "maximize" else -1
        gain = sign * (c["mean"] - b["mean"])
        # Sum the individual mean radii: more conservative than Welch's
        # independent-difference interval and robust for the small default n.
        radius = (b["ci95"][1] - b["mean"]) + (c["ci95"][1] - c["mean"])
        required = abs(b["mean"]) * spec.get("minimum_improvement", 0.02)
        resolved = gain - radius > required
        regression = gain + radius < -required
        result["metrics"][name] = {
            "baseline": b["mean"],
            "candidate": c["mean"],
            "improvement_fraction": gain / abs(b["mean"]) if b["mean"] else None,
            "gain_ci95": [gain - radius, gain + radius],
            "resolved_improvement": resolved,
            "resolved_regression": regression,
        }
        if spec.get("role", "objective") == "constraint":
            operator, threshold = spec.get("operator"), spec.get("value")
            if operator not in ("lte", "gte", "eq") or not _number(threshold):
                result.update(
                    verdict="invalid_measurement",
                    reasons=[f"{name}: invalid constraint definition"],
                )
                return result
            passed = all(
                (
                    v <= threshold
                    if operator == "lte"
                    else v >= threshold
                    if operator == "gte"
                    else v == threshold
                )
                for v in c["values"]
            )
            constraint_failed |= not passed
            if not passed:
                result["reasons"].append(f"{name}: hard constraint failed")
        elif spec.get("role", "objective") == "objective":
            improved |= resolved
            regressed |= regression
    if constraint_failed:
        result["verdict"] = "constraint_failed"
    elif improved:
        result["verdict"] = "promising"
        result["tradeoff"] = regressed
    else:
        result["reasons"].append(
            "gain is unresolved or objectives trade off; retain raw metrics for review"
        )
    return result


def pareto_front(candidates: list[dict], metric_specs: list[dict]) -> list[dict]:
    """Keep candidates not confidently dominated on all objective intervals.

    Each candidate provides a valid summarize() result in ``summary``. Uncertain
    intervals preserve both candidates rather than inventing a weighted score.
    """
    objectives = [s for s in metric_specs if s.get("role", "objective") == "objective"]

    def dominates(a, b):
        strict = False
        for spec in objectives:
            name = spec.get("name", spec.get("metric"))
            x, y = (
                a["summary"]["metrics"][name]["ci95"],
                b["summary"]["metrics"][name]["ci95"],
            )
            if x is None or y is None:
                return False
            if spec.get("direction") == "maximize":
                if x[0] < y[1]:
                    return False
                strict |= x[0] > y[1]
            else:
                if x[1] > y[0]:
                    return False
                strict |= x[1] < y[0]
        return strict

    eligible = [c for c in candidates if c.get("summary", {}).get("valid")]
    return [
        c for c in eligible if not any(o is not c and dominates(o, c) for o in eligible)
    ]
