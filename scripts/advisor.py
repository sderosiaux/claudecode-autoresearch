#!/usr/bin/env python3
"""advisor.py — Analysis engine for autoresearch experiment logs.

Provides UCB1 scoring, experiment suggestions, local minimum detection,
convergence AUC calculation, dimension scoreboard, and failure pattern analysis.

Usage:
    advisor.py suggest|detect|auc|scoreboard|escape|failures <jsonl_path>
"""

import json
import math
import sys
from collections import defaultdict

KNOWN_DIMENSIONS = [
    "algorithm",
    "data-layout",
    "io",
    "runtime-flags",
    "parallelism",
    "build-pipeline",
    "os-kernel",
    "hardware",
    "elimination",
    "problem-reformulation",
    "pipeline-order",
    "cross-language",
    "library-swap",
    "compression",
    "concurrency",
    "measurement",
]


def parse_jsonl(path: str) -> tuple[dict | None, list[dict]]:
    """Parse a JSONL file into (config, experiments).

    Config lines have {"type": "config", ...}. If multiple config lines exist,
    the last one wins. All other valid JSON lines with a "status" field are experiments.
    Blank/malformed lines are skipped.
    """
    config = None
    experiments = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if obj.get("type") == "config":
                config = obj
            elif "status" in obj:
                experiments.append(obj)
    return config, experiments


def dimension_scoreboard(experiments: list[dict]) -> dict:
    """Build per-dimension statistics from experiments.

    Returns {dim: {tries, keeps, discards, consecutive_discards, gains, last_exp, exhausted}}.
    Exhausted = 5+ consecutive discards (across the full timeline for that dimension).
    """
    board: dict[str, dict] = {}

    for exp in experiments:
        dim = exp.get("dimension", "unknown")
        if dim not in board:
            board[dim] = {
                "tries": 0,
                "keeps": 0,
                "discards": 0,
                "consecutive_discards": 0,
                "gains": [],
                "last_exp": None,
                "exhausted": False,
            }
        entry = board[dim]
        entry["tries"] += 1
        entry["last_exp"] = exp.get("n", 0)

        if exp["status"] == "keep":
            entry["keeps"] += 1
            entry["consecutive_discards"] = 0
        elif exp["status"] in ("discard", "crash", "guard_failed"):
            entry["discards"] += 1
            entry["consecutive_discards"] += 1

        if entry["consecutive_discards"] >= 5:
            entry["exhausted"] = True

    return board


def _compute_gains(experiments: list[dict], best_direction: str) -> dict[str, list[float]]:
    """Compute per-dimension gain percentages for kept experiments.

    Gain is measured relative to the running best at the time of the experiment.
    """
    gains: dict[str, list[float]] = defaultdict(list)
    running_best = None
    lower = best_direction == "lower"

    for exp in experiments:
        if running_best is None and exp["status"] == "keep":
            running_best = exp["metric"]
            continue

        if running_best is None:
            continue

        dim = exp.get("dimension", "unknown")
        if exp["status"] == "keep":
            if lower:
                gain_pct = (running_best - exp["metric"]) / abs(running_best) * 100 if running_best != 0 else 0
            else:
                gain_pct = (exp["metric"] - running_best) / abs(running_best) * 100 if running_best != 0 else 0
            gains[dim].append(gain_pct)
            # Update running best
            if lower and exp["metric"] < running_best:
                running_best = exp["metric"]
            elif not lower and exp["metric"] > running_best:
                running_best = exp["metric"]

    return dict(gains)


def ucb_scores(
    experiments: list[dict],
    known_dimensions: list[str] | None = None,
    C: float = 1.41,
) -> dict[str, float]:
    """Compute UCB1 score per dimension.

    Score = keep_rate * (1 + avg_gain/100) + C * sqrt(ln(total) / n_tried)
    Unexplored dimensions get infinity.
    """
    dims = known_dimensions or KNOWN_DIMENSIONS
    board = dimension_scoreboard(experiments)

    total = len(experiments)
    if total == 0:
        return {d: float("inf") for d in dims}

    # Compute gains for avg_gain per dimension
    # We need config for best_direction but we don't have it here.
    # Default to "lower" for gain calculation in UCB; suggest_next passes config.
    # For standalone ucb_scores, we approximate: keep = positive, discard = 0.
    scores = {}
    ln_total = math.log(total) if total > 0 else 0

    for dim in dims:
        if dim not in board or board[dim]["tries"] == 0:
            scores[dim] = float("inf")
            continue

        entry = board[dim]
        n = entry["tries"]
        keep_rate = entry["keeps"] / n if n > 0 else 0
        avg_gain = sum(entry["gains"]) / len(entry["gains"]) if entry["gains"] else 0

        exploitation = keep_rate * (1 + avg_gain / 100)
        exploration = C * math.sqrt(ln_total / n) if n > 0 else float("inf")
        scores[dim] = exploitation + exploration

    # Also include dimensions that appeared in experiments but not in known_dimensions
    for dim in board:
        if dim not in scores:
            entry = board[dim]
            n = entry["tries"]
            keep_rate = entry["keeps"] / n if n > 0 else 0
            avg_gain = sum(entry["gains"]) / len(entry["gains"]) if entry["gains"] else 0
            exploitation = keep_rate * (1 + avg_gain / 100)
            exploration = C * math.sqrt(ln_total / n) if n > 0 else float("inf")
            scores[dim] = exploitation + exploration

    return scores


def suggest_next(
    config: dict,
    experiments: list[dict],
    known_dimensions: list[str] | None = None,
) -> list[dict]:
    """Return top 3 experiment suggestions based on UCB scores.

    Skips exhausted dimensions. Deprioritizes dimensions with 0% keep rate
    after 4+ tries.
    """
    dims = known_dimensions or KNOWN_DIMENSIONS
    board = dimension_scoreboard(experiments)
    scores = ucb_scores(experiments, dims)

    # Compute gains with direction awareness
    best_direction = config.get("bestDirection", "lower")
    gains = _compute_gains(experiments, best_direction)

    # Enrich board with gains
    for dim, gain_list in gains.items():
        if dim in board:
            board[dim]["gains"] = gain_list

    # Recompute scores with gains
    total = len(experiments)
    ln_total = math.log(total) if total > 0 else 0
    C = 1.41

    for dim in list(scores.keys()):
        if dim in board and board[dim]["tries"] > 0:
            entry = board[dim]
            n = entry["tries"]
            keep_rate = entry["keeps"] / n
            avg_gain = sum(entry["gains"]) / len(entry["gains"]) if entry["gains"] else 0
            exploitation = keep_rate * (1 + avg_gain / 100)
            exploration = C * math.sqrt(ln_total / n) if n > 0 else float("inf")
            scores[dim] = exploitation + exploration

    candidates = []
    for dim in scores:
        # Skip exhausted
        if dim in board and board[dim]["exhausted"]:
            continue
        # Deprioritize 0% keep rate after 4+ tries
        if dim in board and board[dim]["tries"] >= 4 and board[dim]["keeps"] == 0:
            scores[dim] = -1  # push to bottom

        entry = board.get(dim, {"tries": 0, "keeps": 0})
        tries = entry["tries"] if isinstance(entry, dict) else 0
        keeps = entry["keeps"] if isinstance(entry, dict) else 0
        keep_rate = keeps / tries if tries > 0 else 0.0

        # Budget recommendation: more tries for promising dimensions
        if tries == 0:
            budget = 3
        elif keep_rate > 0.5:
            budget = 5
        elif keep_rate > 0:
            budget = 3
        else:
            budget = 1

        candidates.append({
            "dimension": dim,
            "ucb_score": scores[dim],
            "tries": tries,
            "keep_rate": round(keep_rate, 3),
            "budget_recommendation": budget,
        })

    # Sort by UCB score descending
    candidates.sort(key=lambda x: x["ucb_score"], reverse=True)
    return candidates[:3]


def detect_local_minimum(config: dict, experiments: list[dict]) -> dict:
    """Detect if the experiment loop is stuck in a local minimum.

    Checks for:
    - Stagnation: best unchanged + many tail discards
    - Diversity collapse: same dimension repeated in tail
    - Diminishing returns: last 3 keeps all <1% gain

    Returns {is_local_minimum, reason, tail_discards, escape}.
    Not triggered with <10 experiments.
    """
    result = {
        "is_local_minimum": False,
        "reason": "",
        "tail_discards": 0,
        "escape": {},
    }

    if len(experiments) < 10:
        return result

    best_direction = config.get("bestDirection", "lower")
    lower = best_direction == "lower"

    # Find running best and best experiment
    running_best = None
    best_exp = None
    keeps = []
    for exp in experiments:
        if exp["status"] == "keep":
            keeps.append(exp)
            if running_best is None:
                running_best = exp["metric"]
                best_exp = exp
            elif lower and exp["metric"] < running_best:
                running_best = exp["metric"]
                best_exp = exp
            elif not lower and exp["metric"] > running_best:
                running_best = exp["metric"]
                best_exp = exp

    if running_best is None or best_exp is None:
        return result

    # Count tail discards (consecutive discards at end)
    tail_discards = 0
    for exp in reversed(experiments):
        if exp["status"] in ("discard", "crash", "guard_failed"):
            tail_discards += 1
        else:
            break
    result["tail_discards"] = tail_discards

    # Build escape recommendation
    def _build_escape(reason: str) -> dict:
        board = dimension_scoreboard(experiments)
        # Find least-tried non-exhausted dimension
        all_dims = set(KNOWN_DIMENSIONS)
        tried_dims = set(board.keys())
        untried = all_dims - tried_dims
        if untried:
            try_dim = sorted(untried)[0]
        else:
            # Pick dimension with fewest tries that isn't exhausted
            eligible = [(d, v) for d, v in board.items() if not v["exhausted"]]
            if eligible:
                try_dim = min(eligible, key=lambda x: x[1]["tries"])[0]
            else:
                try_dim = KNOWN_DIMENSIONS[0]

        # Collect abandoned techniques (dimensions tried and failed)
        abandoned = [d for d, v in board.items() if v["exhausted"]]

        return {
            "revert_to_experiment": best_exp["n"],
            "revert_to_metric": best_exp["metric"],
            "try_dimension": try_dim,
            "abandoned_techniques": abandoned,
            "allow_regression_pct": 5,
        }

    # Check 1: Stagnation — best unchanged + many tail discards
    if tail_discards >= 5:
        result["is_local_minimum"] = True
        result["reason"] = f"Stagnation: {tail_discards} consecutive discards, best unchanged since exp {best_exp['n']}"
        result["escape"] = _build_escape("stagnation")
        return result

    # Check 2: Diversity collapse — tail experiments all same dimension
    tail_size = min(8, len(experiments))
    tail = experiments[-tail_size:]
    tail_dims = [e.get("dimension", "unknown") for e in tail]
    unique_dims = set(tail_dims)
    if len(unique_dims) <= 1 and tail_size >= 8:
        result["is_local_minimum"] = True
        result["reason"] = f"Diversity collapse: last {tail_size} experiments all in dimension '{tail_dims[0]}'"
        result["escape"] = _build_escape("diversity_collapse")
        return result

    # Check 3: Diminishing returns — last 3 keeps all <1% gain
    if len(keeps) >= 4:  # need baseline + 3 keeps
        last_3_keeps = keeps[-3:]
        # Calculate gain of each of the last 3 keeps relative to the keep before it
        gains_pct = []
        for i, k in enumerate(last_3_keeps):
            # Find the best metric before this keep
            prev_best = None
            for prev in keeps:
                if prev["n"] >= k["n"]:
                    break
                if prev_best is None:
                    prev_best = prev["metric"]
                elif lower and prev["metric"] < prev_best:
                    prev_best = prev["metric"]
                elif not lower and prev["metric"] > prev_best:
                    prev_best = prev["metric"]

            if prev_best is not None and prev_best != 0:
                if lower:
                    gain = (prev_best - k["metric"]) / abs(prev_best) * 100
                else:
                    gain = (k["metric"] - prev_best) / abs(prev_best) * 100
                gains_pct.append(abs(gain))
            else:
                gains_pct.append(0)

        if all(g < 1.0 for g in gains_pct) and len(gains_pct) == 3:
            result["is_local_minimum"] = True
            result["reason"] = f"Diminishing returns: last 3 keeps gained {[round(g, 2) for g in gains_pct]}%"
            result["escape"] = _build_escape("diminishing_returns")
            return result

    return result


def compute_convergence_auc(config: dict, experiments: list[dict]) -> float:
    """Compute normalized convergence AUC (0-1). Higher = faster convergence.

    Tracks running best across experiments. Normalizes gains by the total
    improvement achieved. AUC of 1 means all improvement happened in the
    first experiment. AUC of 0 means no improvement.
    """
    if len(experiments) < 2:
        return 0.0

    best_direction = config.get("bestDirection", "lower")
    lower = best_direction == "lower"

    # Track running best
    running_best = None
    baseline = None
    bests = []

    for exp in experiments:
        if exp["status"] == "keep":
            if running_best is None:
                running_best = exp["metric"]
                baseline = exp["metric"]
            elif lower and exp["metric"] < running_best:
                running_best = exp["metric"]
            elif not lower and exp["metric"] > running_best:
                running_best = exp["metric"]
        bests.append(running_best)

    if baseline is None or running_best is None:
        return 0.0

    # Total improvement
    if lower:
        total_gain = baseline - running_best
    else:
        total_gain = running_best - baseline

    if total_gain <= 0:
        return 0.0

    # Compute AUC: sum of normalized gains at each step
    n = len(experiments)
    auc_sum = 0.0
    for i in range(1, n):
        if bests[i] is not None and baseline is not None:
            if lower:
                gain_at_i = baseline - bests[i]
            else:
                gain_at_i = bests[i] - baseline
            normalized = max(0, gain_at_i / total_gain)
            auc_sum += normalized

    # Normalize: perfect AUC = n-1 (all improvement at step 1)
    max_auc = n - 1
    return auc_sum / max_auc if max_auc > 0 else 0.0


def analyze_failures(experiments: list[dict]) -> dict:
    """Analyze discard/crash patterns to find recurring failure modes.

    Groups failure_reason strings, counts frequency, identifies dimensions
    with repeated identical failures (= wasted effort).
    Returns {patterns: [{reason, count, dimensions, suggestion}], waste_ratio}.
    """
    failures = [e for e in experiments if e.get("status") in ("discard", "crash", "guard_failed")]
    if not failures:
        return {"patterns": [], "waste_ratio": 0.0}

    # Group by (reason, dimension) to avoid conflating same reason across different approaches
    group_key_map: dict[tuple[str, str], list[dict]] = defaultdict(list)
    no_diagnosis: list[dict] = []
    for f in failures:
        reason = (f.get("failure_reason") or "").strip()
        dim = f.get("dimension", "unknown")
        if not reason:
            no_diagnosis.append(f)
        else:
            group_key_map[(reason, dim)].append(f)

    patterns = []
    for (reason, dim), exps in sorted(group_key_map.items(), key=lambda x: -len(x[1])):
        suggestion = ""
        if len(exps) >= 3:
            suggestion = f"Same failure repeated {len(exps)}x in [{dim}] — stop trying this approach"
        patterns.append({
            "reason": reason,
            "count": len(exps),
            "dimensions": [dim],
            "suggestion": suggestion,
        })

    if no_diagnosis:
        dims = list(set(e.get("dimension", "unknown") for e in no_diagnosis))
        patterns.append({
            "reason": "no_diagnosis",
            "count": len(no_diagnosis),
            "dimensions": dims,
            "suggestion": f"{len(no_diagnosis)} experiments discarded without diagnosis — add failure_reason",
        })

    repeated = sum(p["count"] for p in patterns if p["count"] >= 3 and p["reason"] != "no_diagnosis")
    waste_ratio = repeated / len(failures) if failures else 0.0

    return {"patterns": patterns[:10], "waste_ratio": round(waste_ratio, 3)}


def cost_summary(experiments: list[dict]) -> dict:
    """Compute cost metrics from elapsed_s field.

    Returns {total_elapsed_s, avg_per_experiment, avg_per_keep, avg_per_discard,
             most_expensive: {n, elapsed_s, description, status}}.
    """
    timed = [e for e in experiments if e.get("elapsed_s", 0) > 0]
    if not timed:
        return {"total_elapsed_s": 0, "avg_per_experiment": 0, "avg_per_keep": 0, "avg_per_discard": 0, "most_expensive": None}

    total = sum(e["elapsed_s"] for e in timed)
    keeps = [e for e in timed if e["status"] == "keep"]
    discards = [e for e in timed if e["status"] in ("discard", "crash", "guard_failed")]
    most_expensive = max(timed, key=lambda e: e["elapsed_s"])

    return {
        "total_elapsed_s": total,
        "avg_per_experiment": round(total / len(timed)),
        "avg_per_keep": round(sum(e["elapsed_s"] for e in keeps) / len(keeps)) if keeps else 0,
        "avg_per_discard": round(sum(e["elapsed_s"] for e in discards) / len(discards)) if discards else 0,
        "most_expensive": {
            "n": most_expensive.get("n", 0),
            "elapsed_s": most_expensive["elapsed_s"],
            "description": most_expensive.get("description", ""),
            "status": most_expensive.get("status", ""),
        },
    }


def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} suggest|detect|auc|scoreboard|escape|failures|cost <jsonl_path>", file=sys.stderr)
        sys.exit(1)

    command = sys.argv[1]
    path = sys.argv[2]
    config, experiments = parse_jsonl(path)

    if config is None:
        config = {"bestDirection": "lower", "maxExperiments": 100}

    if command == "suggest":
        result = suggest_next(config, experiments)
        print(json.dumps(result, indent=2))
    elif command == "detect":
        result = detect_local_minimum(config, experiments)
        # Convert for JSON serialization
        print(json.dumps(result, indent=2))
    elif command == "auc":
        auc = compute_convergence_auc(config, experiments)
        print(json.dumps({"auc": round(auc, 4)}))
    elif command == "scoreboard":
        board = dimension_scoreboard(experiments)
        print(json.dumps(board, indent=2))
    elif command == "escape":
        result = detect_local_minimum(config, experiments)
        if result["is_local_minimum"] and result.get("escape"):
            escape = result["escape"]
            target_n = escape["revert_to_experiment"]
            target_commit = "unknown"
            for exp in experiments:
                if exp.get("n") == target_n:
                    target_commit = exp.get("commit", "unknown")
                    break
            escape["target_commit"] = target_commit
            escape["reason"] = result["reason"]
            print(json.dumps(escape, indent=2))
        else:
            print(json.dumps({"action": "none", "reason": result.get("reason", "not in local minimum")}))
    elif command == "failures":
        result = analyze_failures(experiments)
        print(json.dumps(result, indent=2))
    elif command == "cost":
        result = cost_summary(experiments)
        print(json.dumps(result, indent=2))
    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
