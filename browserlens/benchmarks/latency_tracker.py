"""Latency tracker — measures wall-clock time per step."""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Generator


@dataclass
class LatencyRecord:
    step: int
    phase: str  # "router", "extraction", "diff", "format", "total"
    ms: float


@dataclass
class LatencyTracker:
    """Tracks per-phase latency and computes statistics."""

    records: list[LatencyRecord] = field(default_factory=list)

    @contextmanager
    def measure(self, step: int, phase: str) -> Generator[None, None, None]:
        t0 = time.monotonic()
        yield
        elapsed_ms = (time.monotonic() - t0) * 1000
        self.records.append(LatencyRecord(step=step, phase=phase, ms=elapsed_ms))

    def record(self, step: int, phase: str, ms: float) -> None:
        self.records.append(LatencyRecord(step=step, phase=phase, ms=ms))

    def phase_records(self, phase: str) -> list[LatencyRecord]:
        return [r for r in self.records if r.phase == phase]

    def avg_ms(self, phase: str) -> float:
        recs = self.phase_records(phase)
        if not recs:
            return 0.0
        return sum(r.ms for r in recs) / len(recs)

    def summary(self) -> dict:
        phases = {r.phase for r in self.records}
        return {
            phase: {
                "avg_ms": round(self.avg_ms(phase), 2),
                "max_ms": round(max(r.ms for r in self.phase_records(phase)), 2),
                "count": len(self.phase_records(phase)),
            }
            for phase in sorted(phases)
        }

    def reset(self) -> None:
        self.records.clear()
