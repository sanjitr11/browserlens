"""Layer 3 — Workflow Compiler type definitions."""

from __future__ import annotations

import hashlib
import re
import string
from dataclasses import dataclass, field
from enum import Enum


class ActionType(str, Enum):
    CLICK = "click"
    TYPE = "type"
    SELECT = "select"
    PRESS = "press"
    HOVER = "hover"
    SCROLL = "scroll"
    NAVIGATE = "navigate"
    WAIT = "wait"


class SelectorStrategy(str, Enum):
    TEST_ID = "test_id"
    ROLE_NAME = "role_name"
    LABEL = "label"
    PLACEHOLDER = "placeholder"
    TEXT = "text"
    CSS = "css"
    XPATH = "xpath"


@dataclass
class ElementTarget:
    ref: str
    role: str
    name: str
    selectors: dict[SelectorStrategy, str]
    selector_priority: list[SelectorStrategy]


@dataclass
class TraceStep:
    step_index: int
    action: ActionType
    target: ElementTarget | None  # None for NAVIGATE and WAIT
    value: str | None
    url_before: str
    url_after: str | None = None


@dataclass
class WorkflowTrace:
    task_description: str
    site_domain: str
    steps: list[TraceStep]
    success: bool = True
    recorded_at: str = ""


@dataclass
class ParameterSlot:
    name: str
    step_indices: list[int]
    default_value: str | None = None


@dataclass
class CompiledWorkflow:
    workflow_id: str
    task_description: str
    task_fingerprint: str
    site_domain: str
    script_path: str
    parameter_slots: list[ParameterSlot] = field(default_factory=list)
    step_count: int = 0
    compiled_at: str = ""
    source_trace: WorkflowTrace | None = None  # None after disk deserialization


@dataclass
class StepResult:
    step_index: int
    success: bool
    action: str
    error: str | None = None
    healed: bool = False
    heal_level: int | None = None
    latency_ms: float = 0.0


@dataclass
class ExecutionResult:
    workflow_id: str
    success: bool
    steps_executed: int
    steps_succeeded: int
    step_results: list[StepResult] = field(default_factory=list)
    error: str | None = None
    total_latency_ms: float = 0.0


def normalize_task(description: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    text = description.lower()
    text = text.translate(str.maketrans("", "", string.punctuation))
    text = re.sub(r"\s+", " ", text).strip()
    return text


def make_fingerprint(task_description: str) -> str:
    """sha256 of normalized task description."""
    return hashlib.sha256(normalize_task(task_description).encode()).hexdigest()
