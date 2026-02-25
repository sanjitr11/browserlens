"""Accessibility tree extractor using CDP (Playwright 1.46+)."""

from __future__ import annotations

from playwright.async_api import Page

from browserlens.core.types import PageState, RepresentationType
from browserlens.extractors._cdp import extract_ax_tree
from browserlens.extractors.base import BaseExtractor
from browserlens.formatter.ref_manager import RefManager


class A11yExtractor(BaseExtractor):
    """
    Extracts the full accessibility tree via Chrome DevTools Protocol.
    Node identities are mapped to stable @eN refs via RefManager.
    """

    def __init__(self, ref_manager: RefManager) -> None:
        super().__init__(ref_manager)

    @property
    def representation_type(self) -> RepresentationType:
        return RepresentationType.A11Y_TREE

    async def extract(self, page: Page) -> PageState:
        root = await extract_ax_tree(page, self._refs)
        return PageState(
            url=page.url,
            title=await page.title(),
            representation_type=self.representation_type,
            root=root,
        )
