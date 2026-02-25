"""Tests for extractor utilities that don't require a live browser."""

import pytest

from browserlens.extractors._cdp import _build_tree, _convert_node, _is_interesting
from browserlens.extractors.dom import DOMExtractor
from browserlens.formatter.ref_manager import RefManager


# ---------------------------------------------------------------------------
# Helpers: build mock CDP node dicts
# ---------------------------------------------------------------------------

def _cdp_node(
    node_id: str,
    role: str,
    name: str,
    *,
    parent_id: str | None = None,
    child_ids: list[str] | None = None,
    ignored: bool = False,
    value: str | None = None,
    props: dict | None = None,
    internal: bool = False,  # use "internalRole" type
) -> dict:
    role_type = "internalRole" if internal else "role"
    node: dict = {
        "nodeId": node_id,
        "ignored": ignored,
        "role": {"type": role_type, "value": role},
        "name": {"type": "computedString", "value": name},
        "properties": [],
        "childIds": child_ids or [],
    }
    if parent_id is not None:
        node["parentId"] = parent_id
    if value is not None:
        node["value"] = {"type": "computedString", "value": value}
    if props:
        node["properties"] = [
            {"name": k, "value": {"type": "boolean", "value": v}}
            for k, v in props.items()
        ]
    return node


# ---------------------------------------------------------------------------
# _build_tree / _convert_node (CDP helper)
# ---------------------------------------------------------------------------

class TestCDPConversion:
    def setup_method(self):
        self.rm = RefManager()

    def test_simple_button(self):
        nodes = [
            _cdp_node("1", "RootWebArea", "Page", internal=True, child_ids=["2"]),
            _cdp_node("2", "button", "Submit", parent_id="1"),
        ]
        root = _build_tree(nodes, self.rm)
        # Root is document (mapped from RootWebArea)
        assert root.role == "document"
        assert len(root.children) == 1
        btn = root.children[0]
        assert btn.role == "button"
        assert btn.name == "Submit"
        assert btn.ref.startswith("@e")

    def test_static_text_maps_to_text(self):
        nodes = [
            _cdp_node("1", "RootWebArea", "", internal=True, child_ids=["2"]),
            _cdp_node("2", "StaticText", "Hello", parent_id="1", internal=True),
        ]
        root = _build_tree(nodes, self.rm)
        assert root.children[0].role == "text"

    def test_ignored_node_children_promoted(self):
        """Ignored intermediate nodes: their children attach to the nearest ancestor."""
        nodes = [
            _cdp_node("1", "RootWebArea", "", internal=True, child_ids=["2"]),
            _cdp_node("2", "none", "", parent_id="1", child_ids=["3"], ignored=True),
            _cdp_node("3", "button", "Go", parent_id="2"),
        ]
        root = _build_tree(nodes, self.rm)
        # "2" is ignored → "3" should attach to root
        assert len(root.children) == 1
        assert root.children[0].role == "button"
        assert root.children[0].name == "Go"

    def test_nested_structure(self):
        nodes = [
            _cdp_node("1", "RootWebArea", "Test", internal=True, child_ids=["2"]),
            _cdp_node("2", "main", "", parent_id="1", child_ids=["3", "4"]),
            _cdp_node("3", "button", "OK", parent_id="2"),
            _cdp_node("4", "button", "Cancel", parent_id="2"),
        ]
        root = _build_tree(nodes, self.rm)
        main_nodes = [c for c in root.children if c.role == "main"]
        assert len(main_nodes) == 1
        assert len(main_nodes[0].children) == 2

    def test_checked_boolean_prop(self):
        nodes = [
            _cdp_node("1", "RootWebArea", "", internal=True, child_ids=["2"]),
            _cdp_node("2", "checkbox", "Accept", parent_id="1", props={"checked": True}),
        ]
        root = _build_tree(nodes, self.rm)
        cb = root.children[0]
        assert cb.checked is True

    def test_disabled_prop(self):
        nodes = [
            _cdp_node("1", "RootWebArea", "", internal=True, child_ids=["2"]),
            _cdp_node("2", "button", "Submit", parent_id="1", props={"disabled": True}),
        ]
        root = _build_tree(nodes, self.rm)
        assert root.children[0].disabled is True

    def test_value_on_textbox(self):
        nodes = [
            _cdp_node("1", "RootWebArea", "", internal=True, child_ids=["2"]),
            _cdp_node("2", "textbox", "Search", parent_id="1", value="laptop"),
        ]
        root = _build_tree(nodes, self.rm)
        assert root.children[0].value == "laptop"

    def test_empty_node_list_returns_placeholder(self):
        root = _build_tree([], self.rm)
        assert root.role == "document"

    def test_refs_are_stable_across_calls(self):
        nodes = [
            _cdp_node("1", "RootWebArea", "", internal=True, child_ids=["2"]),
            _cdp_node("2", "button", "Submit", parent_id="1"),
        ]
        root_a = _build_tree(nodes, self.rm)
        root_b = _build_tree(nodes, self.rm)
        assert root_a.children[0].ref == root_b.children[0].ref


class TestIsInteresting:
    def _node(self, role, name="", children=None):
        from browserlens.core.types import StateNode
        return StateNode(ref="@e1", role=role, name=name, children=children or [])

    def test_semantic_role_always_interesting(self):
        assert _is_interesting(self._node("button", "OK"))
        assert _is_interesting(self._node("textbox", "Search"))
        assert _is_interesting(self._node("link", "Home"))

    def test_generic_no_name_no_children_pruned(self):
        assert not _is_interesting(self._node("generic"))

    def test_generic_with_name_kept(self):
        assert _is_interesting(self._node("generic", "Something"))

    def test_generic_with_children_kept(self):
        child = self._node("button", "OK")
        assert _is_interesting(self._node("generic", "", children=[child]))

    def test_text_node_no_name_pruned(self):
        assert not _is_interesting(self._node("text"))

    def test_text_node_with_name_kept(self):
        assert _is_interesting(self._node("text", "Hello world"))


# ---------------------------------------------------------------------------
# DOMExtractor._convert_node (unchanged — still has its own converter)
# ---------------------------------------------------------------------------

class TestDOMExtractor:
    def setup_method(self):
        self.rm = RefManager()
        self.ext = DOMExtractor(self.rm)

    def test_convert_link_node(self):
        raw = {"role": "link", "name": "Home", "tag": "a", "value": "", "checked": None, "children": []}
        node = self.ext._convert_node(raw)
        assert node.role == "link"
        assert node.name == "Home"

    def test_expanded_string_true(self):
        raw = {
            "role": "combobox", "name": "Country", "tag": "select",
            "value": "", "checked": None, "expanded": "true", "children": [],
        }
        node = self.ext._convert_node(raw)
        assert node.expanded is True

    def test_expanded_string_false(self):
        raw = {
            "role": "combobox", "name": "Country", "tag": "select",
            "value": "", "checked": None, "expanded": "false", "children": [],
        }
        node = self.ext._convert_node(raw)
        assert node.expanded is False

    def test_expanded_none_stays_none(self):
        raw = {
            "role": "button", "name": "Go", "tag": "button",
            "value": "", "checked": None, "expanded": None, "children": [],
        }
        node = self.ext._convert_node(raw)
        assert node.expanded is None
