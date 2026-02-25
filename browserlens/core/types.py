"""Shared types and dataclasses for BrowserLens."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RepresentationType(str, Enum):
    A11Y_TREE = "a11y_tree"
    DISTILLED_DOM = "distilled_dom"
    VISION = "vision"
    HYBRID = "hybrid"  # a11y + selective screenshot


@dataclass
class StateNode:
    """A single node in the accessibility/DOM tree."""

    ref: str  # stable @eN reference ID
    role: str  # ARIA role (button, textbox, link, heading, ...)
    name: str  # accessible name
    value: str = ""  # current value (inputs, selects)
    checked: bool | None = None  # checkboxes / radios
    expanded: bool | None = None  # trees, accordions
    disabled: bool = False
    focused: bool = False
    live: str = ""  # aria-live region type ("polite", "assertive", "")
    children: list[StateNode] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def fingerprint(self) -> tuple[str, str]:
        """Role + name tuple used for identity matching when ref IDs don't persist."""
        return (self.role, self.name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "role": self.role,
            "name": self.name,
            "value": self.value,
            "checked": self.checked,
            "expanded": self.expanded,
            "disabled": self.disabled,
            "focused": self.focused,
            "live": self.live,
            "children": [c.to_dict() for c in self.children],
            "attributes": self.attributes,
        }


@dataclass
class PageState:
    """Full snapshot of a page at a given moment."""

    url: str
    title: str
    representation_type: RepresentationType
    root: StateNode  # root of the accessibility/DOM tree
    screenshot_b64: str | None = None  # set when representation includes vision
    step: int = 0  # agent step number that produced this state
    raw_token_count: int = 0  # tokens in the full (unfiltered) representation

    def flat_nodes(self) -> list[StateNode]:
        """Return all nodes as a flat list (depth-first)."""
        result: list[StateNode] = []
        stack = [self.root]
        while stack:
            node = stack.pop()
            result.append(node)
            stack.extend(reversed(node.children))
        return result


@dataclass
class NodeChange:
    """A change to a single node between two PageState snapshots."""

    ref: str
    role: str
    name: str
    changed_props: dict[str, tuple[Any, Any]]  # prop -> (old, new)


@dataclass
class Delta:
    """The diff between two consecutive PageState snapshots."""

    step: int
    added: list[StateNode] = field(default_factory=list)
    removed: list[StateNode] = field(default_factory=list)
    changed: list[NodeChange] = field(default_factory=list)
    unchanged_count: int = 0
    unchanged_summary: str = ""
    is_full_state: bool = False  # True on first step (no previous state to diff against)
    representation_type: RepresentationType = RepresentationType.A11Y_TREE

    @property
    def is_empty(self) -> bool:
        return not self.added and not self.removed and not self.changed

    @property
    def total_changes(self) -> int:
        return len(self.added) + len(self.removed) + len(self.changed)


@dataclass
class PageSignals:
    """Fast signals collected by the router before choosing a representation."""

    url: str
    has_canvas: bool = False
    has_webgl: bool = False
    a11y_coverage: float = 0.0  # 0.0–1.0: ratio of a11y-visible interactive elements
    dom_node_count: int = 0
    dom_max_depth: int = 0
    dom_avg_children: float = 0.0
    dynamic_content_ratio: float = 0.0  # fraction of nodes changing per 500ms sample
    page_type: str = "unknown"  # "form", "dashboard", "article", "search", "unknown"

    @property
    def origin(self) -> str:
        from urllib.parse import urlparse
        parsed = urlparse(self.url)
        return f"{parsed.scheme}://{parsed.netloc}"


@dataclass
class ObservationResult:
    """What BrowserLens returns to the agent after observe()."""

    step: int
    url: str
    representation_type: RepresentationType
    formatted_text: str  # LLM-ready string (full state or delta)
    delta: Delta | None  # None on first step
    page_state: PageState
    token_count: int  # tokens in formatted_text
    latency_ms: float  # wall-clock ms for the full observe() call
