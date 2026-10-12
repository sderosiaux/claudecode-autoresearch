"""Small attributed strategy cards, selected by objective rather than a giant prompt."""

SOURCE = "https://github.com/sderosiaux/performance-engineering-handbook"

CARDS = [
    {
        "id": "allocation-copy",
        "version": 1,
        "title": "Remove unnecessary copies and temporary allocations",
        "tags": ["memory", "mémoire", "allocation", "heap", "rss", "cpu"],
        "preconditions": [
            "Allocation or CPU evidence identifies a copying path",
            "Ownership and lifetime permit reuse",
        ],
        "tradeoffs": [
            "Aliasing and lifetime complexity",
            "Retained buffers can raise RSS",
        ],
        "falsifier": "Allocations fall but target memory/latency does not improve at the same load",
        "source": SOURCE + "/blob/master/10-java-jvm.md",
        "source_status": "locally authored summary; upstream reference is not a pinned import",
    },
    {
        "id": "batching",
        "version": 1,
        "title": "Amortize per-message work with bounded batches",
        "tags": [
            "throughput",
            "débit",
            "consume",
            "consumption",
            "consommation",
            "i/o",
        ],
        "preconditions": [
            "Profiles show per-operation overhead",
            "Latency budget permits batching",
        ],
        "tradeoffs": ["Waiting and p99 may increase", "Buffers may retain more memory"],
        "falsifier": "Throughput rises only because completion is deferred or load changes",
        "source": SOURCE + "/blob/master/22-kafka-messaging.md",
        "source_status": "locally authored summary; upstream reference is not a pinned import",
    },
    {
        "id": "contention",
        "version": 1,
        "title": "Reduce waiting on shared state",
        "tags": ["latency", "latence", "p99", "lock", "contention"],
        "preconditions": [
            "Wall-clock or lock profiles identify waiting",
            "Correctness invariants are explicit",
        ],
        "tradeoffs": [
            "More memory for sharding",
            "Ordering and synchronization complexity",
        ],
        "falsifier": "Wait time falls without an end-to-end gain or correctness breaks",
        "source": SOURCE + "/blob/master/13-latency-analysis.md",
        "source_status": "locally authored summary; upstream reference is not a pinned import",
    },
    {
        "id": "algorithm-data",
        "version": 1,
        "title": "Reduce work in the measured hot path",
        "tags": ["speed", "vitesse", "cpu", "latency", "latence", "throughput"],
        "preconditions": [
            "A profile identifies repeated or unnecessary work",
            "Input distributions are represented",
        ],
        "tradeoffs": ["Index/cache memory", "Data-dependent behavior"],
        "falsifier": "Gain disappears on the approved input distribution",
        "source": SOURCE,
        "source_status": "locally authored general strategy; no upstream text imported",
    },
]


def plan(goal, protocol=None, baseline=None):
    text = (
        goal + " " + " ".join(m["name"] for m in (protocol or {}).get("metrics", []))
    ).lower()
    scored = sorted(
        CARDS, key=lambda c: sum(tag in text for tag in c["tags"]), reverse=True
    )
    return [
        {
            **card,
            "priority": i + 1,
            "status": "proposed",
            "evidence": {
                "kind": "objective-match",
                "baseline_available": bool(baseline),
            },
            "note": "Preconditions must be checked; this is a hypothesis family, not a diagnosed bottleneck.",
        }
        for i, card in enumerate(scored)
    ]
