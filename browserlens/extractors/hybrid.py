"""Hybrid extractor: a11y tree + targeted screenshots of canvas/visual regions."""

from __future__ import annotations

import base64

from playwright.async_api import Page

from browserlens.core.types import PageState, RepresentationType
from browserlens.extractors._cdp import extract_ax_tree
from browserlens.extractors.base import BaseExtractor
from browserlens.formatter.ref_manager import RefManager


class HybridExtractor(BaseExtractor):
    """
    Combines the full a11y tree with screenshots cropped to canvas / visual regions.
    This gives the LLM the structured text it can act on (click, type) plus
    the visual context for areas that a11y cannot describe.
    """

    def __init__(self, ref_manager: RefManager) -> None:
        super().__init__(ref_manager)

    @property
    def representation_type(self) -> RepresentationType:
        return RepresentationType.HYBRID

    async def extract(self, page: Page) -> PageState:
        root = await extract_ax_tree(page, self._refs)
        screenshot_b64 = await self._capture_visual_regions(page)

        return PageState(
            url=page.url,
            title=await page.title(),
            representation_type=self.representation_type,
            root=root,
            screenshot_b64=screenshot_b64,
        )

    async def _capture_visual_regions(self, page: Page) -> str | None:
        """
        Find canvas elements and take a cropped screenshot of their bounding box.
        If multiple canvases exist, fall back to a full viewport screenshot.
        Returns base64 JPEG or None if no canvas elements found.
        """
        boxes = await page.evaluate("""() => {
            const canvases = document.querySelectorAll('canvas, [data-canvas], [data-visual]');
            const boxes = [];
            for (const c of canvases) {
                const r = c.getBoundingClientRect();
                if (r.width > 0 && r.height > 0) {
                    boxes.push({ x: r.left, y: r.top, width: r.width, height: r.height });
                }
            }
            return boxes;
        }""")

        if not boxes:
            return None

        if len(boxes) == 1:
            box = boxes[0]
            try:
                screenshot_bytes = await page.screenshot(
                    type="jpeg",
                    quality=80,
                    clip={
                        "x": max(0, box["x"]),
                        "y": max(0, box["y"]),
                        "width": box["width"],
                        "height": box["height"],
                    },
                )
                return base64.b64encode(screenshot_bytes).decode("utf-8")
            except Exception:
                pass

        # Multiple canvases or clip failed → full viewport
        screenshot_bytes = await page.screenshot(type="jpeg", quality=75)
        return base64.b64encode(screenshot_bytes).decode("utf-8")
