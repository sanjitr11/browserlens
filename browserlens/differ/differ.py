"""StateDiffer — main diff engine."""

from __future__ import annotations

from browserlens.core.types import Delta, PageState, RepresentationType, StateNode
from browserlens.differ.semantic_filter import SemanticFilter
from browserlens.differ.snapshot_store import SnapshotStore
from browserlens.differ.tree_diff import diff_trees


class StateDiffer:
    """
    Compares the current PageState against the stored previous one.
    On the first call (no previous state), returns a Delta with is_full_state=True.
    """

    def __init__(self) -> None:
        self._store = SnapshotStore()
        self._filter = SemanticFilter()

    def diff(self, current: PageState) -> Delta:
        previous = self._store.get_previous()
        self._store.update(current)

        if previous is None:
            # First step — no previous state to diff against
            return Delta(
                step=current.step,
                is_full_state=True,
                representation_type=current.representation_type,
                unchanged_count=len(current.flat_nodes()),
            )

        delta = diff_trees(
            old_root=previous.root,
            new_root=current.root,
            step=current.step,
            rep_type=current.representation_type,
        )

        # Apply semantic noise filter
        delta = self._filter.filter(delta)

        # Build human-readable unchanged summary
        delta.unchanged_summary = self._summarize_unchanged(
            current.root, delta
        )

        return delta

    def get_previous_url(self) -> str | None:
        """Return the URL of the previously stored page state, or None on the first step."""
        prev = self._store.get_previous()
        return prev.url if prev is not None else None

    def force_full_state(self, current: PageState) -> Delta:
        """Update the store and return a full-state delta without running tree diff."""
        self._store.update(current)
        return Delta(
            step=current.step,
            is_full_state=True,
            representation_type=current.representation_type,
            unchanged_count=len(current.flat_nodes()),
        )

    def reset(self) -> None:
        self._store.reset()

    def _summarize_unchanged(self, root: StateNode, delta: Delta) -> str:
        """
        Produce a compact summary of what did NOT change.
        E.g.: "Navigation (3 links), heading, search box — all stable."
        """
        if delta.unchanged_count == 0:
            return ""

        # Collect top-level landmark roles that are entirely unchanged
        changed_refs = {c.ref for c in delta.changed}
        added_refs = {n.ref for n in delta.added}
        removed_refs = {n.ref for n in delta.removed}
        noisy_refs = changed_refs | added_refs | removed_refs

        stable_landmarks: list[str] = []
        for child in root.children:
            if child.ref not in noisy_refs and not _subtree_has_refs(child, noisy_refs):
                label = child.name or child.role
                count = _count_leaves(child)
                if count > 1:
                    stable_landmarks.append(f"{label} ({count} items)")
                else:
                    stable_landmarks.append(label)

        if stable_landmarks:
            joined = ", ".join(stable_landmarks[:5])
            if len(stable_landmarks) > 5:
                joined += f" and {len(stable_landmarks) - 5} more"
            return f"{joined} — unchanged"

        return f"{delta.unchanged_count} nodes unchanged"


def _subtree_has_refs(node: StateNode, refs: set[str]) -> bool:
    if node.ref in refs:
        return True
    return any(_subtree_has_refs(c, refs) for c in node.children)


def _count_leaves(node: StateNode) -> int:
    if not node.children:
        return 1
    return sum(_count_leaves(c) for c in node.children)
