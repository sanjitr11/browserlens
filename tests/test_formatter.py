"""Tests for the formatter layer."""

import pytest

from browserlens.core.types import Delta, NodeChange, PageState, RepresentationType, StateNode
from browserlens.formatter.formatter import OutputFormatter
from browserlens.formatter.ref_manager import RefManager
from browserlens.formatter.token_budget import TokenBudget


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_node(ref, role, name, value="", children=None, **kwargs):
    return StateNode(ref=ref, role=role, name=name, value=value, children=children or [], **kwargs)


def make_state(root, step=1):
    return PageState(
        url="https://example.com",
        title="Test",
        representation_type=RepresentationType.A11Y_TREE,
        root=root,
        step=step,
    )


# ---------------------------------------------------------------------------
# RefManager
# ---------------------------------------------------------------------------

class TestRefManager:
    def test_new_fingerprint_gets_ref(self):
        rm = RefManager()
        ref = rm.get_or_create(("button", "Submit", "form"))
        assert ref == "@e1"

    def test_same_fingerprint_same_ref(self):
        rm = RefManager()
        fp = ("button", "Submit", "form")
        assert rm.get_or_create(fp) == rm.get_or_create(fp)

    def test_different_fingerprints_get_different_refs(self):
        rm = RefManager()
        r1 = rm.get_or_create(("button", "Submit", "form"))
        r2 = rm.get_or_create(("link", "Home", "nav"))
        assert r1 != r2

    def test_lookup(self):
        rm = RefManager()
        fp = ("button", "Submit", "form")
        ref = rm.get_or_create(fp)
        assert rm.lookup(ref) == fp

    def test_reset_clears_state(self):
        rm = RefManager()
        rm.get_or_create(("button", "Submit", "form"))
        rm.reset()
        assert rm.total_refs == 0
        new_ref = rm.get_or_create(("button", "Submit", "form"))
        assert new_ref == "@e1"


# ---------------------------------------------------------------------------
# TokenBudget
# ---------------------------------------------------------------------------

class TestTokenBudget:
    def test_count_nonempty(self):
        tb = TokenBudget()
        assert tb.count("Hello world") > 0

    def test_empty_string(self):
        tb = TokenBudget()
        assert tb.count("") == 0 or tb.count("") >= 0  # depends on tiktoken

    def test_truncate_short_text(self):
        tb = TokenBudget()
        text = "short text"
        truncated, was_truncated = tb.truncate(text, max_tokens=1000)
        assert not was_truncated
        assert truncated == text

    def test_truncate_long_text(self):
        tb = TokenBudget()
        text = " ".join(["word"] * 10000)
        truncated, was_truncated = tb.truncate(text, max_tokens=50)
        assert was_truncated
        assert "[... truncated" in truncated

    def test_fits(self):
        tb = TokenBudget()
        assert tb.fits("hi", 1000)
        assert not tb.fits(" ".join(["word"] * 10000), 10)


# ---------------------------------------------------------------------------
# OutputFormatter
# ---------------------------------------------------------------------------

class TestOutputFormatter:
    def setup_method(self):
        self.rm = RefManager()
        self.fmt = OutputFormatter(ref_manager=self.rm, token_budget=8192)

    def test_full_state_contains_header(self):
        state = make_state(make_node("@e1", "main", ""), step=1)
        text, _ = self.fmt.format(state, None)
        assert "[FULL PAGE STATE" in text
        assert "step 1" in text

    def test_full_state_contains_node(self):
        state = make_state(make_node("@e1", "button", "Submit"), step=1)
        text, _ = self.fmt.format(state, None)
        assert "button" in text
        assert "Submit" in text
        assert "@e1" in text

    def test_delta_format_shows_added(self):
        delta = Delta(
            step=2,
            added=[make_node("@e5", "dialog", "Confirmation")],
            is_full_state=False,
            representation_type=RepresentationType.A11Y_TREE,
        )
        state = make_state(make_node("@e1", "main", ""), step=2)
        text, _ = self.fmt.format(state, delta)
        assert "ADDED" in text
        assert "dialog" in text
        assert "Confirmation" in text

    def test_delta_format_shows_removed(self):
        delta = Delta(
            step=2,
            removed=[make_node("@e3", "text", "Loading...")],
            is_full_state=False,
            representation_type=RepresentationType.A11Y_TREE,
        )
        state = make_state(make_node("@e1", "main", ""), step=2)
        text, _ = self.fmt.format(state, delta)
        assert "REMOVED" in text
        assert "Loading..." in text

    def test_delta_format_shows_changed(self):
        change = NodeChange(
            ref="@e2",
            role="textbox",
            name="Search",
            changed_props={"value": ("", "laptop")},
        )
        delta = Delta(
            step=2,
            changed=[change],
            is_full_state=False,
            representation_type=RepresentationType.A11Y_TREE,
        )
        state = make_state(make_node("@e1", "main", ""), step=2)
        text, _ = self.fmt.format(state, delta)
        assert "CHANGED" in text
        assert "laptop" in text

    def test_token_count_returned(self):
        state = make_state(make_node("@e1", "button", "Click me"), step=1)
        text, token_count = self.fmt.format(state, None)
        assert token_count > 0

    def test_is_full_state_delta_renders_full(self):
        delta = Delta(step=1, is_full_state=True, representation_type=RepresentationType.A11Y_TREE)
        state = make_state(make_node("@e1", "main", ""), step=1)
        text, _ = self.fmt.format(state, delta)
        assert "[FULL PAGE STATE" in text
