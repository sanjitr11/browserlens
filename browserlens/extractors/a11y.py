"""Accessibility tree extractor using Playwright's built-in a11y snapshot."""

from __future__ import annotations

from playwright.async_api import Page

from browserlens.core.types import PageState, RepresentationType, StateNode
from browserlens.extractors.base import BaseExtractor
from browserlens.formatter.ref_manager import RefManager


class A11yExtractor(BaseExtractor):
    """
    Uses Playwright's accessibility.snapshot() to get the full a11y tree.
    Node identities are mapped to stable @eN refs via RefManager.
    """

    def __init__(self, ref_manager: RefManager) -> None:
        super().__init__(ref_manager)

    @property
    def representation_type(self) -> RepresentationType:
        return RepresentationType.A11Y_TREE

    async def extract(self, page: Page) -> PageState:
        snapshot = await page.accessibility.snapshot(interesting_only=True)
        root = self._convert_node(snapshot or {})
        return PageState(
            url=page.url,
            title=await page.title(),
            representation_type=self.representation_type,
            root=root,
        )

    def _convert_node(self, raw: dict, parent_role: str = "") -> StateNode:
        role = raw.get("role", "generic")
        name = raw.get("name", "")

        fingerprint = (role, name, parent_role)
        ref = self._refs.get_or_create(fingerprint)

        node = StateNode(
            ref=ref,
            role=role,
            name=name,
            value=raw.get("value", "") or "",
            checked=raw.get("checked"),
            expanded=raw.get("expanded"),
            disabled=raw.get("disabled", False),
            focused=raw.get("focused", False),
            live=raw.get("live", ""),
        )

        for child_raw in raw.get("children", []):
            node.children.append(self._convert_node(child_raw, parent_role=role))

        return node
