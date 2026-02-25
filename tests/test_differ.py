"""Tests for the differ layer: tree_diff, semantic_filter, snapshot_store, StateDiffer."""

import pytest

from browserlens.core.types import Delta, PageState, RepresentationType, StateNode
from browserlens.differ.differ import StateDiffer
from browserlens.differ.semantic_filter import SemanticFilter
from browserlens.differ.snapshot_store import SnapshotStore
from browserlens.differ.tree_diff import diff_trees


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_node(ref: str, role: str, name: str, value: str = "", children=None, **kwargs) -> StateNode:
    return StateNode(ref=ref, role=role, name=name, value=value, children=children or [], **kwargs)


def make_state(root: StateNode, step: int = 1, url: str = "https://example.com") -> PageState:
    return PageState(
        url=url,
        title="Test Page",
        representation_type=RepresentationType.A11Y_TREE,
        root=root,
        step=step,
    )


# ---------------------------------------------------------------------------
# SnapshotStore
# ---------------------------------------------------------------------------

class TestSnapshotStore:
    def test_initially_empty(self):
        store = SnapshotStore()
        assert store.get_previous() is None

    def test_update_and_retrieve(self):
        store = SnapshotStore()
        state = make_state(make_node("@e1", "button", "Submit"))
        store.update(state)
        assert store.get_previous() is state

    def test_reset_clears_state(self):
        store = SnapshotStore()
        store.update(make_state(make_node("@e1", "button", "Submit")))
        store.reset()
        assert store.get_previous() is None


# ---------------------------------------------------------------------------
# tree_diff
# ---------------------------------------------------------------------------

class TestTreeDiff:
    def test_no_changes(self):
        root = make_node("@e1", "main", "", children=[
            make_node("@e2", "button", "Submit"),
        ])
        delta = diff_trees(root, root, step=2, rep_type=RepresentationType.A11Y_TREE)
        assert delta.is_empty
        assert delta.unchanged_count == 2

    def test_added_node(self):
        old_root = make_node("@e1", "main", "")
        new_root = make_node("@e1", "main", "", children=[
            make_node("@e2", "button", "Submit"),
        ])
        delta = diff_trees(old_root, new_root, step=2, rep_type=RepresentationType.A11Y_TREE)
        assert len(delta.added) == 1
        assert delta.added[0].ref == "@e2"

    def test_removed_node(self):
        old_root = make_node("@e1", "main", "", children=[
            make_node("@e2", "button", "Submit"),
        ])
        new_root = make_node("@e1", "main", "")
        delta = diff_trees(old_root, new_root, step=2, rep_type=RepresentationType.A11Y_TREE)
        assert len(delta.removed) == 1
        assert delta.removed[0].ref == "@e2"

    def test_changed_value(self):
        old_root = make_node("@e1", "textbox", "Search", value="")
        new_root = make_node("@e1", "textbox", "Search", value="laptop")
        delta = diff_trees(old_root, new_root, step=2, rep_type=RepresentationType.A11Y_TREE)
        assert len(delta.changed) == 1
        assert delta.changed[0].changed_props["value"] == ("", "laptop")

    def test_fingerprint_matching(self):
        # Same (role, name, parent_role) but different ref IDs
        old_root = make_node("@e1", "main", "", children=[
            make_node("@e2", "button", "Go"),
        ])
        new_root = make_node("@e1", "main", "", children=[
            make_node("@e99", "button", "Go"),  # ref changed but same fingerprint
        ])
        delta = diff_trees(old_root, new_root, step=2, rep_type=RepresentationType.A11Y_TREE)
        # Should match by fingerprint — not added/removed
        assert len(delta.added) == 0
        assert len(delta.removed) == 0

    def test_changed_disabled_state(self):
        old_root = make_node("@e1", "button", "Submit", disabled=False)
        new_root = make_node("@e1", "button", "Submit", disabled=True)
        delta = diff_trees(old_root, new_root, step=2, rep_type=RepresentationType.A11Y_TREE)
        assert len(delta.changed) == 1
        assert "disabled" in delta.changed[0].changed_props


# ---------------------------------------------------------------------------
# SemanticFilter
# ---------------------------------------------------------------------------

class TestSemanticFilter:
    def setup_method(self):
        self.f = SemanticFilter()

    def _make_delta(self, added=None, removed=None, changed=None) -> Delta:
        from browserlens.core.types import NodeChange
        return Delta(
            step=2,
            added=added or [],
            removed=removed or [],
            changed=changed or [],
        )

    def test_filters_timer_text_node(self):
        timer_node = make_node("@e1", "text", "12:34")
        delta = self._make_delta(added=[timer_node])
        result = self.f.filter(delta)
        assert len(result.added) == 0

    def test_keeps_error_node(self):
        error_node = make_node("@e1", "alert", "Invalid email address")
        delta = self._make_delta(added=[error_node])
        result = self.f.filter(delta)
        assert len(result.added) == 1

    def test_filters_ad_node(self):
        ad_node = make_node("@e1", "region", "Advertisement")
        delta = self._make_delta(added=[ad_node])
        result = self.f.filter(delta)
        assert len(result.added) == 0

    def test_filters_timer_value_change(self):
        from browserlens.core.types import NodeChange
        change = NodeChange(ref="@e1", role="text", name="clock", changed_props={"value": ("12:33", "12:34")})
        delta = self._make_delta(changed=[change])
        result = self.f.filter(delta)
        assert len(result.changed) == 0

    def test_keeps_input_value_change(self):
        from browserlens.core.types import NodeChange
        change = NodeChange(ref="@e1", role="textbox", name="Search", changed_props={"value": ("", "laptop")})
        delta = self._make_delta(changed=[change])
        result = self.f.filter(delta)
        assert len(result.changed) == 1


# ---------------------------------------------------------------------------
# StateDiffer
# ---------------------------------------------------------------------------

class TestStateDiffer:
    def test_first_step_returns_full_state(self):
        differ = StateDiffer()
        state = make_state(make_node("@e1", "main", ""))
        delta = differ.diff(state)
        assert delta.is_full_state

    def test_second_step_returns_delta(self):
        differ = StateDiffer()
        state1 = make_state(make_node("@e1", "main", ""), step=1)
        state2 = make_state(make_node("@e1", "main", "", children=[
            make_node("@e2", "button", "New Button"),
        ]), step=2)
        differ.diff(state1)
        delta = differ.diff(state2)
        assert not delta.is_full_state
        assert len(delta.added) == 1

    def test_reset_returns_full_state_again(self):
        differ = StateDiffer()
        state = make_state(make_node("@e1", "main", ""))
        differ.diff(state)
        differ.reset()
        delta = differ.diff(state)
        assert delta.is_full_state
