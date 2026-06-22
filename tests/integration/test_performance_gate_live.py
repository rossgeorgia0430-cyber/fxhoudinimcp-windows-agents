"""Repeatable Windows/Houdini latency gates for high-frequency agent tools."""

from __future__ import annotations

import statistics
import time

import pytest


pytestmark = pytest.mark.integration


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * percentile)))
    return ordered[index]


def _measure(call, command: str, repetitions: int = 15, **params) -> dict[str, float]:
    samples: list[float] = []
    for _ in range(repetitions):
        start = time.perf_counter()
        call(command, **params)
        samples.append((time.perf_counter() - start) * 1000)
    result = {"p50": statistics.median(samples), "p95": _percentile(samples, 0.95), "max": max(samples)}
    print(
        f"[perf-gate] {command}: p50={result['p50']:.1f}ms "
        f"p95={result['p95']:.1f}ms max={result['max']:.1f}ms"
    )
    return result


class TestAgentHotPathPerformance:
    def test_discovery_and_batch_mutation_latency(self, call):
        geo = call("nodes.create_node", parent_path="/obj", node_type="geo", name="perf")
        box = call("nodes.create_node", parent_path=geo["node_path"], node_type="box")["node_path"]

        discover = _measure(call, "nodes.list_node_types", context="Sop", filter="scatter")
        read = _measure(call, "nodes.get_node_info", node_path=box)
        mutate = _measure(
            call,
            "parameters.set_parameters",
            node_path=box,
            params={"size": [1.0, 2.0, 3.0], "scale": 1.25},
        )

        # These deliberately generous limits catch pathological regressions
        # while remaining stable on a licensed interactive workstation.
        assert discover["p95"] < 5_000
        assert read["p95"] < 5_000
        assert mutate["p95"] < 5_000
