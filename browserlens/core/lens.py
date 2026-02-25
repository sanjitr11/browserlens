"""BrowserLens — main orchestrator class."""

from __future__ import annotations

import time
from typing import Callable

from playwright.async_api import Page

from browserlens.core.types import ObservationResult, RepresentationType
from browserlens.differ.differ import StateDiffer
from browserlens.extractors.a11y import A11yExtractor
from browserlens.extractors.dom import DOMExtractor
from browserlens.extractors.hybrid import HybridExtractor
from browserlens.extractors.vision import VisionExtractor
from browserlens.formatter.formatter import OutputFormatter
from browserlens.formatter.ref_manager import RefManager
from browserlens.router.router import AdaptiveRouter


class BrowserLens:
    """
    Sits between the browser (Playwright) and an LLM agent.

    Usage:
        lens = BrowserLens()
        result = await lens.observe(page)
        # result.formatted_text → send to LLM
        # result.delta         → inspect what changed
    """

    def __init__(
        self,
        *,
        token_budget: int = 4096,
        enable_diffing: bool = True,
        enable_routing: bool = True,
        force_representation: RepresentationType | None = None,
        router_override: Callable | None = None,
    ) -> None:
        self.token_budget = token_budget
        self.enable_diffing = enable_diffing
        self.enable_routing = enable_routing
        self.force_representation = force_representation

        self._step = 0
        self._ref_manager = RefManager()
        self._router = AdaptiveRouter(override=router_override)
        self._differ = StateDiffer()
        self._formatter = OutputFormatter(
            ref_manager=self._ref_manager,
            token_budget=token_budget,
        )
        self._extractors = {
            RepresentationType.A11Y_TREE: A11yExtractor(self._ref_manager),
            RepresentationType.DISTILLED_DOM: DOMExtractor(self._ref_manager),
            RepresentationType.VISION: VisionExtractor(self._ref_manager),
            RepresentationType.HYBRID: HybridExtractor(self._ref_manager),
        }

    async def observe(self, page: Page) -> ObservationResult:
        """
        Observe the current browser page and return a compact, LLM-ready representation.

        On the first call: returns the full page state.
        On subsequent calls: returns only the delta (what changed).
        """
        t0 = time.monotonic()
        self._step += 1

        # Layer 1: choose representation type
        if self.force_representation is not None:
            rep_type = self.force_representation
        elif self.enable_routing:
            rep_type = await self._router.select(page)
        else:
            rep_type = RepresentationType.A11Y_TREE

        # Extract page state
        extractor = self._extractors[rep_type]
        page_state = await extractor.extract(page)
        page_state.step = self._step

        # Layer 2: diff against previous state
        if self.enable_diffing:
            delta = self._differ.diff(page_state)
        else:
            delta = None

        # Format for LLM
        formatted_text, token_count = self._formatter.format(page_state, delta)

        latency_ms = (time.monotonic() - t0) * 1000

        return ObservationResult(
            step=self._step,
            url=page_state.url,
            representation_type=rep_type,
            formatted_text=formatted_text,
            delta=delta,
            page_state=page_state,
            token_count=token_count,
            latency_ms=latency_ms,
        )

    def reset(self) -> None:
        """Reset step counter and all stored state (e.g., start a new task)."""
        self._step = 0
        self._ref_manager.reset()
        self._differ.reset()
