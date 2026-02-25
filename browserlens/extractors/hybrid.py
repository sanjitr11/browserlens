"""Hybrid extractor: a11y tree + targeted screenshots of canvas/visual regions."""

from __future__ import annotations

import base64

from playwright.async_api import Page

from browserlens.core.types import PageState, RepresentationType, StateNode
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
        # Full a11y tree
        snapshot = await page.accessibility.snapshot(interesting_only=True)
        root = self._convert_node(snapshot or {})

        # Screenshot only for canvas / WebGL bounding boxes
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
