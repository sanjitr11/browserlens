"""Token counter — measures tokens per step/task."""

from __future__ import annotations

from dataclasses import dataclass, field

from browserlens.formatter.token_budget import TokenBudget


@dataclass
class StepRecord:
    step: int
    url: str
    tokens: int
    representation: str
    is_delta: bool


@dataclass
class TokenCounter:
    """Accumulates per-step token counts and computes summary statistics."""

    records: list[StepRecord] = field(default_factory=list)
    _budget: TokenBudget = field(default_factory=TokenBudget, repr=False)

    def record(self, step: int, url: str, text: str, representation: str, is_delta: bool) -> int:
        tokens = self._budget.count(text)
        self.records.append(StepRecord(
            step=step,
            url=url,
            tokens=tokens,
            representation=representation,
            is_delta=is_delta,
        ))
        return tokens

    @property
    def total_tokens(self) -> int:
        return sum(r.tokens for r in self.records)

    @property
    def avg_tokens_per_step(self) -> float:
        if not self.records:
            return 0.0
        return self.total_tokens / len(self.records)

    @property
    def max_tokens_per_step(self) -> int:
        if not self.records:
            return 0
        return max(r.tokens for r in self.records)

    def summary(self) -> dict:
        return {
            "total_steps": len(self.records),
            "total_tokens": self.total_tokens,
            "avg_tokens_per_step": round(self.avg_tokens_per_step, 1),
            "max_tokens_per_step": self.max_tokens_per_step,
            "delta_steps": sum(1 for r in self.records if r.is_delta),
            "full_state_steps": sum(1 for r in self.records if not r.is_delta),
        }

    def reset(self) -> None:
        self.records.clear()
