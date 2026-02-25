"""Semantic filter — strips noise from diffs before they reach the LLM."""

from __future__ import annotations

import re

from browserlens.core.types import Delta, NodeChange, StateNode

# Patterns that indicate timer / clock / live-counter content
_TIMER_PATTERNS = [
    re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$"),            # HH:MM or HH:MM:SS
    re.compile(r"^\d+\s*(second|minute|hour|sec|min)s?\s*ago$", re.I),
    re.compile(r"^(just now|moments ago)$", re.I),
    re.compile(r"^\d{1,3}%$"),                            # pure percentage (progress bars)
]

# aria-live regions that change frequently but carry little agent-relevant info
_NOISY_LIVE_ROLES = {"status", "timer", "marquee", "log"}

# Names/roles that typically indicate decorative or ad content
_AD_HINTS = re.compile(
    r"(advertisement|sponsored|promoted|ad choice|ad by)",
    re.I,
)


class SemanticFilter:
    """
    Removes low-signal changes from a Delta before it's formatted for the LLM.

    Preserved (always kept):
    - New error/alert nodes
    - State changes on interactive elements (enabled→disabled, checked, expanded)
    - New modals/dialogs
    - Navigation or URL-level changes
    - New visible text that isn't a timer/counter

    Filtered (removed):
    - Timer/clock value updates
    - Ad content changes
    - aria-live="polite" updates on non-critical regions
    - Purely cosmetic attribute changes (CSS classes are not included in StateNode)
    """

    def filter(self, delta: Delta) -> Delta:
        delta.added = [n for n in delta.added if not self._is_noisy_node(n)]
        delta.removed = [n for n in delta.removed if not self._is_noisy_node(n)]
        delta.changed = [c for c in delta.changed if not self._is_noisy_change(c)]
        return delta

    def _is_noisy_node(self, node: StateNode) -> bool:
        # Ad content
        if _AD_HINTS.search(node.name):
            return True
        # Timer-like text nodes
        if node.role in ("text", "StaticText", "generic") and self._is_timer_text(node.name):
            return True
        # Noisy live regions
        if node.live and node.role in _NOISY_LIVE_ROLES:
            return True
        return False

    def _is_noisy_change(self, change: NodeChange) -> bool:
        # Ad content
        if _AD_HINTS.search(change.name):
            return True
        # Only "value" changed on a timer-like node
        if set(change.changed_props.keys()) == {"value"}:
            _, new_val = change.changed_props["value"]
            if self._is_timer_text(str(new_val)):
                return True
        return False

    def _is_timer_text(self, text: str) -> bool:
        text = text.strip()
        return any(p.match(text) for p in _TIMER_PATTERNS)
