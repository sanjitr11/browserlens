"""Screenshot (vision) extractor."""

from __future__ import annotations

import base64

from playwright.async_api import Page

from browserlens.core.types import PageState, RepresentationType
from browserlens.extractors._cdp import extract_ax_tree
from browserlens.extractors.base import BaseExtractor
from browserlens.formatter.ref_manager import RefManager


class VisionExtractor(BaseExtractor):
    """
    Captures a full-page screenshot as base64. Used for canvas-heavy or
    poorly-labelled pages where text representations lose too much information.
    The a11y tree is still extracted as a skeletal structure so diffing works.
    """

    def __init__(self, ref_manager: RefManager, *, full_page: bool = False) -> None:
        super().__init__(ref_manager)
        self._full_page = full_page

    @property
    def representation_type(self) -> RepresentationType:
        return RepresentationType.VISION

    async def extract(self, page: Page) -> PageState:
        screenshot_bytes = await page.screenshot(
            type="jpeg",
            quality=75,
            full_page=self._full_page,
        )
        screenshot_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")

        # Skeletal a11y tree so diffing has something to work with
        root = await extract_ax_tree(page, self._refs)

        return PageState(
            url=page.url,
            title=await page.title(),
            representation_type=self.representation_type,
            root=root,
            screenshot_b64=screenshot_b64,
        )
