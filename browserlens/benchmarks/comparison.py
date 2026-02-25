"""A/B comparison: BrowserLens vs raw Playwright MCP vs full a11y tree."""

from __future__ import annotations

from dataclasses import dataclass, field

from browserlens.benchmarks.latency_tracker import LatencyTracker
from browserlens.benchmarks.token_counter import TokenCounter


@dataclass
class SystemResult:
    """Benchmark result for one system across a full task."""

    name: str
    token_counter: TokenCounter = field(default_factory=TokenCounter)
    latency_tracker: LatencyTracker = field(default_factory=LatencyTracker)

    def summary(self) -> dict:
        return {
            "system": self.name,
            "tokens": self.token_counter.summary(),
            "latency": self.latency_tracker.summary(),
        }


class BenchmarkComparison:
    """
    Runs the same task scenario against multiple systems and reports
    token usage and latency comparisons.

    Usage:
        bench = BenchmarkComparison()
        baseline = bench.add_system("baseline_a11y")
        browserlens = bench.add_system("browserlens")

        # In your agent loop:
        baseline.token_counter.record(step, url, full_a11y_text, "a11y", False)
        browserlens.token_counter.record(step, url, lens_text, rep_type, is_delta)

        print(bench.report())
    """

    def __init__(self) -> None:
        self._systems: dict[str, SystemResult] = {}

    def add_system(self, name: str) -> SystemResult:
        result = SystemResult(name=name)
        self._systems[name] = result
        return result

    def report(self) -> dict:
        if not self._systems:
            return {}

        summaries = [s.summary() for s in self._systems.values()]

        # Compute token reduction ratios relative to first system (baseline)
        baseline_name = next(iter(self._systems))
        baseline_total = self._systems[baseline_name].token_counter.total_tokens

        for summary in summaries:
            total = summary["tokens"]["total_tokens"]
            if baseline_total > 0:
                summary["token_reduction_vs_baseline"] = round(
                    1 - total / baseline_total, 3
                )
            else:
                summary["token_reduction_vs_baseline"] = 0.0

        return {
            "systems": summaries,
            "baseline": baseline_name,
        }

    def print_report(self) -> None:
        import json
        print(json.dumps(self.report(), indent=2))
