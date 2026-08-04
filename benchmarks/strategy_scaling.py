"""Emit reproducible scaling metrics for Aura's finite-trace strategy path."""

from __future__ import annotations

import json
import time
import tracemalloc

from aura_runtime.policy import AuraSpec
from aura_runtime.verifier import OnlineLTLfMonitor


def benchmark(proposition_count: int) -> dict[str, int | float | str]:
    propositions = {
        f"p{index}": {"event": "run.started" if index % 2 == 0 else "run.completed"}
        for index in range(proposition_count)
    }
    policy = {
        "id": "scaling",
        "description": "Synthetic strategy scaling benchmark",
        "formula": "F (" + " | ".join(propositions) + ")",
        "propositions": propositions,
        "proposition_control": {name: "agent" for name in propositions},
    }
    spec = AuraSpec.model_validate({"version": "0.1", "ltlf_policies": [policy]})
    tracemalloc.start()
    started = time.perf_counter()
    report = OnlineLTLfMonitor(spec).strategy_report().policies[0].strategy
    elapsed = time.perf_counter() - started
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "propositions": proposition_count,
        "total_valuations": report.total_valuation_count,
        "feasible_valuations": report.feasible_valuation_count,
        "reachable_states": report.reachable_state_count,
        "valuation_backend": report.valuation_backend,
        "strategy_backend": report.strategy_backend,
        "elapsed_seconds": round(elapsed, 6),
        "peak_bytes": peak_bytes,
    }


if __name__ == "__main__":
    print(json.dumps([benchmark(size) for size in range(2, 11, 2)], indent=2))
