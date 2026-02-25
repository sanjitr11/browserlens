"""Tests for extractor utilities that don't require a live browser."""

import pytest

from browserlens.extractors.a11y import A11yExtractor
from browserlens.extractors.dom import DOMExtractor
from browserlens.formatter.ref_manager import RefManager


class TestA11yExtractor:
    def setup_method(self):
        self.rm = RefManager()
        self.ext = A11yExtractor(self.rm)

    def test_convert_simple_node(self):
        raw = {"role": "button", "name": "Submit"}
        node = self.ext._convert_node(raw)
        assert node.role == "button"
        assert node.name == "Submit"
        assert node.ref.startswith("@e")

    def test_convert_nested_nodes(self):
        raw = {
            "role": "main",
            "name": "",
            "children": [
                {"role": "button", "name": "OK"},
                {"role": "button", "name": "Cancel"},
            ],
        }
        node = self.ext._convert_node(raw)
        assert len(node.children) == 2
        assert node.children[0].name == "OK"

    def test_checked_state(self):
        raw = {"role": "checkbox", "name": "Accept", "checked": True}
        node = self.ext._convert_node(raw)
        assert node.checked is True

    def test_disabled_state(self):
        raw = {"role": "button", "name": "Submit", "disabled": True}
        node = self.ext._convert_node(raw)
        assert node.disabled is True

    def test_empty_snapshot_returns_generic_root(self):
        node = self.ext._convert_node({})
        assert node.role == "generic"
        assert node.name == ""


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
